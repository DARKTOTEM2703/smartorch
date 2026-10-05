"""
Chain-of-Thought y técnicas de amplificación para modelos pequeños.

Técnicas implementadas:
  1. Expert Persona + Stack Detection  — prompts especializados por tecnología
  2. SOLID/DRY/Clean Architecture      — principios inyectados en tareas de código
  3. Thinking simulado (2-call)        — razonamiento garantizado en 8B
  4. Confidence scoring                — detector de alucinaciones
  5. Self-Verification                 — auto-corrección para tareas críticas
  6. Few-shot YARA/Sigma               — ejemplos reales inyectados al prompt
"""
import re

# ── Anti-alucinación — cláusula en todos los prompts ─────────────────────────
_NO_HALLUCINATION = (
    "REGLA CRITICA: Si no sabes algo con certeza, di exactamente "
    "'No tengo certeza de [X]'. NUNCA inventes nombres de funciones, "
    "APIs, parametros o hechos que no puedas verificar."
)

# ── SOLID/DRY/Clean — inyectado en tareas de código ──────────────────────────
_SOLID_DRY = """
PRINCIPIOS OBLIGATORIOS:
- S  Single Responsibility — cada clase/funcion hace UNA sola cosa
- O  Open/Closed — abierto para extension, cerrado para modificacion
- L  Liskov — las subclases son sustituibles por la base
- I  Interface Segregation — interfaces especificas, no generales
- D  Dependency Inversion — depender de abstracciones, no implementaciones
- DRY  Don't Repeat Yourself — si algo se repite 2 veces, extrae una funcion
- KISS — solucion mas simple que funcione correctamente
- Nombres descriptivos — variable/funcion/clase deben explicarse solas"""

# ── System prompts por stack ──────────────────────────────────────────────────

_BASE_CODE = f"""Eres un ingeniero de software senior con 20 años de experiencia.

PROCESO antes de escribir codigo:
1. Analiza exactamente que se pide
2. Identifica casos borde y dependencias
3. Planifica la arquitectura mas limpia
4. Implementa con los principios de calidad

{_SOLID_DRY}
{_NO_HALLUCINATION}
Codigo correcto > codigo elegante. Menciona bugs obvios aunque no te lo pidan.
Responde en el idioma del usuario."""

SYSTEM_CODE_WEB = f"""{_BASE_CODE}

ESPECIALIDAD — DESARROLLO WEB:
Backend (FastAPI/Django/Express/NestJS):
  - Separar routers, schemas, services, repositories
  - Validacion en la capa de entrada (Pydantic/Zod/class-validator)
  - Inyeccion de dependencias explicita
  - Manejo de errores centralizado (middleware/exception handlers)
Frontend (React/Vue/Angular):
  - Componentes pequenos y reutilizables (< 150 lineas)
  - Estado local vs global — usar local primero
  - Custom hooks/composables para logica reutilizable
  - Accesibilidad (aria-label, roles semanticos)
Base de datos:
  - Migraciones versionadas, nunca ALTER manual
  - Indices en columnas de busqueda frecuente
  - Transacciones explicitas para operaciones multiples"""

SYSTEM_CODE_MOBILE = f"""{_BASE_CODE}

ESPECIALIDAD — DESARROLLO MOBILE:
Flutter (Dart):
  - BLoC o Riverpod para estado — separar UI de logica
  - Widgets pequenos y componibles
  - const constructors donde sea posible
  - Manejo de lifecycle (dispose, mounted checks)
React Native:
  - FlatList sobre ScrollView para listas largas
  - useCallback/useMemo para evitar re-renders
  - StyleSheet.create fuera del componente
  - Platform-specific code separado (.ios.js / .android.js)
Kotlin/Swift nativo:
  - MVVM o Clean Architecture
  - Coroutines/async-await para IO
  - Null safety explicito
Comun mobile:
  - Offline-first — cachear datos, manejar sin conexion
  - Permisos solicitados en contexto, no al arrancar
  - Bateria — evitar wakelock innecesario"""

SYSTEM_CODE_SYSTEMS = f"""{_BASE_CODE}

ESPECIALIDAD — SISTEMAS/BAJO NIVEL:
- Gestion de memoria explicita — documentar ownership
- Manejo de errores sin excepciones — return codes o Result
- Concurrencia — documentar que es thread-safe y que no
- Evitar UB (undefined behavior) — inicializar todo
- RAII en C++ — recursos liberados en destructores
- Rust: preferir safe code, unsafe solo con justificacion
- Go: errores siempre como ultimo return value"""

