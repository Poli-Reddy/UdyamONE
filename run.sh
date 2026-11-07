#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
app_options=(app:app --reload --host 127.0.0.1 --port "${PORT:-8000}")
if [ -f .env ]; then app_options+=(--env-file .env); fi
python -m uvicorn "${app_options[@]}"
