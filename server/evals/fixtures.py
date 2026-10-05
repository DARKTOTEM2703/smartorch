"""Proyectos de juguete para el banco de pruebas. Se escriben en una carpeta temporal en cada corrida."""

CALC = {
    "calc.py": '"""Calculadora minima."""\n\n\ndef add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    # BUG: deberia dividir\n    return a * b\n',
    "tests/__init__.py": "",
    "tests/test_calc.py": (
        "import unittest\n\nfrom calc import add, divide\n\n\nclass CalcTests(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
        "    def test_divide(self):\n        self.assertEqual(divide(6, 3), 2)\n"),
    "README.md": "# Calc\nCalculadora de juguete con pruebas.\n",
}

SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": (
        '"""Precios e impuestos de la tienda."""\n\nTAX_RATE = 0.16\n\n\n'
        "def calc_total(items):\n    subtotal = sum(i['price'] * i['qty'] for i in items)\n    return round(subtotal * (1 + TAX_RATE), 2)\n\n\n"
        "def apply_discount(total, code):\n    if code == 'HALF':\n        return round(total / 2, 2)\n    return total\n"),
    "shop/cart.py": (
        '"""Carrito de compras."""\nfrom shop.pricing import calc_total, apply_discount\n\n\n'
        "class Cart:\n    def __init__(self):\n        self.items = []\n\n"
        "    def add(self, name, price, qty=1):\n        self.items.append({'name': name, 'price': price, 'qty': qty})\n\n"
        "    def total(self, code=None):\n        return apply_discount(calc_total(self.items), code)\n"),
    "tests/__init__.py": "",
    "tests/test_shop.py": (
        "import unittest\n\nfrom shop.cart import Cart\n\n\nclass ShopTests(unittest.TestCase):\n"
        "    def test_total_with_tax(self):\n        c = Cart()\n        c.add('pan', 10, 2)\n        self.assertEqual(c.total(), 23.2)\n\n"
        "    def test_discount(self):\n        c = Cart()\n        c.add('pan', 10, 2)\n        self.assertEqual(c.total('HALF'), 11.6)\n"),
    "README.md": "# Shop\nUna tienda en línea mínima: carrito de compras, precios con impuestos y descuentos.\n",
}

# igual que CALC pero sin el bug: para medir "agregar una funcion" sin mezclarlo con "arreglar un bug"
CALC_OK = {**CALC, "calc.py": CALC["calc.py"].replace("    # BUG: deberia dividir\n    return a * b\n", "    return a / b\n"),
           "SMARTORCH.md": "Convenciones: los tests viven en tests/test_<modulo>.py y usan unittest (clases TestCase).\n"}

FIXTURES = {"calc": CALC, "calc_ok": CALC_OK, "shop": SHOP}
