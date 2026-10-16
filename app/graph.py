"""Grafo del orquestador (módulo 6) con checkpointer de Redis y nodo de aprobación humana."""

import logging
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.analyst_agent import build_analyst_agent
from app.agents.research_agent import build_research_agent
from app.hitl import aprobacion_humana
from app.llm import build_llm, guiones, modo_demo
from app.state import AgentName, Contribution, State

log = logging.getLogger("orquestador")

MAX_STEPS = 6  # decisiones del supervisor por solicitud: corta un posible bucle de correcciones
RECURSION_LIMIT = 30

SUPERVISOR_PROMPT = """Sos el supervisor de un equipo de agentes. No hacés el trabajo de los especialistas: \
decidís quién interviene ahora y validás lo que entregan.

Especialistas disponibles:
- research_agent: busca datos en la documentación técnica. No calcula.
- analyst_agent: hace cálculos y análisis sobre datos ya obtenidos. No busca información.
- synthesizer: redacta la respuesta final para el usuario. Elegirlo termina la tarea.

Dada la conversación actual, ¿quién debe intervenir ahora o es momento de finalizar?

Rúbrica para validar antes de elegir synthesizer (deben cumplirse todos los criterios):
1. Los datos que usa la respuesta salen de la documentación y tienen su fuente.
2. Todos los números que pide la solicitud fueron calculados por analyst_agent, no estimados.
3. La conclusión responde exactamente lo que pregunta la solicitud.
4. Los aportes no se contradicen entre sí.

Si un criterio no se cumple, enviá la tarea de nuevo al especialista responsable con una instrucción \
concreta de qué refinar. La instrucción debe contener todos los datos que el especialista necesita, \
porque no ve la conversación completa."""


class RouteDecision(BaseModel):
    """Decisión del supervisor: a quién delegar y con qué instrucción."""

    next_agent: AgentName
    instruction: str = Field(description="Tarea concreta para el especialista, con los datos que necesita.")
    reasoning: str = Field(description="Por qué esa decisión, citando el criterio de la rúbrica si aplica.")


def _resumen_aportes(state: State) -> str:
    if not state.get("contributions"):
        return "Todavía no hay aportes."
    return "\n\n".join(f"[{c['step']}] {c['agent']} (tarea: {c['task']}):\n{c['output']}" for c in state["contributions"])


def _faltante(state: State) -> AgentName | None:
    """Validación determinista: no se cierra sin al menos un aporte de cada especialista."""
    autores = {c["agent"] for c in state.get("contributions", [])}
    for agente in ("research_agent", "analyst_agent"):
        if agente not in autores:
            return agente
    return None


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None, modelos: dict[str, BaseChatModel] | None = None
) -> CompiledStateGraph:
    if modelos is None:
        if modo_demo():
            log.warning("Sin API key: se usan modelos guionados (modo demostración)")
            modelos = guiones()
        else:
            llm = build_llm()
            modelos = {rol: llm for rol in ("supervisor", "research_agent", "analyst_agent", "synthesizer")}

    decidir = modelos["supervisor"].with_structured_output(RouteDecision)
    especialistas = {
        "research_agent": build_research_agent(modelos["research_agent"]),
        "analyst_agent": build_analyst_agent(modelos["analyst_agent"]),
    }

    async def supervisor(state: State) -> dict:
        solicitud = state["messages"][0].content
        if state.get("steps", 0) >= MAX_STEPS:
            log.warning("Supervisor: se alcanzó el máximo de %d decisiones; se pasa a la síntesis", MAX_STEPS)
            return {"next_agent": "synthesizer", "instruction": "Redactar la respuesta con lo disponible.", "steps": 1}

        decision: RouteDecision = await decidir.ainvoke([
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=f"Solicitud del usuario:\n{solicitud}\n\nAportes hasta ahora:\n{_resumen_aportes(state)}"),
        ])
        destino = decision.next_agent
        if destino == "synthesizer" and (faltante := _faltante(state)):
            # Conflicto: el supervisor quiere cerrar sin un aporte obligatorio. Gana la validación.
            log.warning("Supervisor quiso cerrar sin aporte de %s; se le delega la tarea", faltante)
            destino = faltante
        log.info("Supervisor → %s: %s", destino, decision.reasoning)
        return {"next_agent": destino, "instruction": decision.instruction, "steps": 1,
                "messages": [AIMessage(content=f"Delego en {destino}: {decision.instruction}", name="supervisor")]}

    def nodo_especialista(nombre: AgentName):
        agente = especialistas[nombre]

        async def ejecutar(state: State) -> dict:
            # El especialista recibe solo su instrucción y los aportes previos, no todo el historial.
            entrada = f"Instrucción: {state['instruction']}\n\nAportes previos:\n{_resumen_aportes(state)}"
            resultado = await agente.ainvoke({"messages": [HumanMessage(content=entrada)]})
            salida = resultado["messages"][-1].content
            aporte: Contribution = {"agent": nombre, "task": state["instruction"], "output": salida,
                                    "step": state.get("steps", 0)}
            return {"contributions": [aporte], "messages": [AIMessage(content=salida, name=nombre)]}

        return ejecutar

    async def synthesizer(state: State) -> dict:
        respuesta = await modelos["synthesizer"].ainvoke([
            SystemMessage(content="Redactá la respuesta final para el usuario en español, usando solo los aportes de "
                                  "los especialistas. Mencioná la fuente de los datos."),
            HumanMessage(content=f"Solicitud:\n{state['messages'][0].content}\n\nAportes:\n{_resumen_aportes(state)}"),
        ])
        texto = respuesta.content
        if state.get("approval") and not state["approval"].get("aprobado"):
            texto += f" La acción solicitada ({state['critical_reason']}) no se ejecutó: el operador la rechazó."
        for accion in state.get("actions", []):
            texto += f" Se notificó el resultado en {accion['canal']}."
        aporte: Contribution = {"agent": "synthesizer", "task": state["instruction"], "output": texto,
                                "step": state.get("steps", 0)}
        return {"task_completed": True, "contributions": [aporte],
                "messages": [AIMessage(content=texto, name="synthesizer")]}

    def route_from_supervisor(state: State) -> Literal["research_agent", "analyst_agent", "aprobacion_humana"]:
        # Antes de cerrar, toda tarea pasa por el control de criticidad (human-in-the-loop).
        return "aprobacion_humana" if state["next_agent"] == "synthesizer" else state["next_agent"]

    builder = StateGraph(State)
    builder.add_node("supervisor", supervisor)
    builder.add_node("research_agent", nodo_especialista("research_agent"))
    builder.add_node("analyst_agent", nodo_especialista("analyst_agent"))
    builder.add_node("aprobacion_humana", aprobacion_humana)
    builder.add_node("synthesizer", synthesizer)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_from_supervisor)
    builder.add_edge("research_agent", "supervisor")  # todo aporte vuelve al supervisor para validarlo
    builder.add_edge("analyst_agent", "supervisor")
    builder.add_edge("aprobacion_humana", "synthesizer")
    builder.add_edge("synthesizer", END)
    return builder.compile(checkpointer=checkpointer)
