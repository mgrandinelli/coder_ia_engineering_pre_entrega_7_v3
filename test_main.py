"""Tests de la pre-entrega 7. No usan la red, claves, Redis ni Phoenix reales:
Redis se reemplaza por fakeredis, el checkpointer por uno en memoria y los modelos por guiones.

Ejecutar con: pytest -v
"""

import asyncio
import os
import time

os.environ["PHOENIX_ENABLED"] = "false"
os.environ.pop("OPENAI_API_KEY", None)
os.environ["LLM_PROVIDER"] = "openai"

import fakeredis
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.memory import InMemorySaver

import app.worker as worker_mod
from app import observability
from app.graph import build_graph
from app.hitl import evaluar_criticidad
from app.llm import SOLICITUD_CRITICA, SOLICITUD_DEMO, guiones
from app.main import create_app
from app.worker import JobStatus, JobStore

FINALES = {"DONE", "FAILED", "WAITING_APPROVAL"}


@pytest.fixture
def client():
    with TestClient(create_app(redis=fakeredis.aioredis.FakeRedis(), checkpointer=InMemorySaver())) as cliente:
        yield cliente


def esperar(client: TestClient, job_id: str, estados=FINALES, limite_s: float = 10) -> dict:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        job = client.get(f"/tasks/{job_id}").json()
        if job["status"] in estados:
            return job
        time.sleep(0.05)
    raise AssertionError(f"el trabajo {job_id} no llegó a {estados}")


def crear(client: TestClient, solicitud: str = SOLICITUD_DEMO) -> str:
    respuesta = client.post("/tasks", json={"solicitud": solicitud})
    assert respuesta.status_code == 202
    return respuesta.json()["job_id"]


# --- Orquestación asíncrona y API ---


def test_post_encola_y_devuelve_job_id_sin_esperar_al_grafo(client, monkeypatch):
    construir = worker_mod.build_graph

    def grafo_lento(checkpointer):
        grafo = construir(checkpointer)
        original = grafo.ainvoke

        async def lento(*args, **kwargs):
            await asyncio.sleep(1)
            return await original(*args, **kwargs)

        grafo.ainvoke = lento
        return grafo

    monkeypatch.setattr(worker_mod, "build_graph", grafo_lento)
    inicio = time.perf_counter()
    respuesta = client.post("/tasks", json={"solicitud": SOLICITUD_DEMO})
    demora = time.perf_counter() - inicio

    assert respuesta.status_code == 202 and demora < 0.5
    assert respuesta.json()["status"] == "PENDING"
    assert client.get(f"/tasks/{respuesta.json()['job_id']}").json()["status"] in ("PENDING", "RUNNING")


def test_una_tarea_comun_termina_en_done_con_resultado_y_metricas(client):
    job = esperar(client, crear(client))

    assert job["status"] == "DONE"
    assert "21,6 minutos" in job["resultado"]["respuesta"]
    assert [c["agent"] for c in job["resultado"]["contributions"]] == [
        "research_agent", "analyst_agent", "analyst_agent", "synthesizer"]
    assert job["metricas"]["llamadas_modelo"] >= 1 and job["metricas"]["duracion_s"] >= 0


def test_cinco_peticiones_concurrentes_terminan_todas(client):
    ids = [crear(client) for _ in range(5)]

    assert [esperar(client, i)["status"] for i in ids] == ["DONE"] * 5


def test_trabajo_inexistente_devuelve_404(client):
    assert client.get("/tasks/no-existe").status_code == 404
    assert client.post("/tasks/no-existe/approve", json={"aprobado": True}).status_code == 404


def test_solicitud_vacia_se_rechaza(client):
    assert client.post("/tasks", json={"solicitud": ""}).status_code == 422


# --- Persistencia con Redis ---


def test_job_store_guarda_y_recupera_el_estado():
    async def probar():
        redis = fakeredis.aioredis.FakeRedis()
        store = JobStore(redis)
        job_id = await store.create("pregunta")
        await store.update(job_id, JobStatus.DONE, resultado={"respuesta": "ok"}, metricas={"tokens_entrada": 3})
        return await store.get(job_id), await redis.ttl(f"job:{job_id}")

    job, ttl = asyncio.run(probar())

    assert job["status"] == "DONE" and job["resultado"] == {"respuesta": "ok"} and job["metricas"]["tokens_entrada"] == 3
    assert ttl > 0


