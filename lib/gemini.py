"""Gemini do Ad Animado: assiste o vídeo de referência (estudo e semelhança com a copy).
Dois caminhos: chave da OpenRouter (google/<modelo>) ou chave do Google AI Studio (Interactions API, tem plano gratuito)."""
import os, re, sys, json, time, base64, subprocess, tempfile, urllib.request, urllib.error
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import chaves

API = "https://generativelanguage.googleapis.com/v1beta"
OR_API = os.environ.get("ESTUDIO_OPENROUTER_API") or "https://openrouter.ai/api/v1"     # ESTUDIO_OPENROUTER_API: testes
LIMITE_OR = 8 * 1024 * 1024                              # pela OpenRouter o vídeo vai em base64 no corpo: manda cópia leve acima disso
MODELOS = [dict(id="gemini-3.8-flash", nome="Gemini 3.8 Flash (recomendado)"),
           dict(id="gemini-3.5-flash-lite", nome="Gemini 3.5 Flash-Lite (mais barato)"),
           dict(id="gemini-3.1-pro-preview", nome="Gemini 3.1 Pro (mais caro)")]
# US$ por milhão de tokens (entrada: texto/imagem/vídeo, saída) — tabela da Google em 2026-09-19. O Flash dobra em 2027.
PRECOS = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.7-flash": (0.75, 3.75), "gemini-3.5-flash-lite": (0.30, 2.50),
          "gemini-3.1-flash-lite": (0.25, 1.50), "gemini-3.1-pro-preview": (2.0, 12.0)}
LIMITE_INLINE = 18 * 1024 * 1024
PATH = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

class ErroGemini(RuntimeError):
    def __init__(self, msg, fatal=False): super().__init__(msg); self.fatal = fatal   # fatal: não adianta tentar os outros

def chave_openrouter(): return os.environ.get("OPENROUTER_API_KEY") or chaves.ler().get("openrouter", "")
def chave_google(): return os.environ.get("GEMINI_API_KEY") or chaves.ler().get("gemini", "")
FORCADO = None                                           # "google" | "openrouter": o botão Testar de cada chave testa a sua
def provedor(): return FORCADO or ("openrouter" if chave_openrouter() else "google")
def chave(): return chave_openrouter() if provedor() == "openrouter" else chave_google()
def modelo_padrao(): return chaves.ler().get("modelo_gemini") or MODELOS[0]["id"]

def custo(modelo, u):
    pe, ps = PRECOS.get(modelo, PRECOS["gemini-3.8-flash"])
    return ((u.get("total_input_tokens") or 0) * pe + ((u.get("total_output_tokens") or 0) + (u.get("total_thought_tokens") or 0)) * ps) / 1e6

# ---------------------------------------------------------------- partes da mensagem
def texto(t): return {"type": "text", "text": t}

def imagem(arq): return {"type": "image", "data": base64.b64encode(open(arq, "rb").read()).decode(), "mime_type": "image/jpeg"}

def leve(arq, largura=540, audio=False):
    """Cópia menor para caber no pedido (o limite do envio direto é ~20 MB). Devolve o caminho (temporário)."""
    fd, dest = tempfile.mkstemp(suffix=".mp4"); os.close(fd)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", arq, "-vf", f"scale='min({largura},iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30"]
    cmd += ["-c:a", "aac", "-b:a", "64k", "-ac", "1"] if audio else ["-an"]
    subprocess.run(cmd + ["-movflags", "+faststart", dest], check=True, env=dict(os.environ, PATH=PATH))
    return dest

def video(arq, resolucao="high"):
    """Vídeo inline. O Gemini olha 1 quadro por segundo; resolucao low|medium|high (high pega legenda miúda).
    Campo por parte = "resolution" (a doc de vídeo dizia "media_resolution": a API recusa)."""
    temp = None; lim = LIMITE_OR if provedor() == "openrouter" else LIMITE_INLINE
    if os.path.getsize(arq) > lim: temp = arq = leve(arq)
    if os.path.getsize(arq) > lim: old = temp; temp = arq = leve(arq, 360); os.remove(old)
    p = {"type": "video", "data": base64.b64encode(open(arq, "rb").read()).decode(), "mime_type": "video/mp4", "resolution": resolucao}
    if temp: os.remove(temp)
    return p

# ---------------------------------------------------------------- chamada
def _texto_da_resposta(r):
    if isinstance(r.get("output_text"), str) and r["output_text"].strip(): return r["output_text"]
    partes = []
    for st in r.get("steps") or r.get("outputs") or []:
        if not isinstance(st, dict) or st.get("type") not in (None, "model_output", "output", "text"): continue
        for c in st.get("content") or [st]:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"): partes.append(c["text"])
    return "".join(partes)

