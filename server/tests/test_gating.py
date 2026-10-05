import unittest

from smartorch.core import gating


class GatingTests(unittest.TestCase):
    def test_chitchat_skips_rag(self):
        for text in ["responde solo: listo", "hola como estas", "gracias!", "di hola en una palabra"]:
            self.assertFalse(gating.wants_project_context(text), text)
            self.assertTrue(gating.is_trivial(text), text)

    def test_generic_short_question_skips_rag(self):
        self.assertFalse(gating.wants_project_context("explicame los decoradores de python"))

    def test_project_questions_use_rag(self):
        for text in [
            "como funciona el watcher que reindexa el workspace",
            "arregla el bug en cli.py _send_chat",
            "por que falla `get_index()` al arrancar",
            "revisa searchFormatted y dime si hay errores",
        ]:
            self.assertTrue(gating.wants_project_context(text), text)
            self.assertFalse(gating.is_trivial(text), text)

    def test_long_message_defaults_to_rag(self):
        long_text = "necesito entender cómo está organizada la parte que atiende las peticiones de los clientes"
        self.assertTrue(gating.wants_project_context(long_text))

    def test_empty_is_trivial(self):
        self.assertFalse(gating.wants_project_context(""))
        self.assertTrue(gating.is_trivial(""))


if __name__ == "__main__":
    unittest.main()
