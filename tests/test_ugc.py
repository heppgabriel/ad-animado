"""UGC ultra-realista: empacotamento das falas, prompts com as travas da skill, fluxo completo com IAs falsas e o servidor."""
import io, json, os, re, socket, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error, zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import ugc, ugc_regras as R, gerativa, custos

ROTEIRO = ("Você sabia que quase todas as mulheres depois dos cinquenta sentem isso? Pois é. Eu também sentia, todo santo dia, e achava "
           "que era normal da idade, que não tinha jeito, que era só aceitar e seguir em frente sem reclamar de nada porque todo mundo "
           "passa por isso.\nAté que eu conheci o Flora Max. Hoje eu acordo leve.")

def ffmpeg(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)

def jpg(caminho, cor="red"):
    ffmpeg("-f", "lavfi", "-i", f"color=c={cor}:s=540x960", "-frames:v", "1", caminho); return caminho

def mp4(dur, audio=True):
    fd, arq = tempfile.mkstemp(suffix=".mp4"); os.close(fd)
    a = ["-f", "lavfi", "-i", "sine=f=440:r=48000"] if audio else []
    ffmpeg("-f", "lavfi", "-i", f"testsrc=s=720x1280:r=24:d={dur}", *a, "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio else []), arq)
    dados = open(arq, "rb").read(); os.remove(arq); return dados

class Empacotar(unittest.TestCase):
    def test_junta_curtas_quebra_longas_e_audita(self):
        cl = ugc.empacotar(ROTEIRO, 2.35)
        self.assertTrue(all(c["palavras"] <= 23 for c in cl))
        self.assertEqual(ugc.auditoria(ROTEIRO, [c["fala"] for c in cl]), [])
        self.assertEqual(" ".join(c["fala"] for c in cl).split(), ROTEIRO.split())          # nada reescrito nem reordenado
        self.assertIn("Pois é.", cl[0]["fala"])                                              # curtas juntas no mesmo clipe
        for c in cl: self.assertEqual(c["dur"], next(d for d in (4, 6, 8, 10) if c["palavras"] <= int(d * 2.35)))
        self.assertLess(len(ugc.empacotar(ROTEIRO, 2.9)), len(cl) + 1)

    def test_auditoria_acha_diferenca(self):
        self.assertTrue(ugc.auditoria("um dois três", ["um três"]))

    def test_aviso_de_numero(self):
        self.assertTrue(ugc.avisos_roteiro("faço 50 abdominais"))
        self.assertFalse(ugc.avisos_roteiro("faço cinquenta abdominais"))

class Prompt(unittest.TestCase):
    DESC = dict(tracos="same face shape and jawline, same white blouse", cenario="She sits on a beige couch, hands resting in her lap.",
                pode_mexer="Hands stay in her lap.", camera_tipo="fixa", posicao="from a phone propped on the coffee table",
                enquadramento="framing her from the chest up", ambiencia="quiet living room tone", genero="f", tem_produto=False)

    def test_ordem_travas_e_fala_por_ultimo(self):
        c = dict(n=1, fala="Eu também sentia, todo santo dia.", dur=6, beats=["[0:00-0:03] tired look, body still, only her face moves", "[0:03-0:06] slight nod"], nao_extra="cinematic look")
        p = ugc.montar_prompt(c, self.DESC, dict(personagem=dict(nome="Ana")), dict(voz="Warm calm mature Brazilian Portuguese female voice"))
        self.assertTrue(p.startswith("9:16 vertical. 6 seconds. Single continuous shot. Realistic amateur UGC video."))
        ordem = ["LANGUAGE LOCK", "COMPLETENESS LOCK", "IDENTITY LOCK — HIGHEST PRIORITY: This is ANA.", "Scene:", "Image quality lock",
                 "Camera: Absolutely fixed shot", "[0:00-0:03]", "[0:03-0:06]", "Audio:", "\"Eu também sentia, todo santo dia.\"", "Do not:"]
        pos = [p.index(x) for x in ordem]; self.assertEqual(pos, sorted(pos))
        self.assertIn("Every edge of the frame stays in exactly the same place", p)
        self.assertNotIn("cinematic", p.lower())
        self.assertTrue(p.split("Do not:")[0].rstrip().endswith("\"Eu também sentia, todo santo dia.\""))

    def test_mudo_e_selfie_e_homem(self):
        d = dict(self.DESC, camera_tipo="selfie", genero="m")
        p = ugc.montar_prompt(dict(n=2, fala="", tipo="escuta", dur=10, beats=[]), d, {}, {})
        self.assertIn("NO SPEECH LOCK", p); self.assertIn("He does not speak", p); self.assertIn("This is a selfie", p)
        self.assertNotIn("COMPLETENESS LOCK", p)

class Fluxo(unittest.TestCase):
    """preparar -> vídeos -> montar, com Claude, imagem e Omni falsos (ffmpeg de verdade)."""
    def setUp(self):
        self.d = tempfile.mkdtemp(); d = self.d
        for sub in ("cenas", "fonte"): os.makedirs(os.path.join(d, sub))
        jpg(os.path.join(d, "cenas", "c1.jpg"), "red"); jpg(os.path.join(d, "cenas", "c2.jpg"), "blue")
        jpg(os.path.join(d, "fonte", "avatar.jpg"), "green"); jpg(os.path.join(d, "fonte", "produto.png"), "white")
        self.ped = dict(nome="Teste", roteiro=ROTEIRO, personagem=dict(nome="Ana", voz=""), taxa="calmo", omni="kie", resolucao="720p",
                        broll=True, modelo_img="kie:nano-banana-pro", modelo_broll="kie:kling/v3-turbo-image-to-video", avatar="fonte/avatar.jpg",
                        produto="fonte/produto.png", marca="FLORA MAX", referencia=None, rosto_novo="", pedido_avatar="",
                        cenas=[dict(id="c1", arquivo="cenas/c1.jpg", trocar=True, modo="rosto + cabelo", pedido="segurando o produto", nota="hook"),
                               dict(id="c2", arquivo="cenas/c2.jpg", trocar=False, modo="rosto + cabelo", pedido="", nota="")])
        ugc.grava_json(os.path.join(d, "ugc.json"), self.ped)
        self.chamadas = []
        teste = self
        class Conv:
            def __init__(s, instr): s.instr = instr
            def pedir(s, conteudo, schema, rotulo="", formato=True, **k):
                teste.chamadas.append(rotulo)
                if rotulo.startswith("Troca"): return dict(prompt="Edit image 1. Replace the woman's face. cinematic", avisos=[])
                if rotulo.startswith("Leitura"):
                    return dict(tracos="same face", cenario="She sits on a couch.", pode_mexer="Hands in lap.", camera_tipo="fixa", posicao="from a phone on the table",
                                enquadramento="framing her from the chest up", ambiencia="quiet room", genero="f", tem_produto=rotulo.endswith("c1"), avisos=[])
                if rotulo.startswith("Plano"):
                    n = len(ugc.empacotar(ROTEIRO, 2.35))
                    return dict(voz="Warm calm voice", analise="ok", avisos=[], clipes=[dict(n=i, cena="c1" if i == 1 else "c2", beats=["[0:00-0:02] a", "[0:02-0:04] b"], nao_extra="") for i in range(1, n + 1)],
                                broll=[dict(cobre=2, dur=4, imagem_prompt="hands holding the jar", prompt="Slow push-in, 4 seconds.\n\nHands hold the jar.")])
                raise AssertionError(rotulo)
        class Cl:
            def conversa(s, instr, esforco="high"): return Conv(instr)
        class Img:
            def gerar_imagem(s, prompt, refs=(), aspecto="9:16"):
                teste.chamadas.append(("img", prompt, len(refs))); return open(jpg(tempfile.mktemp(suffix=".jpg"), "yellow"), "rb").read(), 0.02, True
        class Omni:
            def gerar_omni(s, prompt, imagens, duracao, resolucao=None):
                teste.chamadas.append(("omni", duracao, len(imagens), prompt)); return mp4(duracao), 0.3, True
        class Vid:
            def gerar_video(s, prompt, imagem, duracao, audio=False, **k):
                teste.chamadas.append(("kling", duracao)); return mp4(duracao, audio=False), 0.1, True
        self.p = [patch.object(ugc, "claude", lambda d, est: Cl()), patch.object(gerativa, "gerador_imagem", lambda mid=None: Img()),
                  patch.object(gerativa, "gerador_omni", lambda p="kie": Omni()), patch.object(gerativa, "gerador_video", lambda mid=None: Vid())]
        for x in self.p: x.start(); self.addCleanup(x.stop)

    def test_completo(self):
        d = self.d; ped = self.ped; pl = ugc.Plano(d); est = ugc.Estado(d)
        ugc.etapa_referencia(d, ped, pl, est)
        ugc.etapa_avatar(d, ped, pl, est)
        self.assertTrue(pl.cena("c1").get("img_final")); self.assertNotIn("cinematic", pl.cena("c1")["swap_prompt"])
        troca = next(c for c in self.chamadas if isinstance(c, tuple) and c[0] == "img"); self.assertEqual(troca[2], 3)   # cena + avatar + produto
        ugc.etapa_cenas(d, ped, pl, est); ugc.etapa_plano(d, ped, pl, est)
        cl = pl.p["clipes"]; self.assertEqual(len(cl), len(ugc.empacotar(ROTEIRO, 2.35)))
        self.assertTrue(cl[0]["com_produto"]); self.assertIn("@image2", cl[0]["prompt"]); self.assertFalse(cl[1]["com_produto"])
        vozes = {re.search(r"Audio: (.*?)\. Close", c["prompt"]).group(1) for c in cl}; self.assertEqual(vozes, {"Warm calm voice"})
        self.assertEqual(len(pl.p["broll"]), 1)
        ugc.etapa_videos(d, ped, pl, est)
        self.assertTrue(all(c["video_estado"] == "ok" for c in pl.p["clipes"] + pl.p["broll"]))
        omni = [c for c in self.chamadas if isinstance(c, tuple) and c[0] == "omni"]
        self.assertEqual(omni[0][2], 2)                                           # cena + produto no clipe com produto
        self.assertEqual(sorted(x[1] for x in omni), sorted(c["dur"] for c in cl))
        ugc.montar(d, ped, pl, est)
        total = ugc.dur_de(os.path.join(d, "juntos.mp4")); self.assertAlmostEqual(total, sum(c["dur"] for c in cl), delta=0.6)
        self.assertTrue(ugc.tem_audio(os.path.join(d, "juntos.mp4")))
        pl.muda("1", aprovado=True); ugc.montar(d, ped, pl, est)               # com aprovados, só eles entram
        self.assertAlmostEqual(ugc.dur_de(os.path.join(d, "juntos.mp4")), cl[0]["dur"], delta=0.5)

    def test_trocar_cena_reescreve_prompt(self):
        d = self.d; ped = self.ped; pl = ugc.Plano(d); est = ugc.Estado(d)
        ugc.etapa_avatar(d, ped, pl, est); ugc.etapa_cenas(d, ped, pl, est); ugc.etapa_plano(d, ped, pl, est)
        c2 = pl.item("2"); antes = c2["prompt"]
        pl.muda("2", cena="c1"); ugc.remontar(d, ped, pl)
        self.assertNotEqual(pl.item("2")["prompt"], antes); self.assertIn("@image2", pl.item("2")["prompt"])
        pl.muda("2", prompt="MEU PROMPT", prompt_editado=True); ugc.remontar(d, ped, pl)
        self.assertEqual(pl.item("2")["prompt"], "MEU PROMPT")                   # editado à mão não é sobrescrito

def porta_livre():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.ads = os.path.join(cls.tmp, "ads"); cls.porta = porta_livre()
        falso = os.path.join(cls.tmp, "falso.py")
        open(falso, "w").write("import sys,os,json\np=sys.argv[1]\njson.dump(dict(rodando=False,pid=os.getpid(),status='aguardando',etapas=[],log=['ok '+sys.argv[2]]),open(os.path.join(p,'estado.json'),'w'))\n")
        cls.env = dict(os.environ, AD_ANIMADO_PROJETOS=cls.ads, AD_ANIMADO_CHAVES=os.path.join(cls.tmp, "chaves.json"), AD_ANIMADO_PORTA=str(cls.porta),
                       AD_UGC_SCRIPT=falso, HOME=cls.tmp, KIE_API_KEY="kie-teste-1234567890abcdef")
        cls.pr = subprocess.Popen([sys.executable, str(ROOT / "app" / "servidor.py")], env=cls.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(cls.url("/api/health"), timeout=1); break
            except Exception: time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.pr.terminate(); cls.pr.wait()
    @classmethod
    def url(cls, p): return f"http://127.0.0.1:{cls.porta}{p}"
    def post(self, p, corpo, raw=False):
        try: r = urllib.request.urlopen(urllib.request.Request(self.url(p), data=corpo if raw else json.dumps(corpo).encode(), method="POST"), timeout=20); return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

    def test_criar_editar_zip(self):
        self.assertIn(b"UGC ultra-realista", urllib.request.urlopen(self.url("/ugc")).read())
        tok = "ugctoken123"; img = open(jpg(os.path.join(self.tmp, "x.jpg")), "rb").read()
        for n in (1, 2): self.assertEqual(self.post(f"/api/ugc/arquivo?id={tok}&tipo=cena&n={n}&nome=c.jpg", img, raw=True)[0], 200)
        st, r = self.post("/api/ugc/criar", dict(id=tok, nome="Meu UGC", roteiro=ROTEIRO, cenas=[dict(n=1, trocar=True), dict(n=2)]))
        self.assertEqual(st, 400); self.assertIn("avatar", r["erro"])            # pediu avatar sem foto nem descrição
        self.assertEqual(self.post(f"/api/ugc/arquivo?id={tok}&tipo=avatar&nome=a.jpg", img, raw=True)[0], 200)
        st, r = self.post("/api/ugc/criar", dict(id=tok, nome="Meu UGC", roteiro=ROTEIRO, omni="google", cenas=[dict(n=1, trocar=True, pedido="segurando o pote"), dict(n=2)]))
        self.assertEqual(st, 200, r)
        p = os.path.join(self.ads, "UGC", "Meu UGC"); ped = json.load(open(os.path.join(p, "ugc.json")))
        self.assertEqual([c["id"] for c in ped["cenas"]], ["c1", "c2"]); self.assertEqual(ped["cenas"][0]["pedido"], "segurando o pote")
        self.assertTrue(ped["avatar"]); self.assertEqual(ped["omni"], "google")
        time.sleep(0.5)
        self.assertEqual(json.loads(urllib.request.urlopen(self.url("/api/ugc/lista")).read())[0]["nome"], "Meu UGC")
        # plano e vídeos falsos para testar edição, aprovação e o zip
        os.makedirs(os.path.join(p, "videos"))
        for n in (1, 2): open(os.path.join(p, "videos", f"{n:02d}.mp4"), "wb").write(mp4(1))
        desc = dict(tracos="same face", cenario="couch", pode_mexer="", camera_tipo="fixa", posicao="x", enquadramento="y", ambiencia="z", genero="f", tem_produto=False)
        ugc.grava_json(os.path.join(p, "plano.json"), dict(cenas=dict(c1=dict(desc=desc), c2=dict(desc=desc)), voz="voz", broll=[],
            clipes=[dict(n=1, fala="Oi.", dur=4, palavras=1, cena="c1", beats=[], nao_extra="", video="videos/01.mp4", video_estado="ok", aprovado=False, prompt="p1"),
                    dict(n=2, fala="Tchau.", dur=4, palavras=1, cena="c1", beats=[], nao_extra="", video="videos/02.mp4", video_estado="ok", aprovado=False, prompt="p2")]))
        self.assertEqual(self.post("/api/ugc/acao?id=Meu%20UGC", dict(acao="editar", item="2", cena="c2", dur=6))[0], 200)
        pl = json.load(open(os.path.join(p, "plano.json"))); c2 = pl["clipes"][1]
        self.assertEqual((c2["cena"], c2["dur"]), ("c2", 6)); self.assertIn("6 seconds", c2["prompt"])
        self.assertEqual(self.post("/api/ugc/acao?id=Meu%20UGC", dict(acao="aprovar", item="1"))[0], 200)
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(self.url("/api/ugc/zip?id=Meu%20UGC")).read()))
        self.assertEqual(sorted(n.split("/")[-1] for n in z.namelist()), ["01.mp4", "02.mp4", "falas.txt"])
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(self.url("/api/ugc/zip?id=Meu%20UGC&aprovados=1")).read()))
        self.assertEqual(sorted(n.split("/")[-1] for n in z.namelist()), ["01.mp4", "falas.txt"])
        E = json.loads(urllib.request.urlopen(self.url("/api/ugc/estado?id=Meu%20UGC")).read())
        self.assertEqual(len(E["cenas"]), 2); self.assertTrue(E["cenas"][0]["trocar"])
        self.assertEqual(self.post("/api/ugc/acao?id=Meu%20UGC", dict(acao="apagar"))[0], 200)

if __name__ == "__main__":
    unittest.main()
