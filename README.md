# SmartOrch — Proyecto completo

Todo el ecosistema SmartOrch está aquí dentro.

## Estructura

```
E:\smartorch-project\
│
├── smartorch.code-workspace   ← abre esto en VS Code
│
├── server\                    ← Motor FastAPI (puerto 8080)
│   ├── smartorch/
│   │   ├── core/              ← orchestrator, chain, compressor, rag
│   │   ├── api/               ← rutas OpenAI-compatible
│   │   ├── cli.py             ← comando `smartorch` en terminal
│   │   └── config.py
│   ├── run.py
│   └── pyproject.toml
│
└── extension\                 ← Extensión VS Code
    ├── src/
    │   ├── extension.ts
    │   ├── sidebar-provider.ts
    │   ├── smartorch-client.ts
    │   ├── completion-provider.ts
    │   └── system-detector.ts
    ├── media/
    ├── smartorch-0.1.1.vsix   ← extensión instalable
    └── package.json
```

## Cómo usar

```bash
# 1. Instalar el motor (solo la primera vez)
pip install -e E:\smartorch-project\server

# 2. Iniciar servidor
smartorch serve

# 3. Reinstalar extensión si hiciste cambios
cd E:\smartorch-project\extension
npx tsc -p ./
npx vsce package --out smartorch-0.1.1.vsix
code --install-extension smartorch-0.1.1.vsix --force
```

## Modelos Ollama requeridos

```bash
ollama pull qwen2.5-coder:7b
ollama pull hermes3:8b
ollama pull nomic-embed-text
```
