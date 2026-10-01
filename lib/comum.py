"""Caminhos e ambiente do Ad Animado (app separado do Atlas Editor)."""
import os, sys, json, time

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

WINDOWS = os.name == "nt"
if WINDOWS: os.environ.setdefault("PYTHONUTF8", "1")      # processos-filho (animado.py, ia.py) leem/gravam tudo em UTF-8

def trocar(tmp, dest):
    """os.replace com novas tentativas: no Windows ele falha se outro processo (o servidor lendo o estado) está com o arquivo aberto."""
    for k in range(40):
        try: return os.replace(tmp, dest)
        except PermissionError:
            if k == 39: raise
            time.sleep(0.05)

def vivo(pid):
    """O processo ainda existe? (no Windows os.kill(pid, 0) MATA o processo, então pergunta pela API do sistema)."""
    try: pid = int(pid)
    except (TypeError, ValueError): return False
    if WINDOWS:
        import ctypes
        k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        cod = ctypes.c_ulong(); ok = k.GetExitCodeProcess(h, ctypes.byref(cod)); k.CloseHandle(h)
        return bool(ok) and cod.value == 259                                   # STILL_ACTIVE
    try: os.kill(pid, 0); return True
    except (ProcessLookupError, PermissionError, OSError): return False
