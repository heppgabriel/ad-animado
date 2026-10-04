"""Ad Animado — servidor local. Abra http://localhost:4124
Copy + áudio/SRT + referências -> storyboard (Claude) -> imagens por beat -> vídeos -> timeline montada.
Os ads ficam em ~/Ads Animados/<nome>/ ; as chaves em ~/.config/ad-animado/chaves.json (fora de tudo que é servido).
O trabalho pesado roda em lib/animado.py, num processo separado: reiniciar o servidor não interrompe uma geração."""
import os, re, sys, json, math, time, glob, shutil, signal, secrets, errno, threading, subprocess, mimetypes, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

if os.name == "nt" and not sys.flags.utf8_mode:           # Windows: tudo em UTF-8 (acentos dos nomes, JSON, SRT)
    sys.exit(subprocess.call([sys.executable, "-X", "utf8"] + sys.argv))
APP = os.path.dirname(os.path.abspath(__file__)); RAIZ_APP = os.path.dirname(APP); LIB = os.path.join(RAIZ_APP, "lib")
sys.path.insert(0, LIB)
import comum, chaves, custos, gemini, gerativa
PORTA = comum.PORTA
HOST = os.environ.get("AD_ANIMADO_HOST", "127.0.0.1")
VENV_PY = sys.executable
MAX_UPLOAD = int(os.environ.get("AD_ANIMADO_MAX_UPLOAD", str(8 * 1024**3)))
EXT_VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
EXT_SERVIDOS = EXT_VIDEO + (".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".srt", ".json")
mimetypes.add_type("video/mp4", ".mp4"); mimetypes.add_type("image/jpeg", ".jpg"); mimetypes.add_type("image/png", ".png"); mimetypes.add_type("image/webp", ".webp"); mimetypes.add_type("video/quicktime", ".mov"); mimetypes.add_type("application/x-subrip", ".srt")

def entrada(): return os.path.join(comum.RAIZ, ".entrada")   # arquivos enviados antes do ad ser criado

def dentro(p, raiz):
    return os.path.realpath(p).startswith(os.path.realpath(raiz).rstrip(os.sep) + os.sep)

def permitido(p):
    if not isinstance(p, str) or not p or "\x00" in p: return False
    if os.path.realpath(p) == os.path.realpath(chaves.ARQ): return False
    return os.path.splitext(p)[1].lower() in EXT_SERVIDOS and dentro(p, comum.RAIZ)

def vivo(pid): return comum.vivo(pid)

def matar_processo(pid):
    if os.name == "nt": subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else: os.killpg(os.getpgid(pid), signal.SIGTERM)

def slug_nome(nome):
    slug = re.sub(r"[/\\:\x00]", "-", nome).strip().strip(".")
    if not slug or slug.startswith("."): raise ValueError("nome inválido")
    return slug

class ErroEnvio(ValueError):
    def __init__(self, msg, status): super().__init__(msg); self.status = status

def tamanho_corpo(handler, limite=None):
    limite = MAX_UPLOAD if limite is None else limite
    if handler.headers.get("Transfer-Encoding"): raise ValueError("envie o arquivo com Content-Length")
    bruto = handler.headers.get("Content-Length", "0")
    if not re.fullmatch(r"\d{1,12}", bruto): raise ValueError("tamanho do envio inválido")
    n = int(bruto)
    if n > limite: raise ErroEnvio(f"o arquivo passa do limite de {limite / 1024**3:g} GB por envio", 413)
    return n

def receber(handler, dest):
    """Grava o corpo direto no disco em pedaços de 1 MB (vídeo de referência pode ser grande), com troca atômica."""
    falta = tamanho_corpo(handler)
    if not falta: raise ValueError("o arquivo está vazio")
    tmp = dest + "." + secrets.token_hex(8) + ".part"
    try:
        if shutil.disk_usage(os.path.dirname(os.path.abspath(dest))).free < falta + 64 * 1024**2: raise ErroEnvio("sem espaço no disco para receber este arquivo", 507)
        with open(tmp, "xb") as out:
            while falta:
                bloco = handler.rfile.read(min(1 << 20, falta))
                if not bloco: raise ValueError("o envio foi interrompido, tente de novo")
                out.write(bloco); falta -= len(bloco)
        os.replace(tmp, dest)
    except OSError as e:
        if e.errno in (errno.ENOSPC, errno.EDQUOT): raise ErroEnvio("o disco encheu durante o envio", 507) from None
        raise
    finally:
        if os.path.exists(tmp): os.remove(tmp)

# ---------------------------------------------------------------- Ad Animado (lib/animado.py, app/animado.html)
EXT_IMG_REF = (".jpg", ".jpeg", ".png", ".webp")
EXT_AUDIO = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac") + EXT_VIDEO

def animados(): return comum.RAIZ

def pasta_animado(slug):
    if not isinstance(slug, str) or not slug or slug.startswith(".") or any(c in slug for c in ("/", "\\", "\x00")): raise ValueError("projeto inválido")
    p = os.path.realpath(os.path.join(animados(), slug))
    if not dentro(p, animados()) or not os.path.exists(os.path.join(p, "pedido.json")): raise ValueError("projeto inválido")
    return p

def _json_ou(arq, padrao):
    for k in range(5):                                   # no Windows o arquivo pode estar sendo trocado neste instante
        try: return json.load(open(arq, encoding="utf-8"))
        except PermissionError: time.sleep(0.05)
        except (OSError, ValueError): return padrao
    return padrao

def estado_animado_bruto(p):
    st = _json_ou(os.path.join(p, "estado.json"), dict(rodando=False, status="novo", etapas=[], log=[]))
    if st.get("rodando") and not vivo(st.get("pid")):
        st.update(rodando=False, status="erro", mensagem=st.get("mensagem") or "o processo parou no meio (dá para retomar)")
    return st

def lista_animados():
    out = []
    for e in glob.glob(os.path.join(animados(), "*", "pedido.json")):
        p = os.path.dirname(e); ped = _json_ou(e, {}); st = estado_animado_bruto(p); B = _json_ou(os.path.join(p, "storyboard.json"), {})
        capa = next((os.path.join(p, b["img"]) for b in B.get("beats", []) if b.get("img")), None)
        out.append(dict(id=os.path.basename(p), nome=ped.get("nome"), modo=ped.get("modo"), criado=ped.get("criado", ""),
                        beats=len(B.get("beats", [])), capa=capa, final=os.path.join(p, "final.mp4") if os.path.exists(os.path.join(p, "final.mp4")) else None,
                        status=st.get("status"), rodando=bool(st.get("rodando")), mensagem=st.get("mensagem", ""), custo=custos.resumo(p)))
    return sorted(out, key=lambda x: x["criado"], reverse=True)

def estado_animado(slug):
    p = pasta_animado(slug); ped = _json_ou(os.path.join(p, "pedido.json"), {}); st = estado_animado_bruto(p)
    st["log"] = st.get("log", [])[-80:]
    B = _json_ou(os.path.join(p, "storyboard.json"), dict(beats=[]))
    ab = lambda r: os.path.join(p, r) if r else None
    for b in B.get("beats", []):
        b["img_abs"] = ab(b.get("img")); b["video_abs"] = ab(b.get("video")); b["frame_abs"] = ab(b.get("frame_inicial")); b["final_abs"] = ab(b.get("img_final")); b["historico_abs"] = [ab(h) for h in b.get("historico") or []]
    refs = [dict(r, abs=ab(r["arquivo"])) for r in ped.get("refs", [])]
    R = B.get("referencia")
    if R:
        for q in R.get("quadros", []): q["abs"] = ab(q["arquivo"])
        for c in R.get("cenas", []): c["quadro_abs"] = ab(c.get("quadro"))
    C = custos.ler(p)
    return dict(id=slug, pasta=p, pedido=dict(ped, refs=refs, audio_abs=ab(ped.get("audio")), referencia_abs=ab(ped.get("referencia_video"))), estado=st, storyboard=B,
                assinatura=(chaves.ler().get("gen_auth") or "assinatura") == "assinatura",
                modelos_imagem=gerativa.MODELOS_IMAGEM, modelos_video=gerativa.MODELOS_VIDEO,
                modelo_img=ped.get("modelo_img") or gerativa.padrao_img(), modelo_video=ped.get("modelo_video") or gerativa.padrao_video(),
                final=ab("final.mp4") if os.path.exists(os.path.join(p, "final.mp4")) else None,
                srt=ab("legendas_final.srt") if os.path.exists(os.path.join(p, "legendas_final.srt")) else ab("legendas.srt") if os.path.exists(os.path.join(p, "legendas.srt")) else None,
                timeline=_json_ou(os.path.join(p, "timeline.json"), None),
                custo=dict(custos.resumo(p), grok=round(sum(i["usd"] for i in C["itens"] if i["servico"] in ("grok", "kie")), 4)),
                estimativa_video=round(gerativa.custo_estimado_video(sum(max(2, min(15, math.ceil((b.get("fim", 0) - b.get("ini", 0)) + 0.4))) for b in B.get("beats", []))), 2))

def receber_animado(handler, token, tipo, n, nome):
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    ext = os.path.splitext(nome)[1].lower(); pasta = os.path.join(entrada(), token); os.makedirs(pasta, exist_ok=True)
    if tipo == "ref":
        if ext not in EXT_IMG_REF or not re.fullmatch(r"\d{1,2}", n or ""): raise ValueError("a referência precisa ser .jpg, .png ou .webp")
        base = f"ref_{int(n):02d}"
    elif tipo == "audio":
        if ext not in EXT_AUDIO: raise ValueError("o áudio precisa ser .mp3, .wav, .m4a ou um vídeo (.mp4/.mov)")
        base = "audio"
    elif tipo == "srt":
        if ext != ".srt": raise ValueError("o SRT precisa ser .srt")
        base = "legendas"
    elif tipo == "refvideo":
        if ext not in EXT_VIDEO: raise ValueError("o vídeo de referência precisa ser .mp4, .mov ou .m4v")
        base = "referencia"
    else: raise ValueError("tipo inválido")
    dest = os.path.join(pasta, base + ext); receber(handler, dest)
    for velho in glob.glob(os.path.join(pasta, base + ".*")):
        if velho != dest and not velho.endswith(".part"): os.remove(velho)
    return dest

def lancar_animado(p, acao, nums=None):
    st = estado_animado_bruto(p)
    if st.get("rodando"): raise ValueError("este ad animado já está rodando")
    cmd = [VENV_PY, os.environ.get("AD_ANIMADO_SCRIPT") or os.path.join(LIB, "animado.py"), p, acao] + ([",".join(str(int(x)) for x in nums)] if nums else [])
    env = dict({k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}, PYTHONUTF8="1")
    extra = dict(creationflags=0x00000200 | 0x08000000) if comum.WINDOWS else dict(start_new_session=True)   # Windows: grupo próprio, sem janela
    pr = subprocess.Popen(cmd, stdout=open(os.path.join(p, "saida.log"), "a", encoding="utf-8"), stderr=subprocess.STDOUT, env=env, cwd=p, **extra)
    for _ in range(50):
        s = estado_animado_bruto(p)
        if s.get("pid") == pr.pid and s.get("rodando"): break
        time.sleep(0.1)
    return pr.pid

def exigir_chaves_animado(ped=None):
    c = chaves.ler(); ped = ped or {}
    if ped.get("custo", "livre") == "livre" and not chaves.claude_ok(): raise ValueError("configure o Claude primeiro (⚙ › IA & modelos: chave da API ou a sua assinatura)")
    usa = {ped.get("modelo_img") or gerativa.padrao_img(), ped.get("modelo_video") or gerativa.padrao_video()}
    if any(m.startswith("kie:") for m in usa) and not gerativa.chave_kie():
        raise ValueError("não achei a chave da KIE: cole em ⚙ Configurações › Integrações")
    if "grok" not in usa: return
    if (c.get("gen_auth") or "assinatura") == "api":
        if not c.get("xai"): raise ValueError("configure a chave da API da xAI em ⚙ Configurações › Integrações (ou troque para a assinatura do Grok)")
    elif not os.path.exists(chaves.assinatura_grok()):
        raise ValueError("não achei o login do Grok neste Mac: abra o Terminal, rode `grok` e entre com a sua conta (ou use uma chave de API em ⚙)")

def ritmo_valido(r):
    """Segundos por clipe {min, max} escolhidos na tela (ex.: 2 a 3 s num ad dinâmico)."""
    try: mn, mx = float((r or {}).get("min", 2)), float((r or {}).get("max", 3))
    except (TypeError, ValueError, AttributeError): raise ValueError("ritmo inválido")
    if not (0.6 <= mn <= 12 and mn < mx <= 15): raise ValueError("o ritmo precisa ter mínimo entre 0,6 e 12 s e máximo maior que o mínimo (até 15 s)")
    return dict(min=round(mn, 2), max=round(mx, 2))

def criar_animado(d):
    token = d.get("id", ""); nome = str(d.get("nome", "")).strip()
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    if not nome: raise ValueError("dê um nome ao ad")
    ritmo = ritmo_valido(d.get("ritmo"))
    custo = d.get("custo") if d.get("custo") in ("zero", "gemini", "livre") else "zero"
    m_img = d.get("modelo_img") if gerativa.valido(d.get("modelo_img"), "img") else gerativa.padrao_img()
    m_vid = d.get("modelo_video") if gerativa.valido(d.get("modelo_video"), "video") else gerativa.padrao_video()
    exigir_chaves_animado(dict(modelo_img=m_img, modelo_video=m_vid, custo=custo))
    modo = "nativo" if d.get("modo") == "nativo" else "vo"
    ent = os.path.join(entrada(), token)
    refs_env = sorted(glob.glob(os.path.join(ent, "ref_*")))
    if not refs_env: raise ValueError("envie pelo menos uma imagem de referência (o recomendado são duas ou mais)")
    audio = (glob.glob(os.path.join(ent, "audio.*")) or [None])[0]; srt = (glob.glob(os.path.join(ent, "legendas.srt")) or [None])[0]
    refvideo = (glob.glob(os.path.join(ent, "referencia.*")) or [None])[0]
    ref_nivel = str(d.get("ref_nivel") or "auto")
    if ref_nivel not in ("auto", "1", "2", "3", "4", "5"): raise ValueError("nível de referência inválido")
    roteiro = str(d.get("roteiro") or "").strip()
    if modo == "vo" and not audio: raise ValueError("envie o áudio da locução (ou o vídeo com o áudio)")
    if modo == "nativo" and not gerativa.tem_audio_nativo(m_vid):
        raise ValueError(f"{gerativa.nome_modelo(m_vid)} não gera áudio; na fala nativa escolha um modelo de vídeo com áudio (Grok, Kling 2.6, Seedance ou Veo)")
    if modo == "nativo" and not (srt or roteiro or audio): raise ValueError("para fala nativa, mande o roteiro, um SRT ou um áudio de referência")
    p = os.path.join(animados(), slug_nome(nome))
    if os.path.exists(p): raise ValueError(f"já existe um ad animado chamado '{nome}'")
    os.makedirs(os.path.join(p, "refs")); os.makedirs(os.path.join(p, "fonte"))
    info = {int(r.get("n", 0)): r for r in d.get("refs") or []}; refs = []
    for arq in refs_env:
        k = int(re.search(r"ref_(\d+)", arq).group(1)); dest = os.path.join("refs", os.path.basename(arq)); shutil.move(arq, os.path.join(p, dest))
        r = info.get(k, {}); tipo = r.get("tipo") if r.get("tipo") in ("personagem", "produto", "estilo") else "personagem"
        refs.append(dict(arquivo=dest, tipo=tipo, nota=str(r.get("nota") or "").strip()[:200]))
    ped = dict(nome=nome, modo=modo, refs=refs, audio=None, srt=None, roteiro=roteiro[:20000], estilo=str(d.get("estilo") or "").strip()[:2000],
               ritmo=ritmo, modelo_img=m_img, modelo_video=m_vid, referencia_video=None, ref_nivel=ref_nivel, custo=custo, montagem="continuo" if d.get("montagem") == "continuo" else "cortes", criado=time.strftime("%Y-%m-%d %H:%M:%S"))
    if audio: ped["audio"] = os.path.join("fonte", "audio" + os.path.splitext(audio)[1].lower()); shutil.move(audio, os.path.join(p, ped["audio"]))
    if srt: ped["srt"] = os.path.join("fonte", "legendas.srt"); shutil.move(srt, os.path.join(p, ped["srt"]))
    if refvideo:
        os.makedirs(os.path.join(p, "referencia"), exist_ok=True)
        ped["referencia_video"] = os.path.join("referencia", "video" + os.path.splitext(refvideo)[1].lower()); shutil.move(refvideo, os.path.join(p, ped["referencia_video"]))
    shutil.rmtree(ent, ignore_errors=True)
    json.dump(ped, open(os.path.join(p, "pedido.json"), "w"), ensure_ascii=False, indent=1)
    lancar_animado(p, "imagens"); return os.path.basename(p)

def acao_animado(slug, d):
    p = pasta_animado(slug); acao = d.get("acao"); st = estado_animado_bruto(p)
    if acao == "cancelar":
        if not st.get("rodando"): raise ValueError("não tem nada rodando")
        try: matar_processo(int(st["pid"]))
        except (ProcessLookupError, PermissionError, KeyError, ValueError): pass
        return dict(ok=True)
    if st.get("rodando"): raise ValueError("espere a etapa atual terminar (ou cancele)")
    arq_b = os.path.join(p, "storyboard.json"); B = _json_ou(arq_b, dict(beats=[])); por_n = {b["n"]: b for b in B.get("beats", [])}
    def grava():
        tmp = arq_b + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(B, f, ensure_ascii=False, indent=1)
        comum.trocar(tmp, arq_b)
    nums = sorted({int(n) for n in d.get("nums") or [] if int(n) in por_n})
    prompts = {int(k): str(v).strip() for k, v in (d.get("prompts") or {}).items() if str(v).strip() and int(k) in por_n}
    if acao == "apagar":
        shutil.rmtree(p); return dict(ok=True)
    if acao == "retomar":
        fase = "videos" if B.get("aprovado") else "imagens"
        exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, fase); return dict(ok=True)
    finais = {int(k): str(v).strip() for k, v in (d.get("finais") or {}).items() if int(k) in por_n and por_n[int(k)].get("quadro_final")}
    if acao == "editar":
        for n, t in prompts.items(): por_n[n]["prompt"] = t; por_n[n]["prompt_editado"] = True
        for n, t in finais.items():
            if t: por_n[n]["quadro_final"] = t; por_n[n]["final_editado"] = True
        grava(); return dict(ok=True)
    if acao == "refazer":
        for n, t in finais.items():                        # quadros finais (ad contínuo) a refazer
            por_n[n]["img_final_estado"] = "refazer"
            if t: por_n[n]["quadro_final"] = t; por_n[n]["final_editado"] = True
        if not nums and not finais: nums = [b["n"] for b in B["beats"] if b.get("img_estado") in ("refazer", "erro")]
        tem_finais = finais or any(b.get("quadro_final") and b.get("img_final_estado") in ("refazer", "erro") for b in B["beats"])
        if not nums and not tem_finais: raise ValueError("nenhuma imagem marcada para refazer")
        for n in nums:
            por_n[n]["img_estado"] = "refazer"
            if n in prompts: por_n[n]["prompt"] = prompts[n]; por_n[n]["prompt_editado"] = True
        B["aprovado"] = False; grava(); exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, "refazer", nums or None); return dict(ok=True)
    if acao == "validar":
        exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, "validar"); return dict(ok=True)
    if acao == "aprovar":
        ped_ = _json_ou(os.path.join(p, "pedido.json"), {})
        faltam = [b["n"] for b in (B.get("beats", [])[:1] if ped_.get("montagem") == "continuo" else B.get("beats", [])) if not b.get("img")] + \
                 [b["n"] for b in B.get("beats", []) if b.get("quadro_final") and not b.get("img_final")]
        if not B.get("beats") or faltam: raise ValueError(f"ainda faltam imagens: {faltam}")
        B["aprovado"] = True; grava(); exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, "videos"); return dict(ok=True)
    if acao == "refazer_video":
        if not B.get("aprovado"): raise ValueError("aprove as imagens primeiro")
        if not nums: raise ValueError("escolha quais vídeos refazer")
        if _json_ou(os.path.join(p, "pedido.json"), {}).get("montagem") == "continuo":   # os seguintes nascem deste
            nums = [b["n"] for b in B["beats"] if b["n"] >= min(nums)]
        for n in nums:
            if n in prompts: por_n[n]["anim"] = prompts[n]
            por_n[n]["video_estado"] = "pendente"
        grava(); exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, "refazer_video", nums); return dict(ok=True)
    if acao == "montar":
        lancar_animado(p, "montar"); return dict(ok=True)
    if acao == "custo":
        if d.get("custo") not in ("zero", "gemini", "livre"): raise ValueError("opção inválida")
        ped_arq = os.path.join(p, "pedido.json"); ped = _json_ou(ped_arq, {}); ped["custo"] = d["custo"]
        json.dump(ped, open(ped_arq, "w"), ensure_ascii=False, indent=1); return dict(ok=True)
    if acao == "modelos":
        ped_arq = os.path.join(p, "pedido.json"); ped = _json_ou(ped_arq, {})
        if d.get("modelo_img"):
            if not gerativa.valido(d["modelo_img"], "img"): raise ValueError("IA de imagem inválida")
            ped["modelo_img"] = d["modelo_img"]
        if d.get("modelo_video"):
            if not gerativa.valido(d["modelo_video"], "video"): raise ValueError("IA de vídeo inválida")
            if ped.get("modo") == "nativo" and not gerativa.tem_audio_nativo(d["modelo_video"]):
                raise ValueError("esse modelo de vídeo não gera áudio; na fala nativa escolha um com áudio (Grok, Kling 2.6, Seedance ou Veo)")
            ped["modelo_video"] = d["modelo_video"]
        json.dump(ped, open(ped_arq, "w"), ensure_ascii=False, indent=1); return dict(ok=True)
    if acao in ("ritmo", "nivel_ref"):
        ped_arq = os.path.join(p, "pedido.json"); ped = _json_ou(ped_arq, {})
        if acao == "ritmo": ped["ritmo"] = ritmo_valido(d.get("ritmo"))
        else:
            if str(d.get("ref_nivel")) not in ("auto", "1", "2", "3", "4", "5"): raise ValueError("nível inválido")
            if not ped.get("referencia_video"): raise ValueError("este ad não tem vídeo de referência")
            ped["ref_nivel"] = str(d["ref_nivel"])
        json.dump(ped, open(ped_arq, "w"), ensure_ascii=False, indent=1)
        for x in ("storyboard.json", "estado.json", "legendas.srt", "timeline.json", "final.mp4", "legendas_final.srt"):
            try: os.remove(os.path.join(p, x))
            except OSError: pass
        exigir_chaves_animado(_json_ou(os.path.join(p, "pedido.json"), {})); lancar_animado(p, "imagens"); return dict(ok=True)
    if acao == "voltar":
        B["aprovado"] = False; grava(); return dict(ok=True)
    raise ValueError("ação inválida")

