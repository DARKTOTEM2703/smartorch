# SmartOrch — Local AI

> **IA local de alto rendimiento. Sin pagar. Sin enviar tu código a la nube.**

SmartOrch convierte tu máquina en un asistente de IA completo usando modelos Ollama. Mismo pipeline que herramientas de pago (ensemble, RAG, CoT, multi-pass refinement) — corriendo 100% en local.

---

## ¿Qué incluye?

| Capacidad | Detalle |
|---|---|
| **Chat con streaming** | Respuestas en tiempo real con estado del pipeline |
| **Autocompletado inline** | Sugerencias mientras escribes (como Copilot) |
| **RAG automático** | Indexa tu workspace y recupera contexto relevante |
| **Ensemble voting** | Dos modelos compiten — gana el mejor |
| **Multi-pass refinement** | El modelo critica y mejora su propio código |
| **Thinking simulado** | Chain-of-Thought en dos fases para mayor precisión |
| **@smartorch en Chat** | Agente en el panel de Chat de VS Code |

---

## Comandos slash disponibles

Selecciona código → click derecho → **⚡ SmartOrch**

| Comando | Descripción |
|---|---|
| `/solid` | Aplica principios SOLID y DRY |
| `/refactor` | Refactoriza con clean architecture |
| `/review` | Code review exhaustivo |
| `/test` | Genera tests unitarios |
| `/explain` | Explica el código |
| `/fix` | Corrige bugs |
| `/doc` | Genera documentación/JSDoc |
| `/optimize` | Optimiza rendimiento y complejidad |
| `/yara` | Genera regla YARA (ciberseguridad) |
| `/sigma` | Genera regla Sigma (MITRE ATT&CK) |
| `/c2` | Análisis C2/malware (educativo/defensivo) |
| `/pentest` | Guía de pentest |
| `/web` | Feature web React/FastAPI/Vue |
| `/mobile` | Feature mobile Flutter/React Native |

---

## Stack de modelos recomendado

SmartOrch detecta tu hardware y sugiere el stack óptimo:

| VRAM | Stack |
|---|---|
| 12+ GB | qwen2.5-coder:14b + deepseek-r1:14b + nomic-embed-text |
| 7–8 GB | qwen2.5-coder:7b + hermes3:8b + nomic-embed-text |
| 4–6 GB | qwen2.5-coder:7b + nomic-embed-text |
| < 4 GB | qwen2.5-coder:1.5b + nomic-embed-text |

---

## Requisitos

- [Ollama](https://ollama.com) instalado y corriendo
- Al menos un modelo descargado: `ollama pull qwen2.5-coder:7b`
- SmartOrch server corriendo: `smartorch serve` (instalar con `pip install -e E:\SMARTORCH`)

---

## Atajos de teclado

| Atajo | Acción |
|---|---|
| `Ctrl+Shift+L` | Abrir chat SmartOrch |
| `Ctrl+Shift+E` | Explicar código seleccionado |
| `Ctrl+Shift+R` | Code review |

---

## ¿Por qué SmartOrch?

- **Privacidad total** — tu código nunca sale de tu máquina
- **Sin suscripción** — modelos abiertos, corre offline
- **Especializado** — prompts SOLID/DRY, YARA, Sigma, C2 integrados
- **Escalable** — mismo pipeline que herramientas enterprise, en local

---

## Créditos

Desarrollado por [Jafeth Gamboa](mailto:jafeth.gamboa@techmaleon.mx) · TechMaléon
