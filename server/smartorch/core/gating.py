"""
Compuerta de contexto: decide cuando un mensaje merece RAG y razonamiento
encadenado, y cuando basta una respuesta directa.

Sin esto, un "hola" o "responde solo: listo" recibia fragmentos del proyecto
y un prompt largo, y el modelo contestaba sobre el proyecto en vez de a la pregunta.
"""
import re

MIN_RAG_SCORE = 0.40  # por debajo de esto un fragmento es ruido

_CODE_CUES = re.compile(
    r"```"
    r"|\b\w+\.(?:py|js|ts|tsx|jsx|go|rs|java|c|cpp|cs|rb|php|md|json|ya?ml|toml|html|css|sh|ps1)\b"
    r"|[\\/]\w+[\\/]"
    r"|\b[a-z]+_[a-z_]+\b"
    r"|\w+\(\)"
    r"|traceback|stack ?trace|exception|error:",
    re.IGNORECASE,
)
_CAMEL_CASE = re.compile(r"\b[a-z]+[A-Z][A-Za-z]+\b")
_PROJECT_WORDS = re.compile(
    r"\b(?:proyecto|project|repo|repositorio|codebase|workspace|archivo|file|funci[oó]n|function|clase|class"
    r"|m[eé]todo|m[oó]dulo|endpoint|servidor|server|bug|refactor\w*|tests?|commit|import|variable|build)\b",
    re.IGNORECASE,
)
_CHITCHAT = re.compile(
    r"^\s*(?:hola|hey|buenas|buenos d[ií]as|gracias|ok|vale|listo|perfecto|adi[oó]s|chao|responde|di|dime"
    r"|cu[eé]ntame|qu[eé] tal|c[oó]mo est[aá]s)\b",
    re.IGNORECASE,
)


def _has_cues(text: str) -> bool:
    return bool(_CODE_CUES.search(text) or _CAMEL_CASE.search(text) or _PROJECT_WORDS.search(text))


def wants_project_context(text: str) -> bool:
    """True si conviene inyectar fragmentos del proyecto (RAG)."""
    t = (text or "").strip()
    if len(t) < 10:
        return False
    if _has_cues(t):
        return True
    if _CHITCHAT.match(t):
        return False
    return len(t) >= 80


def is_trivial(text: str) -> bool:
    """True para saludos y pedidos cortos que no necesitan el prompt de razonamiento."""
    t = (text or "").strip()
    if len(t) < 10:
        return True
    if _has_cues(t):
        return False
    return bool(_CHITCHAT.match(t)) or len(t) < 40


MINIMAL_SYSTEM = (
    "Eres SmartOrch, un asistente de programación local. "
    "Responde de forma breve y directa, en el idioma del usuario, "
    "y sigue exactamente lo que se te pide."
)
