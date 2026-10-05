"""
smartorch agent — el agente de SmartOrch en la terminal.

Usa el mismo servidor y los mismos permisos que la web y VS Code: lo que modifica o sale a internet
te muestra antes el diff, el comando o la consulta y espera tu respuesta.

    smartorch agent "arregla el bug de divide"
    smartorch agent --plan "migrar a async"        # investiga y propone un plan, sin tocar nada
    smartorch agent --effort maximo "agrega tests"
    smartorch agent --web "actualiza a la última versión de X"
"""
import json
import os
import urllib.error
import urllib.request

from smartorch import cli
from smartorch.cli import C, _c

EFFORTS = ("rapido", "normal", "maximo")
APPROVALS = ("ask", "auto_edits", "readonly")


def _post_stream(path: str, body: dict):
    req = urllib.request.Request(
        cli.SERVER_URL + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cli.API_KEY}", "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=900)


def _send(path: str, body: dict) -> None:
    try:
        cli._request("POST", path, body, timeout=15)
    except Exception as e:  # el servidor ya cerro esa espera: no es grave
        print(_c(C.GRAY, f"  (no se pudo enviar la respuesta: {e})"))


def _ask_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _print_preview(name: str, preview: str) -> None:
    if not preview:
        return
    print()
    for line in preview.splitlines()[:60]:
        if name in ("write_file", "edit_file") and line.startswith("+") and not line.startswith("+++"):
            print("    " + _c(C.GREEN, line))
        elif name in ("write_file", "edit_file") and line.startswith("-") and not line.startswith("---"):
            print("    " + _c(C.RED, line))
        elif line.startswith("@@"):
            print("    " + _c(C.CYAN, line))
        else:
            print("    " + _c(C.GRAY, line))
    if len(preview.splitlines()) > 60:
        print(_c(C.GRAY, "    [... vista previa recortada]"))
    print()


def run_agent(task: str, *, plan: bool = False, effort: str = "normal", approval: str = "ask", web: bool = False,
              workspace: str | None = None, messages: list[dict] | None = None) -> dict:
    """Corre una tarea y devuelve {"final": str, "plan": bool}."""
    workspace = os.path.abspath(workspace or os.getcwd())
    body = {
        "messages": messages or [{"role": "user", "content": task}],
        "workspace": workspace, "mode": "plan" if plan else "agent", "approval": approval,
        "effort": effort, "web": web, "source": "cli", "title": task[:60],
    }
    if cli._conv_id:
        body["conversation_id"] = cli._conv_id

    calls: dict[str, dict] = {}
    result = {"final": "", "plan": plan}
    try:
        resp = _post_stream("/smartorch/agent/run", body)
    except urllib.error.HTTPError as e:
        print(_c(C.RED, f"  Error {e.code}: {e.read().decode(errors='replace')[:200]}"))
        return result
    except urllib.error.URLError as e:
        print(_c(C.RED, f"  No se puede conectar al servidor: {e}"))
        return result

    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:") or line.endswith("[DONE]"):
            continue
        try:
            ev = json.loads(line[5:])
        except ValueError:
            continue
        kind = ev.get("type")
        indent = "    " if ev.get("agent") else "  "

        if kind == "start":
            mode = "plan (solo investigo)" if ev["plan"] else "agente"
            extras = f" · esfuerzo {ev['effort']}" + (" · web" if ev.get("web") else "")
            print(_c(C.GRAY, f"  {os.path.basename(ev['workspace'])} · modo {mode}{extras}"))
        elif kind == "text":
            print(_c(C.DIM, f"{indent}{ev['content'][:200]}"))
        elif kind == "tool_call":
            calls[ev["id"]] = ev
            arg = ev["args"].get("path") or ev["args"].get("pattern") or ev["args"].get("command") \
                or ev["args"].get("query") or ev["args"].get("url") or ev["args"].get("question") or ""
            if ev["name"] not in ("todo_write", "ask_user"):
                print(f"{indent}{_c(C.CYAN, ev['name'])} {_c(C.GRAY, str(arg)[:90])}")
        elif kind == "approval_wait":
            call = calls.get(ev["id"], {})
            _print_preview(call.get("name", ""), call.get("preview", ""))
            what = {"run_command": "Ejecutar este comando", "run_tests": "Ejecutar los tests",
                    "web_search": "Buscar en internet", "web_fetch": "Leer esa página"}.get(call.get("name", ""), "Aplicar este cambio")
            answer = _ask_line(_c(C.YELLOW + C.BOLD, f"  ¿{what}? [s/N] ")).lower()
            _send("/smartorch/agent/approve", {"call_id": ev["id"], "approve": answer in ("s", "si", "sí", "y", "yes")})
        elif kind == "ask":
            print()
            print(_c(C.YELLOW + C.BOLD, f"  ❓ {ev['question']}"))
            for i, option in enumerate(ev.get("options") or [], 1):
                print(f"    {i}) {option}")
            answer = _ask_line(_c(C.YELLOW, "  Tu respuesta: "))
            options = ev.get("options") or []
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                answer = options[int(answer) - 1]
            _send("/smartorch/agent/answer", {"call_id": ev["id"], "answer": answer})
        elif kind == "tool_result":
            if ev["name"] in ("todo_write", "ask_user"):
                continue
            first = (ev["output"].strip().splitlines() or [""])[0][:100]
            print(f"{indent}  " + (_c(C.GREEN, "✔ ") + _c(C.GRAY, first) if ev["ok"] else _c(C.RED, "✘ ") + _c(C.GRAY, first)))
        elif kind == "todo":
            print(_c(C.BOLD, "  Tareas"))
            for t in ev["todos"]:
                print(f"    {'☑' if t['done'] else '☐'} {t['text']}")
        elif kind == "compact":
            print(_c(C.DIM, f"{indent}(contexto compactado)"))
        elif kind == "final" and not ev.get("agent"):
            result["final"] = ev["content"]
            result["plan"] = bool(ev.get("plan"))
            print()
            print(ev["content"])
        elif kind == "error":
            print(_c(C.RED, f"  Error: {ev['message']}"))
        elif kind == "done":
            print()
            print(_c(C.DIM, f"  {ev['steps']} pasos · {ev['elapsed']}s · {ev['model']}"))
    return result


def agent_command(task: str, *, plan: bool, effort: str, approval: str, web: bool, workspace: str | None, yes: bool = False) -> None:
    """Punto de entrada: `smartorch agent ...`. Con --plan ofrece ejecutar el plan al terminar."""
    effort = effort if effort in EFFORTS else "normal"
    approval = "auto_edits" if yes and approval == "ask" else (approval if approval in APPROVALS else "ask")
    print()
    result = run_agent(task, plan=plan, effort=effort, approval=approval, web=web, workspace=workspace)
    if plan and result["final"] and result["plan"]:
        print()
        answer = _ask_line(_c(C.YELLOW + C.BOLD, "  ¿Ejecutar este plan? [s/N] ")).lower()
        if answer in ("s", "si", "sí", "y", "yes"):
            print()
            run_agent("Ejecuta el siguiente plan aprobado, paso a paso, y verifica al terminar:\n\n" + result["final"],
                      plan=False, effort=effort, approval=approval, web=web, workspace=workspace)
    print()
