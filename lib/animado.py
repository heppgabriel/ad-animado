"""Ad Animado: referências + áudio (ou roteiro) -> SRT -> storyboard e prompts pelo Claude -> imagens na IA generativa
-> o Claude confere as imagens -> você aprova -> prompts de animação pelo Claude -> vídeos -> montagem na timeline do SRT.

É a mesma lógica da skill ad-animado-grok: um bloco do SRT = um beat, trilhos AVATAR / B-ROLL / PRODUTO alternados,
LOCKs por trilho (o personagem nunca muda de rosto/roupa, o pack nunca muda de formato), pares espelhados, MOTION LOCK,
start frame de cada clipe = a imagem aprovada do beat de mesmo número, ação legível nos primeiros 2 segundos e a trava
de embalagem nos beats de produto. Todos os prompts são escritos pelo Claude.

  .venv/bin/python lib/animado.py <pasta> imagens              áudio/SRT, storyboard, imagens e conferência
  .venv/bin/python lib/animado.py <pasta> refazer [3,7,12]     refaz as imagens marcadas (ou só as da lista) e confere de novo
  .venv/bin/python lib/animado.py <pasta> validar              confere todas as imagens de novo
  .venv/bin/python lib/animado.py <pasta> videos               prompts de animação, vídeos e montagem (depois da aprovação)
  .venv/bin/python lib/animado.py <pasta> refazer_video 3,7    refaz esses vídeos e monta de novo
  .venv/bin/python lib/animado.py <pasta> montar               só a montagem

Pasta: ~/Edições/.animados/<nome>/ — pedido.json (nome, modo vo|nativo, refs [{arquivo, tipo, nota}], audio, srt, roteiro,
estilo), estado.json (a tela acompanha), storyboard.json (locks + beats), legendas.srt, refs/, imagens/NN.jpg,
videos/NN.mp4, montagem/, final.mp4, timeline.json, custos.json."""
import os, re, sys, json, math, time, glob, shutil, signal, threading, traceback, subprocess
from concurrent.futures import ThreadPoolExecutor

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import comum, chaves, custos, gerativa, animado_regras

ETAPAS = [("audio", "Áudio e SRT"), ("referencia", "Vídeo de referência (estudo e semelhança com a copy)"), ("storyboard", "Storyboard e prompts de imagem (Claude)"), ("imagens", "Imagens (IA generativa)"),
          ("validacao", "Conferência das imagens (Claude)"), ("animacao", "Prompts de animação (Claude)"),
          ("videos", "Vídeos (IA generativa)"), ("montagem", "Montagem na timeline")]
FASE_IMG = ["audio", "referencia", "storyboard", "imagens", "validacao"]
FASE_VID = ["animacao", "videos", "montagem"]
PAR_MAX = 30                     # gerações ao mesmo tempo: o lote inteiro de uma vez (limite de segurança)
PAR_VALID = 4                    # conferências do Claude ao mesmo tempo (lotes de 6 imagens)
W, H, FPS = 1080, 1920, 30
TRILHOS = ("AVATAR", "BROLL", "PRODUTO")
TRAVA_PACK = "The packaging artwork and lettering stay perfectly stable and do not warp, shift or re-render."

def raiz(): return comum.RAIZ

# ---------------------------------------------------------------- estado
class Estado:
    def __init__(self, d, reiniciar=()):
        self.arq = os.path.join(d, "estado.json"); self.lock = threading.RLock()
        try: self.s = json.load(open(self.arq))
        except (OSError, ValueError): self.s = {}
        feitas = {e["id"]: e for e in self.s.get("etapas", [])}
        self.s["etapas"] = [feitas.get(i) or dict(id=i, nome=n, estado="pendente", detalhe="") for i, n in ETAPAS]
        for e in self.s["etapas"]:
            if e["id"] in reiniciar or e["estado"] in ("rodando", "erro"): e["estado"] = "pendente"
        self.s.update(rodando=True, status="rodando", pid=os.getpid(), mensagem="", fim=None)
        self.s.setdefault("log", []); self.salvar()
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
        with self.lock:
            tmp = f"{self.arq}.{threading.get_ident()}.tmp"; json.dump(self.s, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, self.arq)

def roda(est, i, fn, forcar=False):
    if est.et(i)["estado"] == "ok" and not forcar: return
    est.marca(i, "rodando", ""); est.log(f"— {dict(ETAPAS)[i]}")
    try: det = fn()
    except Exception as e: est.marca(i, "erro", str(e)[-400:]); raise
    est.marca(i, "ok", det or "")

class Board:
    """storyboard.json com trava: as gerações rodam em paralelo e cada uma grava o seu beat."""
    def __init__(self, d):
        self.arq = os.path.join(d, "storyboard.json"); self.lock = threading.RLock()
        try: self.b = json.load(open(self.arq))
        except (OSError, ValueError): self.b = dict(beats=[])
    def beat(self, n): return next(x for x in self.b["beats"] if x["n"] == n)
    def muda(self, n, **kw):
        with self.lock: self.beat(n).update(kw); self.salvar()
    def salvar(self):
        with self.lock:
            tmp = f"{self.arq}.{threading.get_ident()}.tmp"; json.dump(self.b, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, self.arq)

