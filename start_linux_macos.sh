#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
AEROPT_PYTHON="${PYTHON:-python3}"
"$AEROPT_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else "AeroOpt için Python 3.10+ gerekli")'
if [[ ! -x ".venv/bin/python" ]]; then
  echo "[AeroOpt] Python ortamı hazırlanıyor..."
  "$AEROPT_PYTHON" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "[AeroOpt] Arayüz başlatılıyor..."
exec .venv/bin/python server.py
