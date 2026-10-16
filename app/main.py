"""API REST del orquestador: encola tareas, informa su estado y recibe aprobaciones humanas.

Uso: uvicorn app.main:app --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.llm import SOLICITUD_DEMO, modo_demo
from app.observability import init_tracing
from app.worker import JobStatus, JobStore, Worker

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

REDIS_URL = os.getenv("REDIS_URL") or "redis://localhost:6380"
CONCURRENCIA = int(os.getenv("WORKER_CONCURRENCY") or 5)


class TaskIn(BaseModel):
    solicitud: str = Field(default=SOLICITUD_DEMO, min_length=1, max_length=4000)


class TaskCreated(BaseModel):
    job_id: str
    status: JobStatus


class Approval(BaseModel):
    aprobado: bool = True
    comentario: str = ""


class TaskOut(BaseModel):
    job_id: str
    status: JobStatus
    solicitud: str
    created_at: str
    updated_at: str
    resultado: dict[str, Any] | None = None
    aprobacion_pendiente: dict[str, Any] | None = None
    metricas: dict[str, Any] | None = None
    error: str | None = None


def _es_sondeo(scope: dict) -> bool:
    """Las consultas de estado (GET /tasks/{id}) y /health no generan trazas: son sondeos repetitivos
    que taparían en el dashboard las trazas de las ejecuciones."""
    return scope.get("method") == "GET" and (scope.get("path", "").startswith("/tasks/") or scope.get("path") == "/health")


def create_app(redis: Redis | None = None, checkpointer: BaseCheckpointSaver | None = None) -> FastAPI:
    """Crea la aplicación. En los tests se inyectan un Redis falso y un checkpointer en memoria."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_tracing()
        cliente = redis or Redis.from_url(REDIS_URL)
        if checkpointer is None:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver

            async with AsyncRedisSaver.from_conn_string(REDIS_URL) as saver:
                await saver.asetup()  # crea los índices de RediSearch que usa el checkpointer
                async with _arrancar(app, cliente, saver):
                    yield
        else:
            async with _arrancar(app, cliente, checkpointer):
                yield

    @asynccontextmanager
    async def _arrancar(app: FastAPI, cliente: Redis, saver: BaseCheckpointSaver):
        app.state.store = JobStore(cliente)
        app.state.worker = Worker(app.state.store, saver, CONCURRENCIA)
        app.state.worker.start()
        logging.getLogger("orquestador").info("API lista: %d workers, modo demostración=%s", CONCURRENCIA, modo_demo())
        try:
            yield
        finally:
            await app.state.worker.stop()

    app = FastAPI(title="Orquestador multi-agente Atlas", version="1.0.0", lifespan=lifespan,
                  telemetry={"exclude": _es_sondeo})

    @app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
    async def crear_tarea(tarea: TaskIn) -> TaskCreated:
        """Registra la tarea en Redis, la encola y devuelve el job_id sin esperar a que termine."""
        job_id = await app.state.store.create(tarea.solicitud)
        await app.state.worker.submit(job_id, tarea.solicitud)
        return TaskCreated(job_id=job_id, status=JobStatus.PENDING)

    @app.get("/tasks/{job_id}", response_model=TaskOut)
    async def consultar_tarea(job_id: str) -> TaskOut:
        """Estado actual del trabajo: PENDING, RUNNING, WAITING_APPROVAL, DONE o FAILED."""
        job = await app.state.store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el trabajo {job_id}")
        return TaskOut(**job)

    @app.post("/tasks/{job_id}/approve", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
    async def aprobar_tarea(job_id: str, decision: Approval) -> TaskCreated:
        """Decisión del operador sobre una tarea crítica: reanuda el grafo desde el punto de pausa."""
        job = await app.state.store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el trabajo {job_id}")
        if job["status"] != JobStatus.WAITING_APPROVAL:
            raise HTTPException(status.HTTP_409_CONFLICT, f"El trabajo está en {job['status']}, no espera aprobación")
        await app.state.worker.resume(job_id, decision.model_dump())
        return TaskCreated(job_id=job_id, status=JobStatus.RUNNING)

    @app.get("/health")
    async def salud() -> dict:
        await app.state.store.redis.ping()
        return {"status": "ok", "modo_demo": modo_demo()}

    return app


app = create_app()
