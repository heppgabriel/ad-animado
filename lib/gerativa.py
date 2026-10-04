"""IA generativa de imagem e vídeo do Ad Animado. Hoje: Grok Imagine (xAI). Só usa a biblioteca padrão.

Provedor novo = uma classe com gerar_imagem / gerar_video / testar, registrada em PROVEDORES e em chaves.GERATIVAS.

Grok (docs.x.ai, 2026-09):
  imagem  POST /v1/images/generations {model, prompt, n, aspect_ratio, resolution, response_format}
          POST /v1/images/edits       {model, prompt, images: [{type:"image_url", url}], aspect_ratio}  (até 5 referências;
          url pode ser data URI base64) -> {data: [{url | b64_json}], usage: {cost_in_usd_ticks}}
  vídeo   POST /v1/videos/generations {model, prompt, image: {url}, duration 1-15, aspect_ratio, resolution, generate_audio}
          -> {request_id};  GET /v1/videos/{id} -> {status: pending|done|expired|failed, video: {url, duration}}
  Preço de tabela (US$): imagem 2.0 ~0,04 (1K low) + 0,01 por imagem de entrada; vídeo 1.5 por segundo: 480p 0,08 ·
  720p 0,14 · 1080p 0,25 (+0,01 pela imagem inicial). Quando a API devolve o custo (cost_in_usd_ticks), vale ele.

Duas formas de entrar (⚙ › IA generativa):
  assinatura  o mesmo login do `grok` no terminal (SuperGrok/X Premium): o token OAuth fica em ~/.grok/auth.json e as
              gerações saem da cota semanal da assinatura, sem cobrança por uso (é assim que as skills capcut-brolls-plus e
              animacao-grok-prompts já geravam). Token vencido -> roda `grok -p` uma vez (ele renova sozinho) e tenta de novo.
  api         chave de API da xAI, cobrada por imagem e por segundo de vídeo.
Downloads das URLs hospedadas pedem User-Agent de navegador (a borda da CDN bloqueia o do urllib: Cloudflare 1010).
Credencial com Zero Data Retention (cabeçalho X-Zero-Data-Retention: true) não devolve URL do vídeo: avisa em vez de falhar calado.

Teste sem gastar: ESTUDIO_XAI_API apontando para um servidor falso."""
import os, re, sys, json, math, time, base64, shutil, threading, subprocess, tempfile, urllib.request, urllib.error, urllib.parse
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import chaves

MODELO_IMG = "grok-imagine-image"                         # ⚙ pode trocar para grok-imagine-image-quality
MODELO_VIDEO = "grok-imagine-video-1.5"
PRECO_IMG, PRECO_ENTRADA = 0.04, 0.01
PRECO_SEG = {"480p": 0.08, "720p": 0.14, "1080p": 0.25}
TICKS_POR_USD = 1e10

class ErroGerativa(Exception):
    def __init__(self, msg, fatal=False): super().__init__(msg); self.fatal = fatal   # fatal: não adianta tentar de novo

def data_uri(arq):
    ext = os.path.splitext(arq)[1].lower()
    mime = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(open(arq, "rb").read()).decode()

NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
             "Accept": "*/*", "Referer": "https://x.ai/"}

