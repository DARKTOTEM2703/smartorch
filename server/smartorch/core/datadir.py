"""
Carpeta de datos de SmartOrch: historial (SQLite), indice TF-IDF y base vectorial del RAG.

Resolucion, de mayor a menor prioridad:
  1. variable SMARTORCH_DATA_DIR
  2. ~/.smartorch/location.json  ->  {"data_dir": "D:\\\\SmartOrchData"}
  3. ~/.smartorch

location.json siempre vive en ~/.smartorch para poder encontrar los datos aunque
esten en otro disco.
"""
import json
import os
import shutil
from typing import Optional

BOOTSTRAP_DIR = os.path.join(os.path.expanduser("~"), ".smartorch")
BOOTSTRAP_FILE = os.path.join(BOOTSTRAP_DIR, "location.json")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEGACY_INDEX = os.path.join(_SERVER_DIR, "index.json")
_LEGACY_RAG = os.path.join(_SERVER_DIR, ".smartorch_db")

# Nombres de lo que vive dentro de la carpeta de datos
ITEMS = {"history.db": "Historial de conversaciones", "index.json": "Indice TF-IDF", "rag": "Base vectorial (RAG)"}


def resolve() -> str:
    env = os.environ.get("SMARTORCH_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    try:
        with open(BOOTSTRAP_FILE, encoding="utf-8") as f:
            configured = json.load(f).get("data_dir")
        if configured:
            return os.path.abspath(os.path.expanduser(configured))
    except (OSError, ValueError):
        pass
    return BOOTSTRAP_DIR


DATA_DIR = resolve()
HISTORY_DB = os.path.join(DATA_DIR, "history.db")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")
CHROMA_DIR = os.path.join(DATA_DIR, "rag")


def migrate_legacy() -> list[str]:
    """Mueve los datos que antes vivian junto al codigo a la carpeta de datos."""
    moved: list[str] = []
    os.makedirs(DATA_DIR, exist_ok=True)
    for legacy, target in ((_LEGACY_INDEX, INDEX_FILE), (_LEGACY_RAG, CHROMA_DIR)):
        if os.path.exists(legacy) and not os.path.exists(target):
            try:
                shutil.move(legacy, target)
                moved.append(target)
            except OSError:
                pass
    return moved


def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def info(path: Optional[str] = None) -> dict:
    base = path or DATA_DIR
    probe = base
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        free_gb = round(shutil.disk_usage(probe).free / 1_073_741_824, 1)
    except OSError:
        free_gb = None
    items = []
    for name, label in ITEMS.items():
        p = os.path.join(base, name)
        items.append({"name": name, "label": label, "exists": os.path.exists(p),
                      "mb": round(_size(p) / 1_048_576, 1) if os.path.exists(p) else 0})
    return {"data_dir": base, "custom": base != BOOTSTRAP_DIR, "free_gb": free_gb,
            "items": items, "total_mb": round(sum(i["mb"] for i in items), 1)}


def set_data_dir(new_dir: str, move: bool = True) -> dict:
    """
    Cambia la carpeta de datos. Con move=True copia lo existente a la nueva carpeta.
    El servidor debe estar detenido (lo valida quien llama). Lo viejo no se borra.
    """
    target = os.path.abspath(os.path.expanduser(new_dir))
    if target == DATA_DIR:
        return {"changed": False, "data_dir": target, "copied": []}
    os.makedirs(target, exist_ok=True)
    probe = os.path.join(target, ".write-test")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise PermissionError(f"No se puede escribir en {target}: {e}") from e

    copied: list[str] = []
    if move:
        needed = sum(i["mb"] for i in info()["items"]) / 1024
        free = info(target)["free_gb"]
        if free is not None and free < needed + 0.1:
            raise OSError(f"Espacio insuficiente en {target}: {free} GB libres, se necesitan {needed:.1f} GB")
        for name in ITEMS:
            src, dst = os.path.join(DATA_DIR, name), os.path.join(target, name)
            if not os.path.exists(src) or os.path.exists(dst):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied.append(name)

    os.makedirs(BOOTSTRAP_DIR, exist_ok=True)
    with open(BOOTSTRAP_FILE, "w", encoding="utf-8") as f:
        json.dump({"data_dir": target}, f, ensure_ascii=False, indent=2)
    return {"changed": True, "data_dir": target, "copied": copied, "previous": DATA_DIR}


def reset_data_dir() -> None:
    try:
        os.remove(BOOTSTRAP_FILE)
    except OSError:
        pass