SYSTEM_CODE_SECURITY = f"""{_BASE_CODE}

ESPECIALIDAD — CODIGO DE SEGURIDAD:
- Input validation en la frontera del sistema (no en interior)
- Parametrize queries — nunca concatenar SQL
- Principio de minimo privilegio
- Secretos en variables de entorno, nunca hardcoded
- OWASP Top 10 en mente para web
- Sanitizar outputs (XSS, injection)
- Logging de seguridad sin datos sensibles"""

# Prompt generico (fallback)
SYSTEM_CODE = _BASE_CODE

SYSTEM_AGENT = f"""Eres un agente de IA experto en automatizacion y desarrollo.
{_NO_HALLUCINATION}

PROCESO:
1. ANALIZA el objetivo completo
2. DESCOMPONE en pasos concretos y ejecutables
3. EJECUTA un paso a la vez, verificando resultados
4. ADAPTA el plan si algo falla
5. CONFIRMA cuando el objetivo esta completado

Nunca asumas — verifica. Si algo es ambiguo, pregunta antes de actuar."""

SYSTEM_SECURITY = f"""Eres un especialista en ciberseguridad con experiencia en:
- Analisis de malware y reverse engineering
- Threat hunting y deteccion de intrusiones
- MITRE ATT&CK, YARA, Sigma, Wazuh
- Red Team / Blue Team / Purple Team
{_NO_HALLUCINATION}

PROCESO para analisis:
1. IDENTIFICO el tipo de amenaza o comportamiento
2. MAPEO a MITRE ATT&CK (tactica + tecnica exacta)
3. ANALIZO IoCs y patrones unicos para deteccion
4. GENERO reglas y recomendaciones precisas

Responde con rigor tecnico. Contexto: educativo y defensivo."""

SYSTEM_COT = f"""Eres un asistente tecnico experto.
{_NO_HALLUCINATION}
Razona antes de responder. Para cada pregunta:
- Analiza el problema
- Considera las alternativas
- Da la respuesta mas precisa y util
Responde en el idioma del usuario. Se directo y completo."""

SYSTEM_DEFAULT = f"""Eres un asistente tecnico experto.
{_NO_HALLUCINATION}
Responde de forma precisa y util en el idioma del usuario."""

# ── Prompts especiales para slash commands ────────────────────────────────────

SLASH_PROMPTS = {
    "solid": (
        "Refactoriza el siguiente codigo aplicando SOLID, DRY y Clean Architecture. "
        "Explica brevemente cada cambio y por que mejora la calidad:\n\n{code}"
    ),
    "refactor": (
        "Refactoriza el siguiente codigo para que sea mas limpio, legible y mantenible. "
        "Aplica SOLID/DRY. Muestra el codigo refactorizado completo:\n\n{code}"
    ),
    "review": (
        "Haz un code review detallado del siguiente codigo. Identifica:\n"
        "1. Bugs o errores logicos\n2. Violaciones de SOLID/DRY\n"
        "3. Problemas de seguridad\n4. Mejoras de rendimiento\n"
        "5. Deuda tecnica\nSe especifico con numeros de linea:\n\n{code}"
    ),
    "test": (
        "Escribe tests unitarios completos para el siguiente codigo. "
        "Cubre: casos normales, casos borde, casos de error. "
        "Usa el framework de testing apropiado para el lenguaje:\n\n{code}"
    ),
    "explain": (
        "Explica el siguiente codigo linea por linea de forma clara. "
        "Incluye el proposito general, como funciona cada parte y "
        "posibles problemas o mejoras:\n\n{code}"
    ),
    "yara": (
        "Analiza el siguiente codigo/muestra y genera una regla YARA precisa. "
        "Incluye strings unicos, condicion robusta y metadata completa "
        "(author, description, severity, mitre ATT&CK):\n\n{code}"
    ),
    "sigma": (
        "Genera una regla Sigma para detectar el siguiente comportamiento/IOC. "
        "Incluye logsource correcto, detection con filtros de falsos positivos "
        "y nivel de severidad justificado:\n\n{code}"
    ),
    "c2": (
        "Analiza el siguiente trafico/codigo de C2 (contexto educativo/defensivo). "
        "Identifica: protocolo, mecanismo de persistencia, tecnicas de evasion, "
        "IoCs para deteccion y reglas de deteccion:\n\n{code}"
    ),
    "pentest": (
        "Analiza el siguiente objetivo/codigo desde perspectiva de pentesting "
        "(contexto educativo/defensivo). Identifica: superficie de ataque, "
        "vulnerabilidades, vectores de explotacion y mitigaciones:\n\n{code}"
    ),
    "mobile": (
        "Desarrolla el siguiente requerimiento para aplicacion movil. "
        "Aplica las mejores practicas de Flutter/React Native/nativo segun el contexto. "
        "SOLID, manejo de estado correcto, performance:\n\n{code}"
    ),
    "web": (
        "Desarrolla el siguiente requerimiento web (frontend/backend/fullstack). "
        "Aplica arquitectura limpia, separacion de capas, validacion correcta:\n\n{code}"
    ),
    "doc": (
        "Genera documentacion completa para el siguiente codigo: "
        "docstrings/JSDoc, descripcion de parametros, ejemplos de uso, "
        "posibles excepciones:\n\n{code}"
    ),
}