# ---------------------------------------------------------------- UGC ultra-realista (lib/ugc.py, app/ugc.html)
import ugc as UGC
EXT_IMG = (".jpg", ".jpeg", ".png", ".webp")

def ugcs(): return UGC.raiz()

def pasta_ugc(slug):
    if not isinstance(slug, str) or not slug or slug.startswith(".") or any(c in slug for c in ("/", "\\", "\x00")): raise ValueError("projeto inválido")
    p = os.path.realpath(os.path.join(ugcs(), slug))
    if not dentro(p, ugcs()) or not os.path.exists(os.path.join(p, "ugc.json")): raise ValueError("projeto inválido")
    return p

def estado_ugc_bruto(p):
    st = _json_ou(os.path.join(p, "estado.json"), dict(rodando=False, status="novo", etapas=[], log=[]))
    if st.get("rodando") and not vivo(st.get("pid")):
        st.update(rodando=False, status="erro", mensagem=st.get("mensagem") or "o processo parou no meio (dá para retomar)")
    return st

def lista_ugc():
    out = []
    for e in glob.glob(os.path.join(ugcs(), "*", "ugc.json")):
        p = os.path.dirname(e); ped = _json_ou(e, {}); st = estado_ugc_bruto(p); P = _json_ou(os.path.join(p, "plano.json"), {})
        capa = next((UGC.imagem_da_cena(p, ped, UGC.Plano(p), c["id"]) for c in ped.get("cenas", [])[:1]), None)
        out.append(dict(id=os.path.basename(p), nome=ped.get("nome"), criado=ped.get("criado", ""), clipes=len(P.get("clipes", [])),
                        prontos=sum(1 for c in P.get("clipes", []) if c.get("video_estado") == "ok"), aprovados=sum(1 for c in P.get("clipes", []) if c.get("aprovado")),
                        capa=capa, status=st.get("status"), rodando=bool(st.get("rodando")), custo=custos.resumo(p)))
    return sorted(out, key=lambda x: x["criado"], reverse=True)