def test_si_el_agente_falla_el_trabajo_queda_en_failed(client, monkeypatch):
    def grafo_roto(checkpointer):
        raise ConnectionError("sin red hacia el proveedor")

    monkeypatch.setattr(worker_mod, "build_graph", grafo_roto)
    job = esperar(client, crear(client))

    assert job["status"] == "FAILED"
    assert job["error"] == "ConnectionError: sin red hacia el proveedor"


def test_una_salida_inesperada_del_modelo_termina_en_failed(client, monkeypatch):
    # Caso de borde: el supervisor se queda sin decisiones (equivale a una salida que no se pudo interpretar).
    def sin_decisiones(checkpointer):
        modelos = guiones()
        modelos["supervisor"].decisiones = iter(())
        return build_graph(checkpointer, modelos)

    monkeypatch.setattr(worker_mod, "build_graph", sin_decisiones)
    job = esperar(client, crear(client))

    assert job["status"] == "FAILED"


# --- Human-in-the-loop ---


@pytest.mark.parametrize("solicitud, critica", [
    (SOLICITUD_DEMO, False),
    (SOLICITUD_CRITICA, True),
    ("Suspender los despliegues.", True),
    ("¿Corresponde suspender los despliegues?", False),
])
def test_evaluar_criticidad(solicitud, critica):
    assert (evaluar_criticidad(solicitud) is not None) == critica


def test_una_tarea_critica_se_pausa_hasta_la_aprobacion(client):
    job_id = crear(client, SOLICITUD_CRITICA)
    pausado = esperar(client, job_id)

    assert pausado["status"] == "WAITING_APPROVAL"
    assert pausado["aprobacion_pendiente"]["motivo"] == "Enviar una notificación a un canal del equipo"

    assert client.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "comentario": "ok"}).status_code == 202
    final = esperar(client, job_id, {"DONE", "FAILED"})

    assert final["status"] == "DONE"
    assert final["resultado"]["acciones"][0]["canal"] == "#atlas-despliegues"
    assert "Se notificó el resultado en #atlas-despliegues" in final["resultado"]["respuesta"]


def test_si_el_operador_rechaza_no_se_ejecuta_la_accion(client):
    job_id = crear(client, SOLICITUD_CRITICA)
    esperar(client, job_id)

    client.post(f"/tasks/{job_id}/approve", json={"aprobado": False, "comentario": "no notificar"})
    final = esperar(client, job_id, {"DONE", "FAILED"})

    assert final["resultado"]["acciones"] == []
    assert "el operador la rechazó" in final["resultado"]["respuesta"]


def test_no_se_puede_aprobar_una_tarea_que_no_espera_aprobacion(client):
    job_id = crear(client)
    esperar(client, job_id)

    assert client.post(f"/tasks/{job_id}/approve", json={"aprobado": True}).status_code == 409


def test_el_grafo_pasa_por_la_aprobacion_humana_antes_de_cerrar():
    aristas = {(a.source, a.target) for a in build_graph(InMemorySaver(), guiones()).get_graph().edges}

    assert ("supervisor", "aprobacion_humana") in aristas
    assert ("aprobacion_humana", "synthesizer") in aristas
    assert ("supervisor", "synthesizer") not in aristas


# --- Observabilidad ---


def test_metrics_callback_registra_latencia_y_tokens():
    metricas = observability.MetricsCallback("job-1")
    mensaje = AIMessage(content="x", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})

    async def probar():
        from uuid import uuid4
        run_id = uuid4()
        await metricas.on_chat_model_start({}, [[]], run_id=run_id)
        await metricas.on_llm_end(LLMResult(generations=[[ChatGeneration(message=mensaje)]]), run_id=run_id)

    asyncio.run(probar())

    assert metricas.resumen()["tokens_entrada"] == 120 and metricas.resumen()["tokens_salida"] == 30
    assert metricas.resumen()["llamadas_modelo"] == 1


def test_sin_phoenix_la_instrumentacion_queda_desactivada():
    assert observability.init_tracing() is False


def test_las_claves_no_estan_en_el_repositorio():
    from pathlib import Path

    base = Path(__file__).parent
    assert "OPENAI_API_KEY=\n" in (base / ".env.example").read_text()
    assert "REDIS_URL=" in (base / ".env.example").read_text()
    assert ".env" in (base / ".gitignore").read_text().split()