def _partes_openrouter(partes):
    """Partes no formato da Interactions API -> conteúdo de chat completions (OpenRouter)."""
    out = []
    for p in partes:
        if p["type"] == "text": out.append({"type": "text", "text": p["text"]})
        elif p["type"] == "image": out.append({"type": "image_url", "image_url": {"url": f"data:{p['mime_type']};base64,{p['data']}"}})
        elif p["type"] == "video": out.append({"type": "video_url", "video_url": {"url": f"data:{p['mime_type']};base64,{p['data']}"}})
    return out

def _pedir_openrouter(partes, schema, rotulo, modelo, sistema, ao_cobrar, log, tentativas):
    k = chave_openrouter(); msgs = ([{"role": "system", "content": sistema}] if sistema else []) + [{"role": "user", "content": _partes_openrouter(partes)}]
    corpo = {"model": "google/" + modelo, "messages": msgs, "provider": {"require_parameters": True},
             "response_format": {"type": "json_schema", "json_schema": {"name": "resposta", "strict": True, "schema": schema}}}
    espera = 5; simples = False
    for tentativa in range(tentativas):
        if simples:                                        # o schema foi recusado: pede JSON comum com o formato no texto
            corpo["response_format"] = {"type": "json_object"}; corpo.pop("provider", None)
            corpo["messages"] = msgs[:-1] + [{"role": "user", "content": _partes_openrouter(partes) + [
                {"type": "text", "text": "Responda SÓ com um JSON neste JSON Schema (todos os campos):\n" + json.dumps(schema, ensure_ascii=False)}]}]
        req = urllib.request.Request(OR_API + "/chat/completions", data=json.dumps(corpo).encode(),
                                     headers={"Authorization": "Bearer " + k, "Content-Type": "application/json",
                                              "HTTP-Referer": "http://127.0.0.1:8790", "X-Title": "Estudio de Edicao"})
        try: r = json.loads(urllib.request.urlopen(req, timeout=600).read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:500]; det = (re.search(r'"message":\s*"([^"]+)', msg) or [None, msg[:160]])[1]
            if e.code == 401: raise ErroGemini(f"a OpenRouter recusou a chave ({det}); confira no ⚙", fatal=True)
            if e.code == 402: raise ErroGemini("a conta da OpenRouter está sem crédito (openrouter.ai/settings/credits)", fatal=True)
            if e.code == 403: raise ErroGemini(f"a OpenRouter bloqueou o pedido ({det})", fatal=True)
            if e.code in (400, 404) and not simples and re.search(r"schema|response_format|structured|require|parameter|No endpoints", msg, re.I):
                simples = True
                if log: log(f"{rotulo}: a OpenRouter não aceitou o formato estrito; pedindo JSON comum")
                continue
            if e.code in (400, 404): raise ErroGemini(f"a OpenRouter recusou o pedido ({e.code}): {det}", fatal=True)
            if e.code in (408, 429, 500, 502, 503, 504) and tentativa < tentativas - 1:
                if log: log(f"{rotulo}: a OpenRouter pediu para esperar ({e.code}); tentando de novo em {espera}s…")
                time.sleep(espera); espera *= 3; continue
            raise ErroGemini(f"a OpenRouter respondeu {e.code}: {det}")
        except (urllib.error.URLError, TimeoutError) as e:
            if tentativa < tentativas - 1: time.sleep(espera); espera *= 3; continue
            raise ErroGemini(f"sem conexão com a OpenRouter ({e})")
        if r.get("error"):
            if tentativa < tentativas - 1: time.sleep(espera); espera *= 3; continue
            raise ErroGemini(f"a OpenRouter devolveu erro: {str(r['error'])[:200]}")
        u = r.get("usage") or {}; ent, sai = u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0
        usd = u.get("cost")
        if usd is None: usd = custo(modelo, dict(total_input_tokens=ent, total_output_tokens=sai))
        if ao_cobrar: ao_cobrar(dict(rotulo=rotulo, modelo=modelo + " (OpenRouter)", entrada=ent, saida=sai, usd=float(usd)))
        t = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if isinstance(t, list): t = "".join(c.get("text", "") for c in t if isinstance(c, dict))
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip())
        try: return json.loads(t)
        except ValueError:
            if tentativa < tentativas - 1: continue
            raise ErroGemini("o Gemini (OpenRouter) devolveu uma resposta que não é JSON")
    raise ErroGemini("o Gemini (OpenRouter) não conseguiu responder")

