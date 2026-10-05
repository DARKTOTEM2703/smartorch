"""
Contexto de proyecto para el modelo: estructura real (analisis) + fragmentos relevantes (RAG).

Un mensaje trivial no recibe nada; una pregunta sobre el proyecto recibe primero el mapa
analizado (carpetas, puntos de entrada, clases y funciones) y despues los fragmentos de codigo
mas parecidos a la pregunta.
"""
import logging
from typing import Callable, Optional

from smartorch.core import analysis, effort as effort_mod, gating, workspaces

logger = logging.getLogger(__name__)

# Sin esto el modelo se disculpa ("no tengo acceso a tus archivos") aunque la estructura este justo debajo.
DIRECTIVE = (
    "Tienes acceso al proyecto del usuario: su estructura real y fragmentos de su código aparecen abajo. "
    "Si te preguntan qué archivos ves o qué contiene el proyecto, respóndelo con esa estructura; "
    "nunca digas que no puedes ver los archivos. 'VSC' significa Visual Studio Code, el editor donde trabaja."
)


def _structure(root: str) -> str:
    try:
        profile = analysis.get_profile(root)
    except Exception as e:  # el analisis nunca debe romper un chat
        logger.warning(f"[CONTEXT] analisis no disponible: {e}")
        return ""
    return "Estructura real del proyecto (analizada):\n" + analysis.overview(profile)


def build(user_text: str, fallback: Optional[Callable[[str], str]] = None) -> str:
    """Devuelve el contexto a inyectar, o '' si el mensaje no lo necesita."""
    if not gating.wants_project_context(user_text):
        return ""
    parts: list[str] = []

    root = workspaces.current_root()
    if root:
        structure = _structure(root)
        if structure:
            parts.append(DIRECTIVE)
            parts.append(structure)

    fragments = ""
    try:
        from smartorch.rag.retriever import search_formatted
        knobs = effort_mod.current().chat
        fragments = search_formatted(user_text, top_k=knobs.rag_top_k, max_chars=knobs.rag_chars)
    except ImportError:
        fragments = ""
    if not fragments and fallback:
        fragments = fallback(user_text)
    if fragments:
        parts.append("Fragmentos de código relevantes:\n" + fragments)

    return "\n\n---\n\n".join(parts)
