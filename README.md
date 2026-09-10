# Vaak Streamlit Tester UI

This repo contains only the Streamlit tester UI and supporting test material for the Vaak API demo. It does not contain the full Vaak backend/product source.

The UI connects to an already running Vaak API service and lets a tester validate:

- service health, voice-model contract, and model quality
- identity enrollment with microphone or uploaded audio
- default envelope creation and live session creation
- speech generation through Vaak
- optional Faster-Whisper STT verification
- guardrail scenarios
- proof, disclosure, attestation, transcript, and report flows

## Environment Variables

Create a `.env` file in the repo root:

```bash
cp .env.example .env
```

Main variables:

```bash
VAAK_BASE_URL=http://127.0.0.1:8478
VAAK_TOKEN=dev-demo-token
VAAK_PRINCIPAL_ID=
VAAK_CONTROLLER_ID=development
VAAK_DISPLAY_NAME="Streamlit Demo User"
VAAK_PROOF_DIR=/tmp/vaak-streamlit-proof
```

Use `VAAK_PRINCIPAL_ID=` empty if you want the UI to auto-create a fresh ID each run. Put a fixed value if a tester must reuse one known principal.

Real secrets must not be committed. Provider keys such as `BLU_KEY` or Kokoro/Bluro configuration belong on the Vaak backend service/VM, not inside this UI repo.

## Run On The Vaak VM

Use this when the Vaak API is already running on the same VM at `127.0.0.1:8478`.

Fast path:

```bash
git clone https://github.com/onkargaikwadai/vaak_project.git
cd vaak_project

chmod +x scripts/run_streamlit_vm.sh
./scripts/run_streamlit_vm.sh
```

Manual path:

```bash
git clone https://github.com/onkargaikwadai/vaak_project.git
cd vaak_project

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env

streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

## Open From Local Laptop Through SSH Tunnel

From Windows PowerShell or your local terminal:

```powershell
ssh -p 2222 -L 8501:127.0.0.1:8501 -L 8478:127.0.0.1:8478 onkar@208.115.210.161
```

Then open:

```text
http://127.0.0.1:8501
```

Important check:

- If the error path shows `/home/onkar/...`, Streamlit is running on the VM.
- If the error path shows `C:\Users\ADMIN\...` or `WinError`, Streamlit is running on Windows locally.

## VM Smoke Test

Before opening the UI, verify Vaak API and Faster-Whisper:

```bash
chmod +x scripts/vm_smoke_test.sh
./scripts/vm_smoke_test.sh
```

Expected:

- `/healthz` returns `ok: true`
- `/v1/voice-model/spec` returns voice contract support flags
- Faster-Whisper model loads successfully

## UI Test Order

1. Open `Service` tab and click `Health Check`.
2. Click `Voice Model Spec`.
3. Go to `Enrollment`.
4. Click `1. Create Principal`.
5. Click `2. Open Enrollment`.
6. Read the generated challenge phrase.
7. Record yourself speaking the challenge phrase, or upload an audio file.
8. Click `3. Complete Enrollment`.
9. Click `4. Create Default Envelope`.
10. Click `5. Open Live Session`.
11. Go to `Speech` and generate speech.
12. Use Faster-Whisper STT only when the model is installed/downloaded on the same machine running Streamlit.
13. Run `Guardrails`, `Proof`, and `Report` tabs for client proof.

## Postman And cURL

Direct API cURL examples are in:

```text
docs/Vaak_Postman_Curl_Collection.md
```

Postman collection/environment files are in:

```text
postman/
```

## Tester Flow

The exact enrollment, TTS, and STT validation flow is documented here:

```text
docs/Tester_TTS_STT_Runbook.md
```