def estado_ugc(slug):
    p = pasta_ugc(slug); ped = _json_ou(os.path.join(p, "ugc.json"), {}); st = estado_ugc_bruto(p); st["log"] = st.get("log", [])[-80:]
    P = _json_ou(os.path.join(p, "plano.json"), {}); ab = lambda r: os.path.join(p, r) if r else None
    pl = UGC.Plano(p)
    cenas = []
    for c in ped.get("cenas", []):
        x = P.get("cenas", {}).get(c["id"], {})
        cenas.append(dict(c, original_abs=ab(c["arquivo"]), final_abs=ab(x.get("img_final")), usada_abs=UGC.imagem_da_cena(p, ped, pl, c["id"]),
                          swap_estado=x.get("swap_estado"), swap_erro=x.get("swap_erro"), swap_prompt=x.get("swap_prompt", ""),
                          swap_hist_abs=[ab(h) for h in x.get("swap_hist") or []], avisos=(x.get("avisos") or []) + (x.get("avisos_troca") or []),
                          lida=bool(x.get("desc"))))
    for c in P.get("clipes", []) + P.get("broll", []):
        c["video_abs"] = ab(c.get("video")); c["img_abs"] = ab(c.get("img")); c["historico_abs"] = [ab(h) for h in c.get("historico") or []]
    ref = P.get("referencia")
    if ref:
        for q in ref.get("quadros", []): q["abs"] = ab(q["arquivo"])
    return dict(id=slug, pasta=p, pedido=dict(ped, avatar_abs=ab(ped.get("avatar")), produto_abs=ab(ped.get("produto")), referencia_abs=ab(ped.get("referencia"))),
                cenas=cenas, plano=P, estado=st, juntos=ab("juntos.mp4") if os.path.exists(os.path.join(p, "juntos.mp4")) else None,
                custo=custos.resumo(p), omni_provedores=gerativa.OMNI_PROVEDORES, modelos_imagem=gerativa.MODELOS_IMAGEM,
                modelos_video=[m for m in gerativa.MODELOS_VIDEO if m["id"] != "kie:google/gemini-omni-flash-1-1"])

