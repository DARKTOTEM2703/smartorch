"""
Niveles de esfuerzo de SmartOrch: Rapido, Normal y Maximo.

Cada nivel mueve parametros concretos, no es una etiqueta: cuantos pasos da el agente, cuanto
contexto recupera, si verifica con sintaxis/tests, si razona en dos pasos, cuanto cuesta en tiempo.
La idea: con un modelo chico, mas calculo bien dirigido (verificar, reintentar, investigar) cierra
parte de la distancia con un modelo grande.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ChatKnobs:
    thinking: Optional[bool] = None      # None = decide el orquestador; True/False = forzar
    ensemble: Optional[bool] = None
    speculative: Optional[bool] = None
    decompose: Optional[bool] = None
    rag_top_k: int = 5
    rag_chars: int = 3000
    max_tokens: int = 2048
    full_cot: bool = True                # False: prompt minimo salvo preguntas de proyecto


@dataclass(frozen=True)
class Effort:
    name: str
    label: str
    description: str
    # agente
    max_steps: int
    num_ctx: int
    num_predict: int
    syntax_check: bool                   # revisar sintaxis tras cada edicion
    run_tests: bool                      # exigir tests tras editar (si el proyecto los tiene)
    repair_attempts: int                 # reintentos cuando los tests fallan
    explore: bool                        # subagente explorador disponible
    plan_first: bool                     # pedir un plan (todo_write) antes de actuar
    chat: ChatKnobs = field(default_factory=ChatKnobs)
    candidates: int = 1                  # intentos completos: si los tests siguen fallando se deshace y se prueba otro enfoque


EFFORTS: dict[str, Effort] = {
    "rapido": Effort(
        name="rapido", label="Rápido", description="Un solo paso, sin verificación extra. Respuestas en segundos.",
        max_steps=4, num_ctx=8192, num_predict=512, syntax_check=False, run_tests=False, repair_attempts=0,
        explore=False, plan_first=False,
        chat=ChatKnobs(thinking=False, ensemble=False, speculative=False, decompose=False,
                       rag_top_k=3, rag_chars=1500, max_tokens=768, full_cot=False),
    ),
    "normal": Effort(
        name="normal", label="Normal", description="Equilibrio entre velocidad y calidad.",
        max_steps=10, num_ctx=12288, num_predict=1024, syntax_check=True, run_tests=False, repair_attempts=0,
        explore=True, plan_first=False,
        chat=ChatKnobs(),
    ),
    "maximo": Effort(
        name="maximo", label="Máximo", description="Planifica, investiga, verifica con tests y reintenta. Más lento, más fiable.",
        max_steps=18, num_ctx=14336, num_predict=1536, syntax_check=True, run_tests=True, repair_attempts=3,
        explore=True, plan_first=True, candidates=3,
        chat=ChatKnobs(thinking=True, ensemble=True, speculative=False, decompose=True,
                       rag_top_k=8, rag_chars=6000, max_tokens=3072),
    ),
}

DEFAULT = "normal"
_ALIASES = {"rápido": "rapido", "fast": "rapido", "low": "rapido", "medium": "normal", "máximo": "maximo",
            "max": "maximo", "high": "maximo"}

_current: ContextVar[Effort] = ContextVar("smartorch_effort", default=EFFORTS[DEFAULT])


def get(name: Optional[str]) -> Effort:
    key = (name or DEFAULT).strip().lower()
    return EFFORTS.get(_ALIASES.get(key, key), EFFORTS[DEFAULT])


def use(name: Optional[str]) -> Effort:
    """Fija el esfuerzo de la peticion en curso (visible en los hilos que esta lance)."""
    level = get(name)
    _current.set(level)
    return level


def current() -> Effort:
    return _current.get()


def catalog() -> list[dict]:
    return [{"id": e.name, "label": e.label, "description": e.description} for e in EFFORTS.values()]
