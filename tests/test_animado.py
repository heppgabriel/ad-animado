"""Ad Animado sem rede: Claude e IA generativa falsos, ffmpeg de verdade para a montagem."""
import json, os, re, subprocess, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
try:
    import ia                                                 # o de verdade, se o SDK estiver instalado
except ModuleNotFoundError:                                   # o SDK da Anthropic não é necessário aqui
    fake_ia = types.ModuleType("ia")
    fake_ia.texto = lambda t: {"type": "text", "text": t}
    fake_ia.imagem = lambda c, tipo="image/jpeg": {"type": "image", "path": c}
    sys.modules["ia"] = fake_ia
import animado, custos

def ff(*a): subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *a], check=True)

class FakeConversa:
    def __init__(self, dono, sistema): self.dono, self.sistema = dono, sistema
    def pedir(self, conteudo, schema, rotulo=""):
        txt = " ".join(b.get("text", "") for b in conteudo) if isinstance(conteudo, list) else conteudo
        props = schema["properties"]; self.dono.chamadas.append(rotulo)
        if "locks" in props:
            n = int(re.search(r"SRT \((\d+) blocos", txt).group(1))
            tr = ["AVATAR", "BROLL", "PRODUTO"]
            return dict(locks=dict(avatar="AVATAR LOCK: same man, grey beard", broll="BROLL LOCK: blue palette", packshot="PACK LOCK: white jar"),
                        regra_de_ouro="never change face or pack", pares_espelhados=["01/03"], cenarios_repetidos=[], notas=["ok"],
                        beats=[dict(n=i, trilho=tr[(i - 1) % 3], ideia=f"ideia {i}", prompt=f"prompt {i}", refs=[1, 2, 9],
                                    **({"continuidade": {2: "ALVO", 3: "TRANSICAO"}.get(i, "CONTINUA"),
                                        "quadro_final": {2: "pack reveal", 3: "doctor office"}.get(i, ""),
                                        "transicao": "whip pan to the office" if i == 3 else ""}
                                       if "continuidade" in props["beats"]["items"]["properties"] and getattr(self.dono, "chaves", True) else {})) for i in range(1, n + 1)])
        if "cenas" in props:
            return dict(resumo="senhor com dor", estilo_visual="3D pixar", ritmo="cortes de 2s", hook="barriga estufando",
                        cenas=[dict(ini=0, fim=2, o_que="barriga estufando", enquadramento="close", camera="push-in", fala="sente?"),
                               dict(ini=2, fim=5, o_que="pote na mesa", enquadramento="médio", camera="orbit", fala="")])
        if "mapa" in props:
            return dict(nota=4, justificativa="mesma estrutura", mapa=[dict(n=1, cena=1, como="mesmo hook"), dict(n=3, cena=2, como="pack"), dict(n=4, cena=9, como="x")])
        if "nota_hook" in props:
            return dict(nota_hook=6, nota_corpo=9, nota_produto=9, critica="hook fraco", beats=[dict(n=1, ideia="dor visual", porque="curiosidade", prompt="HOOK forte: twisted hose inside a translucent belly")])
        if "avaliacoes" in props:
            nums = [int(x) for x in re.search(r"Avalie os beats ([\d, ]+)", txt).group(1).split(",")]
            return dict(resumo="", avaliacoes=[dict(n=k, ok=not (k == 2 and self.dono.reprovar), problemas="rosto mudou" if k == 2 else "",
                                                    prompt_corrigido="prompt 2 corrigido" if k == 2 else "") for k in nums])
        if "motion_lock" in props:
            n = int(re.search(r"n de 1 a (\d+)", txt).group(1))
            return dict(motion_lock="single continuous shot", notas=[], clipes=[dict(n=i, prompt=f"move {i}") for i in range(1, n + 1)])
        raise AssertionError("pedido inesperado")

class FakeClaude:
    def __init__(self): self.chamadas = []; self.reprovar = True
    def conversa(self, sistema, esforco="high"): return FakeConversa(self, sistema)