def receber_ugc(handler, token, tipo, n, nome, proj=None):
    """Envio antes de criar (token) ou para um UGC que já existe (proj): cena (n), avatar, produto, refvideo."""
    ext = os.path.splitext(nome)[1].lower()
    if proj: pasta = pasta_ugc(proj)
    else:
        if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
        pasta = os.path.join(entrada(), "ugc-" + token)
    os.makedirs(pasta, exist_ok=True)
    if tipo in ("cena", "avatar", "produto"):
        if ext not in EXT_IMG: raise ValueError("a imagem precisa ser .jpg, .png ou .webp")
        if tipo == "cena":
            if not re.fullmatch(r"\d{1,2}", n or ""): raise ValueError("cena inválida")
            base = f"cena_{int(n):02d}" if not proj else os.path.join("cenas", f"c{int(n)}-{int(time.time()) % 10**6}")
        else: base = tipo if not proj else os.path.join("fonte", f"{tipo}-{int(time.time()) % 10**6}")
    elif tipo == "refvideo":
        if ext not in EXT_VIDEO: raise ValueError("o vídeo de referência precisa ser .mp4, .mov ou .m4v")
        base = "referencia" if not proj else os.path.join("referencia", f"video-{int(time.time()) % 10**6}")
    else: raise ValueError("tipo inválido")
    os.makedirs(os.path.dirname(os.path.join(pasta, base + ext)), exist_ok=True)
    dest = os.path.join(pasta, base + ext); receber(handler, dest)
    if not proj:
        for velho in glob.glob(os.path.join(pasta, base + ".*")):
            if velho != dest and not velho.endswith(".part"): os.remove(velho)
    return os.path.relpath(dest, pasta)

