#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501

