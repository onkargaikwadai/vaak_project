#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${VAAK_BASE_URL:-http://127.0.0.1:8478}"
TOKEN="${VAAK_TOKEN:-dev-demo-token}"

echo "Checking Vaak API at ${BASE_URL}"
curl -sS "${BASE_URL}/healthz" | python3 -m json.tool

echo
echo "Checking voice model spec"
curl -sS "${BASE_URL}/v1/voice-model/spec" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -m json.tool

echo
echo "Checking Faster-Whisper model load"
python3 - <<'PY'
from faster_whisper import WhisperModel

print("Loading Faster-Whisper small model...")
WhisperModel("small", device="cpu", compute_type="int8")
print("Faster-Whisper is working on this VM.")
PY