def lancar_ugc(p, acao, alvo=None):
    if estado_ugc_bruto(p).get("rodando"): raise ValueError("este UGC já está rodando — espere ou cancele")
    cmd = [VENV_PY, os.environ.get("AD_UGC_SCRIPT") or os.path.join(LIB, "ugc.py"), p, acao] + ([",".join(str(x) for x in alvo)] if alvo else [])
    env = dict({k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}, PYTHONUTF8="1")
    extra = dict(creationflags=0x00000200 | 0x08000000) if comum.WINDOWS else dict(start_new_session=True)
    pr = subprocess.Popen(cmd, stdout=open(os.path.join(p, "saida.log"), "a", encoding="utf-8"), stderr=subprocess.STDOUT, env=env, cwd=p, **extra)
    for _ in range(50):
        s = estado_ugc_bruto(p)
        if s.get("pid") == pr.pid and s.get("rodando"): break
        time.sleep(0.1)
    return pr.pid

def exigir_ugc(ped, videos=False):
    if not chaves.claude_ok(): raise ValueError("configure o Claude primeiro (⚙ Configurações)")
    precisa_kie = (any(c.get("trocar") for c in ped.get("cenas", [])) and (ped.get("modelo_img") or "kie:").startswith("kie:")) \
        or (videos and (ped.get("omni") or "kie") == "kie") or (videos and ped.get("broll"))
    if precisa_kie and not gerativa.chave_kie(): raise ValueError("cole a chave da KIE em ⚙ Configurações")
    if videos and ped.get("omni") == "google" and not chaves.ler().get("gemini"):
        raise ValueError("para o Omni pelo Google, cole a chave do Google AI Studio em ⚙ Configurações › Gemini")

