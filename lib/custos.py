"""Custo de cada vídeo: <projeto>/custos.json, um item por cobrança.

  Claude: tokens exatos que a API devolve em cada resposta (usage) × preço de tabela do modelo (PRECOS).
  Gemini: tokens que a API devolve em cada vídeo assistido (usage) × preço de tabela (gemini.PRECOS).
  Apify:  o valor que a própria Apify registrou para a corrida (usageTotalUsd; o ator cobra por resultado).
  Whisper, recorte da pessoa e render rodam no Mac: custo zero.

  python3 custos.py nota <pasta do projeto> "texto"      observação que aparece na aba Custo
Só usa a biblioteca padrão (roda no Python do sistema e no da .venv)."""
import os, re, sys, json, time, urllib.request, threading
from datetime import datetime

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
_lock = threading.Lock()

# Preço de tabela do Claude, US$ por milhão de tokens (entrada, saída). Cache de 5 min: escrita 1,25x a entrada,
# leitura 0,1x. Os "tokens de pensamento" já vêm dentro da saída. Se a Anthropic mudar o preço, mude aqui.
PRECOS = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-opus-4-8": (5.0, 25.0), "claude-fable-5-1": (10.0, 50.0)}

def custo_claude(modelo, entrada, saida, cache_escrita=0, cache_leitura=0):
    pe, ps = PRECOS.get(modelo, PRECOS["claude-opus-5"])
    return (entrada * pe + saida * ps + cache_escrita * pe * 1.25 + cache_leitura * pe * 0.1) / 1e6

def arq(d): return os.path.join(d, "custos.json")

def ler(d):
    try: return json.load(open(arq(d)))
    except (OSError, ValueError): return dict(itens=[], notas=[])

def _grava(d, C):
    tmp = arq(d) + ".tmp"; json.dump(C, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, arq(d))

def registrar(d, **item):
    with _lock:
        C = ler(d); item.setdefault("quando", time.strftime("%Y-%m-%dT%H:%M:%S")); item["usd"] = round(float(item.get("usd") or 0), 6)
        C["itens"].append(item); _grava(d, C)

def nota(d, texto):
    with _lock:
        C = ler(d)
        if texto not in C.setdefault("notas", []): C["notas"].append(texto); _grava(d, C)

SERVICOS = ("claude", "gemini", "apify", "grok", "kie", "google")   # grok/kie: imagens e vídeos do Ad Animado (lib/gerativa.py)
CONTA = {"claude": "chamadas", "gemini": "vistos", "apify": "buscas", "grok": "geracoes", "kie": "geracoes", "google": "geracoes"}

def resumo(d):
    C = ler(d); s = dict(claude=0.0, gemini=0.0, apify=0.0, grok=0.0, kie=0.0, google=0.0, chamadas=0, vistos=0, buscas=0, geracoes=0)
    for it in C["itens"]:
        s[it["servico"]] = s.get(it["servico"], 0.0) + it["usd"]
        if it["servico"] in CONTA: s[CONTA[it["servico"]]] += 1
    s["total"] = round(sum(s.get(k, 0.0) for k in SERVICOS), 4)
    for k in SERVICOS: s[k] = round(s.get(k, 0.0), 4)
    return s

def token_apify():
    """Ambiente, depois ⚙ Configurações, depois o export do ~/.zshrc."""
    t = os.environ.get("APIFY_API_TOKEN")
    if t: return t.strip()
    import chaves
    if chaves.ler().get("apify"): return chaves.ler()["apify"]
    z = os.path.expanduser("~/.zshrc")
    if os.path.isfile(z):
        m = re.search(r'^\s*export\s+APIFY_API_TOKEN=["\']?([^"\'\s]+)', open(z, errors="ignore").read(), re.M)
        if m: return m.group(1)
    return ""

FONTE_APIFY = "valor que a Apify registrou na corrida"

def apify_run(run_id, token=None):
    """Valor cobrado numa corrida da Apify, como a própria Apify registrou (usageTotalUsd)."""
    req = urllib.request.Request(f"https://api.apify.com/v2/actor-runs/{run_id}", headers={"Authorization": f"Bearer {token or token_apify()}"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]; ev = r.get("chargedEventCounts") or {}
    quando = datetime.fromisoformat(r["startedAt"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%dT%H:%M:%S") if r.get("startedAt") else ""
    return dict(usd=float(r.get("usageTotalUsd") or 0), resultados=ev.get("result"), status=r.get("status"), quando=quando)

def registrar_apify(d, run_id, rotulo, etapa="broll"):
    if any(it.get("run") == run_id for it in ler(d)["itens"]): return None
    r = apify_run(run_id)
    registrar(d, servico="apify", etapa=etapa, rotulo=rotulo, run=run_id, resultados=r["resultados"], usd=r["usd"], status=r["status"],
              quando=r["quando"] or time.strftime("%Y-%m-%dT%H:%M:%S"), fonte=FONTE_APIFY)
    return r

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["nota"]: nota(os.path.abspath(a[1]), a[2]); print("ok")
    else: print(__doc__)
