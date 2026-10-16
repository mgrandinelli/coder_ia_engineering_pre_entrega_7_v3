"""Estado compartido del orquestador: qué se pidió, quién aportó qué y cuándo terminar."""

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import MessagesState

AgentName = Literal["research_agent", "analyst_agent", "synthesizer"]


class Contribution(TypedDict):
    """Un aporte de un especialista: quién lo hizo, qué se le pidió y qué devolvió."""

    agent: AgentName
    task: str
    output: str
    step: int


class State(MessagesState):
    """Hereda `messages` de MessagesState. Cada campo extra tiene un único dueño que lo escribe."""

    next_agent: AgentName | None  # lo escribe el supervisor
    instruction: str  # instrucción puntual del supervisor para el próximo especialista
    task_completed: bool  # lo escribe el sintetizador al cerrar
    # Registro de aportes: se agregan, nunca se pisan (reductor operator.add).
    contributions: Annotated[list[Contribution], operator.add]
    steps: Annotated[int, operator.add]  # decisiones tomadas por el supervisor
    # Human-in-the-loop: los escribe solo el nodo de aprobación humana.
    critical_reason: str | None  # por qué la tarea se consideró crítica (None si no lo es)
    approval: dict | None  # decisión del operador: {"aprobado": bool, "comentario": str}
    actions: Annotated[list[dict], operator.add]  # acciones con efectos secundarios ya ejecutadas