def token_assinatura():
    """O JWT do login do `grok` (~/.grok/auth.json, campo "key" que começa com eyJ)."""
    arq = chaves.assinatura_grok()
    try: dados = json.load(open(arq))
    except (OSError, ValueError): return ""
    achados = []
    def anda(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "key" and isinstance(v, str) and v.startswith("eyJ"): achados.append(v)
                else: anda(v)
        elif isinstance(o, list):
            for i in o: anda(i)
    anda(dados)
    return achados[0] if achados else ""

def binario_grok():
    cands = (os.path.expanduser("~/.grok/bin/grok"), os.path.expanduser("~/.grok/bin/grok.exe"), shutil.which("grok"))
    return next((b for b in cands if b and os.path.exists(b)), None)

def renovar_assinatura():
    """Roda o `grok` uma vez em modo headless: ao abrir, ele renova o token vencido sozinho (gasta quase nada da cota)."""
    b = binario_grok()
    if not b: return False
    try:
        subprocess.run([b, "-p", "Responda apenas: ok", "--output-format", "json", "--max-turns", "1"], capture_output=True,
                       text=True, timeout=90, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
        return True
    except (subprocess.TimeoutExpired, OSError): return False

class Grok:
    nome = "Grok"
    def __init__(self, chave=None):
        c = chaves.ler()
        self.assinatura = chave is None and (c.get("gen_auth") or "assinatura") == "assinatura" and not os.environ.get("XAI_API_KEY")
        if self.assinatura:
            self.chave = token_assinatura()
            if not self.chave: raise ErroGerativa("não achei o login do Grok neste Mac (~/.grok/auth.json): abra o Terminal, rode `grok` e entre com a sua conta", fatal=True)
        else:
            self.chave = chave or c.get("xai") or os.environ.get("XAI_API_KEY", "")
            if not self.chave: raise ErroGerativa("configure a chave da API da xAI em ⚙ Configurações", fatal=True)
        self.api = (os.environ.get("ESTUDIO_XAI_API") or "https://api.x.ai/v1").rstrip("/")
        self.resolucao = c.get("gen_resolucao") or "720p"
        self.modelo_img = c.get("gen_modelo_img") or MODELO_IMG
        self.renovado = False; self.cabecalhos = {}

    def _req(self, metodo, caminho, corpo=None, timeout=180, tentativas=4):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        t = 0
        while t < tentativas:
            req = urllib.request.Request(self.api + caminho, data=dados, method=metodo,
                                         headers={"Authorization": "Bearer " + self.chave, "Content-Type": "application/json",
                                                  "Accept": "application/json", "User-Agent": "AdAnimado/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    self.cabecalhos = {k.lower(): v for k, v in r.headers.items()}
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                det = (e.read() or b"")[:400].decode("utf-8", "replace")
                if e.code in (401, 403) and self.assinatura and not self.renovado:
                    self.renovado = True
                    if renovar_assinatura() and token_assinatura(): self.chave = token_assinatura(); continue
                if e.code in (401, 403):
                    raise ErroGerativa("o login do Grok venceu: abra o Terminal, rode `grok` uma vez e tente de novo" if self.assinatura
                                       else f"o Grok recusou a chave ({e.code}); confira em ⚙ Configurações", fatal=True)
                if e.code == 402: raise ErroGerativa("a conta da xAI está sem crédito (console.x.ai)", fatal=True)
                if e.code == 429 and self.assinatura and t >= tentativas - 1:
                    raise ErroGerativa("a cota semanal do Grok acabou ou está no limite por agora — espere e retome", fatal=True)
                if e.code in (429, 500, 502, 503, 504) and t < tentativas - 1:
                    time.sleep(min(60, 8 * (t + 1))); t += 1; continue
                if e.code == 400 and ("moderat" in det.lower() or "safety" in det.lower() or "policy" in det.lower()):
                    raise ErroGerativa("o Grok bloqueou este prompt pela política de conteúdo — reescreva a cena")
                raise ErroGerativa(f"o Grok respondeu {e.code}: {det}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if t < tentativas - 1: time.sleep(5 * (t + 1)); t += 1; continue
                raise ErroGerativa(f"sem conexão com a API do Grok ({e})")
        raise ErroGerativa("o Grok não respondeu")

    def _baixar(self, url, timeout=300):
        if url.startswith("data:"): return base64.b64decode(url.split(",", 1)[1])
        return urllib.request.urlopen(urllib.request.Request(url, headers=NAVEGADOR), timeout=timeout).read()

    def _custo(self, r, estimado):
        if self.assinatura: return 0.0, False                       # sai da cota semanal, não da carteira
        t = ((r or {}).get("usage") or {}).get("cost_in_usd_ticks")
        return (t / TICKS_POR_USD, False) if isinstance(t, (int, float)) and t > 0 else (estimado, True)

    def gerar_imagem(self, prompt, refs=(), aspecto="9:16"):
        """refs: caminhos das imagens de referência (até 5). Devolve (bytes da imagem, US$, estimado?)."""
        refs = list(refs)[:5]
        if refs:
            corpo = dict(model=self.modelo_img, prompt=prompt, aspect_ratio=aspecto, response_format="b64_json",
                         images=[dict(type="image_url", url=data_uri(a)) for a in refs])
            r = self._req("POST", "/images/edits", corpo)
        else:
            r = self._req("POST", "/images/generations", dict(model=self.modelo_img, prompt=prompt, n=1, aspect_ratio=aspecto,
                                                               response_format="b64_json"))
        d = (r.get("data") or [{}])[0]
        if d.get("b64_json"): img = base64.b64decode(d["b64_json"])
        elif d.get("url"): img = self._baixar(d["url"])
        else: raise ErroGerativa("o Grok não devolveu a imagem (pode ter sido bloqueada pela moderação)")
        usd, est = self._custo(r, PRECO_IMG + PRECO_ENTRADA * len(refs))
        return img, usd, est

    def gerar_video(self, prompt, imagem, duracao, audio=False, aspecto="9:16", log=None, limite=1200, final=None):
        """imagem: start frame (caminho). Devolve (bytes do mp4, US$, estimado?)."""
        dur = max(1, min(15, int(round(duracao))))
        corpo = dict(model=MODELO_VIDEO, prompt=prompt, image=dict(url=data_uri(imagem)), duration=dur,
                     aspect_ratio=aspecto, resolution=self.resolucao, generate_audio=bool(audio))
        rid = self._req("POST", "/videos/generations", corpo).get("request_id")
        if str(self.cabecalhos.get("x-zero-data-retention", "")).lower() == "true":
            raise ErroGerativa("esta credencial do Grok está com Zero Data Retention e não devolve o vídeo — use outra conta/chave", fatal=True)
        if not rid: raise ErroGerativa("o Grok não aceitou o pedido de vídeo")
        t0 = time.time(); r = {}
        while time.time() - t0 < limite:
            time.sleep(5)
            r = self._req("GET", f"/videos/{rid}", timeout=60)
            st = r.get("status")
            if st == "done": break
            if st in ("failed", "expired"):
                raise ErroGerativa(f"o Grok não gerou o vídeo ({st}{': ' + str(r.get('error'))[:200] if r.get('error') else ''})")
        else:
            raise ErroGerativa("o vídeo demorou demais no Grok (mais de 20 min)")
        url = (r.get("video") or {}).get("url")
        if not url: raise ErroGerativa("o Grok terminou sem devolver o vídeo (pode ter sido bloqueado pela moderação)")
        usd, est = self._custo(r, dur * PRECO_SEG.get(self.resolucao, 0.14) + PRECO_ENTRADA)
        return self._baixar(url), usd, est

    def testar(self):
        try:
            if self.assinatura:
                self._req("GET", "/models", timeout=30, tentativas=1)
                return dict(ok=True, msg="conectado pela assinatura do Grok (o mesmo login do terminal · usa a cota semanal)")
            r = self._req("GET", "/api-key", timeout=20, tentativas=1)
            if r.get("api_key_blocked") or r.get("team_blocked"): return dict(ok=False, msg="chave bloqueada na xAI")
            return dict(ok=True, msg="chave válida" + (f" · {r['name']}" if r.get("name") else ""))
        except ErroGerativa as e:
            return dict(ok=False, msg=str(e))

PROVEDORES = {"grok": Grok}

def provedor():
    """O Grok (pela assinatura ou pela chave de API, conforme ⚙)."""
    return Grok()

# ---------------------------------------------------------------- KIE (kie.ai): muitas IAs com uma chave só
"""KIE: POST https://api.kie.ai/api/v1/jobs/createTask {model, input} -> {code: 200, data: {taskId}};
GET /api/v1/jobs/recordInfo?taskId= -> data.state waiting|queuing|generating|success|fail, data.resultJson (texto JSON com
resultUrls), data.failMsg, data.creditsConsumed. As imagens de entrada precisam ser URL: sobem antes pelo
POST https://kieai.redpandaai.co/api/file-base64-upload {base64Data, uploadPath, fileName} -> data.downloadUrl (some em 3 dias).
Resultado expira em 24 h: baixa na hora. Crédito: GET /api/v1/chat/credit -> data (saldo).
A chave: ⚙ ou a que já está no seu computador (variável KIE_API_KEY/KIEAI_API_KEY no ~/.zshrc, ou na configuração do
Claude Code / Claude desktop, onde a skill do terminal a guarda). Teste sem gastar: ESTUDIO_KIE_API / ESTUDIO_KIE_UPLOAD."""
KIE_API = os.environ.get("ESTUDIO_KIE_API") or "https://api.kie.ai"
KIE_UPLOAD = os.environ.get("ESTUDIO_KIE_UPLOAD") or "https://kieai.redpandaai.co"
USD_POR_CREDITO = 0.005                                    # estimativa: pacote de US$ 5 = 1.000 créditos

def _perto(valores, s):
    """O menor valor permitido que cobre `s` segundos (o clipe é cortado no tamanho do corte depois); se nenhum cobre, o maior."""
    return next((v for v in sorted(valores) if v >= s - 0.05), max(valores))

def _res(permitidas, pedida, padrao):
    return pedida if pedida in permitidas else padrao

# Catálogo: o que cada modelo aceita. img: campo das referências, máx. de referências, se aceita zero, campos extras.
# vid: campo da imagem inicial, durações, se tem áudio nativo, campos extras.
KIE_IMG = {
    "nano-banana-pro": dict(nome="Nano Banana Pro (Google)", campo="image_input", max=8, sem_ref=True,
                            extra=lambda: dict(aspect_ratio="9:16", resolution="2K", output_format="jpg")),
    "nano-banana-2": dict(nome="Nano Banana 2 (Google)", campo="image_input", max=14, sem_ref=True,
                          extra=lambda: dict(aspect_ratio="9:16", resolution="2K", output_format="jpg")),
    "seedream/4.5-edit": dict(nome="Seedream 4.5 (ByteDance)", campo="image_urls", max=14, sem_ref=False,
                              extra=lambda: dict(aspect_ratio="9:16", quality="basic")),
    "gpt-image-2-image-to-image": dict(nome="GPT Image 2 (OpenAI)", campo="input_urls", max=16, sem_ref=False,
                                       extra=lambda: dict(aspect_ratio="9:16", resolution="2K")),
}
KIE_VID = {
    "kling/v3-turbo-image-to-video": dict(nome="Kling 3.0 Turbo", durs=list(range(3, 16)), audio=False,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url], duration=str(d), resolution=_res(["720p", "1080p"], res, "720p"))),
    "kling-2.6/image-to-video": dict(nome="Kling 2.6", durs=[5, 10], audio=True,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url], duration=str(d), sound=bool(audio))),
    "bytedance/seedance-2": dict(nome="Seedance 2.0 (ByteDance)", durs=list(range(4, 16)), audio=True,
        monta=lambda url, d, res, audio, fim=None: dict(first_frame_url=url, duration=d, aspect_ratio="9:16",
                                              resolution=_res(["480p", "720p", "1080p"], res, "720p"), generate_audio=bool(audio),
                                              **({"last_frame_url": fim} if fim else {})), final=True),
    "bytedance/seedance-1.5-pro": dict(nome="Seedance 1.5 Pro (ByteDance)", durs=list(range(4, 13)), audio=True,
        monta=lambda url, d, res, audio, fim=None: dict(input_urls=[url], duration=d, aspect_ratio="9:16",
                                              resolution=_res(["480p", "720p", "1080p"], res, "720p"), generate_audio=bool(audio))),
    "hailuo/2-3-image-to-video-pro": dict(nome="Hailuo 2.3 Pro (MiniMax)", durs=[6, 10], audio=False,
        monta=lambda url, d, res, audio, fim=None: dict(image_url=url, duration=str(d), resolution="1080P" if res == "1080p" and d <= 6 else "768P")),
    "wan/2-6-image-to-video": dict(nome="Wan 2.6", durs=[5, 10, 15], audio=False,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url], duration=str(d), resolution=_res(["720p", "1080p"], res, "720p"))),
    "grok-imagine/image-to-video": dict(nome="Grok Imagine (pela KIE)", durs=list(range(6, 31)), audio=False,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url], duration=str(d), mode="normal", aspect_ratio="9:16",
                                              resolution=_res(["480p", "720p", "1080p"], res, "720p"))),
    "google/gemini-omni-flash-1-1": dict(nome="Gemini Omni Flash 1.1 (Google)", durs=[4, 6, 8, 10], audio=True,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url] + ([fim] if fim else []), duration=str(d), aspect_ratio="9:16",
                                              resolution=_res(["360p", "720p", "1080p", "4k"], res, "720p"))),
    "veo-3-1": dict(nome="Veo 3.1 (Google)", durs=[4, 6, 8], audio=True,
        monta=lambda url, d, res, audio, fim=None: dict(image_urls=[url] + ([fim] if fim else []), generation_type="FIRST_AND_LAST_FRAMES_2_VIDEO",
                                              aspect_ratio="9:16", duration=d, resolution=_res(["720p", "1080p"], res, "720p")), final=True),
}

