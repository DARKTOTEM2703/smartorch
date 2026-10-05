# SmartOrch

Asistente de programación **100 % local**: corre sobre modelos de [Ollama](https://ollama.com), sin enviar tu código a ningún servicio y sin límites de sesión. Funciona igual desde la **terminal**, la **web** y un **panel nativo de VS Code**, todos con el mismo servidor y el mismo historial de conversaciones.

## La idea

Los modelos pequeños (7–8B) no necesitan más VRAM para rendir mejor: necesitan **un sistema que piense por ellos lo que no tienen que recordar**. SmartOrch saca del modelo todo lo que un programa hace mejor:

| El modelo grande lo hace "de memoria" | SmartOrch lo resuelve fuera del modelo |
|---|---|
| Saber dónde está cada cosa en el proyecto | **Grafo de código** en SQLite (símbolos, quién llama a quién, imports) y **mapa de resúmenes** jerárquico |
| Acertar a la primera | **Verificación**: sintaxis, tests y reparación automática; si no basta, **varios intentos** deshaciendo el fallido |
| Escribir ediciones de texto exacto | Herramientas pensadas para modelos chicos: `append_file`, `add_to_class`, lectura obligatoria antes de editar |
| Obedecer instrucciones largas | **Salida estructurada** forzada por esquema JSON cuando el modelo narra en vez de actuar, y un **juez de completitud** |
| Recordar lo que funcionó | **Memoria de experiencias** por proyecto: solo tareas verificadas con tests |
| Mantener contexto largo | Compactación por enmascarado de observaciones, subagente explorador, `SMARTORCH.md` |

Lo que **no** promete: igualar a un modelo de 70B en razonamiento abstracto puro. Se mide con un banco de pruebas (ver abajo) y no se da nada por hecho.

## Estructura

```
smartorch-project/
├── server/            Motor Python (FastAPI + CLI + web UI)
│   ├── smartorch/
│   │   ├── agent/     bucle del agente, herramientas con sandbox, búsqueda web
│   │   ├── core/      esfuerzo, grafo de código, mapa, experiencias, análisis, RAG, historial
│   │   ├── api/       servidor (compatible con OpenAI + endpoints propios)
│   │   ├── ui/static/ interfaz web (también es la del panel de VS Code)
│   │   ├── cli.py, cli_agent.py
│   ├── evals/         banco de pruebas del agente
│   └── tests/
└── extension/         Extensión de VS Code (panel de chat nativo, vistas, autocompletado)
```

## Instalación

```bash
# Motor
pip install -e server
ollama pull hermes3:8b          # agente (llamadas a herramientas)
ollama pull qwen2.5-coder:7b    # chat de código
ollama pull nomic-embed-text    # búsqueda semántica

# Extensión (usa el CLI de VS Code, no `code` si tienes Cursor)
cd extension
npm install && npm run compile
npx vsce package --no-dependencies --out smartorch-2.1.0.vsix
<ruta-a-VS-Code>/bin/code --install-extension smartorch-2.1.0.vsix --force
```

## Uso

```bash
smartorch serve                      # servidor en http://127.0.0.1:8080 (la web UI está en la raíz)
smartorch agent "arregla el bug" --effort maximo     # agente en la terminal
smartorch agent "explica el proyecto" --plan         # solo investiga y propone un plan
smartorch map [carpeta]              # construye el mapa de resúmenes (incremental)
smartorch history | smartorch resume # historial compartido con la web y VS Code
smartorch data [carpeta]             # elegir dónde viven la base de datos y los índices
```

**Modos:** Preguntar · Plan (solo investiga) · Agente (edita con tu aprobación). **Permisos:** preguntar siempre, editar archivos solo, solo lectura.
**Esfuerzo:** *Rápido* (un paso, sin verificar), *Normal* (verifica con tests, repara 2 veces), *Máximo* (planifica, investiga, verifica y prueba hasta 3 enfoques).

En VS Code: panel **SmartOrch** en la barra lateral con **Chat**, **Historial** y **Proyecto** (análisis, *salud del proyecto* con duplicados, funciones largas, clases grandes y ciclos de importación, mapa de resúmenes y experiencias aprendidas). Los cambios propuestos se abren en el diff nativo de VS Code.

## Seguridad

- El agente trabaja confinado al workspace; rechaza `.env`, llaves y rutas fuera del proyecto, y bloquea comandos destructivos.
- Toda modificación y todo comando pasan por tu aprobación (o por el modo que elijas) y quedan en `agent-audit.log`.
- La búsqueda web es opcional, cada consulta se aprueba, y el contenido que trae se trata como **dato no confiable** (protección contra inyección de instrucciones; protección SSRF; respeta robots.txt). `SMARTORCH_WEB=0` la desactiva.
- Lo que SmartOrch aprende de un proyecto vive en tu disco y puedes verlo y borrarlo.

## Banco de pruebas

```bash
cd server
python evals/run.py --repeat 4                       # todas las tareas y los 3 niveles
python evals/run.py --task fix_divide add_function --effort normal --repeat 6
python evals/run.py --model qwen2.5-coder:7b         # comparar modelos
```

Cada tarea corre sobre un proyecto de juguete con verificación automática. Con un modelo de 8B hay variación entre corridas: usa varias repeticiones antes de concluir que algo mejoró.

## Tests

```bash
cd server && python -m unittest discover -s tests
```

## Variables de entorno útiles

`SMARTORCH_WEB=0` (sin búsqueda web) · `SMARTORCH_DONATE_URL` (enlace de apoyo opcional; sin anuncios).