import threading
class FakeGen:
    def __init__(self, base, com_audio=False): self.base = base; self.prompts = []; self.com_audio = com_audio; self.lock = threading.Lock()
    def gerar_imagem(self, prompt, refs=(), aspecto="9:16"):
        self.prompts.append(prompt); return (self.base / "img.jpg").read_bytes(), 0.05, True
    def gerar_video(self, prompt, imagem, duracao, audio=False, **kw):
        self.prompts.append(prompt); arq = self.base / f"v{duracao}{'a' if audio else ''}.mp4"
        with self.lock: self._cria(arq, duracao, audio)
        return arq.read_bytes(), duracao * 0.14, True
    def _cria(self, arq, duracao, audio):
        if not arq.exists():
            if audio: ff("-f", "lavfi", "-i", f"testsrc2=s=720x1280:d={duracao}", "-f", "lavfi", "-i", f"sine=f=300:d={max(1, duracao - 1.5)}",
                         "-af", "apad", "-t", str(duracao), "-shortest", "-pix_fmt", "yuv420p", str(arq))
            else: ff("-f", "lavfi", "-i", f"testsrc2=s=720x1280:d={duracao}", "-pix_fmt", "yuv420p", str(arq))

class AdAnimado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-animado-"); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.d = self.base / "ad"; (self.d / "refs").mkdir(parents=True); (self.d / "fonte").mkdir()
        ff("-f", "lavfi", "-i", "color=c=orange:s=768x1344", "-frames:v", "1", str(self.base / "img.jpg"))
        for k in (1, 2): ff("-f", "lavfi", "-i", "color=c=gray:s=600x600", "-frames:v", "1", str(self.d / "refs" / f"ref_0{k}.jpg"))
        ff("-f", "lavfi", "-i", "sine=f=220:d=7.5", str(self.d / "fonte" / "audio.wav"))
        blocos = [(0.2, 1.9, "Você sente o intestino preso?"), (2.0, 3.1, "Todo dia"), (3.2, 5.6, "Isso tem nome."), (5.7, 7.2, "Clique no botão.")]
        (self.d / "fonte" / "legendas.srt").write_text(animado.escrever_srt(blocos))
        self.ped = dict(nome="Teste", modo="vo", refs=[dict(arquivo="refs/ref_01.jpg", tipo="personagem", nota=""), dict(arquivo="refs/ref_02.jpg", tipo="produto", nota="pote")],
                        audio="fonte/audio.wav", srt="fonte/legendas.srt", roteiro="", estilo="pixar")
        (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl = FakeClaude(); self.gen = FakeGen(self.base)
        for alvo, nome, valor in [(animado, "claude", lambda d, est, ped=None: self.cl), (animado.gerativa, "provedor", lambda: self.gen)]:
            pt = patch.object(alvo, nome, valor); pt.start(); self.addCleanup(pt.stop)

    def rodar(self, acao, nums=None):
        argv = ["animado.py", str(self.d), acao] + ([",".join(map(str, nums))] if nums else [])
        with patch.object(sys, "argv", argv), patch.object(animado.signal, "signal"): animado.main()
        return json.loads((self.d / "estado.json").read_text()), json.loads((self.d / "storyboard.json").read_text())

    def test_srt_roundtrip_and_segmentation(self):
        b = [(0.0, 1.5, "a b"), (1.6, 3.25, "c")]
        self.assertEqual(animado.ler_srt(animado.escrever_srt(b)), b)
        palavras = []; t = 0.0
        for k, w in enumerate("Você já sentiu isso, todo dia de manhã? Pois é. Tem um jeito simples de resolver e eu vou te mostrar agora mesmo aqui.".split()):
            palavras.append(dict(w=w, t=t, e=t + 0.3)); t += 0.34 + (0.5 if w.endswith((".", "?")) else 0)
        for ritmo in [(1, 2), (2, 3), (3, 4.2), (4, 6)]:
            bl = animado.srt_de_palavras(palavras, ritmo)
            self.assertEqual(" ".join(x[2] for x in bl).split(), [p["w"] for p in palavras])   # nenhuma palavra some
            self.assertTrue(all(z - a <= ritmo[1] + 0.45 for a, z, _ in bl), (ritmo, bl))
            self.assertTrue(all(bl[i][1] <= bl[i + 1][0] for i in range(len(bl) - 1)))
        self.assertGreater(len(animado.srt_de_palavras(palavras, (1, 2))), len(animado.srt_de_palavras(palavras, (4, 6))))
        longo = animado.dividir_longos([(0.0, 7.0, "um dois tres quatro cinco seis sete oito nove dez")], (2, 3))
        self.assertEqual(len(longo), 3); self.assertEqual(longo[-1][1], 7.0); self.assertEqual(longo[0][0], 0.0)
        self.assertEqual(animado.ritmo_de({"ritmo": {"min": 2, "max": 3}}), (2.0, 3.0))
        r = animado.srt_de_roteiro("Primeira fala curta. Segunda fala, um pouco maior que a primeira!", (3, 4.2))
        self.assertEqual(len(r), 2); self.assertTrue(r[0][1] < r[1][0])

    def test_full_flow_images_validation_redo_videos_and_timeline(self):
        est, B = self.rodar("imagens")
        self.assertEqual(est["status"], "aguardando"); self.assertEqual(len(B["beats"]), 4)
        self.assertEqual([b["n"] for b in B["beats"]], [1, 2, 3, 4])
        self.assertEqual(B["beats"][0]["refs"], [1, 2])                      # referência inexistente (9) cai fora
        self.assertEqual(B["beats"][1]["img_estado"], "refazer"); self.assertIn("02", est["mensagem"])
        self.assertTrue(any("AVATAR LOCK" in p for p in self.gen.prompts)); self.assertTrue(all("No captions" in p for p in self.gen.prompts))
        self.assertEqual(B["beats"][0]["prompt"], "HOOK forte: twisted hose inside a translucent belly")      # a revisão reescreveu o hook
        self.assertEqual(B["revisao"]["nota_hook"], 6); self.assertEqual(B["revisao"]["reescritos"], [1])
        self.cl.reprovar = False
        est, B = self.rodar("refazer")
        self.assertEqual(B["beats"][1]["prompt"], "prompt 2 corrigido"); self.assertEqual(B["beats"][1]["img_estado"], "ok")
        self.assertEqual(len(B["beats"][1]["historico"]), 1)
        B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertTrue(all(b["video_estado"] == "ok" for b in B["beats"]))
        self.assertIn(animado.TRAVA_PACK, next(p for p in self.gen.prompts if "move 3" in p))   # beat 3 = PRODUTO
        tl = json.loads((self.d / "timeline.json").read_text())
        self.assertEqual([c["n"] for c in tl["clipes"]], [1, 2, 3, 4])
        self.assertAlmostEqual(tl["clipes"][1]["ini"], 2.0, places=2)          # cada clipe começa no tempo do SRT
        self.assertAlmostEqual(animado.ffprobe_dur(str(self.d / "final.mp4")), 7.5, delta=0.25)
        self.assertTrue(animado.tem_audio(str(self.d / "final.mp4")))
        r = custos.resumo(str(self.d)); self.assertGreater(r["grok"], 0); self.assertEqual(r["total"], round(r["grok"] + r["claude"], 4))
        # refazer um vídeo só gera aquele e monta de novo
        antes = len(self.gen.prompts); est, B = self.rodar("refazer_video", [2])
        self.assertEqual(len(self.gen.prompts) - antes, 1); self.assertEqual(est["status"], "pronto")

    def test_render_follows_chosen_video_model(self):
        self.ped["modelo_video"] = "kie:kling-2.6/image-to-video"; (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl.reprovar = False; self.rodar("imagens")
        B = json.loads((self.d / "storyboard.json").read_text()); B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        with patch.object(animado.gerativa, "gerador_video", lambda mid=None: self.gen):
            est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertTrue(all(b["render"] in (5, 10) for b in B["beats"]))            # Kling 2.6 só gera 5 ou 10 s
        tl = json.loads((self.d / "timeline.json").read_text())
        self.assertAlmostEqual(tl["clipes"][1]["ini"], 2.0, places=2)              # mas a timeline continua batendo com o SRT

    def test_reference_video_is_studied_compared_and_applied(self):
        (self.d / "referencia").mkdir()
        ff("-f", "lavfi", "-i", "testsrc2=s=540x960:d=5", "-pix_fmt", "yuv420p", str(self.d / "referencia" / "video.mp4"))
        self.ped.update(referencia_video="referencia/video.mp4", ref_nivel="auto"); (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl.reprovar = False
        est, B = self.rodar("imagens")
        self.assertEqual(est["status"], "aguardando", est.get("mensagem"))
        R = B["referencia"]; self.assertEqual((R["nota"], R["nivel"]), (4, 4)); self.assertTrue(R["automatico"])
        self.assertEqual(B["beats"][0]["ref_cena"], 1); self.assertEqual(B["beats"][3]["ref_cena"], 0)       # cena 9 não existe
        self.assertTrue(all(c.get("quadro") for c in R["cenas"]))
        self.assertTrue(any("LAST reference image" in p for p in self.gen.prompts))                     # nível 4: quadro vai de composição
        self.assertTrue(any("Semelhança" in c or "Referência" in c for c in self.cl.chamadas))

    def test_continuous_ad_chains_each_video_from_the_previous_cut_frame(self):
        self.ped["montagem"] = "continuo"; (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl.reprovar = False; self.cl.chaves = False
        est, B = self.rodar("imagens")
        self.assertEqual(est["status"], "aguardando", est.get("mensagem"))
        self.assertTrue(B["beats"][0]["img"]); self.assertTrue(all(not b.get("img") for b in B["beats"][1:]))   # só o 1º vira imagem
        B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        inicios = []; real = self.gen.gerar_video
        def grava(prompt, imagem, duracao, audio=False, **kw):
            inicios.append(os.path.basename(imagem)); return real(prompt, imagem, duracao, audio)
        self.gen.gerar_video = grava
        est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertTrue(inicios[0].startswith("01-")); self.assertTrue(all(x.startswith(f"continua-{k:02d}") for k, x in enumerate(inicios[1:], 2)))
        self.assertTrue(all(b.get("frame_inicial") for b in B["beats"][1:]))
        tl = json.loads((self.d / "timeline.json").read_text()); self.assertAlmostEqual(tl["clipes"][2]["ini"], 3.2, places=2)
        inicios.clear(); est, B = self.rodar("refazer_video", [3])
        self.assertEqual(len(inicios), 2)                                                     # refaz o 3 e o 4 (que continua do 3)

    def _continuo_com_chaves(self):
        self.ped["montagem"] = "continuo"; (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl.reprovar = False
        est, B = self.rodar("imagens")
        self.assertEqual(est["status"], "aguardando", est.get("mensagem"))
        self.assertEqual([b["continuidade"] for b in B["beats"]], ["CONTINUA", "ALVO", "TRANSICAO", "CONTINUA"])
        self.assertTrue(B["beats"][1]["img_final"] and B["beats"][2]["img_final"]); self.assertFalse(B["beats"][3].get("img_final"))
        self.assertEqual(B["beats"][2]["validacao_final"]["ok"], True)
        B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        chamadas = []; real = self.gen.gerar_video
        def grava(prompt, imagem, duracao, audio=False, **kw):
            chamadas.append(dict(inicio=os.path.basename(imagem), final=os.path.basename(kw["final"]) if kw.get("final") else None, prompt=prompt))
            return real(prompt, imagem, duracao, audio)
        self.gen.gerar_video = grava
        return chamadas

    def test_continuous_keyframes_without_final_frame_model_use_edit_transition(self):
        chamadas = self._continuo_com_chaves()
        est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertIn("-final-", chamadas[2]["inicio"])                          # TRANSICAO: começa no novo ambiente
        self.assertEqual(B["beats"][2]["transicao_entrada"], "smoothleft")         # whip pan feito na edição
        self.assertTrue(chamadas[3]["inicio"].startswith("continua-04"))           # depois segue do corte do 3
        self.assertTrue(all(c["final"] is None for c in chamadas))
        self.assertAlmostEqual(animado.ffprobe_dur(str(self.d / "final.mp4")), 7.5, delta=0.3)

    def test_continuous_keyframes_with_final_frame_model(self):
        chamadas = self._continuo_com_chaves()
        with patch.object(animado.gerativa, "tem_quadro_final", lambda mid=None: True):
            est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertEqual([bool(c["final"]) for c in chamadas], [False, True, True, False])  # ALVO e TRANSICAO terminam no quadro-chave
        self.assertIn("whip pan", chamadas[2]["prompt"]); self.assertTrue(chamadas[2]["inicio"].startswith("continua-03"))

    def test_missing_video_enters_as_still_image(self):
        self.cl.reprovar = False; self.rodar("imagens")
        B = json.loads((self.d / "storyboard.json").read_text()); B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        real = self.gen.gerar_video
        def falha(prompt, imagem, duracao, audio=False, **kw):
            if "move 4" in prompt: raise animado.gerativa.ErroGerativa("bloqueado")
            return real(prompt, imagem, duracao, audio)
        self.gen.gerar_video = falha
        est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto"); self.assertIn("04", est["mensagem"])
        tl = json.loads((self.d / "timeline.json").read_text()); self.assertEqual(tl["clipes"][3]["origem"], "imagem")

    def test_native_speech_mode_keeps_clip_audio(self):
        self.ped.update(modo="nativo", audio=None); (self.d / "pedido.json").write_text(json.dumps(self.ped))
        self.cl.reprovar = False; self.rodar("imagens")
        B = json.loads((self.d / "storyboard.json").read_text()); B["aprovado"] = True; (self.d / "storyboard.json").write_text(json.dumps(B))
        est, B = self.rodar("videos")
        self.assertEqual(est["status"], "pronto", est.get("mensagem"))
        self.assertTrue(any("says this line" in p for p in self.gen.prompts))
        self.assertTrue(animado.tem_audio(str(self.d / "final.mp4")))
        tl = json.loads((self.d / "timeline.json").read_text()); self.assertEqual(len(tl["clipes"]), 4)

class ModoDeCusto(unittest.TestCase):
    def test_gemini_only_when_allowed(self):
        import chaves, gemini
        with tempfile.TemporaryDirectory() as t:
            k = Path(t) / "c.json"
            def cfg(**kw): k.write_text(json.dumps(dict(gemini="AIza-teste", **kw)))
            with patch.object(chaves, "ARQ", str(k)), patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "GEMINI_API_KEY": ""}):
                cfg(); self.assertFalse(animado.gemini_liberado({"custo": "zero"}))           # chave paga: fora no modo zero
                self.assertTrue(animado.gemini_liberado({"custo": "gemini"})); self.assertTrue(animado.gemini_liberado({}))
                cfg(gemini_gratis=True); self.assertTrue(animado.gemini_liberado({"custo": "zero"}))
                cfg(gemini_gratis=True, openrouter="sk-or-x"); self.assertFalse(animado.gemini_liberado({"custo": "zero"}))  # OpenRouter é paga
        self.assertEqual(animado.custo_de({}), "livre"); self.assertEqual(animado.custo_de({"custo": "zero"}), "zero")

class Assinatura(unittest.TestCase):
    def test_token_from_grok_login_and_no_cost(self):
        import gerativa, chaves
        with tempfile.TemporaryDirectory() as t:
            arq = Path(t) / "auth.json"; arq.write_text(json.dumps({"contas": [{"tipo": "oauth", "key": "eyJfalso.token.x"}]}))
            k = Path(t) / "chaves.json"; k.write_text(json.dumps({"gen_auth": "assinatura"}))
            with patch.object(chaves, "assinatura_grok", lambda: str(arq)), patch.object(chaves, "ARQ", str(k)), patch.dict(os.environ, {"XAI_API_KEY": ""}):
                self.assertEqual(gerativa.token_assinatura(), "eyJfalso.token.x")
                g = gerativa.Grok(); self.assertTrue(g.assinatura); self.assertEqual(g.chave, "eyJfalso.token.x")
                self.assertEqual(g._custo({"usage": {"cost_in_usd_ticks": 5e8}}, 1.0), (0.0, False))
                self.assertEqual(gerativa.custo_estimado_video(30), 0.0)

import http.server, threading as _th
class FakeKie(http.server.BaseHTTPRequestHandler):
    pedidos = []; tarefas = {}
    def log_message(self, *a): pass
    def _json(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.headers.get("Authorization") != "Bearer kie-teste-1234567890abcdef": return self._json(dict(code=401, msg="bad key"))
        if self.path == "/api/file-base64-upload":
            return self._json(dict(success=True, code=200, data=dict(downloadUrl=f"http://127.0.0.1:{self.server.server_port}/up/{corpo['fileName']}")))
        if self.path == "/api/v1/jobs/createTask":
            FakeKie.pedidos.append(corpo); tid = f"t{len(FakeKie.pedidos)}"; FakeKie.tarefas[tid] = corpo
            return self._json(dict(code=200, msg="success", data=dict(taskId=tid)))
    def do_GET(self):
        if self.path.startswith("/api/v1/jobs/recordInfo"):
            tid = self.path.split("=")[1]; video = "video" in FakeKie.tarefas[tid]["model"]
            url = f"http://127.0.0.1:{self.server.server_port}/res/{'v.mp4' if video else 'i.jpg'}"
            return self._json(dict(code=200, data=dict(taskId=tid, state="success", resultJson=json.dumps(dict(resultUrls=[url])), creditsConsumed=10)))
        if self.path == "/api/v1/chat/credit": return self._json(dict(code=200, data=500))
        arq = self.server.base / ("v.mp4" if self.path.endswith("v.mp4") else "img.jpg")
        b = arq.read_bytes(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

class Kie(unittest.TestCase):
    def setUp(self):
        import gerativa, chaves
        self.g = gerativa; self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); base = Path(self.tmp.name)
        ff("-f", "lavfi", "-i", "color=c=blue:s=576x1024", "-frames:v", "1", str(base / "img.jpg"))
        ff("-f", "lavfi", "-i", "testsrc2=s=576x1024:d=5", "-pix_fmt", "yuv420p", str(base / "v.mp4"))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeKie); srv.base = base
        _th.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_port}"; FakeKie.pedidos = []
        k = base / "chaves.json"; k.write_text(json.dumps({"gen_resolucao": "720p"}))
        casa = base / "casa"; casa.mkdir(); (casa / ".zshrc").write_text('export PATH=x\nexport KIE_API_KEY="kie-teste-1234567890abcdef"\n')
        for alvo, nome, valor in [(gerativa, "KIE_API", url), (gerativa, "KIE_UPLOAD", url), (chaves, "ARQ", str(k))]:
            pt = patch.object(alvo, nome, valor); pt.start(); self.addCleanup(pt.stop)
        pt = patch.dict(os.environ, {"HOME": str(casa), "KIE_API_KEY": "", "KIEAI_API_KEY": "", "KIE_AI_API_KEY": ""}); pt.start(); self.addCleanup(pt.stop)
        self.base = base

    def test_key_found_on_computer_and_image_with_refs(self):
        self.assertEqual(self.g.chave_kie(), "kie-teste-1234567890abcdef")
        k = self.g.gerador_imagem("kie:nano-banana-pro")
        dados, usd, est = k.gerar_imagem("a man", [str(self.base / "img.jpg")])
        self.assertTrue(dados.startswith(b"\xff\xd8")); self.assertAlmostEqual(usd, 0.05)
        p = FakeKie.pedidos[-1]; self.assertEqual(p["model"], "nano-banana-pro"); self.assertEqual(p["input"]["aspect_ratio"], "9:16")
        self.assertEqual(len(p["input"]["image_input"]), 1); self.assertIn("/up/", p["input"]["image_input"][0])
        self.assertTrue(k.testar()["ok"])

    def test_model_that_needs_reference_falls_back_on_broll(self):
        self.g.gerador_imagem("kie:seedream/4.5-edit").gerar_imagem("molecules", [])
        self.assertEqual(FakeKie.pedidos[-1]["model"], "nano-banana-2")

    def test_video_duration_snaps_to_what_the_model_supports(self):
        self.assertEqual(self.g.ajustar_duracao("kie:kling-2.6/image-to-video", 3), 5)
        self.assertEqual(self.g.ajustar_duracao("kie:hailuo/2-3-image-to-video-pro", 7), 10)
        self.assertEqual(self.g.ajustar_duracao("kie:bytedance/seedance-2", 2), 4)
        v = self.g.gerador_video("kie:kling-2.6/image-to-video")
        dados, usd, est = v.gerar_video("move", str(self.base / "img.jpg"), 3, audio=True)
        p = FakeKie.pedidos[-1]["input"]; self.assertEqual(p["duration"], "5"); self.assertTrue(p["sound"]); self.assertGreater(len(dados), 1000)
        self.g.gerador_video("kie:bytedance/seedance-2").gerar_video("move", str(self.base / "img.jpg"), 6)
        p = FakeKie.pedidos[-1]["input"]; self.assertEqual((p["duration"], p["aspect_ratio"], p["generate_audio"]), (6, "9:16", False))
        self.assertFalse(self.g.tem_audio_nativo("kie:wan/2-6-image-to-video")); self.assertTrue(self.g.tem_audio_nativo("kie:veo-3-1"))

if __name__ == "__main__":
    unittest.main()
