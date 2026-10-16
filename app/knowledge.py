"""Base de conocimientos: la documentación técnica de la plataforma Atlas de pre-entregas anteriores.

Simula la base vectorial con una búsqueda BM25 en memoria sobre las secciones de cada documento,
para que el orquestador funcione sin servicios externos.
"""

import re
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

DATA_DIR = Path(__file__).parent.parent / "data"


def tokenizar(texto: str) -> list[str]:
    return re.findall(r"[\w-]+", texto.lower())


@lru_cache(maxsize=1)
def secciones() -> list[dict]:
    """Divide cada documento en secciones `## ...`: cada sección es una unidad de búsqueda."""
    resultado = []
    for ruta in sorted(DATA_DIR.glob("*.md")):
        cuerpo = re.sub(r"^---\n.*?\n---\n", "", ruta.read_text(encoding="utf-8"), flags=re.DOTALL)
        for bloque in re.split(r"\n(?=## )", cuerpo):
            if bloque.startswith("## "):
                titulo, _, texto = bloque.partition("\n")
                resultado.append({"fuente": ruta.name, "seccion": titulo[3:].strip(), "texto": texto.strip()})
    return resultado


@lru_cache(maxsize=1)
def _indice() -> BM25Okapi:
    return BM25Okapi([tokenizar(f"{s['seccion']} {s['texto']}") for s in secciones()])


def buscar(consulta: str, k: int = 3) -> list[dict]:
    puntajes = _indice().get_scores(tokenizar(consulta))
    mejores = sorted(range(len(puntajes)), key=lambda i: puntajes[i], reverse=True)[:k]
    return [secciones()[i] for i in mejores if puntajes[i] > 0]