def criar_ugc(d):
    token = d.get("id", ""); nome = str(d.get("nome", "")).strip()
    if not re.fullmatch(r"[a-z0-9]{8,40}", token): raise ValueError("envio inválido")
    if not nome: raise ValueError("dê um nome ao UGC")
    roteiro = str(d.get("roteiro") or "").strip()
    if not roteiro: raise ValueError("cole o roteiro (a fala do avatar)")
    ent = os.path.join(entrada(), "ugc-" + token); env_cenas = sorted(glob.glob(os.path.join(ent, "cena_*")))
    if not env_cenas: raise ValueError("envie pelo menos uma imagem inicial da cena")
    info = {int(c.get("n", 0)): c for c in d.get("cenas") or []}
    algum_troca = any(bool(info.get(int(re.search(r"cena_(\d+)", a).group(1)), {}).get("trocar")) for a in env_cenas)
    avatar = (glob.glob(os.path.join(ent, "avatar.*")) or [None])[0]
    if algum_troca and not avatar and not str(d.get("rosto_novo") or "").strip():
        raise ValueError("para montar o avatar nas cenas, envie a foto da avatar ou descreva o rosto novo")
    m_img = d.get("modelo_img") if gerativa.valido(d.get("modelo_img"), "img") else "kie:nano-banana-pro"
    m_broll = d.get("modelo_broll") if gerativa.valido(d.get("modelo_broll"), "video") else "kie:kling/v3-turbo-image-to-video"
    ped = dict(nome=nome, roteiro=roteiro[:30000], personagem=dict(nome=re.sub(r"[^\wÀ-ÿ -]", "", str((d.get("personagem") or {}).get("nome") or "ANA"))[:30] or "ANA",
               voz=str((d.get("personagem") or {}).get("voz") or "").strip()[:400]),
               taxa="rapido" if d.get("taxa") == "rapido" else "calmo", omni="google" if d.get("omni") == "google" else "kie",
               resolucao=d.get("resolucao") if d.get("resolucao") in ("360p", "720p", "1080p", "4k") else "720p",
               broll=bool(d.get("broll")), modelo_img=m_img, modelo_broll=m_broll, rosto_novo=str(d.get("rosto_novo") or "").strip()[:600],
               pedido_avatar=str(d.get("pedido_avatar") or "").strip()[:600], marca=str(d.get("marca") or "").strip()[:80],
               obs=str(d.get("obs") or "").strip()[:2000], avatar=None, produto=None, referencia=None, cenas=[], criado=time.strftime("%Y-%m-%d %H:%M:%S"))
    exigir_ugc(dict(ped, cenas=[dict(trocar=algum_troca)]))
    p = os.path.join(ugcs(), slug_nome(nome))
    if os.path.exists(p): raise ValueError(f"já existe um UGC chamado '{nome}'")
    for sub in ("cenas", "fonte", "referencia"): os.makedirs(os.path.join(p, sub))
    for a in env_cenas:
        k = int(re.search(r"cena_(\d+)", a).group(1)); dest = os.path.join("cenas", f"c{k}" + os.path.splitext(a)[1].lower()); shutil.move(a, os.path.join(p, dest))
        c = info.get(k, {}); modo = c.get("modo") if c.get("modo") in ("rosto + cabelo", "só rosto", "pessoa inteira") else "rosto + cabelo"
        ped["cenas"].append(dict(id=f"c{k}", arquivo=dest, trocar=bool(c.get("trocar")), modo=modo,
                                 pedido=str(c.get("pedido") or "").strip()[:400], nota=str(c.get("nota") or "").strip()[:300]))
    for tipo, pasta in (("avatar", "fonte"), ("produto", "fonte"), ("referencia", "referencia")):
        a = (glob.glob(os.path.join(ent, tipo + ".*")) or [None])[0]
        if a: ped[tipo] = os.path.join(pasta, tipo + os.path.splitext(a)[1].lower()); shutil.move(a, os.path.join(p, ped[tipo]))
    shutil.rmtree(ent, ignore_errors=True)
    UGC.grava_json(os.path.join(p, "ugc.json"), ped)
    lancar_ugc(p, "preparar"); return os.path.basename(p)

def acao_ugc(slug, d):
    p = pasta_ugc(slug); acao = d.get("acao"); st = estado_ugc_bruto(p)
    if acao == "cancelar":
        if not st.get("rodando"): raise ValueError("não tem nada rodando")
        try: matar_processo(int(st["pid"]))
        except (ProcessLookupError, PermissionError, KeyError, ValueError, OSError): pass
        return dict(ok=True)
    if st.get("rodando"): raise ValueError("espere a etapa atual terminar (ou cancele)")
    arq_p = os.path.join(p, "ugc.json"); ped = _json_ou(arq_p, {}); pl = UGC.Plano(p)
    if acao == "apagar": shutil.rmtree(p); return dict(ok=True)
    if acao == "retomar": exigir_ugc(ped); lancar_ugc(p, "preparar"); return dict(ok=True)
    if acao == "plano": exigir_ugc(ped); lancar_ugc(p, "plano"); return dict(ok=True)
    ids_cenas = {c["id"] for c in ped.get("cenas", [])}
    if acao == "cena":                                      # muda a config de uma cena e refaz (troca de rosto / leitura / prompts)
        cid = d.get("cena")
        if cid not in ids_cenas: raise ValueError("cena inválida")
        c = next(x for x in ped["cenas"] if x["id"] == cid)
        if "trocar" in d: c["trocar"] = bool(d["trocar"])
        if "pedido" in d: c["pedido"] = str(d["pedido"] or "").strip()[:400]
        if "nota" in d: c["nota"] = str(d["nota"] or "").strip()[:300]
        if d.get("modo") in ("rosto + cabelo", "só rosto", "pessoa inteira"): c["modo"] = d["modo"]
        if d.get("arquivo"):                                 # imagem nova (enviada antes por /api/ugc/arquivo?proj=)
            rel = os.path.normpath(str(d["arquivo"]))
            if not rel.startswith("cenas" + os.sep) or not os.path.exists(os.path.join(p, rel)): raise ValueError("imagem inválida")
            c["arquivo"] = rel
            with pl.lock: pl.cena(cid).update(img_final=None, desc=None, swap_prompt_editado=False); pl.salvar()
        with pl.lock:
            if d.get("swap_prompt"): pl.cena(cid).update(swap_prompt=str(d["swap_prompt"]).strip(), swap_prompt_editado=True)
            elif "pedido" in d or "modo" in d: pl.cena(cid).update(swap_prompt_editado=False)
            pl.cena(cid)["desc"] = None; pl.salvar()
        UGC.grava_json(arq_p, ped); exigir_ugc(ped); lancar_ugc(p, "cena", [cid]); return dict(ok=True)
    if acao == "nova_cena":
        rel = os.path.normpath(str(d.get("arquivo") or ""))
        if not rel.startswith("cenas" + os.sep) or not os.path.exists(os.path.join(p, rel)): raise ValueError("imagem inválida")
        k = 1 + max([int(c["id"][1:]) for c in ped.get("cenas", [])] or [0]); cid = f"c{k}"
        ped["cenas"].append(dict(id=cid, arquivo=rel, trocar=bool(d.get("trocar")), modo="rosto + cabelo",
                                 pedido=str(d.get("pedido") or "").strip()[:400], nota=str(d.get("nota") or "").strip()[:300]))
        UGC.grava_json(arq_p, ped); exigir_ugc(ped); lancar_ugc(p, "cena", [cid]); return dict(ok=True, cena=cid)
    if acao == "fonte":                                     # avatar / produto / referência trocados depois de criar
        for tipo in ("avatar", "produto", "referencia"):
            if d.get(tipo):
                rel = os.path.normpath(str(d[tipo]))
                if not os.path.exists(os.path.join(p, rel)) or rel.startswith(".."): raise ValueError("arquivo inválido")
                ped[tipo] = rel
        UGC.grava_json(arq_p, ped); return dict(ok=True)
    if acao == "config":
        if d.get("omni") in ("kie", "google"): ped["omni"] = d["omni"]
        if d.get("resolucao") in ("360p", "720p", "1080p", "4k"): ped["resolucao"] = d["resolucao"]
        if "broll" in d: ped["broll"] = bool(d["broll"])
        if d.get("modelo_broll") and gerativa.valido(d["modelo_broll"], "video"): ped["modelo_broll"] = d["modelo_broll"]
        if d.get("modelo_img") and gerativa.valido(d["modelo_img"], "img"): ped["modelo_img"] = d["modelo_img"]
        if d.get("taxa") in ("calmo", "rapido"): ped["taxa"] = d["taxa"]
        UGC.grava_json(arq_p, ped); return dict(ok=True)
    if acao == "editar":                                    # clipe: cena, duração, prompt à mão, beats; B-roll: prompt, duração
        k = str(d.get("item")); it = pl.item(k)
        with pl.lock:
            if k.startswith("b"):
                if d.get("prompt"): it["prompt"] = str(d["prompt"]).strip()
                if d.get("imagem_prompt"): it["imagem_prompt"] = str(d["imagem_prompt"]).strip(); it["img"] = None
                if d.get("dur"): it["dur"] = max(3, min(int(d["dur"]), 8))
            else:
                if d.get("cena") in ids_cenas: it["cena"] = d["cena"]
                if d.get("dur") in (4, 6, 8, 10, "4", "6", "8", "10"): it["dur"] = int(d["dur"])
                if d.get("prompt"): it["prompt"] = str(d["prompt"]).strip(); it["prompt_editado"] = True
                elif d.get("refazer_prompt"): it["prompt_editado"] = False
            pl.salvar()
        if not k.startswith("b") and not it.get("prompt_editado"):
            desc = (pl.p["cenas"].get(it["cena"]) or {}).get("desc")
            if not desc: raise ValueError("essa cena ainda não foi lida pelo Claude; espere ou refaça a cena")
            UGC.remontar(p, ped, pl)
        return dict(ok=True)
    if acao == "aprovar":
        k = str(d.get("item")); pl.muda(k, aprovado=bool(d.get("valor", True))); return dict(ok=True)
    if acao == "videos":
        alvo = [str(x) for x in d.get("itens") or []]
        if alvo:
            for k in alvo: pl.item(k)
        exigir_ugc(ped, videos=True); lancar_ugc(p, "videos", alvo or None); return dict(ok=True)
    if acao == "montar": lancar_ugc(p, "montar"); return dict(ok=True)
    raise ValueError("ação inválida")

