#!/usr/bin/env bash
# Ad Animado — instalação (Mac ou Linux). Rode de dentro da pasta baixada:
#     ./instalar.sh
# ou, para baixar e instalar de uma vez a partir do GitHub:
#     AD_ANIMADO_REPO=https://github.com/USUARIO/ad-animado.git bash instalar.sh
# Pode rodar de novo a qualquer momento para atualizar: os ads (~/Ads Animados) e as
# chaves (~/.config/ad-animado) ficam fora da pasta do app e nunca são apagados.
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"
DEST="${AD_ANIMADO_DEST:-$HOME/Ad Animado}"
REPO="${AD_ANIMADO_REPO:-}"

echo "== Ad Animado — instalação =="
falta=()
command -v git >/dev/null 2>&1 || falta+=(git)
command -v ffmpeg >/dev/null 2>&1 || falta+=(ffmpeg)
command -v curl >/dev/null 2>&1 || falta+=(curl)
if [ ${#falta[@]} -gt 0 ]; then
  if [[ "$OSTYPE" == darwin* ]] && command -v brew >/dev/null 2>&1; then
    echo "Instalando com o Homebrew: ${falta[*]}"; brew install "${falta[@]}"
  else
    echo "Faltam: ${falta[*]}. Instale (no Mac: brew install ${falta[*]}) e rode de novo."; exit 1
  fi
fi

# Python 3.12: usa o do sistema se for 3.12; senão instala um pelo uv (evita o Python do Homebrew,
# que no macOS 26 quebra o ensurepip por causa do libexpat).
PY=""
for c in python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys,pyexpat; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  command -v uv >/dev/null 2>&1 || { echo "Instalando o uv (gerenciador de Python)..."; curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
  uv python install 3.12
  PY="$(uv python find 3.12)"
fi
echo "Python: $("$PY" --version)"

# Código: a pasta atual (se já é o app) ou um clone/atualização do GitHub.
if [ -f "$AQUI/app/servidor.py" ] && [ -z "$REPO" ]; then
  DEST="$AQUI"
  [ -d "$DEST/.git" ] && git -C "$DEST" pull --ff-only 2>/dev/null || true
elif [ -n "$REPO" ]; then
  if [ -d "$DEST/.git" ]; then git -C "$DEST" pull --ff-only; else git clone "$REPO" "$DEST"; fi
else
  echo "Rode este script de dentro da pasta do Ad Animado, ou passe AD_ANIMADO_REPO=<link do GitHub>."; exit 1
fi
cd "$DEST"

echo "Preparando o ambiente Python (na primeira vez demora alguns minutos)..."
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
chmod +x iniciar.sh parar.sh instalar.sh
mkdir -p "$HOME/Ads Animados"

if [[ "$OSTYPE" == darwin* ]] && command -v osacompile >/dev/null 2>&1 && [ -d "$HOME/Desktop" ]; then
  APP="$HOME/Desktop/Ad Animado.app"
  CMD="'$DEST/iniciar.sh' >>'$HOME/.ad-animado.log' 2>&1"
  SRC=$(mktemp -t ad-animado).applescript
  printf 'do shell script "%s"\n' "$(printf '%s' "$CMD" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')" > "$SRC"
  osacompile -o "$APP" "$SRC" 2>/dev/null && echo "Criei o ícone \"Ad Animado\" na área de trabalho." || true
  rm -f "$SRC"
fi

echo
echo "Pronto. Abrindo http://localhost:${PORTA:-4124} ..."
echo "Primeiro passo: ⚙ Configurações (Claude pela assinatura, Grok, KIE)."
./iniciar.sh
