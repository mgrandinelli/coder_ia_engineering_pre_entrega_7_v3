"""Human-in-the-loop: pausa obligatoria antes de ejecutar acciones con efectos secundarios.

Una tarea es crítica cuando la solicitud pide una acción que modifica algo fuera del sistema
(notificar a un canal, suspender despliegues). El grafo se detiene con `interrupt` en el nodo
`aprobacion_humana` y solo continúa cuando llega la decisión por POST /tasks/{id}/approve.
"""

import logging
import re

from langgraph.types import interrupt

from app.state import State

log = logging.getLogger("orquestador.hitl")

# Acciones con efectos secundarios: patrón que las detecta en la solicitud y descripción.
ACCIONES_CRITICAS = {
    r"\bnotific\w*|\bavis\w* al canal|#[\w-]+": "Enviar una notificación a un canal del equipo",
    # Solo una orden ("suspender los despliegues."), no una pregunta ("¿corresponde suspender los despliegues?").
    r"\bsuspend\w* (?:los |el )?despliegues\b(?![^.?]*\?)": "Suspender los despliegues de producción",
}


def evaluar_criticidad(solicitud: str) -> str | None:
    """Devuelve el motivo si la solicitud pide una acción con efectos secundarios; None si no."""
    motivos = [descripcion for patron, descripcion in ACCIONES_CRITICAS.items() if re.search(patron, solicitud, re.I)]
    return "; ".join(motivos) or None


def notificar_equipo(canal: str, mensaje: str) -> dict:
    """Acción con efecto secundario (simulada): en producción publicaría en el canal del equipo."""
    log.info("Notificación enviada a %s", canal)
    return {"accion": "notificar_equipo", "canal": canal, "mensaje": mensaje}


async def aprobacion_humana(state: State) -> dict:
    """Si la tarea es crítica, detiene el grafo hasta recibir la aprobación de un operador."""
    solicitud = state["messages"][0].content
    motivo = evaluar_criticidad(solicitud)
    if motivo is None:
        return {"critical_reason": None}

    resumen = state["contributions"][-1]["output"] if state.get("contributions") else ""
    # interrupt guarda el estado en el checkpointer y corta la ejecución; al reanudar devuelve la decisión.
    decision: dict = interrupt({"motivo": motivo, "solicitud": solicitud, "resultado_parcial": resumen})
    aprobado = bool(decision.get("aprobado"))
    log.info("Decisión del operador: %s", "aprobado" if aprobado else "rechazado")

    acciones = []
    if aprobado:
        canal = re.search(r"#[\w-]+", solicitud)
        acciones.append(notificar_equipo(canal.group() if canal else "#equipo", resumen))
    return {"critical_reason": motivo, "approval": decision, "actions": acciones}
