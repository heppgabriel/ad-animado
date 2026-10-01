#!/usr/bin/env bash
# Abre o Ad Animado: sobe o servidor se ainda não estiver rodando e abre o navegador.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PORTA="${PORTA:-4124}"
LOG="$HOME/.ad-animado.log"
if curl -fsS "http://127.0.0.1:$PORTA/api/health" >/dev/null 2>&1; then
  echo "Já estava rodando." >> "$LOG"
else
  nohup .venv/bin/python app/servidor.py >> "$LOG" 2>&1 &
  disown
  for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PORTA/api/health" >/dev/null 2>&1 && break; sleep 1; done
fi
if command -v open >/dev/null 2>&1; then open "http://localhost:$PORTA"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORTA"; fi