MODELOS_IMAGEM = [dict(id="grok", nome="Grok Imagine (xAI · assinatura ou chave)")] + \
                 [dict(id="kie:" + k, nome=v["nome"] + " · KIE") for k, v in KIE_IMG.items()]
MODELOS_VIDEO = [dict(id="grok", nome="Grok Imagine Video (xAI · assinatura ou chave)", audio=True)] + \
                [dict(id="kie:" + k, nome=v["nome"] + " · KIE", audio=v["audio"], final=bool(v.get("final"))) for k, v in KIE_VID.items()]

def _procura_chave_kie(texto):
    """Chave da KIE num texto de configuração: KIE_API_KEY=..., "KIE_API_KEY": "...", export KIEAI_API_KEY=... """
    m = re.search(r"""KIE[_A-Z]*API[_A-Z]*KEY["']?\s*[:=]\s*["']?([A-Za-z0-9_\-]{20,})""", texto)
    return m.group(1) if m else ""

def chave_kie():
    """⚙ primeiro; depois a que já está no computador (variável de ambiente, ~/.zshrc, config do Claude Code/desktop)."""
    k = chaves.ler().get("kie")
    if k: return k
    for v in ("KIE_API_KEY", "KIEAI_API_KEY", "KIE_AI_API_KEY"):
        if os.environ.get(v): return os.environ[v].strip()
    casa = os.path.expanduser("~")
    for arq in (".zshrc", ".zprofile", ".zshenv", ".bash_profile", ".bashrc", ".profile", ".claude.json", ".claude/settings.json",
                ".claude/settings.local.json", "Library/Application Support/Claude/claude_desktop_config.json", ".config/kie/config.json"):
        try: k = _procura_chave_kie(open(os.path.join(casa, arq), errors="ignore").read())
        except OSError: continue
        if k: return k
    return ""

