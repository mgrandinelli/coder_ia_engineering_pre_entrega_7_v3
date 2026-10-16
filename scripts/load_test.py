"""Prueba de carga: 5 peticiones concurrentes contra la API, con aprobación automática de las tareas críticas.

Uso: python scripts/load_test.py [--url http://localhost:8000] [--n 5]
Mide la latencia de punta a punta de cada trabajo y guarda el resumen en screenshots/carga.json.
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

SOLICITUD = (
    "Según la documentación de Atlas, ¿cuántos minutos de caída por mes permite el objetivo de "
    "disponibilidad? Si en lo que va del mes hubo 12 minutos de caída, ¿qué porcentaje del presupuesto "
    "de error se consumió y corresponde suspender los despliegues?"
)
SALIDA = Path(__file__).resolve().parent.parent / "screenshots" / "carga.json"
# Precio de gpt-4o-mini en USD por token (el mismo que usa Phoenix para estimar el costo).
PRECIO_ENTRADA = 0.15 / 1_000_000
PRECIO_SALIDA = 0.60 / 1_000_000


def costo(metricas: dict | None) -> float:
    metricas = metricas or {}
    return metricas.get("tokens_entrada", 0) * PRECIO_ENTRADA + metricas.get("tokens_salida", 0) * PRECIO_SALIDA


def solicitudes(n: int) -> list[str]:
    """Alterna solicitudes comunes y críticas (piden notificar a un canal, así que necesitan aprobación)."""
    return [SOLICITUD + (" Notificar el resultado al canal #atlas-despliegues." if i % 2 == 0 else "") for i in range(n)]


async def ejecutar(cliente: httpx.AsyncClient, solicitud: str, pausa_operador_s: float) -> dict:
    inicio = time.perf_counter()
    job_id = (await cliente.post("/tasks", json={"solicitud": solicitud})).json()["job_id"]
    aprobado = False
    while True:
        job = (await cliente.get(f"/tasks/{job_id}")).json()
        if job["status"] == "WAITING_APPROVAL" and not aprobado:
            await asyncio.sleep(pausa_operador_s)  # el operador revisa y aprueba
            await cliente.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "comentario": "Revisado"})
            aprobado = True
        elif job["status"] in ("DONE", "FAILED"):
            return {"job_id": job_id, "status": job["status"], "critica": aprobado,
                    "latencia_s": round(time.perf_counter() - inicio, 3), "metricas": job.get("metricas"),
                    "costo_usd": round(costo(job.get("metricas")), 6)}
        await asyncio.sleep(0.2)


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    k = (len(ordenados) - 1) * p
    inferior = int(k)
    superior = min(inferior + 1, len(ordenados) - 1)
    return round(ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * (k - inferior), 3)


async def main(url: str = "http://localhost:8000", n: int = 5, pausa_operador_s: float = 1.0) -> dict:
    async with httpx.AsyncClient(base_url=url, timeout=60) as cliente:
        inicio = time.perf_counter()
        trabajos = await asyncio.gather(*(ejecutar(cliente, s, pausa_operador_s) for s in solicitudes(n)))
        total = round(time.perf_counter() - inicio, 3)

    latencias = [t["latencia_s"] for t in trabajos]
    resumen = {
        "peticiones": n,
        "duracion_total_s": total,
        "latencia_p50_s": percentil(latencias, 0.50),
        "latencia_p95_s": percentil(latencias, 0.95),
        "latencia_media_s": round(statistics.mean(latencias), 3),
        "costo_medio_por_ejecucion_usd": round(statistics.mean(t["costo_usd"] for t in trabajos), 6),
        "costo_total_usd": round(sum(t["costo_usd"] for t in trabajos), 6),
        "tokens_totales": sum((t["metricas"] or {}).get("tokens_entrada", 0) + (t["metricas"] or {}).get("tokens_salida", 0)
                              for t in trabajos),
        "trabajos": trabajos,
    }
    print(f"{'job_id':<34}{'estado':<8}{'crítica':<9}{'latencia (s)':>13}{'tokens':>8}{'costo (USD)':>13}")
    for t in trabajos:
        tokens = (t["metricas"] or {}).get("tokens_entrada", 0) + (t["metricas"] or {}).get("tokens_salida", 0)
        print(f"{t['job_id']:<34}{t['status']:<8}{'sí' if t['critica'] else 'no':<9}{t['latencia_s']:>13.3f}"
              f"{tokens:>8}{t['costo_usd']:>13.6f}")
    print(f"\n{n} peticiones concurrentes en {total} s | p50 {resumen['latencia_p50_s']} s | "
          f"p95 {resumen['latencia_p95_s']} s | tokens {resumen['tokens_totales']} | "
          f"costo medio por ejecución {resumen['costo_medio_por_ejecucion_usd']:.6f} USD")
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.n))
