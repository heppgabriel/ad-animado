#!/usr/bin/env bash
# Encerra o servidor do Ad Animado. Uma geração em andamento continua (roda em outro processo).
set -euo pipefail
PORTA="${PORTA:-4124}"
PID=$(lsof -tiTCP:"$PORTA" -sTCP:LISTEN 2>/dev/null || true)
if [ -z "$PID" ]; then echo "O Ad Animado não está rodando."; else kill $PID; echo "Ad Animado encerrado."; fi