def zip_ugc(slug, so_aprovados):
    """Pasta com os vídeos numerados (01.mp4, 02.mp4… e os B-rolls como 03-broll.mp4), na ordem do roteiro."""
    p = pasta_ugc(slug); P = _json_ou(os.path.join(p, "plano.json"), {}); ped = _json_ou(os.path.join(p, "ugc.json"), {})
    itens = []
    for c in P.get("clipes", []):
        if c.get("video") and (c.get("aprovado") or not so_aprovados): itens.append((os.path.join(p, c["video"]), f"{c['n']:02d}.mp4"))
        for b in P.get("broll", []):
            if b.get("cobre") == c["n"] and b.get("video") and (b.get("aprovado") or not so_aprovados):
                itens.append((os.path.join(p, b["video"]), f"{c['n']:02d}-broll-{b['id']}.mp4"))
    if not itens: raise ValueError("nenhum vídeo " + ("aprovado" if so_aprovados else "pronto") + " ainda")
    roteiro = "\n".join(f"{c['n']:02d} · {c['dur']} s · {c['fala']}" for c in P.get("clipes", []))
    destino = os.path.join(p, ".zip"); os.makedirs(destino, exist_ok=True)
    arq = os.path.join(destino, f"{slug_nome(ped.get('nome') or slug)}{' (aprovados)' if so_aprovados else ''}.zip")
    import zipfile
    with zipfile.ZipFile(arq, "w", zipfile.ZIP_STORED) as z:
        pasta = slug_nome(ped.get("nome") or slug)
        for src, nome in itens: z.write(src, f"{pasta}/{nome}")
        z.writestr(f"{pasta}/falas.txt", roteiro)
    return arq

# ---------------------------------------------------------------- configurações e custos
def testar_claude():
    try: r = subprocess.run([VENV_PY, os.path.join(LIB, "ia.py"), "testar"], capture_output=True, text=True, timeout=90,
                            env=dict({k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}, PYTHONUTF8="1"))
    except subprocess.TimeoutExpired: return dict(ok=False, msg="o Claude demorou demais para responder")
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError): return dict(ok=False, msg=(r.stderr or "falhou")[-200:])

def salvar_config(d):
    if d.get("modelo") and d["modelo"] not in [m["id"] for m in chaves.MODELOS]: raise ValueError("modelo inválido")
    if d.get("modelo_gemini") and d["modelo_gemini"] not in [m["id"] for m in gemini.MODELOS]: raise ValueError("modelo do Gemini inválido")
    chaves.gravar(gemini_gratis=bool(d["gemini_gratis"]) if "gemini_gratis" in d else None,
                  claude_auth=d.get("claude_auth") if d.get("claude_auth") in [x["id"] for x in chaves.AUTH_CLAUDE] else None,
                  claude_token=d.get("claude_token") or None, anthropic=d.get("anthropic") or None,
                  gemini=d.get("gemini") or None, openrouter=d.get("openrouter") or None, xai=d.get("xai") or None, kie=d.get("kie") or None,
                  gen_resolucao=d.get("gen_resolucao") if d.get("gen_resolucao") in [r["id"] for r in chaves.RESOLUCOES] else None,
                  gen_auth=d.get("gen_auth") if d.get("gen_auth") in [a["id"] for a in chaves.AUTH_GERATIVA] else None,
                  gen_img=d.get("gen_img") if gerativa.valido(d.get("gen_img"), "img") else None,
                  gen_video=d.get("gen_video") if gerativa.valido(d.get("gen_video"), "video") else None,
                  gen_modelo_img=d.get("gen_modelo_img") if d.get("gen_modelo_img") in [m["id"] for m in chaves.MODELOS_IMG] else None,
                  modelo=d.get("modelo") or None, modelo_gemini=d.get("modelo_gemini") or None)
    return chaves.publico()

