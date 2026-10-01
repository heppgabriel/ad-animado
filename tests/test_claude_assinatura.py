"""Claude pela assinatura (Claude Code headless): sem rede — _consultar trocado por um falso."""
import json, os, sys, tempfile, unittest, types
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "lib"))
try:
    import ia, chaves
except ModuleNotFoundError:          # sem o SDK anthropic neste Python
    ia = None

def R(**kw):
    base = dict(is_error=False, session_id="s1", usage=dict(input_tokens=10, output_tokens=5), structured_output=None, result="",
                errors=None, subtype="success", api_error_status=None)
    base.update(kw); return types.SimpleNamespace(**base)

@unittest.skipIf(ia is None, "SDK anthropic ausente")
class Assinatura(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        k = Path(self.tmp.name) / "chaves.json"; k.write_text(json.dumps({"claude_auth": "assinatura", "claude_token": "sk-ant-oat-x"}))
        pt = patch.object(chaves, "ARQ", str(k)); pt.start(); self.addCleanup(pt.stop)
        self.cobrado = []; self.chamadas = []

    def falso(self, respostas):
        it = iter(respostas)
        async def f(sistema, conteudo, schema, modelo, esforco, sessao, log_arq=None):
            self.chamadas.append(dict(sistema=sistema, conteudo=conteudo, sessao=sessao, esforco=esforco)); return next(it)
        return patch.object(ia, "_consultar", f)

    def test_structured_output_session_and_no_charge(self):
        cl = ia.Claude(log=lambda *_: None, ao_cobrar=self.cobrado.append)
        self.assertTrue(cl.assinatura); self.assertTrue(chaves.claude_ok())
        with self.falso([R(structured_output={"a": 1}), R(structured_output={"b": 2}, session_id="s1")]):
            cv = cl.conversa("sistema", esforco="medium")
            self.assertEqual(cv.pedir([ia.texto("oi")], {"type": "object"}), {"a": 1})
            self.assertEqual(cv.pedir("de novo", {"type": "object"}), {"b": 2})
        self.assertIsNone(self.chamadas[0]["sessao"]); self.assertEqual(self.chamadas[1]["sessao"], "s1")      # continua a mesma conversa
        self.assertIn("JSON Schema", self.chamadas[0]["conteudo"][-1]["text"])
        self.assertEqual([c["usd"] for c in self.cobrado], [0.0, 0.0]); self.assertIn("assinatura", self.cobrado[0]["modelo"])

    def test_json_in_text_and_retry(self):
        cl = ia.Claude(log=lambda *_: None)
        with self.falso([R(result="não sei"), R(result='```json\n{"ok": true}\n```')]):
            self.assertEqual(cl.conversa("s").pedir("x", {"type": "object"}), {"ok": True})

    def test_limit_and_login_errors_are_explained(self):
        cl = ia.Claude(log=lambda *_: None)
        with self.falso([R(is_error=True, api_error_status=429, errors=["rate limited"])]):
            with self.assertRaisesRegex(RuntimeError, "limite do seu plano"): cl.conversa("s").pedir("x", {})
        with self.falso([R(is_error=True, api_error_status=401, errors=["invalid auth"])]):
            with self.assertRaisesRegex(RuntimeError, "setup-token"): cl.conversa("s").pedir("x", {})

    def test_env_drops_api_key_and_sets_token(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-api-real"}):
            env = ia.ambiente_assinatura()
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ); self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat-x")

if __name__ == "__main__":
    unittest.main()
