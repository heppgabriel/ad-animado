"""UGC ultra-realista: roteiro + imagens iniciais (+ avatar, produto, vídeo de referência) -> clipes de A-roll no Gemini Omni
Flash (avatar falando, com lip-sync) e, se ligado, B-roll no Kling. Segue as skills omni-ugc-director e troca-rosto-pinterest.

  python lib/ugc.py <pasta> preparar          referência -> troca de rosto -> descrição das cenas -> plano (prompts)
  python lib/ugc.py <pasta> cena c2           refaz uma cena (troca de rosto, se pedida) e reescreve os prompts dela
  python lib/ugc.py <pasta> plano             refaz o plano (prompts) com as imagens atuais
  python lib/ugc.py <pasta> videos [1,2,b1]   gera os vídeos (todos os pendentes, ou os escolhidos) — todos ao mesmo tempo
  python lib/ugc.py <pasta> montar            junta os clipes num vídeo só (juntos.mp4), com os B-rolls por cima

Arquivos: ugc.json (o pedido, o que você escolheu na tela), plano.json (cenas, clipes, prompts, vídeos), estado.json, custos.json,
cenas/, avatar/, referencia/, videos/, juntos.mp4."""
import os, re, sys, json, math, time, shutil, signal, threading, traceback, difflib, subprocess
from concurrent.futures import ThreadPoolExecutor
LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum, chaves, custos, gerativa
import ugc_regras as R

ETAPAS = [("referencia", "Vídeo de referência"), ("avatar", "Avatar nas cenas (troca de rosto)"), ("cenas", "Leitura das imagens (Claude)"),
          ("plano", "Clipes e prompts (Claude)"), ("videos", "Vídeos (Omni / Kling)"), ("montagem", "Todos juntos")]
PAR_MAX = 30
W, H, FPS = 1080, 1920, 30

def raiz(): return os.path.join(comum.RAIZ, "UGC")

# ---------------------------------------------------------------- estado e arquivos
def ler_json(arq, padrao):
    for _ in range(5):
        try: return json.load(open(arq, encoding="utf-8"))
        except PermissionError: time.sleep(0.05)
        except (OSError, ValueError): return padrao
    return padrao

def grava_json(arq, obj):
    tmp = f"{arq}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=1)
    comum.trocar(tmp, arq)

class Estado:
    def __init__(self, d, reiniciar=()):
        self.arq = os.path.join(d, "estado.json"); self.lock = threading.RLock(); self.s = ler_json(self.arq, {})
        feitas = {e["id"]: e for e in self.s.get("etapas", [])}
        self.s["etapas"] = [feitas.get(i) or dict(id=i, nome=n, estado="pendente", detalhe="") for i, n in ETAPAS]
        for e in self.s["etapas"]:
            if e["id"] in reiniciar or e["estado"] in ("rodando", "erro"): e["estado"] = "pendente"
        self.s.update(rodando=True, status="rodando", pid=os.getpid(), mensagem="", fim=None); self.s.setdefault("log", []); self.salvar()
    def et(self, i): return next(e for e in self.s["etapas"] if e["id"] == i)
    def marca(self, i, estado, detalhe=None):
        with self.lock:
            e = self.et(i); e["estado"] = estado
            if detalhe is not None: e["detalhe"] = detalhe
            self.salvar()
    def log(self, msg):
        with self.lock:
            linha = time.strftime("%H:%M:%S ") + str(msg); print(linha, flush=True)
            self.s["log"] = (self.s["log"] + [linha])[-400:]; self.salvar()
    def fim(self, status, mensagem=""):
        with self.lock: self.s.update(rodando=False, status=status, mensagem=mensagem, fim=time.time()); self.salvar()
    def salvar(self):
        with self.lock: grava_json(self.arq, self.s)

def roda(est, i, fn, forcar=False):
    if est.et(i)["estado"] == "ok" and not forcar: return
    est.marca(i, "rodando", ""); est.log(f"— {dict(ETAPAS)[i]}")
    try: det = fn()
    except Exception as e: est.marca(i, "erro", str(e)[-400:]); raise
    est.marca(i, "ok", det or "")