# ---------------------------------------------------------------- SRT
def tc(s):
    ms = int(round(max(0.0, s) * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

def seg(t):
    h, m, r = t.strip().replace(".", ",").split(":"); s, ms = (r.split(",") + ["0"])[:2]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000

def ler_srt(texto):
    """[(ini, fim, texto)] na ordem do arquivo. Aceita SRT com uma ou duas trilhas de texto por bloco."""
    blocos = []
    for b in re.split(r"\n\s*\n", texto.replace("\r", "").strip()):
        ls = [l for l in b.split("\n") if l.strip()]
        k = next((i for i, l in enumerate(ls) if "-->" in l), None)
        if k is None: continue
        a, z = ls[k].split("-->")
        blocos.append((seg(a), seg(z.split()[0]), " ".join(ls[k + 1:]).strip()))
    return blocos

def escrever_srt(blocos):
    return "\n".join(f"{i}\n{tc(a)} --> {tc(z)}\n{t}\n" for i, (a, z, t) in enumerate(blocos, 1))

RITMO_PADRAO = (2.0, 3.0)                                   # segundos por clipe (mín, máx) — o ritmo do ad escolhido na tela

def ritmo_de(ped):
    r = ped.get("ritmo") or {}
    try: mn, mx = float(r.get("min", RITMO_PADRAO[0])), float(r.get("max", RITMO_PADRAO[1]))
    except (TypeError, ValueError): mn, mx = RITMO_PADRAO
    mn = max(0.6, min(mn, 12.0)); mx = max(mn + 0.3, min(mx, 15.0))
    return mn, mx

def srt_de_palavras(palavras, ritmo=RITMO_PADRAO):
    """palavras: [{w, t, e}] do whisper. Um bloco = um clipe, no ritmo pedido (ex.: 2 a 3 s): quebra na pontuação e nas
    pausas quando já tem o mínimo, quebra no meio da frase quando passa do alvo, nunca passa do máximo (a não ser uma
    palavra só mais longa que isso). Bloco curto demais cola no vizinho."""
    mn, mx = ritmo; alvo = (mn + mx) / 2
    blocos, atual = [], []
    for i, w in enumerate(palavras):
        atual.append(w)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        dur = w["e"] - atual[0]["t"]
        pausa = (prox["t"] - w["e"]) if prox else 9
        fim_frase = bool(re.search(r"[.!?…]$", w["w"])); virgula = bool(re.search(r"[,;:]$", w["w"]))
        dur_com_prox = (prox["e"] - atual[0]["t"]) if prox else 0
        if (not prox or (pausa >= 0.45 and dur >= mn * 0.6) or (fim_frase and dur >= mn * 0.8) or (virgula and dur >= alvo * 0.85)
                or (dur >= alvo and pausa >= 0.1) or dur >= alvo * 1.15 or dur_com_prox > mx):
            blocos.append([atual[0]["t"], w["e"], " ".join(x["w"] for x in atual)]); atual = []
    i = 0
    while i < len(blocos):                                   # cola os curtos demais no vizinho mais próximo
        a, z, t = blocos[i]
        if z - a < mn * 0.6 and len(blocos) > 1:
            if i + 1 < len(blocos) and (i == 0 or blocos[i + 1][0] - z <= a - blocos[i - 1][1]) and blocos[i + 1][1] - a <= mx + 0.4:
                blocos[i + 1] = [a, blocos[i + 1][1], t + " " + blocos[i + 1][2]]; blocos.pop(i); continue
            if i > 0 and z - blocos[i - 1][0] <= mx + 0.4:
                blocos[i - 1] = [blocos[i - 1][0], z, blocos[i - 1][2] + " " + t]; blocos.pop(i); continue
        i += 1
    return [(round(a, 3), round(z, 3), re.sub(r"\s+", " ", t).strip()) for a, z, t in blocos]

def dividir_longos(blocos, ritmo=RITMO_PADRAO):
    """SRT enviado pronto: bloco mais longo que o ritmo pede vira vários clipes, repartindo o tempo pelas letras de cada
    pedaço (sem tempo por palavra, é a melhor estimativa). Bloco curto fica como está — o SRT é seu."""
    mn, mx = ritmo; alvo = (mn + mx) / 2; out = []
    for a, z, t in blocos:
        ws = t.split(); n = max(1, min(len(ws), round((z - a) / alvo))) if z - a > mx else 1
        if n == 1: out.append((a, z, t)); continue
        tam = math.ceil(len(ws) / n); partes = [" ".join(ws[k:k + tam]) for k in range(0, len(ws), tam)]
        letras = sum(len(p) for p in partes) or 1; ini = a
        for k, p in enumerate(partes):
            fim_p = z if k == len(partes) - 1 else ini + (z - a) * len(p) / letras
            out.append((round(ini, 3), round(fim_p, 3), p)); ini = fim_p
    return out

def srt_de_roteiro(texto, ritmo=RITMO_PADRAO, palavras_por_seg=2.6):
    """Sem áudio (fala nativa do personagem): quebra o roteiro em falas do tamanho do ritmo e estima a duração de cada uma."""
    mn, mx = ritmo; max_palavras = max(2, int(mx * palavras_por_seg))
    partes = []
    for frase in re.split(r"(?<=[.!?…])\s+|\n+", texto.strip()):
        frase = frase.strip()
        if not frase: continue
        ws = frase.split()
        while len(ws) > max_palavras:                        # fala longa demais para um clipe: quebra na vírgula ou em partes iguais
            tam = math.ceil(len(ws) / math.ceil(len(ws) / max_palavras))
            k = next((j + 1 for j in range(max(1, tam // 2), min(max_palavras, len(ws) - 1)) if re.search(r"[,;:]$", ws[j])), tam)
            partes.append(" ".join(ws[:k])); ws = ws[k:]
        partes.append(" ".join(ws))
    t, out = 0.0, []
    for p in partes:
        dur = max(mn, min(mx, len(p.split()) / palavras_por_seg + 0.3))
        out.append((round(t, 3), round(t + dur, 3), p)); t += dur + 0.15
    return out

# ---------------------------------------------------------------- mídia
def ffprobe_dur(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", arq], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return 0.0

def tem_audio(arq):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", arq], capture_output=True, text=True)
    return bool(r.stdout.strip())

def ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode: raise RuntimeError("ffmpeg: " + (r.stderr.strip()[-300:] or "falhou"))

def mini(d, arq, lado=512):
    """Cópia pequena em JPEG para mandar ao Claude (gasta menos tokens)."""
    pasta = os.path.join(d, ".mini"); os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, f"{lado}-{os.path.basename(os.path.dirname(arq))}-{os.path.splitext(os.path.basename(arq))[0]}.jpg")
    if not os.path.exists(dest) or os.path.getmtime(dest) < os.path.getmtime(arq):
        ff("-i", arq, "-vf", f"scale='min({lado},iw)':-2", "-frames:v", "1", "-q:v", "4", dest)
    return dest

def fim_da_fala(arq, dur):
    """Onde a fala acaba no clipe (fala nativa): o começo do último silêncio que vai até o fim."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", arq, "-af", "silencedetect=noise=-35dB:d=0.35", "-f", "null", "-"],
                       capture_output=True, text=True)
    ini = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    fim = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    if len(ini) > len(fim) and ini[-1] > 0.8: return min(dur, ini[-1] + 0.3)
    return dur

# ---------------------------------------------------------------- Claude
def S(): return {"type": "string"}
def I(): return {"type": "integer"}
def B(): return {"type": "boolean"}
def arr(x): return {"type": "array", "items": x}
def obj(**p): return {"type": "object", "additionalProperties": False, "required": list(p), "properties": p}

INSTR_STORY = """Você é o diretor de criação e de arte de anúncios animados verticais (9:16) de resposta direta, especialista
em prompts para IA de imagem. Recebe as imagens de referência (personagem, produto, estilo), o SRT da fala do anúncio e as
notas do pedido. Você escreve o STORYBOARD e os PROMPTS DE IMAGEM que a IA generativa vai usar. Quem gera é outra IA; você só
escreve. Nunca invente copy.

""" + animado_regras.REGRAS_AD + """

""" + animado_regras.GUIA_IMAGEM + """

REGRAS DO STORYBOARD (siga todas):
1. UM BLOCO DO SRT = UM BEAT. Não agrupe nem divida. A numeração bate 1:1 com o SRT, sem furo: beat n = bloco n.
2. papel de cada beat: HOOK (os que caem nos primeiros ~3 s), CORPO, PRODUTO (a fala cita o produto/oferta) ou CTA (o pedido
   de ação no fim). hook = o conceito visual do hook em uma frase (qual dor, qual metáfora, qual curiosidade abre).
3. Cada beat tem um TRILHO, e os trilhos se alternam (ad com o personagem em 100% das cenas fica monótono):
   AVATAR = o personagem vivendo o problema/alívio (dor emocional, rotina, antes/depois);
   BROLL = o mecanismo e as metáforas visuais (ciência, anatomia, moléculas, comparação, a dor como objeto), sem gente;
   PRODUTO = packshot, o produto digital concretizado e o CTA.
   Se não houver referência de produto, ainda marque os beats de produto como PRODUTO e diga nas notas que falta o packshot.
4. produto_detalhe (em inglês): a descrição exaustiva do produto para a IA — físico: formato, material, cores, tampa, posição
   de marca/nome, conteúdo; digital: como ele vira coisa concreta na tela (telas, módulos, entregáveis, bônus, resultado).
   Tire da referência, da copy e das notas; o que não souber, deixe de fora (não invente nome, preço nem número).
5. LOCKS (em inglês), um por trilho, descritos a partir do que você VÊ nas referências, com detalhe específico:
   avatar: rosto, formato do queixo, cabelo (cor e grisalho), sobrancelha, barba, tom de pele, olhos, porte, guarda-roupa por
     cenário (roupa padrão + roupa de dormir), estilo de render da referência (Pixar, 2D, realista, anime...) com os materiais
     e a luz que definem esse estilo, e um NEGATIVE. Ex.: "dark trimmed beard with grey on the chin", nunca só "beard".
   broll: paleta, tipo de câmera, material, luz, vinheta e um NEGATIVE — no mesmo estilo de render do anúncio.
   packshot: formato exato da embalagem (pouch/pote/frasco — errar isso é o erro mais comum), cores, onde fica cada elemento,
     e a regra: só a marca e o nome do produto ficam nítidos, todo o resto da arte sai pequeno ou desfocado (IA reproduz letra
     miúda como texto quebrado). NEGATIVE proibindo mudar o formato do pack.
   Lock que não se aplica (sem produto, por exemplo) = texto vazio.
6. regra_de_ouro: uma linha — o personagem nunca muda de rosto/roupa e o pack nunca muda de formato.
7. PROMPT de cada beat, em inglês, seguindo o guia acima (3 a 6 frases). NÃO repita o lock no prompt — o sistema acrescenta o
   lock do trilho sozinho. Beat que mistura personagem e produto diz de onde vem o quê. porque = em português, curto, por que
   essa imagem segura a atenção naquele ponto (o que ela tem de novo, estranho ou específico).
8. refs = os números das imagens de referência que a IA deve receber naquele beat (personagem nos AVATAR, produto nos
   PRODUTO, estilo quando ajudar; no máximo 5). BROLL sem gente pode ir sem referência ou só com a de estilo.
9. pares_espelhados: os beats de antes/depois que compartilham cenário e enquadramento (o mesmo sofá, o mesmo quarto) —
   são as viradas do anúncio. cenarios_repetidos: beats que acontecem no mesmo lugar, para manter a mesma casa.
10. notas: observações de produção (beats abaixo de 1 s, onde falta packshot real, etc.)."""

PAPEIS = ("HOOK", "CORPO", "PRODUTO", "CTA")
SCHEMA_STORY = obj(hook=S(), produto_detalhe=S(), locks=obj(avatar=S(), broll=S(), packshot=S()), regra_de_ouro=S(),
                   beats=arr(obj(n=I(), papel={"type": "string", "enum": list(PAPEIS)}, trilho={"type": "string", "enum": list(TRILHOS)},
                                 ideia=S(), porque=S(), prompt=S(), refs=arr(I()))),
                   pares_espelhados=arr(S()), cenarios_repetidos=arr(S()), notas=arr(S()))

CONTINUIDADES = ("CONTINUA", "ALVO", "TRANSICAO")
SCHEMA_STORY_CONT = obj(hook=S(), produto_detalhe=S(), locks=obj(avatar=S(), broll=S(), packshot=S()), regra_de_ouro=S(),
                        beats=arr(obj(n=I(), papel={"type": "string", "enum": list(PAPEIS)}, trilho={"type": "string", "enum": list(TRILHOS)},
                                      ideia=S(), porque=S(), prompt=S(), refs=arr(I()),
                                      continuidade={"type": "string", "enum": list(CONTINUIDADES)}, quadro_final=S(), transicao=S())),
                        pares_espelhados=arr(S()), cenarios_repetidos=arr(S()), notas=arr(S()))

REGRAS_CONTINUO = """AD CONTÍNUO (plano-sequência) — QUADROS-CHAVE E TRANSIÇÕES. O anúncio é uma tomada contínua: cada clipe começa no quadro
em que o anterior termina. Para bater certo com a copy, você define QUADROS-CHAVE (imagens geradas) onde for preciso:
- O beat 1 sempre tem o quadro INICIAL (o prompt dele) — é o hook, o quadro mais forte.
- continuidade de cada beat:
  CONTINUA = o clipe só continua do anterior (a câmera/ação segue); quadro_final e transicao vazios.
  ALVO = o clipe precisa TERMINAR numa composição exata (revelação do produto, a tela do CTA, o antes/depois, o rosto do personagem
    de novo nítido). quadro_final = o prompt (em inglês, no mesmo formato dos outros) da imagem em que o clipe termina.
    Use também um ALVO a cada ~4 beats para reancorar a identidade do personagem (em tomadas longas ela vai se perdendo).
  TRANSICAO = a copy muda de ambiente, de tempo ou de lugar de repente ("no dia seguinte", "no consultório", "dentro do intestino").
    Nunca corte seco: quadro_final = o prompt da imagem do NOVO ambiente (onde o clipe chega) e transicao = COMO a câmera leva de um
    ao outro, de um jeito filmável: whip pan, zoom que atravessa (entra pela boca/pela barriga/pela tela), match cut pela forma, morph
    de um objeto no outro, flash de luz, a câmera passa por trás de um objeto e sai no outro lugar. Escreva o tipo e o gesto.
- O quadro_final tem que bater com a copy daquele beat e com o que foi idealizado; o personagem e o pack seguem os locks.
- Nos beats CONTINUA, o prompt descreve o que deve estar na tela ao fim do trecho (guia da animação), não vira imagem."""

REVISAO_STORY = """Agora troque de chapéu: você é o diretor de criação mais exigente da agência e vai CRITICAR este storyboard
antes de gastar uma geração. Dê nota de 0 a 10:
- nota_hook: o 1º quadro para o dedo? A dor aparece de forma visual e concreta (metáfora física)? Abre curiosidade (a pessoa
  PRECISA ver o próximo corte)? Tem quebra de padrão? Sem produto, sem cena calma de apresentação?
- nota_corpo: cada beat traz informação visual nova? Alterna trilhos e escalas? Tem um novo gancho a cada ~5 s? O mecanismo
  virou imagem específica?
- nota_produto: o produto (físico ou digital) está descrito com o máximo de especificidade e detalhe? O digital virou coisa
  concreta (telas, entregáveis, resultado), nunca "celular com app"? O CTA é uma ação física específica?
Reescreva TODO beat que esteja abaixo do padrão (obrigatoriamente os do hook se nota_hook < 9, e os de produto se
nota_produto < 9). Devolva só os beats reescritos, com ideia, porque e prompt novos (mesmas regras de prompt). critica = o
que estava fraco, em português, curto."""

SCHEMA_REVISAO = obj(nota_hook=I(), nota_corpo=I(), nota_produto=I(), critica=S(),
                     beats=arr(obj(n=I(), ideia=S(), porque=S(), prompt=S())))

INSTR_VALID = """Você é o editor que confere as imagens geradas para um anúncio animado antes de virarem vídeo. Recebe as
referências, os LOCKS e, para cada beat, o papel (HOOK/CORPO/PRODUTO/CTA), o que o storyboard pede e a imagem que saiu. Para
cada beat decida ok ou refazer.

Refazer quando: o personagem mudou de rosto, cabelo, barba, porte ou roupa em relação à referência/lock; a embalagem mudou de
formato, cor ou posição dos elementos; apareceu texto, legenda, letra quebrada ou logo inventado; mão/corpo deformado; a
imagem não mostra o que o beat pede (ideia do storyboard); estilo de render diferente do resto; enquadramento que não
funciona em 9:16 ou sujeito no terço de baixo (onde entra a legenda); par espelhado que não bate com o seu par; pose parada
de catálogo, sem ação para o vídeo continuar.
Mais exigente ainda nos papéis que vendem: HOOK que não para o dedo (sem dor visual clara, sem curiosidade, cena calma ou
genérica) é refazer; PRODUTO genérico (pack sem detalhe, "celular com app" em produto digital, entregável sem nada
específico) é refazer. Não reprove por detalhe irrelevante.
Se for refazer: problemas = o que está errado, em português, curto; prompt_corrigido = o prompt inteiro reescrito em inglês
(mesmo formato do storyboard, sem o lock) corrigindo exatamente isso. Se estiver ok: problemas e prompt_corrigido vazios."""

SCHEMA_VALID = obj(avaliacoes=arr(obj(n=I(), ok=B(), problemas=S(), prompt_corrigido=S())), resumo=S())

INSTR_ANIM = """Você escreve os PROMPTS DE ANIMAÇÃO de um anúncio animado vertical, especialista no modelo de vídeo escolhido.
Cada clipe começa na imagem aprovada do beat de mesmo número (start frame) e vai para a timeline exatamente no tempo do SRT.

{guia_modelo}

""" + animado_regras.MOVIMENTO_POR_PAPEL + """

1. motion_lock (em inglês, um bloco só, vale para todos os clipes, curto): single continuous shot, no cuts, identity and
   wardrobe locked to the start frame, hands intact, no text/captions/logos. {audio_lock}
2. Para cada beat, prompt em inglês com SÓ MOVIMENTO no jeito que o modelo acima lê melhor: o que o sujeito faz, o que a câmera
   faz, o que a luz faz. Nada de redescrever o que já está na imagem. 1 a 3 frases.
3. A AÇÃO TEM QUE LER NOS PRIMEIROS 2 SEGUNDOS — o corte só mostra o começo do clipe. Em corte de até 1,5 s o movimento
   começa no primeiro quadro.
4. Pares espelhados animam do mesmo jeito (mesma câmera), para a virada ficar clara.
5. notas: beats curtos que precisam de ação imediata e qualquer cuidado de render."""

SCHEMA_ANIM = obj(motion_lock=S(), clipes=arr(obj(n=I(), prompt=S())), notas=arr(S()))

CUSTOS = {
    "zero": "100% sem API paga: o Claude pela sua assinatura; o Gemini só se a chave for do plano gratuito (senão o Claude estuda a referência)",
    "gemini": "Claude pela assinatura; pode usar a API do Gemini (paga) para assistir o vídeo de referência",
    "livre": "pode usar as APIs como estão em ⚙ (Claude e Gemini)",
}
def continuo(ped):
    """Ad contínuo: um plano-sequência — só o beat 1 vira imagem; cada vídeo começa no quadro em que o anterior é cortado."""
    return ped.get("montagem") == "continuo"

def custo_de(ped): return ped.get("custo") if ped.get("custo") in CUSTOS else "livre"

def claude(d, est, ped=None):
    import ia
    ped = ped if ped is not None else _pedido(d)
    def cobra(item): custos.registrar(d, servico="claude", etapa="animado", **item)
    return ia.Claude(log=est.log, ao_cobrar=cobra, assinatura=custo_de(ped) in ("zero", "gemini") or None)

def _pedido(d):
    try: return json.load(open(os.path.join(d, "pedido.json")))
    except (OSError, ValueError): return {}

def gemini_liberado(ped):
    """Pode chamar o Gemini neste ad? zero: só a chave do Google no plano gratuito (a OpenRouter é paga); gemini/livre: sim."""
    import gemini
    if not gemini.chave(): return False
    if custo_de(ped) != "zero": return True
    return bool(chaves.ler().get("gemini_gratis")) and gemini.provedor() == "google"

def blocos_refs(d, ped):
    import ia
    out = []
    for k, r in enumerate(ped["refs"], 1):
        out.append(ia.texto(f"Referência {k} — {r.get('tipo') or 'referência'}{(': ' + r['nota']) if r.get('nota') else ''}"))
        out.append(ia.imagem(mini(d, os.path.join(d, r["arquivo"]), 768)))
    return out

def lista_beats(beats):
    return "\n".join(f"{b['n']:02d} · {tc(b['ini'])} → {tc(b['fim'])} ({b['fim'] - b['ini']:.2f}s) · {b['texto']}" for b in beats)

# ---------------------------------------------------------------- etapas da fase de imagens
def etapa_audio(d, ped, bd, est):
    if ped.get("srt"):
        blocos = dividir_longos(ler_srt(open(os.path.join(d, ped["srt"]), encoding="utf-8-sig").read()), ritmo_de(ped)); origem = "SRT enviado"
    elif ped.get("audio"):
        import transcrever
        arq = os.path.join(d, ped["audio"]); saida = os.path.join(d, "transcricao.json")
        est.log("transcrevendo o áudio (whisper)…"); transcrever.transcrever(arq, saida)
        r = json.load(open(saida))["whisper"]
        palavras = [dict(w=w["word"].strip(), t=w["start"], e=w["end"]) for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]
        if not palavras: raise RuntimeError("não achei fala no áudio")
        blocos = srt_de_palavras(palavras, ritmo_de(ped)); origem = "gerado do áudio"
    elif (ped.get("roteiro") or "").strip():
        blocos = srt_de_roteiro(ped["roteiro"], ritmo_de(ped)); origem = "estimado pelo roteiro (fala nativa)"
    else:
        raise RuntimeError("mande o áudio, um SRT ou o roteiro")
    if not blocos: raise RuntimeError("o SRT ficou vazio")
    open(os.path.join(d, "legendas.srt"), "w").write(escrever_srt(blocos))
    total = ffprobe_dur(os.path.join(d, ped["audio"])) if ped.get("audio") else 0.0
    with bd.lock:
        bd.b = dict(beats=[dict(n=i, ini=a, fim=z, texto=t) for i, (a, z, t) in enumerate(blocos, 1)],
                    dur_audio=round(max(total, blocos[-1][1]), 3), aprovado=False)
        bd.salvar()
    mn, mx = ritmo_de(ped)
    return f"{len(blocos)} beats · {origem} · ritmo {mn:g}–{mx:g}s por clipe"

# ---------------------------------------------------------------- vídeo de referência (opcional)
NIVEIS_REF = {
    5: "5 — CÓPIA FIEL: a copy é praticamente a mesma (muda o produto ou detalhes). Replique a referência cena a cena: mesmo "
       "cenário, enquadramento, ação, câmera, estilo de render e ritmo. Só troque o que a copy nova muda (o produto, nomes, números).",
    4: "4 — MUITO PRÓXIMO: mesma estrutura e a maioria das cenas da referência, na mesma ordem; adapte só onde a fala nova diverge.",
    3: "3 — MESMA ESTRUTURA: o mesmo mecanismo de hook, o mesmo ritmo, estilo e tipos de cena; as cenas são reescritas para a copy "
       "nova, reaproveitando as da referência onde a fala combina.",
    2: "2 — SÓ A LINGUAGEM VISUAL: estilo de render, paleta, luz, energia de câmera e ritmo de cortes da referência; as cenas são "
       "todas criadas para a copy nova.",
    1: "1 — INSPIRAÇÃO SOLTA: a copy não tem nada a ver com a referência. Pegue só a vibe visual; todas as cenas (takes) são criadas "
       "do zero para a copy atual, fazendo sentido com ela.",
}

SCHEMA_REF_ANALISE = obj(resumo=S(), estilo_visual=S(), ritmo=S(), hook=S(),
                         cenas=arr(obj(ini={"type": "number"}, fim={"type": "number"}, o_que=S(), enquadramento=S(), camera=S(), fala=S())))
SCHEMA_REF_COMPARA = obj(nota=I(), justificativa=S(), mapa=arr(obj(n=I(), cena=I(), como=S())))

INSTR_REF = """Você estuda um VÍDEO DE REFERÊNCIA de anúncio para outro anúncio ser feito a partir dele. Descreva o que um diretor
precisa para reproduzi-lo: resumo, estilo visual (render, paleta, luz, texturas), ritmo (duração média dos cortes), como é o hook
(o que aparece nos primeiros ~3 s e por que prende) e as CENAS em ordem, sem buraco: ini/fim em segundos, o que acontece,
enquadramento, movimento de câmera e a fala daquele trecho (se houver)."""

INSTR_COMPARA = """Compare a COPY NOVA (SRT em beats) com o VÍDEO DE REFERÊNCIA (cenas + transcrição) e dê a nota de semelhança
de 1 a 5:
5 = praticamente a mesma copy (muda o produto, nomes, números) · 4 = mesma estrutura e boa parte das falas · 3 = mesma estrutura
de argumento com falas diferentes · 2 = assunto parecido, falas diferentes · 1 = nada a ver (a referência só serve pelo visual).
justificativa = em português, curta. mapa = para cada beat da copy nova (n), o número da cena da referência (índice começando em 1)
que melhor corresponde a ele pela fala/função (0 se nenhuma) e como aproveitar (curto)."""

def quadros_ref(d, arq, dur, n_max=24):
    """Quadros da referência para o Claude (e para a tela): um a cada ~dur/24 s, pequenos."""
    pasta = os.path.join(d, "referencia", "quadros"); shutil.rmtree(pasta, ignore_errors=True); os.makedirs(pasta)
    n = int(max(6, min(n_max, math.ceil(dur / 1.2)))); out = []
    for k in range(n):
        t = round(dur * (k + 0.5) / n, 2); dest = os.path.join(pasta, f"q{k + 1:02d}.jpg")
        ff("-ss", f"{t:.2f}", "-i", arq, "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", dest)
        out.append(dict(t=t, arquivo=os.path.relpath(dest, d)))
    return out

def etapa_referencia(d, ped, bd, est):
    if not ped.get("referencia_video"):
        with bd.lock: bd.b["referencia"] = None; bd.salvar()
        return "sem vídeo de referência"
    import ia
    arq = os.path.join(d, ped["referencia_video"]); dur = ffprobe_dur(arq)
    if dur <= 0: raise RuntimeError("não consegui ler o vídeo de referência")
    transcricao = ""
    if tem_audio(arq):
        try:
            import transcrever
            transcricao = transcrever.transcrever(arq, os.path.join(d, "referencia", "transcricao.json"))
        except Exception as e: est.log(f"referência: sem transcrição ({str(e)[:120]})")
    quadros = quadros_ref(d, arq, dur)
    analise = None
    import gemini
    if gemini_liberado(ped):                               # o Gemini assiste o vídeo inteiro (melhor para movimento e ritmo)
        try:
            def cobra(i): custos.registrar(d, servico="gemini", etapa="animado", **i)
            analise = gemini.pedir([gemini.video(arq), gemini.texto(INSTR_REF + (f"\nTranscrição: {transcricao[:4000]}" if transcricao else ""))],
                                   SCHEMA_REF_ANALISE, rotulo="Referência (Gemini)", ao_cobrar=cobra, log=est.log)
        except Exception as e: est.log(f"referência: o Gemini não assistiu ({str(e)[:160]}); o Claude olha pelos quadros")
    if analise is None:                                    # o Claude olha pelos quadros + transcrição
        conteudo = [ia.texto(f"Vídeo de referência de {dur:.1f} s. Quadros em ordem, com o segundo de cada um:")]
        for q in quadros: conteudo += [ia.texto(f"{q['t']:.1f} s"), ia.imagem(os.path.join(d, q["arquivo"]))]
        conteudo.append(ia.texto(f"Transcrição da fala: {transcricao[:4000] or '(sem fala)'}"))
        analise = claude(d, est, ped).conversa(INSTR_REF, esforco="medium").pedir(conteudo, SCHEMA_REF_ANALISE, rotulo="Referência (Claude)")
    cenas = analise.get("cenas") or []
    lista_cenas = "\n".join(f"{k}. {c['ini']:.1f}–{c['fim']:.1f}s · {c['o_que']} · {c.get('enquadramento', '')} · câmera: {c.get('camera', '')}"
                            + (f" · fala: {c['fala']}" if c.get("fala") else "") for k, c in enumerate(cenas, 1))
    C = claude(d, est, ped).conversa(INSTR_COMPARA, esforco="medium").pedir(
        f"VÍDEO DE REFERÊNCIA\nResumo: {analise.get('resumo')}\nHook: {analise.get('hook')}\nCenas:\n{lista_cenas}\n"
        f"Transcrição: {transcricao[:4000] or '(sem fala)'}\n\nCOPY NOVA ({len(bd.b['beats'])} beats):\n{lista_beats(bd.b['beats'])}",
        SCHEMA_REF_COMPARA, rotulo="Semelhança referência × copy")
    nota = max(1, min(5, int(C["nota"] or 1)))
    escolhido = ped.get("ref_nivel")
    nivel = int(escolhido) if str(escolhido) in ("1", "2", "3", "4", "5") else nota
    mapa = {int(m["n"]): dict(cena=int(m["cena"]), como=m["como"]) for m in C["mapa"] if 1 <= int(m.get("cena") or 0) <= len(cenas)}
    def quadro_da(c):                                      # o quadro mais perto do meio da cena
        meio = (c["ini"] + c["fim"]) / 2
        return min(quadros, key=lambda q: abs(q["t"] - meio))["arquivo"] if quadros else None
    with bd.lock:
        bd.b["referencia"] = dict(nota=nota, justificativa=C["justificativa"], nivel=nivel, automatico=nivel == nota and escolhido in (None, "", "auto"),
                                  analise=analise, transcricao=transcricao[:6000], quadros=quadros,
                                  cenas=[dict(c, quadro=quadro_da(c)) for c in cenas])
        for b in bd.b["beats"]:
            m = mapa.get(b["n"]); b["ref_cena"] = m["cena"] if m else 0; b["ref_como"] = m["como"] if m else ""
        bd.salvar()
    return f"semelhança {nota}/5 · usando o nível {nivel}{' (automático)' if escolhido in (None, '', 'auto') else ' (escolhido por você)'}"

def bloco_referencia(d, bd):
    """O que o storyboard recebe da referência: o nível e a regra dele, o estudo, o mapa beat -> cena e os quadros das cenas."""
    import ia
    R = bd.b.get("referencia")
    if not R: return []
    A = R["analise"]; cenas = R["cenas"]
    txt = (f"VÍDEO DE REFERÊNCIA — semelhança com a copy: {R['nota']}/5 ({R['justificativa']}). NÍVEL A USAR: "
           f"{NIVEIS_REF[R['nivel']]}\nAs regras do anúncio (hook, corpo, produto) continuam valendo; no nível 4 e 5, se a referência já "
           f"tem um hook forte, replique o hook dela.\nResumo: {A.get('resumo')}\nEstilo visual: {A.get('estilo_visual')}\n"
           f"Ritmo: {A.get('ritmo')}\nHook da referência: {A.get('hook')}\nCenas da referência:\n"
           + "\n".join(f"{k}. {c['ini']:.1f}–{c['fim']:.1f}s · {c['o_que']} · {c.get('enquadramento', '')} · câmera: {c.get('camera', '')}"
                        for k, c in enumerate(cenas, 1))
           + "\nCorrespondência sugerida (beat -> cena): " + "; ".join(f"{b['n']}->{b['ref_cena']} ({b.get('ref_como', '')})"
                                                                     for b in bd.b["beats"] if b.get("ref_cena")))
    out = [ia.texto(txt)]
    usadas = sorted({b["ref_cena"] for b in bd.b["beats"] if b.get("ref_cena")}) or list(range(1, len(cenas) + 1))
    for k in usadas[:16]:
        c = cenas[k - 1]
        if c.get("quadro"): out += [ia.texto(f"Quadro da cena {k} da referência:"), ia.imagem(os.path.join(d, c["quadro"]))]
    return out

def quadro_ref_do_beat(d, bd, b):
    """Nível 4 e 5: o quadro da cena correspondente vai junto como referência de composição para a IA de imagem."""
    R = bd.b.get("referencia")
    if not R or R.get("nivel", 0) < 4 or not b.get("ref_cena"): return None
    c = R["cenas"][b["ref_cena"] - 1] if b["ref_cena"] <= len(R["cenas"]) else None
    return os.path.join(d, c["quadro"]) if c and c.get("quadro") else None

def etapa_storyboard(d, ped, bd, est):
    import ia
    beats = bd.b["beats"]; n = len(beats)
    cl = claude(d, est, ped); cv = cl.conversa(INSTR_STORY, esforco="high")
    modo = ("O áudio é de LOCUÇÃO (VO): a fala vem da trilha de áudio, os clipes são mudos."
            if ped.get("modo") != "nativo" else
            "Anúncio de FALA DO PERSONAGEM: o personagem fala as linhas no próprio vídeo (áudio nativo). Beats AVATAR mostram "
            "o rosto de forma que a boca apareça.")
    mn, mx = ritmo_de(ped)
    conteudo = [ia.texto(f"Anúncio: {ped['nome']}\n{modo}\n{animado_regras.guia_imagem_modelo(ped.get('modelo_img') or gerativa.padrao_img())}\nRitmo escolhido pelo editor: clipes de {mn:g} a {mx:g} s — "
                         f"{'bem dinâmico: cada imagem precisa ler num relance, composição simples e sujeito grande' if mx <= 2.5 else 'dinâmico' if mx <= 3.5 else 'ritmo normal'}.\n"
                         f"Notas de estilo/pedido: {ped.get('estilo') or '(nenhuma)'}\n\nImagens de referência:")]
    conteudo += blocos_refs(d, ped)
    conteudo += bloco_referencia(d, bd)
    if continuo(ped):
        conteudo.append(ia.texto(REGRAS_CONTINUO + ("\nO modelo de vídeo escolhido aceita quadro final: o clipe vai terminar exatamente no "
                                 "quadro_final (a transição acontece dentro do clipe)." if gerativa.tem_quadro_final(ped.get("modelo_video")) else
                                 "\nO modelo de vídeo escolhido NÃO aceita quadro final: num ALVO o beat seguinte recomeça do quadro_final (reancora), "
                                 "e numa TRANSICAO o clipe já começa no quadro_final do novo ambiente — a transição que você escrever é feita na edição.")))
    conteudo.append(ia.texto(f"SRT ({n} blocos = {n} beats):\n{lista_beats(beats)}\n\n"
                             + (f"Copy/roteiro de apoio:\n{ped['roteiro'][:6000]}\n\n" if ped.get("roteiro") else "")
                             + f"Devolva exatamente {n} beats, n de 1 a {n}."))
    SCH = SCHEMA_STORY_CONT if continuo(ped) else SCHEMA_STORY
    R = cv.pedir(conteudo, SCH, rotulo="Storyboard")
    for tentativa in range(2):
        nums = sorted(b["n"] for b in R["beats"])
        if nums == list(range(1, n + 1)): break
        R = cv.pedir(f"A numeração não bate com o SRT: vieram {len(nums)} beats ({nums[:40]}). Refaça com exatamente {n} beats, "
                     f"n de 1 a {n}, um por bloco do SRT.", SCH, rotulo="Storyboard (correção)")
    else:
        raise RuntimeError("o Claude não conseguiu fechar um beat por bloco do SRT")
    por_n = {b["n"]: dict(b) for b in R["beats"]}; nrefs = len(ped["refs"])
    try:                                                    # o diretor de criação critica e reescreve o que está fraco
        V = cv.pedir(REVISAO_STORY, SCHEMA_REVISAO, rotulo="Revisão do hook e do produto")
        for x in V["beats"]:
            if x["n"] in por_n and x["prompt"].strip(): por_n[x["n"]].update(ideia=x["ideia"], porque=x["porque"], prompt=x["prompt"])
        revisao = dict(nota_hook=V["nota_hook"], nota_corpo=V["nota_corpo"], nota_produto=V["nota_produto"], critica=V["critica"],
                       reescritos=sorted(x["n"] for x in V["beats"] if x["n"] in por_n))
        est.log(f"revisão: hook {V['nota_hook']}/10 · corpo {V['nota_corpo']}/10 · produto {V['nota_produto']}/10 · reescritos {revisao['reescritos']}")
    except Exception as e:
        revisao = None; est.log(f"revisão do storyboard não rodou ({e}); seguindo com a primeira versão")
    with bd.lock:
        for b in bd.b["beats"]:
            s = por_n[b["n"]]
            cont = s.get("continuidade") if s.get("continuidade") in CONTINUIDADES and continuo(ped) else "CONTINUA"
            if cont != "CONTINUA" and not (s.get("quadro_final") or "").strip(): cont = "CONTINUA"
            b.update(continuidade=cont, quadro_final=(s.get("quadro_final") or "").strip() if cont != "CONTINUA" else "",
                     transicao=(s.get("transicao") or "").strip() if cont == "TRANSICAO" else "",
                     img_final=None, img_final_estado="pendente" if cont != "CONTINUA" else "", validacao_final=None, historico_final=[])
            b.update(trilho=s["trilho"], papel=s.get("papel") or "CORPO", ideia=s["ideia"], porque=s.get("porque", ""), prompt=s["prompt"].strip(),
                     refs=[r for r in s["refs"] if 1 <= r <= nrefs][:5], img=None, img_estado="pendente", validacao=None,
                     historico=[], prompt_editado=False)
        bd.b.update(hook=R.get("hook", ""), produto_detalhe=R.get("produto_detalhe", ""), revisao=revisao,
                    locks=R["locks"], regra_de_ouro=R["regra_de_ouro"], pares_espelhados=R["pares_espelhados"],
                    cenarios_repetidos=R["cenarios_repetidos"], notas=R["notas"])
        bd.salvar()
    cont = {t: sum(1 for b in bd.b["beats"] if b["trilho"] == t) for t in TRILHOS}
    return f"{n} beats · AVATAR {cont['AVATAR']} · B-ROLL {cont['BROLL']} · PRODUTO {cont['PRODUTO']}"

def prompt_imagem(ped, bd, b, com_quadro=False):
    locks = bd.b.get("locks") or {}
    lock = {"AVATAR": locks.get("avatar"), "BROLL": locks.get("broll"), "PRODUTO": locks.get("packshot")}.get(b["trilho"]) or ""
    partes = []
    if b.get("refs"):
        desc = "; ".join(f"image {i} = {ped['refs'][r - 1].get('tipo') or 'reference'}" + (f" ({ped['refs'][r - 1]['nota']})" if ped['refs'][r - 1].get('nota') else "")
                         for i, r in enumerate(b["refs"], 1))
        partes.append(f"Reference images: {desc}. Keep the character's identity and the product packaging exactly as in the references.")
    if com_quadro:
        partes.append(f"The LAST reference image is a frame from the reference ad: match its composition, framing, camera angle and scene "
                      f"layout{' as closely as possible' if (bd.b.get('referencia') or {}).get('nivel') == 5 else ''}, but with our character and product.")
    partes.append(b["prompt"])
    if b["trilho"] == "PRODUTO" and bd.b.get("produto_detalhe"): partes.append("Product details: " + bd.b["produto_detalhe"])
    if b["trilho"] == "PRODUTO" and locks.get("avatar") and "character" in b["prompt"].lower(): partes.append(locks["avatar"])
    if lock: partes.append(lock)
    partes.append("Vertical 9:16 frame. No captions, subtitles, on-screen text, watermarks or invented logos.")
    return "\n\n".join(p.strip() for p in partes if p and p.strip())

def gerar_imagens(d, ped, bd, est, nums, final=False):
    """final=True: o quadro FINAL do beat (ad contínuo: ALVO/TRANSICAO) em vez do inicial."""
    g = gerativa.gerador_imagem(ped.get("modelo_img")); os.makedirs(os.path.join(d, "imagens"), exist_ok=True)
    erros = []; fatal = threading.Event()
    K = dict(img="img_final", estado="img_final_estado", erro="img_final_erro", val="validacao_final", hist="historico_final") if final else \
        dict(img="img", estado="img_estado", erro="img_erro", val="validacao", hist="historico")
    def uma(n):
        if fatal.is_set(): return
        b = bd.beat(n); refs = [os.path.join(d, ped["refs"][r - 1]["arquivo"]) for r in b.get("refs") or []]
        qref = None if final else quadro_ref_do_beat(d, bd, b)
        if qref: refs = refs[:4] + [qref]
        bd.muda(n, **{K["estado"]: "gerando", K["erro"]: ""})
        try:
            alvo = dict(b, prompt=b["quadro_final"]) if final else b
            dados, usd, estimado = g.gerar_imagem(prompt_imagem(ped, bd, alvo, com_quadro=bool(qref)), refs)
            custos.registrar(d, servico="kie" if isinstance(g, gerativa.Kie) else "grok", etapa="imagem", rotulo=f"{'quadro final' if final else 'imagem'} do beat {n:02d}",
                             usd=usd, estimado=estimado, modelo=gerativa.nome_modelo(ped.get("modelo_img") or gerativa.padrao_img())
                             + (" · assinatura (cota semanal)" if getattr(g, "assinatura", False) else ""))
            dest = os.path.join("imagens", f"{n:02d}{'-final' if final else ''}-{int(time.time() * 1000) % 10**8}.jpg")
            open(os.path.join(d, dest), "wb").write(dados)
            hist = ([b[K["img"]]] if b.get(K["img"]) else []) + (b.get(K["hist"]) or [])
            bd.muda(n, **{K["img"]: dest, K["estado"]: "gerada", K["val"]: None, K["hist"]: hist[:6]}, video=None, video_estado="pendente")
            est.log(f"{'quadro final' if final else 'imagem'} {n:02d} pronto")
        except Exception as e:
            bd.muda(n, **{K["estado"]: "erro", K["erro"]: str(e)[:300]}); erros.append(n); est.log(f"{'quadro final' if final else 'imagem'} {n:02d}: {e}")
            if getattr(e, "fatal", False): fatal.set()
    with ThreadPoolExecutor(max(1, min(PAR_MAX, len(nums)))) as ex: list(ex.map(uma, nums))
    if fatal.is_set(): raise RuntimeError(next(b.get(K["erro"]) for b in bd.b["beats"] if b.get(K["erro"])))
    return erros

def validar(d, ped, bd, est, nums, final=False):
    import ia
    campo, est_k, val_k = ("img_final", "img_final_estado", "validacao_final") if final else ("img", "img_estado", "validacao")
    nums = [n for n in nums if bd.beat(n).get(campo)]
    if not nums: return 0
    locks = bd.b.get("locks") or {}
    cabeca = [ia.texto("Referências do anúncio:")] + blocos_refs(d, ped) + [ia.texto(
        f"Conceito do hook: {bd.b.get('hook') or '-'}\nProduto: {bd.b.get('produto_detalhe') or '-'}\nLOCKS\nAVATAR: {locks.get('avatar') or '-'}\nB-ROLL: {locks.get('broll') or '-'}\nPACKSHOT: {locks.get('packshot') or '-'}\n"
        f"Regra de ouro: {bd.b.get('regra_de_ouro') or '-'}\nPares espelhados: {'; '.join(bd.b.get('pares_espelhados') or []) or '-'}")]
    cl = claude(d, est, ped); ruins = [0]; trava = threading.Lock()
    def um_lote(lote):
        conteudo = list(cabeca)
        for n in lote:
            b = bd.beat(n)
            if final:
                conteudo.append(ia.texto(f"Beat {n:02d} — QUADRO FINAL ({b.get('continuidade')}{': transição ' + b['transicao'] if b.get('transicao') else ''}) "
                                         f"[{b['trilho']}] · fala: {b['texto']}\nIdeia: {b.get('ideia')}\nPrompt: {b['quadro_final']}"))
            else:
                conteudo.append(ia.texto(f"Beat {n:02d} [{b.get('papel') or 'CORPO'} · {b['trilho']}] · fala: {b['texto']}\nIdeia: {b.get('ideia')}\nPrompt: {b['prompt']}"))
            conteudo.append(ia.imagem(mini(d, os.path.join(d, b[campo]), 640)))
        conteudo.append(ia.texto(f"Avalie os beats {', '.join(str(n) for n in lote)} (um item por beat)."))
        R = cl.conversa(INSTR_VALID, esforco="medium").pedir(conteudo, SCHEMA_VALID, rotulo=f"Conferência {lote[0]:02d}–{lote[-1]:02d}")
        for a in R["avaliacoes"]:
            if a["n"] not in lote: continue
            bom = bool(a["ok"])
            bd.muda(a["n"], **{val_k: dict(ok=bom, problemas=a["problemas"], prompt_corrigido=a["prompt_corrigido"].strip()),
                               est_k: "ok" if bom else "refazer"})
            if not bom:
                with trava: ruins[0] += 1
                est.log(f"beat {a['n']:02d}{' (quadro final)' if final else ''} para refazer: {a['problemas']}")
    lotes = [nums[k:k + 6] for k in range(0, len(nums), 6)]
    with ThreadPoolExecutor(max(1, min(PAR_VALID, len(lotes)))) as ex: list(ex.map(um_lote, lotes))
    return ruins[0]

def fase_imagens(d, ped, bd, est):
    roda(est, "audio", lambda: etapa_audio(d, ped, bd, est))
    roda(est, "referencia", lambda: etapa_referencia(d, ped, bd, est))
    roda(est, "storyboard", lambda: etapa_storyboard(d, ped, bd, est))
    def imgs():
        alvo = bd.b["beats"][:1] if continuo(ped) else bd.b["beats"]      # contínuo: só o primeiro quadro é imagem
        faltam = [b["n"] for b in alvo if not b.get("img") or b.get("img_estado") == "erro"]
        erros = gerar_imagens(d, ped, bd, est, faltam)
        if faltam and len(erros) == len(faltam) and not any(b.get("img") for b in alvo): raise RuntimeError("nenhuma imagem foi gerada")
        finais = [b["n"] for b in bd.b["beats"] if b.get("quadro_final") and (not b.get("img_final") or b.get("img_final_estado") == "erro")]
        erros_f = gerar_imagens(d, ped, bd, est, finais, final=True) if finais else []
        return (f"{len(alvo) - len(erros)} de {len(alvo)} imagens" + (f" · {len(erros)} com erro" if erros else "")
                + (f" · {len(finais) - len(erros_f)} quadro(s)-chave de transição/alvo" if finais else "")
                + (" · os outros beats nascem do vídeo anterior" if continuo(ped) else ""))
    roda(est, "imagens", imgs)
    def conf():
        validar(d, ped, bd, est, [b["n"] for b in bd.b["beats"] if b.get("img") and not b.get("validacao")])
        validar(d, ped, bd, est, [b["n"] for b in bd.b["beats"] if b.get("img_final") and not b.get("validacao_final")], final=True)
        tot = len(para_refazer(bd))
        return "todas aprovadas pelo Claude" if not tot else f"{tot} para refazer"
    roda(est, "validacao", conf, forcar=True)

def para_refazer(bd):
    """Imagens marcadas para refazer: ("img"|"final", n)."""
    return ([("img", b["n"]) for b in bd.b["beats"] if b.get("img_estado") in ("refazer", "erro")]
            + [("final", b["n"]) for b in bd.b["beats"] if b.get("quadro_final") and b.get("img_final_estado") in ("refazer", "erro")])

def fase_refazer(d, ped, bd, est, nums=None):
    """nums: imagens iniciais a refazer (ou as marcadas); os quadros finais marcados vão junto."""
    if not nums: nums = [n for t, n in para_refazer(bd) if t == "img"]
    finais = [n for t, n in para_refazer(bd) if t == "final"]
    if not nums and not finais: return
    with bd.lock:
        for n in nums:
            b = bd.beat(n); v = b.get("validacao") or {}
            if not b.get("prompt_editado") and v.get("prompt_corrigido"): b["prompt"] = v["prompt_corrigido"]
            b["prompt_editado"] = False
        for n in finais:
            b = bd.beat(n); v = b.get("validacao_final") or {}
            if not b.get("final_editado") and v.get("prompt_corrigido"): b["quadro_final"] = v["prompt_corrigido"]
            b["final_editado"] = False
        bd.salvar()
    est.marca("imagens", "rodando", f"refazendo {len(nums) + len(finais)}")
    est.log(f"— refazendo {', '.join([f'{n:02d}' for n in nums] + [f'{n:02d} (final)' for n in finais])}")
    erros = gerar_imagens(d, ped, bd, est, nums) if nums else []
    erros_f = gerar_imagens(d, ped, bd, est, finais, final=True) if finais else []
    est.marca("imagens", "ok", f"refeitas {len(nums) + len(finais) - len(erros) - len(erros_f)} de {len(nums) + len(finais)}")
    def conf():
        validar(d, ped, bd, est, [n for n in nums if n not in erros])
        validar(d, ped, bd, est, [n for n in finais if n not in erros_f], final=True)
        tot = len(para_refazer(bd))
        return "todas aprovadas pelo Claude" if not tot else f"{tot} para refazer"
    roda(est, "validacao", conf, forcar=True)

# ---------------------------------------------------------------- fase de vídeos
def slots(bd, modo):
    """Tempo de cada beat na timeline (VO): do início dele ao início do próximo — sem buraco; o primeiro começa no 0 e o
    último vai até o fim do áudio."""
    bs = bd.b["beats"]; total = bd.b.get("dur_audio") or bs[-1]["fim"]; out = {}
    for i, b in enumerate(bs):
        a = 0.0 if i == 0 else b["ini"]; z = bs[i + 1]["ini"] if i + 1 < len(bs) else max(total, b["fim"])
        out[b["n"]] = (a, z)
    return out

def render_seg(ped, dur_slot, b):
    """Tamanho pedido à IA: o corte + folga, ajustado ao que o modelo escolhido consegue gerar (alguns só fazem 5/10 s)."""
    alvo = max(3, math.ceil(b["fim"] - b["ini"] + 1.2)) if ped.get("modo") == "nativo" else max(2, math.ceil(dur_slot + 0.4))
    return gerativa.ajustar_duracao(ped.get("modelo_video"), alvo)

def etapa_animacao(d, ped, bd, est):
    import ia
    nativo = ped.get("modo") == "nativo"
    audio_lock = ("Native audio ON: the character's voice speaks the line of each clip in Brazilian Portuguese with lip sync; no music."
                  if nativo else "Audio off (the voice-over comes from the edit).")
    mn, mx = ritmo_de(ped)
    sl = slots(bd, ped.get("modo")); conteudo = [ia.texto(
        f"Anúncio: {ped['nome']}\nConceito do hook: {bd.b.get('hook') or '-'}\nRitmo do ad: cortes de {mn:g} a {mx:g} s — a ação principal precisa acontecer já no começo do clipe.\nPares espelhados: {'; '.join(bd.b.get('pares_espelhados') or []) or '-'}\n"
        f"Lock do personagem: {(bd.b.get('locks') or {}).get('avatar') or '-'}\nBeats (imagem aprovada de cada um logo abaixo):")]
    for b in bd.b["beats"]:
        a, z = sl[b["n"]]
        R_ = bd.b.get("referencia")
        if R_ and R_.get("nivel", 0) >= 3 and b.get("ref_cena") and b["ref_cena"] <= len(R_["cenas"]):
            c_ = R_["cenas"][b["ref_cena"] - 1]
            conteudo.append(ia.texto(f"(referência nível {R_['nivel']}: na cena correspondente a câmera faz \"{c_.get('camera', '')}\" — {c_.get('o_que', '')})"))
        if continuo(ped):
            ch = {"ALVO": f"TERMINA no quadro-chave (imagem abaixo): {b.get('quadro_final', '')[:250]}",
                  "TRANSICAO": f"TRANSIÇÃO para o novo ambiente ({b.get('transicao', '')}) — quadro-chave abaixo: {b.get('quadro_final', '')[:250]}"}.get(b.get("continuidade"), "")
            conteudo.append(ia.texto(f"(beat {b['n']:02d}{' começa onde o ' + format(b['n'] - 1, '02d') + ' termina' if b['n'] > 1 else ' começa na imagem inicial'}"
                                     f"{'; ' + ch if ch else '; deve chegar em: ' + b.get('prompt', '')[:250]})"))
            if b.get("img_final"): conteudo.append(ia.imagem(mini(d, os.path.join(d, b["img_final"]), 384)))
        conteudo.append(ia.texto(f"Beat {b['n']:02d} [{b.get('papel') or 'CORPO'} · {b['trilho']}] · corte {z - a:.2f}s · render {render_seg(ped, z - a, b)}s · fala: {b['texto']}\nIdeia: {b.get('ideia')}"))
        if b.get("img"): conteudo.append(ia.imagem(mini(d, os.path.join(d, b["img"]), 384)))
    conteudo.append(ia.texto(f"Escreva o motion_lock e um clipe por beat (n de 1 a {len(bd.b['beats'])})."))
    if continuo(ped):
        conteudo.insert(1, ia.texto(
            "AD CONTÍNUO: cada clipe começa EXATAMENTE no quadro em que o anterior foi cortado (não existe imagem nova entre eles). "
            "Escreva cada prompt como a continuação física do anterior: parta do estado em que o clipe anterior termina e leve a cena "
            "até o que o beat pede (o 'Ideia' dele), com um movimento de câmera contínuo que emende no seguinte. Sem cortes, sem trocar de "
            "lugar do nada, sem voltar atrás. Como só o beat 1 tem imagem, descreva no prompt o que precisa aparecer. "
            + ("O modelo aceita quadro final: nos beats ALVO/TRANSICAO o clipe termina exatamente no quadro-chave — descreva o movimento "
               "(e a transição) que leva até ele." if gerativa.tem_quadro_final(ped.get("modelo_video")) else
               "O modelo NÃO aceita quadro final: num beat TRANSICAO o clipe já COMEÇA no quadro-chave do novo ambiente (a transição é feita "
               "na edição), então descreva o movimento dentro do novo ambiente; num ALVO, descreva o movimento que leva perto do quadro-chave "
               "(o beat seguinte recomeça dele).")))
    guia = f"MODELO DE VÍDEO: {gerativa.nome_modelo(ped.get('modelo_video') or gerativa.padrao_video())}\n" + animado_regras.guia_video(ped.get("modelo_video") or gerativa.padrao_video())
    R = claude(d, est, ped).conversa(INSTR_ANIM.replace("{audio_lock}", audio_lock).replace("{guia_modelo}", guia), esforco="high").pedir(conteudo, SCHEMA_ANIM, rotulo="Prompts de animação")
    por_n = {c["n"]: c["prompt"].strip() for c in R["clipes"]}
    faltam = [b["n"] for b in bd.b["beats"] if b["n"] not in por_n]
    if faltam: raise RuntimeError(f"o Claude não escreveu a animação dos beats {faltam}")
    with bd.lock:
        bd.b.update(motion_lock=R["motion_lock"].strip(), notas_animacao=R["notas"])
        for b in bd.b["beats"]:
            a, z = sl[b["n"]]
            b.update(anim=por_n[b["n"]], corte=round(z - a, 3), render=render_seg(ped, z - a, b))
            if b.get("video_estado") != "ok": b.update(video=None, video_estado="pendente", video_erro="")
        bd.salvar()
    return f"{len(por_n)} clipes · {sum(b['render'] for b in bd.b['beats'])}s de render"

def prompt_video(ped, bd, b):
    """O prompt final do clipe, no formato que o modelo de vídeo escolhido lê melhor (animado_regras.GUIAS_VIDEO)."""
    fam = animado_regras.familia(ped.get("modelo_video") or gerativa.padrao_video()); partes = [b["anim"]]
    if ped.get("modo") == "nativo":
        if fam == "veo":
            partes.append(f'The character says, in Brazilian Portuguese: "{b["texto"]}"' if b["trilho"] == "AVATAR"
                          else f'Voice-over in Brazilian Portuguese: "{b["texto"]}"')
            partes.append("Ambient noise: soft room tone.")
        else:
            partes.append(f'The character says this line out loud in Brazilian Portuguese, natural lip sync, same voice as the other clips: "{b["texto"]}"'
                          if b["trilho"] == "AVATAR" else f'A voice-over in Brazilian Portuguese says: "{b["texto"]}"')
            if fam == "grok": partes.append("No music.")
    if b.get("_usa_final"):
        partes.append("The clip must end exactly on the provided final frame" + (f", getting there with this transition: {b['transicao']}." if b.get("transicao") else "."))
    if b["trilho"] == "PRODUTO": partes.append(TRAVA_PACK)
    partes.append(bd.b.get("motion_lock") or "")
    if fam == "seedance": partes.append("Preserve the composition and colors of the start frame; avoid jitter and bent limbs.")
    return "\n\n".join(p.strip() for p in partes if p and p.strip())

def quadro_de_corte(d, ped, bd, n):
    """Ad contínuo: o quadro em que o vídeo do beat n é cortado na timeline (é daí que o próximo continua)."""
    b = bd.beat(n); src = os.path.join(d, b["video"]); dur = ffprobe_dur(src)
    if ped.get("modo") == "nativo": t = fim_da_fala(src, dur)
    else:
        a, z = slots(bd, ped.get("modo"))[n]; t = min(z - a, dur)
    dest = os.path.join("imagens", f"continua-{n + 1:02d}-{int(time.time() * 1000) % 10**8}.jpg")
    ff("-ss", f"{max(0.0, t - 1.5 / FPS):.3f}", "-i", src, "-frames:v", "1", "-q:v", "2", os.path.join(d, dest))
    return dest

def gerar_videos(d, ped, bd, est, nums):
    g = gerativa.gerador_video(ped.get("modelo_video")); os.makedirs(os.path.join(d, "videos"), exist_ok=True)
    erros = []; fatal = threading.Event()
    def um(n, inicio, final=None):
        if fatal.is_set(): return
        b = bd.beat(n); bd.muda(n, video_estado="gerando", video_erro="")
        try:
            pv = prompt_video(ped, bd, dict(b, _usa_final=bool(final)))
            extra = {"final": os.path.join(d, final)} if final else {}
            dados, usd, estimado = g.gerar_video(pv, os.path.join(d, inicio), b["render"], audio=ped.get("modo") == "nativo", **extra)
            custos.registrar(d, servico="kie" if isinstance(g, gerativa.Kie) else "grok", etapa="video", rotulo=f"vídeo do beat {n:02d} ({b['render']}s)",
                             usd=usd, estimado=estimado, modelo=gerativa.nome_modelo(ped.get("modelo_video") or gerativa.padrao_video())
                             + (" · assinatura (cota semanal)" if getattr(g, "assinatura", False) else ""))
            dest = os.path.join("videos", f"{n:02d}-{int(time.time() * 1000) % 10**8}.mp4")
            open(os.path.join(d, dest), "wb").write(dados)
            bd.muda(n, video=dest, video_estado="ok"); est.log(f"vídeo {n:02d} pronto")
        except Exception as e:
            bd.muda(n, video_estado="erro", video_erro=str(e)[:300]); erros.append(n); est.log(f"vídeo {n:02d}: {e}")
            if getattr(e, "fatal", False): fatal.set()
    if continuo(ped):
        # Em sequência: cada vídeo precisa do anterior pronto para saber de onde começa.
        primeiro = bd.b["beats"][0]["n"]; suporta = gerativa.tem_quadro_final(ped.get("modelo_video"))
        for n in sorted(nums):
            if fatal.is_set(): break
            b = bd.beat(n); entrada = ""
            if n == primeiro: inicio = b["img"]
            else:
                ant = bd.beat(n - 1)
                if ant.get("video_estado") != "ok" or not ant.get("video"):
                    bd.muda(n, video_estado="erro", video_erro=f"o vídeo do beat {n - 1:02d} não ficou pronto; este continua dele"); erros.append(n); continue
                if not suporta and ant.get("continuidade") == "ALVO" and ant.get("img_final"):
                    inicio = ant["img_final"]; entrada = "dissolve"          # reancora no quadro-chave (sem quadro final no modelo)
                else: inicio = quadro_de_corte(d, ped, bd, n - 1)
            final = None
            if b.get("img_final"):
                if suporta: final = b["img_final"]                         # o clipe termina no quadro-chave (transição dentro do clipe)
                elif b.get("continuidade") == "TRANSICAO":
                    inicio = b["img_final"]; entrada = tipo_transicao(b.get("transicao"))   # começa no novo ambiente; a edição faz a transição
            bd.muda(n, frame_inicial=inicio if n != primeiro else None, transicao_entrada=entrada)
            um(n, inicio, final)
    else:
        with ThreadPoolExecutor(max(1, min(PAR_MAX, len(nums)))) as ex: list(ex.map(lambda n: um(n, bd.beat(n)["img"]), nums))
    if fatal.is_set(): raise RuntimeError(next(b.get("video_erro") for b in bd.b["beats"] if b.get("video_erro")))
    return erros

def tipo_transicao(txt):
    """O texto da transição (do storyboard) -> uma transição do ffmpeg (xfade) para quando o modelo não faz a transição sozinho."""
    t = (txt or "").lower()
    for chaves_, x in ((("whip", "chicote", "pan rápido", "swish"), "smoothleft"), (("zoom", "atravessa", "through", "entra"), "zoomin"),
                       (("flash", "luz", "light"), "fadewhite"), (("morph", "match", "forma", "transforma"), "dissolve"),
                       (("circle", "íris", "iris", "círculo"), "circleopen"), (("wipe", "passa por trás", "behind"), "wipeleft")):
        if any(c in t for c in chaves_): return x
    return "fade"

VF = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}"

def montar(d, ped, bd, est):
    """Cada beat entra no seu tempo do SRT. VO: o clipe é cortado no tamanho do beat (e congela o último quadro se for
    mais curto); beat sem vídeo entra com a imagem parada, para a timeline nunca ter buraco. Fala nativa: cada clipe vai
    até o fim da própria fala, com o áudio dele."""
    m = os.path.join(d, "montagem"); shutil.rmtree(m, ignore_errors=True); os.makedirs(m)
    nativo = ped.get("modo") == "nativo"; sl = slots(bd, ped.get("modo")); lista, linha, t = [], [], 0.0
    for b in bd.b["beats"]:
        src = os.path.join(d, b["video"]) if b.get("video") and b.get("video_estado") == "ok" else None
        img = os.path.join(d, b.get("img") or b.get("frame_inicial")) if (b.get("img") or b.get("frame_inicial")) else None
        if not src and not img: raise RuntimeError(f"o beat {b['n']:02d} não tem imagem nem vídeo")
        if nativo:
            dur = fim_da_fala(src, ffprobe_dur(src)) if src else max(1.2, b["fim"] - b["ini"])
        else:
            a, z = sl[b["n"]]; dur = z - a
        dur = max(0.2, dur); out = os.path.join(m, f"{b['n']:03d}.mp4")
        vcod = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS)]
        acod = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
        if src and nativo and tem_audio(src):
            ff("-i", src, "-t", f"{dur:.3f}", "-vf", VF + ",tpad=stop_mode=clone:stop_duration=15", "-af", "apad", *vcod, *acod, out)
        elif src:
            entrada = ["-i", src, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"] if nativo else ["-i", src]
            ff(*entrada, "-t", f"{dur:.3f}", "-map", "0:v", *(["-map", "1:a"] if nativo else []),
               "-vf", VF + ",tpad=stop_mode=clone:stop_duration=15", *vcod, *(acod if nativo else ["-an"]), out)
        else:
            entrada = ["-loop", "1", "-i", img] + (["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"] if nativo else [])
            ff(*entrada, "-t", f"{dur:.3f}", "-map", "0:v", *(["-map", "1:a"] if nativo else []), "-vf", VF, *vcod,
               *(acod if nativo else ["-an"]), out)
        lista.append(out)
        linha.append(dict(n=b["n"], ini=round(t, 3), fim=round(t + dur, 3), trilho=b.get("trilho"), texto=b["texto"],
                          origem="video" if src else "imagem"))
        t += dur
    if not nativo:                                         # transições feitas na edição (modelo sem quadro final): funde o par
        D = 0.35; grupos = []; fundiu = False
        for b, seg in zip(bd.b["beats"], lista):
            tr = b.get("transicao_entrada") if b.get("video_estado") == "ok" else ""
            if tr and grupos and ffprobe_dur(grupos[-1]) > D + 0.1 and ffprobe_dur(seg) > D + 0.1:
                a_ = grupos[-1]; la = ffprobe_dur(a_); out = os.path.join(m, f"x{b['n']:03d}.mp4")
                ff("-i", a_, "-i", seg, "-filter_complex",
                   f"[0:v]tpad=stop_mode=clone:stop_duration={D}[a];[a][1:v]xfade=transition={tr}:duration={D}:offset={la:.3f},format=yuv420p[v]",
                   "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS), "-an", out)
                grupos[-1] = out; fundiu = True
            else: grupos.append(seg)
        lista = grupos
    txt = os.path.join(m, "lista.txt"); open(txt, "w").write("".join(f"file '{os.path.basename(x)}'\n" for x in lista))
    corrido = os.path.join(m, "corrido.mp4")
    if not nativo and fundiu:                              # pedaços fundidos: recodifica para a emenda ficar limpa
        ff("-f", "concat", "-safe", "0", "-i", txt, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", corrido)
    else: ff("-f", "concat", "-safe", "0", "-i", txt, "-c", "copy", corrido)
    final = os.path.join(d, "final.mp4")
    if nativo:
        shutil.copy2(corrido, final)
        blocos = [(x["ini"], x["fim"], x["texto"]) for x in linha]
    else:
        ff("-i", corrido, "-i", os.path.join(d, ped["audio"]), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "192k", "-t", f"{t:.3f}", "-movflags", "+faststart", final)
        blocos = [(b["ini"], b["fim"], b["texto"]) for b in bd.b["beats"]]
    open(os.path.join(d, "legendas_final.srt"), "w").write(escrever_srt(blocos))
    json.dump(dict(dur=round(t, 3), clipes=linha, fps=FPS, w=W, h=H), open(os.path.join(d, "timeline.json"), "w"), ensure_ascii=False, indent=1)
    parados = [x["n"] for x in linha if x["origem"] == "imagem"]
    return f"{len(linha)} clipes · {t:.1f}s" + (f" · {len(parados)} ainda com imagem parada" if parados else "")

def fase_videos(d, ped, bd, est, nums=None):
    if not bd.b.get("aprovado"): raise RuntimeError("aprove as imagens antes de gerar os vídeos")
    if ped.get("modo") == "nativo" and not gerativa.tem_audio_nativo(ped.get("modelo_video")):
        raise RuntimeError(f"{gerativa.nome_modelo(ped.get('modelo_video'))} não gera áudio: na fala nativa escolha um modelo de vídeo com áudio")
    with bd.lock:                                          # troca de modelo de vídeo depois dos prompts: recalcula o tamanho pedido
        sl = slots(bd, ped.get("modo"))
        for b in bd.b["beats"]:
            if b.get("anim") and b.get("video_estado") != "ok": b["render"] = render_seg(ped, sl[b["n"]][1] - sl[b["n"]][0], b)
        bd.salvar()
    faltam = [b["n"] for b in (bd.b["beats"][:1] if continuo(ped) else bd.b["beats"]) if not b.get("img")] + \
             [b["n"] for b in bd.b["beats"] if b.get("quadro_final") and not b.get("img_final")]
    if faltam: raise RuntimeError(f"os beats {faltam} ainda não têm imagem")
    if nums and continuo(ped):                              # refazer um vídeo do meio muda o começo de todos os seguintes
        nums = [b["n"] for b in bd.b["beats"] if b["n"] >= min(nums)]
    if nums is None:
        roda(est, "animacao", lambda: etapa_animacao(d, ped, bd, est), forcar=any(not b.get("anim") for b in bd.b["beats"]))
        def vids():
            todos = [b["n"] for b in bd.b["beats"] if b.get("video_estado") != "ok"]
            erros = gerar_videos(d, ped, bd, est, todos)
            if erros and len(erros) == len(todos): raise RuntimeError("nenhum vídeo foi gerado")
            return f"{len(bd.b['beats']) - len(erros)} de {len(bd.b['beats'])} vídeos" + (f" · {len(erros)} com erro" if erros else "")
        roda(est, "videos", vids, forcar=True)
    else:
        est.marca("videos", "rodando", f"refazendo {len(nums)}"); est.log(f"— refazendo os vídeos {', '.join(f'{n:02d}' for n in nums)}")
        erros = gerar_videos(d, ped, bd, est, nums)
        est.marca("videos", "ok", f"refeitos {len(nums) - len(erros)} de {len(nums)}")
    roda(est, "montagem", lambda: montar(d, ped, bd, est), forcar=True)

# ---------------------------------------------------------------- principal
def main():
    d = os.path.abspath(sys.argv[1]); acao = sys.argv[2] if len(sys.argv) > 2 else "imagens"
    nums = [int(x) for x in re.findall(r"\d+", sys.argv[3])] if len(sys.argv) > 3 else None
    ped = json.load(open(os.path.join(d, "pedido.json"))); bd = Board(d)
    reiniciar = {"refazer": (), "validar": ("validacao",), "videos": (), "refazer_video": (), "montar": ("montagem",)}.get(acao, ())
    est = Estado(d, reiniciar); est.s["acao"] = acao; est.salvar()
    def parar(*_):
        est.log("cancelado"); est.fim("erro", "cancelado — dá para retomar"); os._exit(1)
    signal.signal(signal.SIGTERM, parar)
    try:
        if acao == "imagens": fase_imagens(d, ped, bd, est)
        elif acao == "refazer": fase_refazer(d, ped, bd, est, nums)
        elif acao == "validar":
            roda(est, "validacao", lambda: f"{validar(d, ped, bd, est, [b['n'] for b in bd.b['beats'] if b.get('img')])} para refazer", forcar=True)
        elif acao == "videos": fase_videos(d, ped, bd, est)
        elif acao == "refazer_video": fase_videos(d, ped, bd, est, nums or [b["n"] for b in bd.b["beats"] if b.get("video_estado") == "erro"])
        elif acao == "montar": roda(est, "montagem", lambda: montar(d, ped, bd, est), forcar=True)
        else: raise RuntimeError("ação inválida")
    except Exception as e:
        est.log("ERRO: " + str(e)); traceback.print_exc(); est.fim("erro", str(e)[:400]); sys.exit(1)
    if acao in ("imagens", "refazer", "validar"):
        faltam = sorted({n for _, n in para_refazer(bd)})
        est.fim("aguardando", f"{len(faltam)} imagem(ns) para refazer: {', '.join(f'{n:02d}' for n in faltam)}" if faltam
                else "o Claude aprovou todas as imagens — confira e aprove para gerar os vídeos")
    else:
        erros = [b["n"] for b in bd.b["beats"] if b.get("video_estado") == "erro"]
        est.fim("pronto", f"montado, mas {len(erros)} beat(s) entraram com a imagem parada: {', '.join(f'{n:02d}' for n in erros)}" if erros
                else "vídeo montado na timeline")

if __name__ == "__main__":
    main()
