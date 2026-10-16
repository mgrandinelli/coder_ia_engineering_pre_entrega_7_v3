"""Observabilidad: trazas a Arize Phoenix con OpenInference, y métricas de cada llamada al modelo en el log."""

import logging
import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry import trace

log = logging.getLogger("orquestador.metricas")

PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME") or "atlas-orquestador"
_instrumentado = False


def init_tracing() -> bool:
    """Registra Phoenix como destino de las trazas e instrumenta LangChain/LangGraph.

    Con OpenInference, cada nodo del grafo, cada llamada al modelo (con sus tokens) y cada
    herramienta queda como un span. Se desactiva con PHOENIX_ENABLED=false (por ejemplo, en los tests).
    """
    global _instrumentado
    if _instrumentado or (os.getenv("PHOENIX_ENABLED") or "true").lower() == "false":
        return _instrumentado
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or "http://localhost:6006/v1/traces"
    proveedor = register(project_name=PROJECT_NAME, endpoint=endpoint, batch=True, verbose=False)
    LangChainInstrumentor().instrument(tracer_provider=proveedor)
    _instrumentado = True
    log.info("Trazas activas: proyecto '%s' en %s", PROJECT_NAME, endpoint)
    return True


tracer = trace.get_tracer("atlas-orquestador")


def traced(nombre: str) -> Callable:
    """Decorador: envuelve una corrutina en un span con el job_id como atributo."""

    def decorador(funcion: Callable) -> Callable:
        @wraps(funcion)
        async def envoltura(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(nombre) as span:
                job_id = kwargs.get("job_id") or (args[1] if len(args) > 1 else None)
                if job_id:
                    span.set_attribute("job.id", str(job_id))
                return await funcion(*args, **kwargs)

        return envoltura

    return decorador


class MetricsCallback(AsyncCallbackHandler):
    """Registra la latencia y los tokens de cada llamada al modelo, y los acumula por trabajo."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._inicio: dict[UUID, float] = {}
        self.llamadas = 0
        self.tokens_entrada = 0
        self.tokens_salida = 0
        self.latencia_modelo_s = 0.0

    async def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs: Any) -> None:
        self._inicio[run_id] = time.perf_counter()

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        latencia = time.perf_counter() - self._inicio.pop(run_id, time.perf_counter())
        uso = {}
        for generaciones in response.generations:
            for generacion in generaciones:
                mensaje = getattr(generacion, "message", None)
                uso = getattr(mensaje, "usage_metadata", None) or uso
        entrada, salida = uso.get("input_tokens", 0), uso.get("output_tokens", 0)
        self.llamadas += 1
        self.tokens_entrada += entrada
        self.tokens_salida += salida
        self.latencia_modelo_s += latencia
        log.info("job=%s llamada_modelo latencia=%.3fs tokens_entrada=%d tokens_salida=%d",
                 self.job_id, latencia, entrada, salida)

    def resumen(self) -> dict:
        return {"llamadas_modelo": self.llamadas, "tokens_entrada": self.tokens_entrada,
                "tokens_salida": self.tokens_salida, "latencia_modelo_s": round(self.latencia_modelo_s, 3)}