def get_slash_prompt(command: str, code: str) -> str | None:
    """Devuelve el prompt de un slash command con el codigo inyectado."""
    template = SLASH_PROMPTS.get(command.lstrip("/"))
    if not template:
        return None
    return template.format(code=code)


# ── Detección de stack tecnológico ────────────────────────────────────────────

_WEB_KW = re.compile(
    r'\b(react|vue|angular|nextjs|nuxt|svelte|django|fastapi|flask|'
    r'express|nestjs|laravel|rails|spring|asp\.net|graphql|rest api|'
    r'html|css|tailwind|bootstrap|postgresql|mysql|mongodb|redis|'
    r'docker|kubernetes|nginx|webpack|vite)\b',
    re.IGNORECASE,
)
_MOBILE_KW = re.compile(
    r'\b(flutter|dart|react native|kotlin|swift|ios|android|'
    r'jetpack compose|swiftui|expo|capacitor|ionic|xamarin|'
    r'viewmodel|lifecycle|coroutine|async/await mobile)\b',
    re.IGNORECASE,
)
_SYSTEMS_KW = re.compile(
    r'\b(rust|golang|go lang|c\+\+|c language|embedded|kernel|'
    r'assembly|asm|memoria|puntero|pointer|thread|mutex|semaphore|'
    r'socket|syscall|driver|firmware|rtos)\b',
    re.IGNORECASE,
)
_SECURITY_CODE_KW = re.compile(
    r'\b(sql injection|xss|csrf|owasp|sanitize|escape|jwt|oauth|'
    r'authentication|authorization|crypto|hash|encrypt|decrypt|'
    r'buffer overflow|rop chain|heap spray)\b',
    re.IGNORECASE,
)


def detect_stack(messages: list[dict]) -> str:
    """Detecta el stack tecnológico para elegir el system prompt correcto."""
    text = " ".join(
        m.get("content", "") for m in messages if m.get("role") == "user"
    )[-1500:]
    if _SECURITY_CODE_KW.search(text):
        return "security_code"
    if _MOBILE_KW.search(text):
        return "mobile"
    if _WEB_KW.search(text):
        return "web"
    if _SYSTEMS_KW.search(text):
        return "systems"
    return "generic"


# ── Detección de seguridad ────────────────────────────────────────────────────

_SECURITY_KW = {
    "yara", "sigma", "malware", "payload", "c2", "ransomware", "trojan",
    "dropper", "shellcode", "exploit", "cve", "wazuh", "siem", "threat",
    "ioc", "mitre", "att&ck", "reverse", "threat hunting", "blue team",
    "red team", "purple team", "backdoor", "rootkit", "keylogger",
}


def _is_security(messages: list[dict]) -> bool:
    text = " ".join(
        m.get("content", "").lower()
        for m in messages if m.get("role") == "user"
    )[-1000:]
    return any(kw in text for kw in _SECURITY_KW)


# ── Inyección de CoT ─────────────────────────────────────────────────────────

def inject_cot(messages: list[dict], task_type: str) -> list[dict]:
    """
    Inyecta el system prompt especializado según el tipo de tarea y stack.
    Para tareas de código detecta automáticamente web/mobile/sistemas.
    """
    if task_type != "security" and _is_security(messages):
        task_type = "security"

    if task_type == "code":
        stack = detect_stack(messages)
        system_map_code = {
            "web":           SYSTEM_CODE_WEB,
            "mobile":        SYSTEM_CODE_MOBILE,
            "systems":       SYSTEM_CODE_SYSTEMS,
            "security_code": SYSTEM_CODE_SECURITY,
            "generic":       SYSTEM_CODE,
        }
        system_prompt = system_map_code.get(stack, SYSTEM_CODE)
    else:
        system_map = {
            "agent":    SYSTEM_AGENT,
            "security": SYSTEM_SECURITY,
            "chat":     SYSTEM_COT,
            "general":  SYSTEM_COT,
            "compress": SYSTEM_DEFAULT,
        }
        system_prompt = system_map.get(task_type, SYSTEM_DEFAULT)

    few_shot = _get_few_shot(messages)
    if few_shot:
        system_prompt = system_prompt + "\n\nREFERENCIA — formato esperado:" + few_shot

    has_system = any(m.get("role") == "system" for m in messages)
    if has_system:
        return [
            {**m, "content": f"{system_prompt}\n\n---\n\n{m['content']}"}
            if m.get("role") == "system" else m
            for m in messages
        ]
    return [{"role": "system", "content": system_prompt}] + list(messages)


