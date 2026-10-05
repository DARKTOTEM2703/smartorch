"""Router — clasifica la tarea y selecciona el modelo óptimo"""
import re
from smartorch.config import MODELS

# Palabras clave por categoría
_CODE_KEYWORDS = re.compile(
    r'\b(code|código|programa|script|función|class|def |import|bug|error|'
    r'python|javascript|typescript|bash|sql|html|css|api|endpoint|refactor|'
    r'compile|debugg?|fix|implement|write a|crea un|escrib[ei])\b',
    re.IGNORECASE
)
_AGENT_KEYWORDS = re.compile(
    r'\b(analiz[ae]|revis[ae]|lee|leer|edita|modifica|crea|genera|busca|'
    r'encuentra|explica todo|recorre|itera|paso a paso|plan|diseña|arquitectura)\b',
    re.IGNORECASE
)
_SECURITY_KEYWORDS = re.compile(
    r'\b(yara|sigma|malware|payload|c2|ransomware|trojan|dropper|shellcode|'
    r'exploit|cve|wazuh|siem|threat|ioc|mitre|att&ck|reverse.?engineer|'
    r'threat.?hunt|blue.?team|red.?team|purple.?team|backdoor|rootkit|'
    r'keylogger|pentest|pentesting|nmap|metasploit|burp|nuclei|ffuf)\b',
    re.IGNORECASE
)

TASK_TYPES = {
    "code":     "Tarea de programación / código",
    "agent":    "Tarea de análisis / agente multi-paso",
    "security": "Tarea de ciberseguridad / análisis",
    "chat":     "Conversación / pregunta general",
}


def classify(messages: list[dict]) -> str:
    """Clasifica el tipo de tarea basado en el último mensaje del usuario."""
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content", "")
            break

    if not user_text:
        return "chat"

    # Seguridad tiene prioridad — detectar antes que código genérico
    if _SECURITY_KEYWORDS.search(user_text):
        return "security"

    # Heurística por palabras clave
    if _CODE_KEYWORDS.search(user_text):
        return "code"
    if _AGENT_KEYWORDS.search(user_text) and len(user_text) > 80:
        return "agent"

    # Si el mensaje es corto y directo → chat
    if len(user_text) < 120:
        return "chat"

    return "agent"


def pick_model(task_type: str) -> str:
    """Devuelve el nombre del modelo Ollama para el tipo de tarea."""
    return MODELS.get(task_type, MODELS["chat"])


def route(messages: list[dict]) -> tuple[str, str]:
    """Clasifica y devuelve (task_type, model_name)."""
    task_type = classify(messages)
    model     = pick_model(task_type)
    return task_type, model
