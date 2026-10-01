"""Chaves e preferências do Ad Animado (Claude, Gemini, Grok/xAI, KIE).
Ficam só neste computador, em ~/.config/ad-animado/chaves.json (permissão 600) — separadas do Atlas Editor.
A página nunca recebe a chave inteira: só se está configurada e os 4 últimos caracteres."""
import os, json

ARQ = os.path.abspath(os.path.expanduser(os.environ.get("AD_ANIMADO_CHAVES") or os.environ.get("ESTUDIO_CHAVES") or "~/.config/ad-animado/chaves.json"))
PASTA = os.path.dirname(ARQ)
MODELOS = [dict(id="claude-opus-5", nome="Claude Opus 5 (recomendado)"),
           dict(id="claude-sonnet-5", nome="Claude Sonnet 5 (mais barato)")]
PADRAO = dict(anthropic="", gemini="", openrouter="", xai="", kie="",
              modelo="claude-opus-5", modelo_gemini="gemini-3.8-flash",
              gen_provedor="grok", gen_resolucao="720p", gen_auth="assinatura", gen_modelo_img="grok-imagine-image",
              gen_img="grok", gen_video="grok", claude_auth="assinatura", claude_token="", gemini_gratis=False)
AUTH_CLAUDE = [dict(id="assinatura", nome="Minha assinatura do Claude (Pro/Max) — usa o limite do plano, pelo Claude Code deste computador"),
               dict(id="api", nome="Chave da API da Anthropic (paga por uso)")]
def claude_ok():
    """Dá para chamar o Claude? Pela chave da API, ou pela assinatura (o login do Claude Code / token do setup-token)."""
    d = ler()
    return bool(d.get("anthropic")) or (d.get("claude_auth") == "assinatura")
GERATIVAS = [dict(id="grok", nome="Grok Imagine (xAI)", chave="xai")]
# Grok: pela assinatura (o login do `grok` no terminal, ~/.grok/auth.json — cota semanal, sem cobrança por uso)
# ou por uma chave de API da xAI (cobrada por imagem/segundo de vídeo).
AUTH_GERATIVA = [dict(id="assinatura", nome="Assinatura do Grok (o mesmo login do terminal · usa a cota semanal)"),
                 dict(id="api", nome="Chave de API da xAI (cobra por uso)")]
MODELOS_IMG = [dict(id="grok-imagine-image", nome="Grok Imagine (padrão, mais rápido)"),
               dict(id="grok-imagine-image-quality", nome="Grok Imagine Quality (melhor qualidade)")]
def assinatura_grok(): return os.path.expanduser("~/.grok/auth.json")
RESOLUCOES = [dict(id="480p", nome="480p (mais barato, para testar)"), dict(id="720p", nome="720p (recomendado)"),
              dict(id="1080p", nome="1080p (mais caro)")]

def ler():
    d = dict(PADRAO)
    if os.path.exists(ARQ):
        try: d.update(json.load(open(ARQ)))
        except ValueError: pass
    return d

def gravar(**novos):
    d = ler()
    for k, v in novos.items():
        if v is None: continue
        d[k] = v.strip() if isinstance(v, str) else v
    os.makedirs(PASTA, mode=0o700, exist_ok=True); os.chmod(PASTA, 0o700)
    tmp = ARQ + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=1)
    os.replace(tmp, ARQ); os.chmod(ARQ, 0o600)
    return d

def publico():
    """O que a página pode ver."""
    import gemini, gerativa
    d = ler(); final = lambda k: ("…" + k[-4:]) if len(k or "") > 8 else ""
    return dict(anthropic=dict(ok=bool(d["anthropic"]), final=final(d["anthropic"])),
                claude_auth=d.get("claude_auth") or "assinatura", auths_claude=AUTH_CLAUDE, gemini_gratis=bool(d.get("gemini_gratis")),
                claude_token=dict(ok=bool(d.get("claude_token")), final=final(d.get("claude_token", ""))),
                gemini=dict(ok=bool(d.get("gemini")), final=final(d.get("gemini", ""))),
                openrouter=dict(ok=bool(d.get("openrouter")), final=final(d.get("openrouter", ""))),
                xai=dict(ok=bool(d.get("xai")), final=final(d.get("xai", ""))),
                gen_resolucao=d.get("gen_resolucao") or PADRAO["gen_resolucao"], resolucoes=RESOLUCOES,
                gen_auth=d.get("gen_auth") or PADRAO["gen_auth"], auths=AUTH_GERATIVA, assinatura_ok=os.path.exists(assinatura_grok()),
                gen_modelo_img=d.get("gen_modelo_img") or PADRAO["gen_modelo_img"], modelos_img=MODELOS_IMG,
                kie=dict(ok=bool(gerativa.chave_kie()), final=final(gerativa.chave_kie()), detectada=bool(gerativa.chave_kie()) and not d.get("kie")),
                gen_img=d.get("gen_img") or "grok", gen_video=d.get("gen_video") or "grok",
                modelos_imagem=gerativa.MODELOS_IMAGEM, modelos_video=gerativa.MODELOS_VIDEO,
                modelo=d.get("modelo") or PADRAO["modelo"], modelos=MODELOS,
                modelo_gemini=d.get("modelo_gemini") or PADRAO["modelo_gemini"], modelos_gemini=gemini.MODELOS)
