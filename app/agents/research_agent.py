"""Agente de Investigación: busca información en la documentación técnica. No hace cálculos."""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app import knowledge

PROMPT = (
    "Sos el agente de investigación. Tu única tarea es encontrar en la documentación técnica de la "
    "plataforma Atlas los datos que pide la instrucción, usando la herramienta buscar_documentacion.\n"
    "Respondé con los datos encontrados, textuales y con su fuente entre corchetes. No hagas cálculos "
    "ni conclusiones. Si la documentación no tiene el dato, decilo explícitamente."
)


@tool
def buscar_documentacion(consulta: str) -> str:
    """Busca en la documentación técnica de la plataforma Atlas (arquitectura, API, despliegues,
    incidentes, observabilidad y seguridad) y devuelve las 3 secciones más relevantes con su fuente.

    Usar para obtener datos documentados: objetivos de nivel de servicio, límites, plazos, códigos
    de error, procedimientos o reglas. La consulta conviene escribirla con los términos técnicos
    que usaría la documentación (por ejemplo "objetivo de disponibilidad presupuesto de error").
    """
    resultados = knowledge.buscar(consulta)
    if not resultados:
        return "Sin resultados para esa consulta. Probar con otros términos."
    return "\n\n".join(f"[{r['fuente']} · {r['seccion']}]\n{r['texto']}" for r in resultados)


TOOLS = [buscar_documentacion]


def build_research_agent(model: BaseChatModel):
    return create_react_agent(model, TOOLS, prompt=PROMPT, name="research_agent")
