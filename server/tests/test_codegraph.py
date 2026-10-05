import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smartorch.agent import tools
from smartorch.core import codegraph, datadir

SHOP = '''import math
import re
from pricing import apply_discount


class Cart:
    """Carrito."""

    def __init__(self):
        self.items = []

    def add(self, price):
        self.items.append(price)

    def total(self):
        return apply_discount(sum(self.items))


def helper(x):
    return re.search("a", x)


def area(r):
    """Area de un circulo."""
    total = r * r
    total = total * math.pi
    total = total + 0
    return total
'''

PRICING = '''def apply_discount(amount):
    """Aplica 10% de descuento."""
    return amount * 0.9


def area_square(side):
    total = side * side
    total = total * 3
    total = total + 0
    return total


def search(q):
    return q
'''

DUP = '''def area_rect(w):
    total = w * w
    total = total * 7
    total = total + 0
    return total
'''


class CodeGraphTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="so-graph-")
        for name, body in {"shop.py": SHOP, "pricing.py": PRICING, "other.py": DUP}.items():
            Path(self.root, name).write_text(body, encoding="utf-8")
        patcher = mock.patch.object(datadir, "DATA_DIR", tempfile.mkdtemp(prefix="so-graphdata-"))
        patcher.start()
        self.addCleanup(patcher.stop)
        codegraph.build(self.root)

    def test_symbols_are_found_with_signature_and_class_members(self):
        found = codegraph.find(self.root, "Cart.total")
        self.assertEqual((found[0]["file"], found[0]["kind"]), ("shop.py", "method"))
        self.assertTrue(found[0]["sig"].startswith("def total(self"))
        self.assertEqual(codegraph.find(self.root, "Cart")[0]["kind"], "class")
        self.assertEqual([m["name"] for m in codegraph.find_children(self.root, "Cart", "shop.py")], ["__init__", "add", "total"])

    def test_callers_and_callees_follow_the_code(self):
        self.assertEqual([c["src"] for c in codegraph.callers(self.root, "apply_discount")], ["Cart.total"])
        callee_names = [c["qual"] for c in codegraph.callees(self.root, "Cart.total")]
        self.assertEqual(callee_names, ["apply_discount"])

    def test_module_qualified_calls_do_not_point_at_project_symbols(self):
        # helper() llama a re.search; pricing.search existe pero no es esa funcion
        self.assertEqual([c["qual"] for c in codegraph.callees(self.root, "helper")], [])
        self.assertEqual(codegraph.callers(self.root, "search", def_file="pricing.py"), [])

    def test_slice_contains_code_callers_and_callees_only(self):
        text = codegraph.slice_for(self.root, "Cart.total")
        self.assertIn("return apply_discount(sum(self.items))", text)
        self.assertIn("Llama a: def apply_discount(amount)", text)
        self.assertNotIn("def helper", text)
        self.assertIn("No hay ningún símbolo", codegraph.slice_for(self.root, "no_existe"))
        self.assertIn("Lo llaman: Cart.total", codegraph.slice_for(self.root, "apply_discount"))

    def test_duplicates_ignore_names_and_constants(self):
        groups = codegraph.duplicates(self.root)
        flat = {tuple(sorted(d["qual"] for d in g)) for g in groups}
        self.assertEqual(flat, {("area_rect", "area_square")})  # area usa math.pi: no es el mismo codigo

    def test_incremental_build_only_reparses_what_changed(self):
        self.assertEqual(codegraph.build(self.root)["parsed"], 0)
        Path(self.root, "pricing.py").write_text(PRICING + "\ndef extra():\n    return 1\n", encoding="utf-8")
        stats = codegraph.build(self.root)
        self.assertEqual((stats["parsed"], stats["reused"]), (1, 2))
        self.assertTrue(codegraph.find(self.root, "extra"))
        Path(self.root, "other.py").unlink()
        self.assertEqual(codegraph.build(self.root)["removed"], 1)
        self.assertFalse(codegraph.find(self.root, "area_rect"))

    def test_smells_flag_long_functions_big_classes_and_cycles(self):
        body = "".join(f"    x{i} = {i}\n" for i in range(70))
        Path(self.root, "long.py").write_text("def long_one():\n" + body + "    return 1\n", encoding="utf-8")
        methods = "".join(f"    def m{i}(self):\n        return {i}\n" for i in range(18))
        Path(self.root, "big.py").write_text("class Big:\n" + methods, encoding="utf-8")
        Path(self.root, "a.py").write_text("import b\n", encoding="utf-8")
        Path(self.root, "b.py").write_text("import a\n", encoding="utf-8")
        codegraph.build(self.root)
        s = codegraph.smells(self.root)
        self.assertEqual(s["long_functions"][0]["qual"], "long_one")
        self.assertEqual(s["big_classes"][0]["qual"], "Big")
        self.assertTrue(any(set(c) >= {"a.py", "b.py"} for c in s["cycles"]))

    def test_importers_and_broken_files_do_not_crash(self):
        Path(self.root, "broken.py").write_text("def x(:\n", encoding="utf-8")
        codegraph.build(self.root)
        self.assertEqual(codegraph.importers(self.root, "pricing.py"), ["shop.py"])

    def test_js_files_contribute_symbols_and_imports(self):
        Path(self.root, "app.js").write_text("import x from './util'\nfunction boot() {}\n", encoding="utf-8")
        codegraph.build(self.root)
        self.assertTrue(codegraph.find(self.root, "boot"))

    def test_agent_tool_symbol_context(self):
        out = tools.symbol_context(tools.Sandbox(self.root), "apply_discount")
        self.assertTrue(out.ok)
        self.assertIn("Aplica 10%", out.output)
        self.assertFalse(tools.symbol_context(tools.Sandbox(self.root), "nada_asi").ok)
        self.assertIn("symbol_context", [s["function"]["name"] for s in tools.specs_for()])


if __name__ == "__main__":
    unittest.main()
