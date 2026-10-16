"""Trabajos en segundo plano: estado de cada job en Redis y workers asíncronos que ejecutan el grafo."""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from redis.asyncio import Redis

from app.graph import RECURSION_LIMIT, build_graph
from app.observability import MetricsCallback, traced

log = logging.getLogger("orquestador.worker")

JOB_TTL_S = 7 * 24 * 3600  # los trabajos se conservan una semana en Redis


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


class JobStore:
    """Estado de los trabajos en Redis: un hash `job:<id>` por trabajo."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    @staticmethod
    def _clave(job_id: str) -> str:
        return f"job:{job_id}"

    async def create(self, solicitud: str) -> str:
        job_id = uuid.uuid4().hex
        ahora = datetime.now(UTC).isoformat()
        await self.redis.hset(self._clave(job_id), mapping={
            "job_id": job_id, "status": JobStatus.PENDING, "solicitud": solicitud,
            "created_at": ahora, "updated_at": ahora,
        })
        await self.redis.expire(self._clave(job_id), JOB_TTL_S)
        return job_id

    async def update(self, job_id: str, status: JobStatus, **campos: Any) -> None:
        valores = {k: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for k, v in campos.items()}
        await self.redis.hset(self._clave(job_id), mapping={
            "status": status, "updated_at": datetime.now(UTC).isoformat(), **valores})

    async def get(self, job_id: str) -> dict | None:
        datos = await self.redis.hgetall(self._clave(job_id))
        if not datos:
            return None
        datos = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                 for k, v in datos.items()}
        for campo in ("resultado", "aprobacion_pendiente", "metricas"):
            if campo in datos:
                datos[campo] = json.loads(datos[campo])
        return datos


def _sumar(previo: dict, actual: dict) -> dict:
    """Suma las métricas de los tramos de un trabajo (antes y después de una pausa para aprobación)."""
    return {k: round(previo.get(k, 0) + v, 3) if isinstance(v, float) else previo.get(k, 0) + v
            for k, v in actual.items()}


class Worker:
    """Consume una cola en memoria con N tareas asíncronas. Cada trabajo corre el grafo en su propio hilo
    de LangGraph (thread_id = job_id), con el estado guardado en el checkpointer de Redis."""

    def __init__(self, store: JobStore, checkpointer: BaseCheckpointSaver, concurrencia: int = 5) -> None:
        self.store = store
        self.checkpointer = checkpointer
        self.concurrencia = concurrencia
        self.cola: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._tareas: list[asyncio.Task] = []

    def start(self) -> None:
        self._tareas = [asyncio.create_task(self._consumir(), name=f"worker-{i}") for i in range(self.concurrencia)]

    async def stop(self) -> None:
        for tarea in self._tareas:
            tarea.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)

    async def submit(self, job_id: str, solicitud: str) -> None:
        entrada = {"messages": [HumanMessage(content=solicitud)], "contributions": [], "steps": 0,
                   "task_completed": False, "actions": []}
        await self.cola.put((job_id, entrada))

    async def resume(self, job_id: str, decision: dict) -> None:
        await self.store.update(job_id, JobStatus.RUNNING)
        await self.cola.put((job_id, Command(resume=decision)))

    async def _consumir(self) -> None:
        while True:
            job_id, entrada = await self.cola.get()
            try:
                await self._run(job_id, entrada)
            finally:
                self.cola.task_done()

    @traced("job")
    async def _run(self, job_id: str, entrada: Any) -> None:
        metricas = MetricsCallback(job_id)
        inicio = time.perf_counter()
        previo = (await self.store.get(job_id) or {}).get("metricas") or {}  # tramo anterior a una pausa
        await self.store.update(job_id, JobStatus.RUNNING)
        try:
            graph = build_graph(self.checkpointer)
            config = {"configurable": {"thread_id": job_id}, "recursion_limit": RECURSION_LIMIT,
                      "callbacks": [metricas], "metadata": {"job_id": job_id}}
            estado = await graph.ainvoke(entrada, config)
        except Exception as exc:
            # Sin esto, el cliente quedaría consultando un trabajo que nunca termina.
            log.exception("job=%s falló", job_id)
            await self.store.update(job_id, JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}",
                                    metricas=_sumar(previo, metricas.resumen()))
            return

        acumuladas = _sumar(previo, {**metricas.resumen(), "duracion_s": round(time.perf_counter() - inicio, 3)})
        duracion = acumuladas["duracion_s"]
        if "__interrupt__" in estado:
            pausa = estado["__interrupt__"][0].value
            log.info("job=%s esperando aprobación humana: %s", job_id, pausa["motivo"])
            await self.store.update(job_id, JobStatus.WAITING_APPROVAL, aprobacion_pendiente=pausa,
                                    metricas=acumuladas)
            return

        resultado = {
            "respuesta": estado["messages"][-1].content,
            "contributions": estado["contributions"],
            "acciones": estado.get("actions", []),
            "aprobacion": estado.get("approval"),
        }
        await self.store.update(job_id, JobStatus.DONE, resultado=resultado, metricas=acumuladas)
        log.info("job=%s terminado en %.3fs", job_id, duracion)
