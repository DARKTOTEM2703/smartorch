"""
Banco de pruebas de SmartOrch.

Corre tareas reales del agente sobre proyectos de juguete, en cada nivel de esfuerzo, y mide
exito (con una verificacion automatica), pasos, herramientas usadas y tiempo. Sirve para decidir
con datos si una tecnica (esfuerzo maximo, explorador, verificacion con tests...) realmente ayuda.

Uso (con Ollama corriendo):
    python evals/run.py                         # todas las tareas, los 3 niveles
    python evals/run.py --effort rapido normal  # solo algunos niveles
    python evals/run.py --task fix_divide       # una tarea
    python evals/run.py --repeat 3              # repite para promediar la variabilidad
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from fixtures import FIXTURES  # noqa: E402
from smartorch.agent import loop  # noqa: E402


def write_fixture(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"so-eval-{name}-"))
    for rel, text in FIXTURES[name].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def tests_pass(root: Path) -> bool:
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-q"], cwd=root, capture_output=True, text=True, timeout=240)
    return r.returncode == 0


def read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


# ── Tareas: (id, fixture, prompt, comprobacion(root, final_text) -> bool) ────

def check_fix_divide(root, final):
    return tests_pass(root)


def check_add_function(root, final):
    ns: dict = {}
    try:
        exec(read(root, "calc.py"), ns)
        has_test = any("power" in read(root, rel) for rel in ("tests/test_calc.py", "tests/test_power.py"))
        return ns["power"](2, 3) == 8 and has_test and tests_pass(root)
    except Exception:
        return False


def check_find_symbol(root, final):
    return "pricing" in final.lower() and "apply_discount" in final


def check_rename(root, final):
    sources = [read(root, f"shop/{n}") for n in ("pricing.py", "cart.py")] + [read(root, "tests/test_shop.py")]
    return all("calc_total" not in s for s in sources) and "compute_total" in sources[0] and "compute_total" in sources[1] and tests_pass(root)


def check_explain(root, final):
    low = final.lower()
    return any(k in low for k in ("tienda", "carrito", "shop")) and any(k in low for k in ("impuesto", "descuento", "precio"))


def check_new_file(root, final):
    return "def " in read(root, "shop/stats.py") and "shop/stats.py" in final.replace("\\", "/") or "stats" in final.lower() and (root / "shop/stats.py").exists()


TASKS = [
    ("fix_divide", "calc", "Los tests de este proyecto fallan. Encuentra el bug y arréglalo.", check_fix_divide),
    ("add_function", "calc_ok", "Agrega a calc.py una función power(a, b) que devuelva a elevado a b, y un test para ella.", check_add_function),
    ("find_symbol", "shop", "¿En qué archivo se aplica el descuento y cómo se llama la función?", check_find_symbol),
    ("rename", "shop", "Renombra la función calc_total a compute_total en todo el proyecto, incluidos los tests, sin romper nada.", check_rename),
    ("explain", "shop", "Explícame qué hace este proyecto.", check_explain),
]


def run_one(task_id, fixture, prompt, check, effort, model=None):
    root = write_fixture(fixture)
    events: list[dict] = []
    handled: set = set()

    def worker():
        for ev in loop.run([{"role": "user", "content": prompt}], str(root), model=model, approval="ask", effort=effort):
            events.append(ev)

    started = time.time()
    th = threading.Thread(target=worker)
    th.start()
    while th.is_alive() and time.time() - started < 900:
        for ev in list(events):
            if ev["type"] in ("approval_wait", "ask") and ev["id"] not in handled:
                handled.add(ev["id"])
                loop.registry.decide(ev["id"], True if ev["type"] == "approval_wait" else "usa tu mejor criterio")
        time.sleep(0.05)
    th.join(timeout=5)
    elapsed = time.time() - started

    final = next((e["content"] for e in reversed(events) if e["type"] == "final" and "agent" not in e), "")
    done = next((e for e in reversed(events) if e["type"] == "done"), {})
    calls = [e for e in events if e["type"] == "tool_call"]
    failures = [e for e in events if e["type"] == "tool_result" and not e["ok"]]
    try:
        ok = bool(check(root, final))
    except Exception:
        ok = False
    return {"task": task_id, "effort": effort, "ok": ok, "seconds": round(elapsed, 1), "steps": done.get("steps", 0),
            "tool_calls": len(calls), "tool_failures": len(failures), "final": final[:160].replace("\n", " ")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--effort", nargs="+", default=["rapido", "normal", "maximo"])
    parser.add_argument("--task", nargs="+", default=[t[0] for t in TASKS])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default=None, help="modelo del agente (por defecto el configurado)")
    parser.add_argument("--ablate", nargs="*", default=[], choices=["split", "judge", "add_to_class", "force"],
                        help="apaga piezas del agente para medir si ayudan o estorban")
    args = parser.parse_args()

    # el banco de pruebas no debe escribir experiencias ni grafos en los datos reales del usuario
    import tempfile
    from smartorch.core import datadir
    datadir.DATA_DIR = tempfile.mkdtemp(prefix="so-eval-data-")

    from smartorch.agent import tools as T
    if "split" in args.ablate:
        loop.split_request = lambda text: [text]
    if "judge" in args.ablate or "force" in args.ablate:
        real = loop._forced_calls
        loop._forced_calls = lambda st, convo, specs, narration, completion_of=None: (
            [] if (completion_of is not None and "judge" in args.ablate) or (completion_of is None and "force" in args.ablate)
            else real(st, convo, specs, narration, completion_of))
    if "add_to_class" in args.ablate:
        T.EDIT_TOOLS[:] = [t for t in T.EDIT_TOOLS if t != "add_to_class"]
    if args.ablate:
        print("piezas apagadas:", ", ".join(args.ablate), flush=True)

    results = []
    for task_id, fixture, prompt, check in TASKS:
        if task_id not in args.task:
            continue
        for effort in args.effort:
            for n in range(args.repeat):
                r = run_one(task_id, fixture, prompt, check, effort, args.model)
                results.append(r)
                print(f"{'OK ' if r['ok'] else 'FAIL'} {task_id:13s} {effort:7s} {r['seconds']:6.1f}s  pasos={r['steps']:2d}  herramientas={r['tool_calls']:2d}  fallos={r['tool_failures']}", flush=True)

    print("\nResumen por nivel de esfuerzo")
    print(f"{'nivel':8s} {'exito':>8s} {'tiempo medio':>13s} {'pasos medios':>13s}")
    for effort in args.effort:
        rs = [r for r in results if r["effort"] == effort]
        if rs:
            print(f"{effort:8s} {sum(r['ok'] for r in rs):>4d}/{len(rs):<3d} {sum(r['seconds'] for r in rs)/len(rs):>12.1f}s {sum(r['steps'] for r in rs)/len(rs):>13.1f}")

    print("\nPor tarea (aciertos/intentos)")
    for task_id in args.task:
        cells = []
        for effort in args.effort:
            rs = [r for r in results if r["task"] == task_id and r["effort"] == effort]
            cells.append(f"{effort}={sum(r['ok'] for r in rs)}/{len(rs)}")
        print(f"  {task_id:13s} " + "  ".join(cells))

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultados guardados en {path}")


if __name__ == "__main__":
    main()
