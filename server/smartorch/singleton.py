"""
Una sola instancia del servidor por maquina: si ya hay un SmartOrch en el puerto, los demas lo reutilizan.

Asi abrir varias terminales (o la extension, el script de reinicio y `smartorch serve` a la vez) nunca levanta
varios servidores ni varios indexados: todos hablan con el mismo, que ya conoce los proyectos registrados.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from smartorch.config import API_KEY, HOST, PORT


def _url(path: str) -> str:
    host = "127.0.0.1" if HOST in ("0.0.0.0", "") else HOST
    return f"http://{host}:{PORT}{path}"


def running_server(timeout: float = 2.0) -> Optional[dict]:
    """El /health del SmartOrch que ya corre en el puerto, o None. Otro programa en el puerto no cuenta."""
    try:
        with urllib.request.urlopen(_url("/health"), timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data if data.get("service") == "SmartOrch" else None
    except Exception:  # noqa: BLE001 - nada escucha, o responde otra cosa
        return None


def register_workspace(root: str, timeout: float = 600.0) -> dict:
    """Pide al servidor que ya corre que indexe y registre este proyecto (no arranca nada nuevo)."""
    req = urllib.request.Request(
        _url("/smartorch/index"), data=json.dumps({"root": root}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())
