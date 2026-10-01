"""Caminhos e ambiente do Ad Animado (app separado do Atlas Editor)."""
import os, sys, json

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(APP_DIR, "config.json"))) if os.path.exists(os.path.join(APP_DIR, "config.json")) else {}

def raiz():
    """Pasta dos ads (um subdiretório por ad). Lida a cada chamada: os testes trocam AD_ANIMADO_PROJETOS depois do import."""
    patch = globals().get("RAIZ")
    if patch is not None: return patch
    return os.path.abspath(os.path.expanduser(os.environ.get("AD_ANIMADO_PROJETOS") or CONFIG.get("pasta_projetos") or "~/Ads Animados"))

def __getattr__(nome):
    if nome == "RAIZ": return raiz()
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")

PY = sys.executable
PORTA = int(os.environ.get("AD_ANIMADO_PORTA") or os.environ.get("PORTA") or CONFIG.get("porta", 4124))
MODELOS = os.path.abspath(os.path.expanduser(os.environ.get("AD_ANIMADO_MODELOS") or os.path.join(APP_DIR, "modelos")))
_extras = [os.path.dirname(PY), "/usr/local/bin"]
if sys.platform == "darwin": _extras.append("/opt/homebrew/bin")
os.environ["PATH"] = os.pathsep.join(dict.fromkeys(os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep) + _extras))