def testar_config(qual):
    if qual == "anthropic": return testar_claude()
    if qual in ("gemini", "openrouter"): return gemini.testar(qual)
    if qual == "xai": return gerativa.testar()
    if qual == "kie": return gerativa.testar_kie()
    raise ValueError("teste inválido")

def todos_custos():
    out = []; T = dict(claude=0.0, gemini=0.0, grok=0.0, kie=0.0, google=0.0, total=0.0)
    for a in lista_animados():
        r = custos.resumo(os.path.join(animados(), a["id"]))
        out.append(dict(id=a["id"], nome=a["nome"], criado=a["criado"], resumo=r))
    for a in lista_ugc():
        r = custos.resumo(os.path.join(ugcs(), a["id"]))
        out.append(dict(id=a["id"], nome="UGC · " + (a["nome"] or ""), criado=a["criado"], resumo=r, ugc=True))
        for k in T: T[k] = round(T[k] + float(r.get(k) or 0), 6)
    return dict(ads=out, total=T)

# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def _origem_ok(self):
        """Só aceita POST desta própria página (evita que um site aberto no navegador mande comandos para cá)."""
        o = self.headers.get("Origin")
        if not o: return True
        return urllib.parse.urlparse(o).hostname in ("localhost", "127.0.0.1", HOST)

    def _arquivo(self, p, pagina=False):
        if not (pagina or permitido(p)) or not os.path.isfile(p): return self.send_error(404)
        tam = os.path.getsize(p); tipo = mimetypes.guess_type(p)[0] or "application/octet-stream"
        rng = self.headers.get("Range"); ini, fim = 0, tam - 1; code = 200
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    ini = int(m.group(1))
                    if m.group(2): fim = min(int(m.group(2)), tam - 1)
                    elif tipo.startswith(("video/", "audio/")): fim = min(tam - 1, ini + (8 << 20) - 1)   # pedaços de 8 MB: vídeo parado não prende conexão
                elif m.group(2): ini = max(0, tam - int(m.group(2)))
                else: ini = tam
                if ini >= tam or ini > fim:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{tam}")
                    self.send_header("Content-Length", "0"); self.end_headers(); return
                code = 206
        self.send_response(code); self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo.startswith("text/") else ""))
        self.send_header("Accept-Ranges", "bytes"); self.send_header("Content-Length", str(fim - ini + 1)); self.send_header("Cache-Control", "no-cache")
        if not pagina:
            disp = "inline" if tipo.startswith(("video/", "audio/", "image/")) else "attachment"
            self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(p)))
        if code == 206: self.send_header("Content-Range", f"bytes {ini}-{fim}/{tam}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(ini); falta = fim - ini + 1
                while falta > 0:
                    ch = f.read(min(1 << 20, falta))
                    if not ch: break
                    self.wfile.write(ch); falta -= len(ch)
        except (BrokenPipeError, ConnectionResetError): pass

    def _baixar(self, arq):
        tam = os.path.getsize(arq)
        self.send_response(200); self.send_header("Content-Type", "application/zip"); self.send_header("Content-Length", str(tam))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(arq)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        try:
            with open(arq, "rb") as f: shutil.copyfileobj(f, self.wfile, 1 << 20)
        except (BrokenPipeError, ConnectionResetError): pass
        finally:
            try: os.remove(arq)
            except OSError: pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ("/api/health", "/health"): return self._json(dict(ok=True, app="ad-animado"))
            if u.path == "/favicon.ico": self.send_response(204); self.end_headers(); return
            if u.path in ("/", "/animado"): return self._arquivo(os.path.join(APP, "animado.html"), pagina=True)
            if u.path == "/ugc": return self._arquivo(os.path.join(APP, "ugc.html"), pagina=True)
            if u.path == "/api/ugc/lista": return self._json(lista_ugc())
            if u.path == "/api/ugc/estado": return self._json(estado_ugc(q.get("id")))
            if u.path == "/api/ugc/zip": return self._baixar(zip_ugc(q.get("id"), q.get("aprovados") == "1"))
            if u.path == "/f": return self._arquivo(q.get("p", ""))
            if u.path == "/api/animado/lista": return self._json(lista_animados())
            if u.path == "/api/animado/estado": return self._json(estado_animado(q.get("id")))
            if u.path == "/api/config": return self._json(dict(chaves.publico(), pasta=comum.RAIZ))
            if u.path == "/api/custos": return self._json(todos_custos())
        except (ValueError, KeyError) as e:
            return self._json(dict(erro=str(e)), 400)
        self.send_error(404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not self._origem_ok(): return self._json(dict(erro="origem não permitida"), 403)
        try:
            if u.path == "/api/animado/arquivo":
                receber_animado(self, q.get("id", ""), q.get("tipo", ""), q.get("n", ""), q.get("nome", "")); return self._json(dict(ok=True))
            if u.path == "/api/ugc/arquivo":
                rel = receber_ugc(self, q.get("id", ""), q.get("tipo", ""), q.get("n", ""), q.get("nome", ""), q.get("proj") or None)
                return self._json(dict(ok=True, arquivo=rel))
        except ErroEnvio as e: return self._json(dict(erro=str(e)), e.status)
        except (ValueError, KeyError) as e: return self._json(dict(erro=str(e)), 400)
        try: corpo = self.rfile.read(tamanho_corpo(self, 8 * 1024**2))
        except ValueError as e: return self._json(dict(erro=str(e)), 413)
        try:
            d = json.loads(corpo or b"{}")
            if u.path == "/api/animado/criar": return self._json(dict(ok=True, id=criar_animado(d)))
            if u.path == "/api/animado/acao": return self._json(acao_animado(q.get("id"), d))
            if u.path == "/api/ugc/criar": return self._json(dict(ok=True, id=criar_ugc(d)))
            if u.path == "/api/ugc/acao": return self._json(acao_ugc(q.get("id"), d))
            if u.path == "/api/config": return self._json(salvar_config(d))
            if u.path == "/api/config/testar": return self._json(testar_config(q.get("qual")))
        except (ValueError, KeyError) as e:
            return self._json(dict(erro=str(e)), 400)
        self.send_error(404)

if __name__ == "__main__":
    os.makedirs(comum.RAIZ, exist_ok=True)
    print(f"Ad Animado em http://localhost:{PORTA}  (ads em {comum.RAIZ})", flush=True)
    ThreadingHTTPServer((HOST, PORTA), H).serve_forever()