def pedir(partes, schema, rotulo="Gemini", modelo=None, sistema=None, ao_cobrar=None, log=None, tentativas=4, motor=None):
    """Manda as partes (texto/vídeo/imagem) e devolve o JSON no formato do schema. ao_cobrar(dict) a cada resposta cobrada.
    motor: ignorado (compatibilidade com o Atlas Editor)."""
    modelo = modelo or modelo_padrao()
    if provedor() == "openrouter": return _pedir_openrouter(partes, schema, rotulo, modelo, sistema, ao_cobrar, log, tentativas)
    k = chave_google()
    if not k: raise ErroGemini("nem a chave da OpenRouter nem a do Gemini estão configuradas (⚙ Configurações)")
    corpo = {"model": modelo, "input": partes, "store": False,
             "response_format": {"type": "text", "mime_type": "application/json", "schema": schema}}
    if sistema: corpo["system_instruction"] = sistema
    dados = json.dumps(corpo).encode(); espera = 5
    for tentativa in range(tentativas):
        req = urllib.request.Request(API + "/interactions", data=dados, headers={"x-goog-api-key": k, "Content-Type": "application/json"})
        try: r = json.loads(urllib.request.urlopen(req, timeout=600).read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:400]
            if e.code in (401, 403):
                raise ErroGemini("o Google recusou o acesso (" + (re.search(r'"message":\s*"([^"]+)', msg) or [None, msg[:120]])[1]
                                 + "); confira a conta no Google AI Studio (faturamento e status do projeto)", fatal=True)
            if e.code == 400: raise ErroGemini(f"o Gemini recusou o pedido (400): {msg[:200]}", fatal=True)
            if e.code in (429, 500, 502, 503, 504) and tentativa < tentativas - 1:
                if log: log(f"{rotulo}: o Gemini pediu para esperar ({e.code}); tentando de novo em {espera}s…")
                time.sleep(espera); espera *= 3; continue
            raise ErroGemini(f"o Gemini respondeu {e.code}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if tentativa < tentativas - 1: time.sleep(espera); espera *= 3; continue
            raise ErroGemini(f"sem conexão com o Gemini ({e})")
        u = r.get("usage") or {}; usd = custo(modelo, u)
        if ao_cobrar: ao_cobrar(dict(rotulo=rotulo, modelo=modelo, entrada=u.get("total_input_tokens") or 0,
                                     saida=(u.get("total_output_tokens") or 0) + (u.get("total_thought_tokens") or 0), usd=usd))
        if r.get("status") in ("failed", "cancelled"): raise ErroGemini(f"o Gemini não terminou ({r.get('status')})")
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", _texto_da_resposta(r).strip())
        try: return json.loads(t)
        except ValueError:
            if tentativa < tentativas - 1: continue
            raise ErroGemini("o Gemini devolveu uma resposta que não é JSON")
    raise ErroGemini("o Gemini não conseguiu responder")

# ---------------------------------------------------------------- schemas (JSON Schema)
S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
def obj(**p): return {"type": "object", "properties": p, "required": list(p)}

# ---------------------------------------------------------------- utilidades
def duracao(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq], capture_output=True, text=True,
                       env=dict(os.environ, PATH=PATH))
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

SCH_OK = obj(ok=B)

def funciona(ao_cobrar=None):
    """Chamada mínima (centavos de centavo) para saber se a chave consegue gerar. Devolve (True, "") ou (False, motivo)."""
    if not chave(): return False, "sem chave da OpenRouter nem do Gemini"
    try: pedir([texto("Responda ok = true.")], SCH_OK, rotulo="teste do Gemini", ao_cobrar=ao_cobrar, tentativas=2); return True, ""
    except ErroGemini as e: return False, str(e)

def testar(qual=None):
    global FORCADO
    FORCADO = {"gemini": "google", "openrouter": "openrouter"}.get(qual)
    try: return _testar()
    finally: FORCADO = None

def _testar():
    k = chave()
    if not k: return dict(ok=False, msg="nenhuma chave salva")
    if provedor() == "openrouter":
        try:
            req = urllib.request.Request(OR_API + "/key", headers={"Authorization": "Bearer " + k})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("data") or {}
            lim = d.get("limit_remaining"); cred = f" · crédito restante US$ {lim:.2f}" if isinstance(lim, (int, float)) else ""
        except urllib.error.HTTPError as e: return dict(ok=False, msg="chave da OpenRouter inválida" if e.code in (401, 403) else f"a OpenRouter respondeu {e.code}")
        except Exception: return dict(ok=False, msg="sem conexão com a OpenRouter")
        ok, motivo = funciona()
        return dict(ok=ok, msg=f"OpenRouter ok · google/{modelo_padrao()} respondendo{cred}" if ok else motivo)
    try:
        req = urllib.request.Request(API + "/models?pageSize=200", headers={"x-goog-api-key": k})
        ids = {m["name"].split("/")[-1] for m in json.loads(urllib.request.urlopen(req, timeout=20).read()).get("models", [])}
        mod = modelo_padrao()
        if mod not in ids: return dict(ok=False, msg=f"chave válida, mas {mod} não aparece nesta conta")
        ok, motivo = funciona()
        return dict(ok=ok, msg=f"chave válida · {mod} respondendo" if ok else motivo)
    except urllib.error.HTTPError as e:
        return dict(ok=False, msg="chave inválida" if e.code in (400, 401, 403) else f"o Gemini respondeu {e.code}")
    except Exception:
        return dict(ok=False, msg="sem conexão com o Gemini")


if __name__ == "__main__":
    if sys.argv[1:2] == ["testar"]: print(json.dumps(testar(), ensure_ascii=False))
