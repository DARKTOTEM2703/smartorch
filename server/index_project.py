#!/usr/bin/env python3
"""
SmartOrch — Indexar proyecto de código
Uso: python index_project.py <ruta_del_proyecto>

Ejemplo:
  python index_project.py Z:\CYBERRANGE_V2
  python index_project.py E:\mi_proyecto
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartorch.core.indexer import reindex

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python index_project.py <ruta_del_proyecto>")
        print("\nEjemplos:")
        print("  python index_project.py Z:\\CYBERRANGE_V2")
        print("  python index_project.py E:\\mi_proyecto")
        sys.exit(1)

    root = sys.argv[1]
    if not os.path.isdir(root):
        print(f"Error: '{root}' no es un directorio válido")
        sys.exit(1)

    print(f"\n[*] Indexando: {root}")
    print("="*50)
    n = reindex(root)
    print("="*50)
    print(f"[✓] Indexado: {n} chunks guardados en index.json")
    print(f"[✓] SmartOrch usará este índice automáticamente")
    print("\nAhora inicia SmartOrch: python run.py")