class Plano:
    """plano.json com trava (as gerações rodam em paralelo e cada uma grava o seu clipe)."""
    def __init__(self, d):
        self.arq = os.path.join(d, "plano.json"); self.lock = threading.RLock()
        self.p = ler_json(self.arq, {})
        self.p.setdefault("cenas", {}); self.p.setdefault("clipes", []); self.p.setdefault("broll", [])
    def item(self, k):
        k = str(k)
        if k.startswith("b"): return next(x for x in self.p["broll"] if x["id"] == k)
        return next(x for x in self.p["clipes"] if str(x["n"]) == k)
    def muda(self, k, **kw):
        with self.lock: self.item(k).update(kw); self.salvar()
    def cena(self, cid): return self.p["cenas"].setdefault(cid, {})
    def muda_cena(self, cid, **kw):
        with self.lock: self.cena(cid).update(kw); self.salvar()
    def salvar(self):
        with self.lock: grava_json(self.arq, self.p)

def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode: raise RuntimeError("ffmpeg: " + (r.stderr.strip()[-300:] or "falhou"))

def dur_de(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def tem_audio(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", arq], capture_output=True, text=True)
    return bool(r.stdout.strip())

def mini(d, arq, lado=768):
    pasta = os.path.join(d, ".mini"); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, f"{lado}-{os.path.basename(os.path.dirname(arq))}-{os.path.splitext(os.path.basename(arq))[0]}.jpg")
    if not os.path.exists(dest) or os.path.getmtime(dest) < os.path.getmtime(arq):
        ff("-i", arq, "-vf", f"scale='min({lado},iw)':-2", "-frames:v", "1", "-q:v", "4", dest)
    return dest

def claude(d, est):
    import ia
    def cobra(item): custos.registrar(d, servico="claude", etapa="ugc", **item)
    return ia.Claude(log=est.log, ao_cobrar=cobra)

def imagem_da_cena(d, ped, pl, cid):
    """A imagem que vale para a cena: a da troca de rosto (se pediu e já saiu) ou a original."""
    c = next((x for x in ped.get("cenas", []) if x["id"] == cid), None)
    if not c: return None
    st = pl.p["cenas"].get(cid, {})
    if c.get("trocar") and st.get("img_final"): return os.path.join(d, st["img_final"])
    return os.path.join(d, c["arquivo"])

# ---------------------------------------------------------------- empacotar as falas (algoritmo guloso da skill)
def palavras(t): return re.findall(r"\S+", t)

def frases(roteiro):
    """Frases na ordem, sem mexer em nada (pontuação incluída). Quebra em . ! ? … e em quebra de linha."""
    out = []
    for linha in re.split(r"\n+", roteiro.replace("\r", "")):
        linha = linha.strip()
        if not linha: continue
        out += [f.strip() for f in re.findall(r"[^.!?…]+(?:[.!?…]+[\"”')]*|$)", linha) if f.strip()]
    return out

def capacidade(dur, taxa): return int(math.floor(dur * taxa + 1e-9))

def quebrar(frase, cap):
    """Frase longa demais (> 10 s) quebrada numa vírgula/pausa natural; sem pausa, no limite de palavras."""
    ws = palavras(frase); partes = []
    while len(ws) > cap:
        corte = max((i + 1 for i, w in enumerate(ws[:cap]) if re.search(r"[,;:—–-]$", w) and i + 1 >= cap // 3), default=cap)
        partes.append(" ".join(ws[:corte])); ws = ws[corte:]
    if ws: partes.append(" ".join(ws))
    return partes

def empacotar(roteiro, taxa):
    """Menor número de clipes sem estourar 10 s: junta frases curtas, só quebra as que passam de 10 s. Nunca reordena."""
    cap = capacidade(10, taxa); pedacos = []
    for f in frases(roteiro): pedacos += quebrar(f, cap)
    clipes, atual = [], []
    for p in pedacos:
        if atual and len(palavras(" ".join(atual + [p]))) > cap: clipes.append(" ".join(atual)); atual = []
        atual.append(p)
    if atual: clipes.append(" ".join(atual))
    return [dict(n=i + 1, fala=c, palavras=len(palavras(c)), dur=duracao_para(len(palavras(c)), taxa)) for i, c in enumerate(clipes)]

def duracao_para(n_palavras, taxa):
    return next((d for d in R.DURACOES if n_palavras <= capacidade(d, taxa)), R.DURACOES[-1])

def auditoria(roteiro, falas):
    """Roteiro = soma das falas, palavra por palavra. Devolve [] se idêntico, senão as diferenças."""
    norm = lambda t: re.findall(r"\w+", t.lower())
    dif = [x for x in difflib.unified_diff(norm(roteiro), norm(" ".join(falas)), lineterm="", n=0) if not x.startswith(("---", "+++", "@@"))]
    return dif[:60]

def avisos_roteiro(roteiro):
    av = []
    if re.search(r"\d", roteiro): av.append("o roteiro tem números em dígitos: escreva por extenso e com o substantivo junto (\"cinquenta abdominais\"), senão o Omni fala errado ou no plural")
    return av

# ---------------------------------------------------------------- prompts (montados pelo código, com as travas literais)
def limpa(t): return re.sub(r"(?i)\bcinematic(ally)?\b", "", t or "").replace("  ", " ").strip()

def montar_prompt(clipe, desc, ped, plano):
    """Prompt do A-roll no Omni, na ordem obrigatória da skill. A fala fecha o prompt (só o Do not depois)."""
    pron = R.PRONOMES.get(desc.get("genero") or "f", R.PRONOMES["f"]); nome = (ped.get("personagem") or {}).get("nome") or "ANA"
    mudo = clipe.get("tipo") == "escuta" or not clipe.get("fala", "").strip()
    dur = int(clipe["dur"]); b = []
    idioma = ped.get("idioma") or R.detectar_idioma(ped.get("roteiro") or clipe.get("fala") or "")
    b.append(f"9:16 vertical. {dur} seconds. Single continuous shot. Realistic amateur UGC video.")
    if mudo: b.append(R.no_speech_lock(pron))
    else: b += [R.language_lock(idioma), R.completeness_lock(pron)]
    b.append(R.identity_lock(nome.upper(), pron, limpa(desc.get("tracos") or "same face, same hair, same clothes")))
    cena = limpa(desc.get("cenario") or "Same setting and posture as @image1.")
    mexe = limpa(desc.get("pode_mexer") or "")
    so_rosto = f"Only {pron['P'].lower()} face moves: expression, mouth, eyes, eyebrows, plus the natural micro-settling of someone sitting still."
    produto = ""
    if clipe.get("com_produto"):
        produto = (" The product package is exactly the one in @image2: the label artwork and lettering stay sharp, stable and readable "
                   "throughout and do not warp, shift or re-render.")
    b.append(f"Scene: {cena} {mexe} {so_rosto}{produto}".replace("  ", " ").strip())
    b.append(R.IMAGE_QUALITY_LOCK)
    if desc.get("camera_tipo") == "selfie": b.append(R.camera_selfie(pron, desc.get("enquadramento") or "framing from the chest up"))
    else: b.append(R.camera_fixa(pron, desc.get("posicao") or "from a phone propped in front of " + pron["O"], desc.get("enquadramento") or "framing from the chest up"))
    for beat in clipe.get("beats") or []: b.append(limpa(beat))
    amb = desc.get("ambiencia") or "quiet indoor room tone"
    if mudo: b.append(R.audio_mudo(amb)); b.append(R.do_not_mudo(pron, limpa(clipe.get("nao_extra"))))
    else:
        b.append(R.audio_fala(pron, limpa(plano.get("voz") or "Natural warm voice, casual conversational tone"), amb, clipe["fala"], idioma))
        b.append(R.do_not(pron, limpa(clipe.get("nao_extra")), idioma))
    return "\n\n".join(x for x in b if x)

def remontar(d, ped, pl, so_cena=None):
    """Reescreve os prompts (menos os que você editou à mão) — chamado quando uma imagem de cena muda ou um clipe troca de cena."""
    for c in pl.p["clipes"]:
        if so_cena and c.get("cena") != so_cena: continue
        if c.get("prompt_editado"): continue
        desc = (pl.p["cenas"].get(c.get("cena")) or {}).get("desc") or {}
        c["com_produto"] = bool(ped.get("produto") and desc.get("tem_produto"))
        c["prompt"] = montar_prompt(c, desc, ped, pl.p)
    pl.salvar()

# ---------------------------------------------------------------- etapas
def etapa_referencia(d, ped, pl, est):
    if not ped.get("referencia"):
        with pl.lock: pl.p["referencia"] = None; pl.salvar()
        return "sem vídeo de referência"
    arq = os.path.join(d, ped["referencia"]); dur = dur_de(arq)
    if dur <= 0: raise RuntimeError("não consegui ler o vídeo de referência")
    pasta = os.path.join(d, "referencia", "quadros"); shutil.rmtree(pasta, ignore_errors=True); os.makedirs(pasta)
    n = int(max(6, min(16, math.ceil(dur / 2)))); quadros = []
    for k in range(n):
        t = round(dur * (k + 0.5) / n, 2); dest = os.path.join(pasta, f"q{k + 1:02d}.jpg")
        ff("-ss", f"{t:.2f}", "-i", arq, "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", dest)
        quadros.append(dict(t=t, arquivo=os.path.relpath(dest, d)))
    transcricao = ""
    if tem_audio(arq):
        try:
            import transcrever
            est.log("transcrevendo a referência (whisper)…")
            transcricao = transcrever.transcrever(arq, os.path.join(d, "referencia", "transcricao.json"))
        except Exception as e: est.log(f"sem transcrição da referência ({e})")
    with pl.lock: pl.p["referencia"] = dict(dur=round(dur, 2), quadros=quadros, transcricao=transcricao[:6000]); pl.salvar()
    return f"{n} quadros · {round(dur)} s"

SCHEMA_TROCA = {"type": "object", "properties": {"prompt": {"type": "string"}, "avisos": {"type": "array", "items": {"type": "string"}}},
                "required": ["prompt", "avisos"], "additionalProperties": False}

def trocar_rosto(d, ped, pl, est, cid):
    """Troca de rosto de uma cena: o Claude escreve o prompt (skill troca-rosto) e a IA de imagem gera. Refs: cena, avatar, produto."""
    import ia
    c = next(x for x in ped["cenas"] if x["id"] == cid); st = pl.cena(cid)
    refs = [os.path.join(d, c["arquivo"])]
    if ped.get("avatar"): refs.append(os.path.join(d, ped["avatar"]))
    quer_produto = bool(ped.get("produto")) and bool(re.search(r"produt|pote|frasco|embalag|segur|caixa|garraf", (c.get("pedido") or "") + " " + (ped.get("pedido_avatar") or ""), re.I))
    if quer_produto: refs.append(os.path.join(d, ped["produto"]))
    prompt = (st.get("swap_prompt_editado") and st.get("swap_prompt")) or None
    if not prompt:
        conteudo = [ia.texto("image 1 — a cena (frame real que é o molde):"), ia.imagem(mini(d, refs[0]))]
        if ped.get("avatar"): conteudo += [ia.texto("image 2 — a avatar (a identidade nova):"), ia.imagem(mini(d, refs[1]))]
        if quer_produto: conteudo += [ia.texto(f"image {len(refs)} — o produto (PNG do pote; o rótulo tem que ficar nítido):"), ia.imagem(mini(d, refs[-1]))]
        conteudo.append(ia.texto(
            f"Modo: {'com avatar master (image 2)' if ped.get('avatar') else 'rosto novo descrito: ' + (ped.get('rosto_novo') or 'pessoa comum, mesma faixa de idade e biotipo, aparência real')}.\n"
            f"O que trocar: {c.get('modo') or 'rosto + cabelo'}.\nMarca do produto: {ped.get('marca') or '-'}.\n"
            f"Pedido extra do usuário para esta cena: {c.get('pedido') or ped.get('pedido_avatar') or 'nenhum'}.\n"
            "Escreva o prompt do Nano Banana Pro pronto (sem colchetes) e os avisos."))
        R_ = claude(d, est).conversa(R.INSTR_TROCA, esforco="medium").pedir(conteudo, SCHEMA_TROCA, rotulo=f"Troca de rosto {cid}")
        prompt = limpa(R_["prompt"]); pl.muda_cena(cid, avisos_troca=R_["avisos"])
    mid = ped.get("modelo_img") or "kie:nano-banana-pro"
    g = gerativa.gerador_imagem(mid)
    pl.muda_cena(cid, swap_estado="gerando", swap_prompt=prompt, swap_erro="")
    try:
        dados, usd, estimado = g.gerar_imagem(prompt, refs)
    except Exception as e:
        pl.muda_cena(cid, swap_estado="erro", swap_erro=str(e)[:300]); raise
    custos.registrar(d, servico="kie" if isinstance(g, gerativa.Kie) else "grok", etapa="ugc", rotulo=f"troca de rosto {cid}",
                     usd=usd, estimado=estimado, modelo=gerativa.nome_modelo(mid))
    os.makedirs(os.path.join(d, "avatar"), exist_ok=True)
    dest = os.path.join("avatar", f"{cid}-{int(time.time() * 1000) % 10**8}.jpg"); open(os.path.join(d, dest), "wb").write(dados)
    hist = ([st["img_final"]] if st.get("img_final") else []) + (st.get("swap_hist") or [])
    pl.muda_cena(cid, img_final=dest, swap_estado="ok", swap_hist=hist[:6], desc=None)
    est.log(f"avatar na cena {cid} pronto")

def etapa_avatar(d, ped, pl, est, so=None):
    alvo = [c["id"] for c in ped.get("cenas", []) if c.get("trocar") and (so is None or c["id"] in so)]
    if not alvo: return "nenhuma cena pede troca de rosto"
    if not ped.get("avatar") and not ped.get("rosto_novo"): raise RuntimeError("para trocar o rosto, envie a foto da avatar ou descreva o rosto novo")
    erros = []
    def um(cid):
        try: trocar_rosto(d, ped, pl, est, cid)
        except Exception as e: erros.append(cid); est.log(f"troca de rosto {cid}: {e}")
    with ThreadPoolExecutor(max(1, min(PAR_MAX, len(alvo)))) as ex: list(ex.map(um, alvo))
    if len(erros) == len(alvo): raise RuntimeError("nenhuma troca de rosto saiu: " + (pl.cena(erros[0]).get("swap_erro") or ""))
    return f"{len(alvo) - len(erros)} de {len(alvo)} cenas" + (f" · erro em {', '.join(erros)}" if erros else "")

SCHEMA_CENA = {"type": "object", "properties": {
    "tracos": {"type": "string"}, "cenario": {"type": "string"}, "pode_mexer": {"type": "string"},
    "camera_tipo": {"type": "string", "enum": ["fixa", "selfie"]}, "posicao": {"type": "string"}, "enquadramento": {"type": "string"},
    "ambiencia": {"type": "string"}, "genero": {"type": "string", "enum": ["f", "m"]}, "tem_produto": {"type": "boolean"},
    "avisos": {"type": "array", "items": {"type": "string"}}},
    "required": ["tracos", "cenario", "pode_mexer", "camera_tipo", "posicao", "enquadramento", "ambiencia", "genero", "tem_produto", "avisos"],
    "additionalProperties": False}

def descrever(d, ped, pl, est, cid):
    import ia
    img = imagem_da_cena(d, ped, pl, cid)
    c = next(x for x in ped["cenas"] if x["id"] == cid)
    R_ = claude(d, est).conversa(R.INSTR_CENA, esforco="medium").pedir(
        [ia.texto(f"Cena {cid}. Nome do personagem: {(ped.get('personagem') or {}).get('nome') or 'ANA'}. Nota do usuário: {c.get('nota') or '-'}"),
         ia.imagem(mini(d, img))], SCHEMA_CENA, rotulo=f"Leitura da cena {cid}")
    pl.muda_cena(cid, desc={k: (limpa(v) if isinstance(v, str) else v) for k, v in R_.items() if k != "avisos"}, avisos=R_["avisos"])

def etapa_cenas(d, ped, pl, est, so=None):
    alvo = [c["id"] for c in ped.get("cenas", []) if (so is None or c["id"] in so) and (so is not None or not pl.cena(c["id"]).get("desc"))]
    if not alvo: return "todas lidas"
    with ThreadPoolExecutor(max(1, min(4, len(alvo)))) as ex: list(ex.map(lambda cid: descrever(d, ped, pl, est, cid), alvo))
    return f"{len(alvo)} imagem(ns)"

def schema_plano(broll):
    clip = {"type": "object", "properties": {"n": {"type": "integer"}, "cena": {"type": "string"},
            "beats": {"type": "array", "items": {"type": "string"}}, "nao_extra": {"type": "string"}},
            "required": ["n", "cena", "beats", "nao_extra"], "additionalProperties": False}
    props = {"voz": {"type": "string"}, "analise": {"type": "string"}, "avisos": {"type": "array", "items": {"type": "string"}},
             "clipes": {"type": "array", "items": clip}}
    if broll:
        props["broll"] = {"type": "array", "items": {"type": "object", "properties": {
            "cobre": {"type": "integer"}, "dur": {"type": "integer"}, "imagem_prompt": {"type": "string"}, "prompt": {"type": "string"}},
            "required": ["cobre", "dur", "imagem_prompt", "prompt"], "additionalProperties": False}}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

def etapa_plano(d, ped, pl, est):
    import ia
    taxa = R.TAXAS.get(ped.get("taxa") or "calmo", 2.35)
    pacote = empacotar(ped["roteiro"], taxa)
    if not pacote: raise RuntimeError("o roteiro está vazio")
    dif = auditoria(ped["roteiro"], [c["fala"] for c in pacote])
    if dif: raise RuntimeError("a auditoria de integridade achou diferença entre o roteiro e as falas: " + " ".join(dif[:20]))
    cenas = [c for c in ped["cenas"]]
    conteudo = [ia.texto("CENAS (imagens iniciais disponíveis):")]
    for c in cenas:
        desc = pl.cena(c["id"]).get("desc") or {}
        conteudo += [ia.texto(f"Cena {c['id']} — nota do usuário: {c.get('nota') or '-'} · cenário: {desc.get('cenario', '-')} · "
                              f"câmera: {desc.get('camera_tipo', '-')} · produto na imagem: {'sim' if desc.get('tem_produto') else 'não'}"),
                     ia.imagem(mini(d, imagem_da_cena(d, ped, pl, c["id"]), 512))]
    ref = pl.p.get("referencia")
    if ref:
        conteudo.append(ia.texto(f"VÍDEO DE REFERÊNCIA ({ref['dur']} s) — quadros em ordem" + (f"; fala: {ref['transcricao']}" if ref.get("transcricao") else "")))
        for q in ref["quadros"][:12]: conteudo.append(ia.imagem(os.path.join(d, q["arquivo"])))
    if ped.get("produto"): conteudo += [ia.texto(f"PRODUTO (marca: {ped.get('marca') or '-'}):"), ia.imagem(mini(d, os.path.join(d, ped["produto"]), 512))]
    conteudo.append(ia.texto(
        f"Personagem: {(ped.get('personagem') or {}).get('nome') or 'ANA'}. Voz pedida pelo usuário: {(ped.get('personagem') or {}).get('voz') or '(você escolhe)'}.\n"
        f"Idioma da fala: {R.IDIOMAS.get(ped.get('idioma') or R.detectar_idioma(ped['roteiro']), R.IDIOMAS['pt'])['nome']} (a voz tem que ser descrita nesse idioma).\n"
        f"Ritmo de fala: {ped.get('taxa') or 'calmo'} ({taxa} palavras/s).\nB-ROLL: {'LIGADO' if ped.get('broll') else 'desligado (não proponha)'}.\n"
        f"Observações do usuário: {ped.get('obs') or '-'}\n\nCLIPES (falas travadas):\n" +
        "\n".join(f"{c['n']:02d} · {c['dur']} s · {c['palavras']} palavras · \"{c['fala']}\"" for c in pacote)))
    S = schema_plano(bool(ped.get("broll")))
    resp = claude(d, est).conversa(R.INSTR_PLANO, esforco="high").pedir(conteudo, S, rotulo="Plano do UGC", formato=False)
    ids = {c["id"] for c in cenas}; por_n = {c["n"]: c for c in resp.get("clipes", [])}
    antigos = {c["n"]: c for c in pl.p.get("clipes", [])}
    clipes = []
    for c in pacote:
        r = por_n.get(c["n"]) or {}
        cena = r.get("cena") if r.get("cena") in ids else (antigos.get(c["n"], {}).get("cena") if antigos.get(c["n"], {}).get("cena") in ids else cenas[0]["id"])
        beats = [limpa(x) for x in (r.get("beats") or [])][:3] or [f"[0:00-0:0{c['dur'] - 1 if c['dur'] < 10 else 9}] natural engaged expression while talking, body still, only her face moves"]
        clipes.append(dict(c, tipo="aroll", cena=cena, beats=beats, nao_extra=limpa(r.get("nao_extra")), video=None, video_estado="pendente",
                           aprovado=False, historico=[], prompt_editado=False))
    broll = []
    if ped.get("broll"):
        for k, b in enumerate(resp.get("broll") or [], 1):
            alvo = next((c for c in clipes if c["n"] == b.get("cobre")), None)
            if not alvo: continue
            broll.append(dict(id=f"b{k}", cobre=alvo["n"], dur=max(3, min(int(b.get("dur") or 4), alvo["dur"], 8)), cena=alvo["cena"],
                              imagem_prompt=limpa(b["imagem_prompt"]), prompt=limpa(b["prompt"]), img=None, img_estado="pendente",
                              video=None, video_estado="pendente", aprovado=False, historico=[]))
    with pl.lock:
        pl.p.update(clipes=clipes, broll=broll, voz=limpa(resp.get("voz")), analise=resp.get("analise", ""),
                    avisos=avisos_roteiro(ped["roteiro"]) + list(resp.get("avisos") or []), auditoria="idêntico"); pl.salvar()
    remontar(d, ped, pl)
    return f"{len(clipes)} clipes de A-roll" + (f" · {len(broll)} B-roll" if broll else "") + f" · {sum(c['dur'] for c in clipes)} s"

# ---------------------------------------------------------------- vídeos
def gerar_broll_imagem(d, ped, pl, est, b):
    mid = ped.get("modelo_img") or "kie:nano-banana-pro"; g = gerativa.gerador_imagem(mid)
    refs = [imagem_da_cena(d, ped, pl, b["cena"])] + ([os.path.join(d, ped["produto"])] if ped.get("produto") else [])
    pl.muda(b["id"], img_estado="gerando")
    dados, usd, estimado = g.gerar_imagem(b["imagem_prompt"], refs)
    custos.registrar(d, servico="kie" if isinstance(g, gerativa.Kie) else "grok", etapa="ugc", rotulo=f"imagem do B-roll {b['id']}", usd=usd, estimado=estimado, modelo=gerativa.nome_modelo(mid))
    os.makedirs(os.path.join(d, "broll"), exist_ok=True)
    dest = os.path.join("broll", f"{b['id']}-{int(time.time() * 1000) % 10**8}.jpg"); open(os.path.join(d, dest), "wb").write(dados)
    pl.muda(b["id"], img=dest, img_estado="ok"); return dest

def gerar_um(d, ped, pl, est, k):
    it = pl.item(k); eh_broll = str(k).startswith("b")
    pl.muda(k, video_estado="gerando", video_erro="")
    try:
        if eh_broll:
            img = it.get("img") or gerar_broll_imagem(d, ped, pl, est, it)
            mid = ped.get("modelo_broll") or "kie:kling/v3-turbo-image-to-video"
            g = gerativa.gerador_video(mid)
            dados, usd, estimado = g.gerar_video(it["prompt"], os.path.join(d, img), it["dur"], audio=False)
            servico, rot, modelo = ("kie" if isinstance(g, gerativa.Kie) else "grok"), f"B-roll {k} ({it['dur']}s)", gerativa.nome_modelo(mid)
        else:
            imgs = [imagem_da_cena(d, ped, pl, it["cena"])]
            if it.get("com_produto") and ped.get("produto"): imgs.append(os.path.join(d, ped["produto"]))
            g = gerativa.gerador_omni(ped.get("omni") or "kie")
            dados, usd, estimado = g.gerar_omni(it["prompt"], imgs, it["dur"], ped.get("resolucao"))
            servico = "google" if isinstance(g, gerativa.GoogleOmni) else "kie"
            rot, modelo = f"clipe {int(k):02d} ({it['dur']}s)", "Gemini Omni Flash 1.1 · " + ("Google" if servico == "google" else "KIE")
        custos.registrar(d, servico=servico, etapa="ugc", rotulo=rot, usd=usd, estimado=estimado, modelo=modelo)
        os.makedirs(os.path.join(d, "videos"), exist_ok=True)
        nome = f"{('B' + str(k)[1:]) if eh_broll else f'{int(k):02d}'}-{int(time.time() * 1000) % 10**8}.mp4"
        dest = os.path.join("videos", nome); open(os.path.join(d, dest), "wb").write(dados)
        hist = ([it["video"]] if it.get("video") else []) + (it.get("historico") or [])
        pl.muda(k, video=dest, video_estado="ok", aprovado=False, historico=hist[:6]); est.log(f"vídeo {k} pronto")
    except Exception as e:
        pl.muda(k, video_estado="erro", video_erro=str(e)[:300]); est.log(f"vídeo {k}: {e}")
        if getattr(e, "fatal", False): raise

def etapa_videos(d, ped, pl, est, so=None):
    todos = [str(c["n"]) for c in pl.p["clipes"]] + [b["id"] for b in pl.p.get("broll", [])]
    alvo = [k for k in todos if (k in so if so else pl.item(k).get("video_estado") != "ok")]
    if not alvo: return "nada para gerar"
    fatal = []
    def um(k):
        if fatal: return
        try: gerar_um(d, ped, pl, est, k)
        except Exception as e: fatal.append(str(e))
    with ThreadPoolExecutor(max(1, min(PAR_MAX, len(alvo)))) as ex: list(ex.map(um, alvo))
    if fatal: raise RuntimeError(fatal[0])
    erros = [k for k in alvo if pl.item(k).get("video_estado") == "erro"]
    if len(erros) == len(alvo): raise RuntimeError("nenhum vídeo foi gerado: " + (pl.item(erros[0]).get("video_erro") or ""))
    return f"{len(alvo) - len(erros)} de {len(alvo)} vídeos" + (f" · erro em {', '.join(erros)}" if erros else "")

# ---------------------------------------------------------------- montagem (todos juntos)
VF = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p"

def montar(d, ped, pl, est):
    clipes = [c for c in pl.p["clipes"] if c.get("video") and c.get("video_estado") == "ok"]
    if any(c.get("aprovado") for c in clipes): clipes = [c for c in clipes if c.get("aprovado")]
    if not clipes: raise RuntimeError("nenhum clipe com vídeo pronto")
    m = os.path.join(d, ".montagem"); shutil.rmtree(m, ignore_errors=True); os.makedirs(m)
    brolls = {b["cobre"]: b for b in pl.p.get("broll", []) if b.get("video") and b.get("video_estado") == "ok" and (b.get("aprovado") or not any(x.get("aprovado") for x in pl.p["broll"]))}
    lista = []
    for c in clipes:
        src = os.path.join(d, c["video"]); out = os.path.join(m, f"{c['n']:03d}.mp4")
        aud = ["-map", "0:a"] if tem_audio(src) else []
        b = brolls.get(c["n"])
        if b:
            bd = min(dur_de(os.path.join(d, b["video"])), b["dur"], dur_de(src))
            ff("-i", src, "-i", os.path.join(d, b["video"]), "-filter_complex",
               f"[0:v]{VF}[a];[1:v]{VF},setpts=PTS-STARTPTS[b];[a][b]overlay=enable='lte(t,{bd:.3f})':eof_action=pass[v]",
               "-map", "[v]", *aud, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out)
        else:
            ff("-i", src, "-vf", VF, *(["-map", "0:v"] + aud), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out)
        if not aud:                                        # clipe sem áudio: põe silêncio para o concat não desalinhar
            tmp = out + ".s.mp4"
            ff("-i", out, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", tmp); os.replace(tmp, out)
        lista.append(out)
    txt = os.path.join(m, "lista.txt"); open(txt, "w", encoding="utf-8").write("".join(f"file '{os.path.basename(x)}'\n" for x in lista))
    final = os.path.join(d, "juntos.mp4")
    ff("-f", "concat", "-safe", "0", "-i", txt, "-c", "copy", "-movflags", "+faststart", final)
    with pl.lock: pl.p["juntos"] = dict(clipes=[c["n"] for c in clipes], dur=round(dur_de(final), 2), quando=time.strftime("%Y-%m-%d %H:%M:%S")); pl.salvar()
    shutil.rmtree(m, ignore_errors=True)
    return f"{len(clipes)} clipes · {round(dur_de(final), 1)} s"

# ---------------------------------------------------------------- principal
def main():
    d = os.path.abspath(sys.argv[1]); acao = sys.argv[2] if len(sys.argv) > 2 else "preparar"
    alvo = [x for x in re.split(r"[,\s]+", sys.argv[3]) if x] if len(sys.argv) > 3 else None
    ped = ler_json(os.path.join(d, "ugc.json"), {}); pl = Plano(d)
    reiniciar = {"plano": ("plano",), "montar": ("montagem",), "videos": ("videos",), "cena": ("avatar", "cenas")}.get(acao, ())
    est = Estado(d, reiniciar); est.s["acao"] = acao; est.salvar()
    def parar(*_):
        est.log("cancelado"); est.fim("erro", "cancelado — dá para retomar"); os._exit(1)
    signal.signal(signal.SIGTERM, parar)
    try:
        if acao == "preparar":
            roda(est, "referencia", lambda: etapa_referencia(d, ped, pl, est))
            roda(est, "avatar", lambda: etapa_avatar(d, ped, pl, est))
            roda(est, "cenas", lambda: etapa_cenas(d, ped, pl, est))
            roda(est, "plano", lambda: etapa_plano(d, ped, pl, est))
        elif acao == "cena":
            so = alvo or []
            if any(c.get("trocar") for c in ped["cenas"] if c["id"] in so): roda(est, "avatar", lambda: etapa_avatar(d, ped, pl, est, so), forcar=True)
            roda(est, "cenas", lambda: etapa_cenas(d, ped, pl, est, so), forcar=True)
            for cid in so: remontar(d, ped, pl, so_cena=cid)
        elif acao == "plano":
            roda(est, "cenas", lambda: etapa_cenas(d, ped, pl, est))
            roda(est, "plano", lambda: etapa_plano(d, ped, pl, est), forcar=True)
        elif acao == "videos": roda(est, "videos", lambda: etapa_videos(d, ped, pl, est, alvo), forcar=True)
        elif acao == "montar": roda(est, "montagem", lambda: montar(d, ped, pl, est), forcar=True)
        else: raise RuntimeError("ação inválida")
    except Exception as e:
        est.log("ERRO: " + str(e)); traceback.print_exc(); est.fim("erro", str(e)[:400]); sys.exit(1)
    if acao in ("preparar", "cena", "plano"):
        est.fim("aguardando", "confira as imagens e os prompts; depois gere os vídeos")
    elif acao == "videos":
        erros = [c for c in pl.p["clipes"] + pl.p.get("broll", []) if c.get("video_estado") == "erro"]
        est.fim("aguardando", f"{len(erros)} vídeo(s) com erro — refaça" if erros else "assista e aprove os vídeos um por um")
    else: est.fim("pronto", "vídeo montado")

if __name__ == "__main__":
    main()