class Kie:
    nome = "KIE"
    assinatura = False
    def __init__(self, modelo_img=None, modelo_video=None):
        self.chave = chave_kie()
        if not self.chave: raise ErroGerativa("não achei a chave da KIE: cole em ⚙ Configurações (kie.ai › API Key)", fatal=True)
        self.modelo_img, self.modelo_video = modelo_img, modelo_video
        self.resolucao = chaves.ler().get("gen_resolucao") or "720p"; self.enviados = {}; self.lock = threading.Lock()

    def _req(self, metodo, url, corpo=None, timeout=120, tentativas=4):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        for t in range(tentativas):
            req = urllib.request.Request(url, data=dados, method=metodo, headers={"Authorization": "Bearer " + self.chave,
                                         "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "AdAnimado/1.0"})
            try:
                r = json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
            except urllib.error.HTTPError as e:
                det = (e.read() or b"")[:300].decode("utf-8", "replace")
                if e.code in (401, 403): raise ErroGerativa("a KIE recusou a chave; confira em ⚙ Configurações", fatal=True)
                if e.code in (429, 500, 502, 503, 504) and t < tentativas - 1: time.sleep(8 * (t + 1)); continue
                raise ErroGerativa(f"a KIE respondeu {e.code}: {det}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if t < tentativas - 1: time.sleep(5 * (t + 1)); continue
                raise ErroGerativa(f"sem conexão com a KIE ({e})")
            cod = r.get("code")
            if cod in (401, 403): raise ErroGerativa("a KIE recusou a chave; confira em ⚙ Configurações", fatal=True)
            if cod == 402: raise ErroGerativa("a conta da KIE está sem créditos (kie.ai › Billing)", fatal=True)
            if cod in (429, 455, 500, 501, 505) and t < tentativas - 1: time.sleep(8 * (t + 1)); continue
            if cod not in (None, 200): raise ErroGerativa(f"a KIE recusou o pedido ({cod}): {str(r.get('msg'))[:250]}")
            return r
        raise ErroGerativa("a KIE não respondeu")

    def subir(self, arq):
        """Imagem local -> URL temporária na KIE (a API só aceita URL). Uma vez por arquivo."""
        chave = (arq, os.path.getmtime(arq))
        with self.lock:
            if chave in self.enviados: return self.enviados[chave]
        nome = f"atlas-{int(time.time() * 1000)}-{os.path.basename(arq)}"
        r = self._req("POST", KIE_UPLOAD + "/api/file-base64-upload", dict(base64Data=data_uri(arq), uploadPath="atlas-editor", fileName=nome))
        url = ((r.get("data") or {}).get("downloadUrl") or (r.get("data") or {}).get("fileUrl"))
        if not url: raise ErroGerativa("a KIE não devolveu o endereço da imagem enviada")
        with self.lock: self.enviados[chave] = url
        return url

    def tarefa(self, modelo, entrada, limite=1800):
        """Cria a tarefa e espera. Devolve (bytes do resultado, créditos)."""
        tid = ((self._req("POST", KIE_API + "/api/v1/jobs/createTask", dict(model=modelo, input=entrada)).get("data")) or {}).get("taskId")
        if not tid: raise ErroGerativa("a KIE não aceitou a tarefa")
        t0 = time.time()
        while time.time() - t0 < limite:
            time.sleep(5)
            d = self._req("GET", KIE_API + "/api/v1/jobs/recordInfo?taskId=" + urllib.parse.quote(tid), timeout=60).get("data") or {}
            st = d.get("state")
            if st == "success":
                try: res = json.loads(d.get("resultJson") or "{}")
                except ValueError: res = {}
                urls = res.get("resultUrls") or res.get("result_urls") or []
                if not urls: raise ErroGerativa("a KIE terminou sem devolver o arquivo")
                dados = urllib.request.urlopen(urllib.request.Request(urls[0], headers=NAVEGADOR), timeout=300).read()
                return dados, float(d.get("creditsConsumed") or 0)
            if st == "fail":
                msg = str(d.get("failMsg") or "falhou")[:250]
                raise ErroGerativa("a IA bloqueou este prompt pela política de conteúdo — reescreva a cena" if re.search(r"sensitiv|policy|moderat|nsfw|safety", msg, re.I)
                                   else f"a KIE não gerou ({msg})")
        raise ErroGerativa("a KIE demorou demais (mais de 30 min)")

    def _usd(self, creditos): return round(creditos * USD_POR_CREDITO, 4)

    def gerar_imagem(self, prompt, refs=(), aspecto="9:16"):
        spec = KIE_IMG[self.modelo_img]; refs = list(refs)[:spec["max"]]
        if not refs and not spec["sem_ref"]:
            spec_modelo = "nano-banana-2"; spec = KIE_IMG[spec_modelo]          # beat sem referência (B-roll): este modelo exige imagem
        else: spec_modelo = self.modelo_img
        entrada = dict(prompt=prompt, **spec["extra"]()); entrada[spec["campo"]] = [self.subir(a) for a in refs]
        dados, cred = self.tarefa(spec_modelo, entrada)
        return dados, self._usd(cred), True

    def gerar_video(self, prompt, imagem, duracao, audio=False, aspecto="9:16", log=None, limite=1800, final=None):
        """final: imagem em que o clipe tem que terminar (só nos modelos com quadro final: Seedance 2, Veo 3.1)."""
        spec = KIE_VID[self.modelo_video]; d = _perto(spec["durs"], duracao)
        fim = self.subir(final) if final and spec.get("final") else None
        dados, cred = self.tarefa(self.modelo_video, dict(prompt=prompt, **spec["monta"](self.subir(imagem), d, self.resolucao, audio and spec["audio"], fim)))
        return dados, self._usd(cred), True

    def gerar_omni(self, prompt, imagens, duracao, resolucao=None):
        """UGC: Gemini Omni Flash 1.1 com várias imagens de referência (a 1ª é o @image1 / quadro inicial; até 7)."""
        d = _perto([4, 6, 8, 10], duracao)
        entrada = dict(prompt=prompt, image_urls=[self.subir(a) for a in list(imagens)[:7]], duration=str(d), aspect_ratio="9:16",
                       resolution=_res(["360p", "720p", "1080p", "4k"], resolucao or self.resolucao, "720p"))
        dados, cred = self.tarefa("google/gemini-omni-flash-1-1", entrada)
        return dados, self._usd(cred), True

    def testar(self):
        try:
            r = self._req("GET", KIE_API + "/api/v1/chat/credit", timeout=20, tentativas=1)
            return dict(ok=True, msg=f"chave válida · {r.get('data')} créditos na KIE")
        except ErroGerativa as e: return dict(ok=False, msg=str(e))

# ---------------------------------------------------------------- Gemini Omni direto no Google
"""Google AI Studio (Interactions API): POST /v1beta/interactions {model: gemini-omni-1.1-flash, input: [imagens..., texto],
response_format: {type: video, aspect_ratio, resolution, delivery: uri}} -> steps[].content[] {type: video, data|uri}.
A duração vem do texto do prompt ("8 seconds"). Sem plano gratuito (~US$ 0,10 por segundo em 720p). Chave: a do Gemini em ⚙."""
GOOGLE_API = os.environ.get("ESTUDIO_GOOGLE_API") or "https://generativelanguage.googleapis.com/v1beta"
OMNI_GOOGLE = "gemini-omni-1.1-flash"
PRECO_OMNI_GOOGLE = {"360p": 0.05, "720p": 0.10, "1080p": 0.15, "4k": 0.40}   # US$/s, estimativa

class GoogleOmni:
    nome = "Google (Gemini API)"
    assinatura = False
    def __init__(self):
        self.chave = os.environ.get("GEMINI_API_KEY") or chaves.ler().get("gemini", "")
        if not self.chave: raise ErroGerativa("para o Omni pelo Google, cole a chave do Google AI Studio em ⚙ Configurações › Gemini", fatal=True)
        self.resolucao = chaves.ler().get("gen_resolucao") or "720p"

    def _baixar(self, uri, limite=600):
        t0 = time.time(); ultimo = ""
        while time.time() - t0 < limite:
            req = urllib.request.Request(uri, headers={"x-goog-api-key": self.chave, **NAVEGADOR})
            try:
                dados = urllib.request.urlopen(req, timeout=300).read()
                if dados[4:8] == b"ftyp" or len(dados) > 100_000: return dados
                ultimo = dados[:200].decode("utf-8", "replace")                     # ainda processando (JSON de estado)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403): raise ErroGerativa("o Google recusou a chave ao baixar o vídeo", fatal=True)
                ultimo = f"HTTP {e.code}"
            time.sleep(6)
        raise ErroGerativa(f"o vídeo do Google não ficou pronto para baixar ({ultimo[:120]})")

    def gerar_omni(self, prompt, imagens, duracao, resolucao=None):
        res = _res(["360p", "720p", "1080p", "4k"], resolucao or self.resolucao, "720p")
        partes = [dict(type="image", data=base64.b64encode(open(a, "rb").read()).decode(), mime_type=_mime(a)) for a in list(imagens)[:7]]
        corpo = dict(model=OMNI_GOOGLE, input=partes + [dict(type="text", text=prompt)], store=False,
                     generation_config=dict(video_config=dict(task="image_to_video" if partes else "text_to_video")),
                     response_format=dict(type="video", aspect_ratio="9:16", resolution=res, delivery="uri"))
        req = urllib.request.Request(GOOGLE_API + "/interactions", data=json.dumps(corpo).encode(),
                                     headers={"x-goog-api-key": self.chave, "Content-Type": "application/json"})
        for t in range(3):
            try: r = json.loads(urllib.request.urlopen(req, timeout=1200).read()); break
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")[:400]
                if e.code in (401, 403): raise ErroGerativa("o Google recusou a chave (o Omni não tem plano gratuito: ative o faturamento no AI Studio)", fatal=True)
                if e.code in (429, 500, 502, 503, 504) and t < 2: time.sleep(15 * (t + 1)); continue
                if re.search(r"safety|policy|blocked", msg, re.I): raise ErroGerativa("o Google bloqueou este prompt pela política de conteúdo — neutralize a fala/CTA")
                raise ErroGerativa(f"o Google respondeu {e.code}: {msg[:200]}")
            except (urllib.error.URLError, TimeoutError) as e:
                if t < 2: time.sleep(10); continue
                raise ErroGerativa(f"sem conexão com o Google ({e})")
        if r.get("status") in ("failed", "cancelled"): raise ErroGerativa(f"o Omni não gerou ({r.get('status')})")
        for st in r.get("steps") or r.get("outputs") or []:
            for c in (st.get("content") if isinstance(st, dict) else None) or [st]:
                if isinstance(c, dict) and c.get("type") == "video":
                    if c.get("data"): dados = base64.b64decode(c["data"])
                    elif c.get("uri"): dados = self._baixar(c["uri"])
                    else: continue
                    return dados, round(_perto([4, 6, 8, 10], duracao) * PRECO_OMNI_GOOGLE.get(res, 0.10), 4), True
        raise ErroGerativa("o Google respondeu sem vídeo (o prompt pode ter sido filtrado)")

def _mime(arq):
    return {".png": "image/png", ".webp": "image/webp"}.get(os.path.splitext(arq)[1].lower(), "image/jpeg")

OMNI_PROVEDORES = [dict(id="kie", nome="KIE (créditos da KIE)"), dict(id="google", nome="Google AI Studio (chave do Gemini, ~US$ 0,10/s)")]
def gerador_omni(provedor="kie"):
    return GoogleOmni() if provedor == "google" else Kie()

# ---------------------------------------------------------------- escolha por projeto
def padrao_img(): return chaves.ler().get("gen_img") or "grok"
def padrao_video(): return chaves.ler().get("gen_video") or "grok"
def valido(mid, tipo): return mid in [m["id"] for m in (MODELOS_IMAGEM if tipo == "img" else MODELOS_VIDEO)]

def gerador_imagem(mid=None):
    mid = mid or padrao_img()
    if mid.startswith("kie:") and mid[4:] in KIE_IMG: return Kie(modelo_img=mid[4:])
    return provedor()

def gerador_video(mid=None):
    mid = mid or padrao_video()
    if mid.startswith("kie:") and mid[4:] in KIE_VID: return Kie(modelo_video=mid[4:])
    return provedor()

def ajustar_duracao(mid, segundos):
    """Quanto o clipe precisa ter no modelo escolhido (alguns só geram 5/10 s): o menor tamanho permitido que cobre o corte."""
    if mid and mid.startswith("kie:") and mid[4:] in KIE_VID: return _perto(KIE_VID[mid[4:]]["durs"], segundos)
    return max(1, min(15, int(math.ceil(segundos))))

def tem_quadro_final(mid):
    """O modelo aceita quadro inicial E final (o clipe termina numa imagem escolhida)? Seedance 2.0 e Veo 3.1 (pela KIE)."""
    mid = mid or padrao_video()
    return mid.startswith("kie:") and bool(KIE_VID.get(mid[4:], {}).get("final"))

def tem_audio_nativo(mid):
    return next((m.get("audio", False) for m in MODELOS_VIDEO if m["id"] == (mid or padrao_video())), False)

def nome_modelo(mid):
    return next((m["nome"] for m in MODELOS_IMAGEM + MODELOS_VIDEO if m["id"] == mid), mid or "")

def testar_kie():
    try: return Kie(modelo_img="nano-banana-2").testar()
    except ErroGerativa as e: return dict(ok=False, msg=str(e))

def custo_estimado_video(segundos):
    c = chaves.ler()
    if (c.get("gen_auth") or "assinatura") == "assinatura": return 0.0
    res = c.get("gen_resolucao") or "720p"
    return segundos * PRECO_SEG.get(res, 0.14)

def testar():
    try: return provedor().testar()
    except ErroGerativa as e: return dict(ok=False, msg=str(e))

if __name__ == "__main__":
    if sys.argv[1:] == ["testar"]: print(json.dumps(testar(), ensure_ascii=False))
