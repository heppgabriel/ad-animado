"""Servidor do Ad Animado: sobe de verdade numa porta livre, com pasta de ads e chaves temporárias."""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def porta_livre():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.ads = os.path.join(cls.tmp, "ads"); cls.porta = porta_livre()
        os.makedirs(os.path.join(cls.tmp, ".grok")); open(os.path.join(cls.tmp, ".grok", "auth.json"), "w").write("{}")   # "login do Grok" falso
        falso = os.path.join(cls.tmp, "falso.py")      # no lugar do lib/animado.py: só marca o estado como rodando
        open(falso, "w").write("import sys,os,json,time\np=sys.argv[1]\njson.dump(dict(rodando=True,pid=os.getpid(),status='rodando',etapas=[],log=['ok '+sys.argv[2]]),open(os.path.join(p,'estado.json'),'w'))\ntime.sleep(3)\n"
                               "json.dump(dict(rodando=False,pid=os.getpid(),status='aguardando',etapas=[],log=['fim']),open(os.path.join(p,'estado.json'),'w'))\n")
        env = dict(os.environ, AD_ANIMADO_PROJETOS=cls.ads, AD_ANIMADO_CHAVES=os.path.join(cls.tmp, "chaves.json"),
                   AD_ANIMADO_PORTA=str(cls.porta), AD_ANIMADO_SCRIPT=falso, HOME=cls.tmp)
        cls.pr = subprocess.Popen([sys.executable, str(ROOT / "app" / "servidor.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(cls.url("/api/health"), timeout=1); break
            except Exception: time.sleep(0.1)

    @classmethod
    def tearDownClass(cls): cls.pr.terminate(); cls.pr.wait()

    @classmethod
    def url(cls, p): return f"http://127.0.0.1:{cls.porta}{p}"

    def get(self, p, **h): return urllib.request.urlopen(urllib.request.Request(self.url(p), headers=h), timeout=10)
    def post(self, p, corpo, raw=False, **h):
        dados = corpo if raw else json.dumps(corpo).encode()
        try: r = urllib.request.urlopen(urllib.request.Request(self.url(p), data=dados, headers=h, method="POST"), timeout=20); return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

    def test_health_pagina_e_config_sem_vazar_chave(self):
        self.assertEqual(json.loads(self.get("/api/health").read())["app"], "ad-animado")
        self.assertIn(b"Ad Animado", self.get("/").read())
        st, c = self.post("/api/config", dict(kie="kie-segredo-1234567890", gen_img="grok"))
        self.assertEqual(st, 200); self.assertTrue(c["kie"]["ok"]); self.assertNotIn("segredo", json.dumps(c))
        self.assertEqual(json.loads(open(os.path.join(self.tmp, "chaves.json")).read())["kie"], "kie-segredo-1234567890")
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "chaves.json")).st_mode & 0o777), "0o600")
        with self.assertRaises(urllib.error.HTTPError): self.get("/f?p=" + urllib.request.quote(os.path.join(self.tmp, "chaves.json")))

    def test_post_de_outro_site_e_recusado(self):
        st, _ = self.post("/api/config", dict(kie="x" * 20), Origin="https://site-malicioso.com")
        self.assertEqual(st, 403)

    def test_criar_ad_listar_servir_video_em_pedacos_e_apagar(self):
        tok = "abc123def456"
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        self.assertEqual(self.post(f"/api/animado/arquivo?id={tok}&tipo=ref&n=1&nome=a.png", png, raw=True)[0], 200)
        self.assertEqual(self.post(f"/api/animado/arquivo?id={tok}&tipo=audio&nome=vo.mp3", b"ID3" + b"0" * 50, raw=True)[0], 200)
        st, r = self.post("/api/animado/criar", dict(id=tok, nome="Meu Ad", ritmo=dict(min=2, max=3), custo="zero", modelo_img="grok", modelo_video="grok"))
        self.assertEqual(st, 200, r); self.assertEqual(r["id"], "Meu Ad")
        pasta = os.path.join(self.ads, "Meu Ad")
        self.assertTrue(os.path.exists(os.path.join(pasta, "pedido.json")))
        self.assertFalse(os.path.exists(os.path.join(self.ads, ".entrada", tok)))   # a entrada foi limpa
        L = json.loads(self.get("/api/animado/lista").read()); self.assertEqual([a["nome"] for a in L], ["Meu Ad"])
        E = json.loads(self.get("/api/animado/estado?id=Meu%20Ad").read()); self.assertEqual(E["pedido"]["ritmo"], dict(min=2.0, max=3.0))
        st, r = self.post("/api/animado/criar", dict(id="outro123token", nome="Meu Ad"))
        self.assertEqual(st, 400)
        vid = os.path.join(pasta, "final.mp4"); open(vid, "wb").write(os.urandom(9 << 20))   # 9 MB
        r = self.get("/f?p=" + urllib.request.quote(vid), Range="bytes=0-")
        self.assertEqual(r.status, 206); self.assertEqual(r.headers["Content-Range"], f"bytes 0-{(8 << 20) - 1}/{9 << 20}")
        for _ in range(60):
            if not json.loads(self.get("/api/animado/estado?id=Meu%20Ad").read())["estado"]["rodando"]: break
            time.sleep(0.1)
        self.assertEqual(self.post("/api/animado/acao?id=Meu%20Ad", dict(acao="apagar"))[0], 200)
        self.assertFalse(os.path.exists(pasta))

    def test_caminho_fora_da_pasta_nao_e_servido(self):
        with self.assertRaises(urllib.error.HTTPError): self.get("/f?p=/etc/passwd")
        with self.assertRaises(urllib.error.HTTPError): self.get("/api/animado/estado?id=..%2F..")

if __name__ == "__main__":
    unittest.main()