# ── Confidence scoring / Anti-alucinación ────────────────────────────────────

_UNCERTAINTY_RE = re.compile(
    r'\b(creo que|podria ser|probablemente|quizas|tal vez|no estoy seguro|'
    r'no se si|asumo que|supongo|me parece|deberia ser|'
    r'I think|maybe|probably|might be|could be|I believe|'
    r"I'm not sure|I assume|should be|I suppose)\b",
    re.IGNORECASE,
)


def confidence_score(text: str) -> float:
    """Puntaje de confianza 0.0–1.0. < 0.6 → alta incertidumbre."""
    words = max(len(text.split()), 1)
    hits  = len(_UNCERTAINTY_RE.findall(text))
    ratio = hits / (words / 50)
    return max(0.0, min(1.0, 1.0 - ratio * 0.3))


# ── Thinking mode ────────────────────────────────────────────────────────────

_THINKING_RE = re.compile(r'<thinking>(.*?)</thinking>', re.DOTALL | re.IGNORECASE)


def extract_thinking(text: str) -> tuple[str, str]:
    m = _THINKING_RE.search(text)
    if m:
        thinking = m.group(1).strip()
        answer   = _THINKING_RE.sub('', text).strip()
        return thinking, answer
    return '', text


def should_think(messages: list[dict], task_type: str) -> bool:
    """Activa thinking 2-call para code/agent/security con queries sustanciales."""
    if task_type not in ('code', 'agent', 'security'):
        return False
    user_text = next(
        (m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'),
        ''
    )
    return len(user_text.strip()) > 60


# ── Few-shot para seguridad ───────────────────────────────────────────────────

_YARA_EXAMPLE = """
Ejemplo de regla YARA bien construida:
```yara
rule Ransomware_FileExtension_Change {
    meta:
        author      = "SmartOrch"
        description = "Detecta cambio masivo de extensiones -- patron de ransomware"
        severity    = "critical"
        mitre       = "T1486"
    strings:
        $ext1 = ".locked" ascii nocase
        $ext2 = ".encrypted" ascii nocase
        $ransom = "YOUR FILES ARE ENCRYPTED" ascii wide nocase
    condition:
        any of ($ext*) and $ransom
}
```"""

_SIGMA_EXAMPLE = """
Ejemplo de regla Sigma bien construida:
```yaml
title: Suspicious PowerShell Download Cradle
id: a7e3b2c1-4f5d-6e7f-8a9b-0c1d2e3f4a5b
status: stable
description: Detecta descarga de payload via PowerShell -- T1059.001
tags:
  - attack.execution
  - attack.t1059.001
logsource:
  product: windows
  category: process_creation
detection:
  selection:
    Image|endswith: '\\powershell.exe'
    CommandLine|contains:
      - 'IEX'
      - 'Invoke-Expression'
      - 'DownloadString'
  condition: selection
falsepositives:
  - Scripts de administracion legitimos
level: high
```"""

_SECURITY_KW_MAP = {
    "yara":  _YARA_EXAMPLE,
    "sigma": _SIGMA_EXAMPLE,
}


def _get_few_shot(messages: list[dict]) -> str:
    text = " ".join(
        m.get("content", "").lower() for m in messages if m.get("role") == "user"
    )[-500:]
    for kw, example in _SECURITY_KW_MAP.items():
        if kw in text:
            return example
    return ""


# ── Self-Verification ─────────────────────────────────────────────────────────

def build_verification_prompt(query: str, draft: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Eres un revisor tecnico experto. Encuentra errores en respuestas de IA. "
                "Se especialmente critico con: APIs inventadas, logica incorrecta, "
                "afirmaciones sin base. " + _NO_HALLUCINATION
            ),
        },
        {
            "role": "user",
            "content": (
                f"Pregunta original: {query}\n\n"
                f"Respuesta propuesta:\n{draft}\n\n"
                "Revisa:\n"
                "1. Es correcta? Hay bugs o errores logicos?\n"
                "2. Aplica SOLID/DRY correctamente?\n"
                "3. Hay afirmaciones inventadas o sin base?\n\n"
                "Si es correcta: 'VERIFICADO: [resumen 1 linea]'\n"
                "Si hay errores: 'CORREGIDO:\n[version corregida completa]'"
            ),
        },
    ]
