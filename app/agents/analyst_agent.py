"""Agente de Análisis: procesa los datos que obtuvo la investigación. No busca información."""

import ast
import operator

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

PROMPT = (
    "Sos el agente de análisis. Recibís datos ya investigados y una instrucción de cálculo. Hacé todas "
    "las cuentas con las herramientas, nunca de memoria.\n"
    "Respondé con los resultados numéricos, cómo se obtuvieron y la conclusión que pide la instrucción. "
    "No busques información nueva: si falta un dato, indicá cuál."
)

_OPERADORES = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def _evaluar(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, int | float):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.left), _evaluar(nodo.right))
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand))
    raise ValueError("solo se permiten números y los operadores + - * / **")


@tool
def calcular(expresion: str) -> str:
    """Evalúa una expresión aritmética y devuelve el resultado redondeado a 4 decimales.

    Usar para cualquier cuenta: porcentajes, diferencias, conversiones de unidades.
    Acepta números y los operadores + - * / ** con paréntesis, por ejemplo "(75 - 55.56) / 100 * 21.6".
    No acepta variables ni funciones. Si la expresión no es válida, devuelve un mensaje de error.
    """
    try:
        return str(round(_evaluar(ast.parse(expresion, mode="eval").body), 4))
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return f"Error: {exc}"


@tool
def presupuesto_de_error(slo_porcentaje: float, ventana_dias: int, minutos_de_caida: float) -> dict:
    """Calcula el presupuesto de error de un objetivo de disponibilidad (SLO).

    Usar cuando hay un porcentaje de disponibilidad objetivo (por ejemplo 99.95), la ventana en días
    en que se mide (por ejemplo 30) y los minutos de indisponibilidad ya ocurridos.
    Devuelve los minutos de caída permitidos en la ventana, el porcentaje del presupuesto consumido
    y los minutos que quedan.
    """
    permitidos = ventana_dias * 24 * 60 * (100 - slo_porcentaje) / 100
    return {
        "minutos_permitidos": round(permitidos, 2),
        "minutos_consumidos": minutos_de_caida,
        "porcentaje_consumido": round(100 * minutos_de_caida / permitidos, 2),
        "minutos_restantes": round(permitidos - minutos_de_caida, 2),
    }


TOOLS = [calcular, presupuesto_de_error]


def build_analyst_agent(model: BaseChatModel):
    return create_react_agent(model, TOOLS, prompt=PROMPT, name="analyst_agent")
