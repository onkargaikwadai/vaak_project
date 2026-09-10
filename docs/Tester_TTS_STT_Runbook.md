# Vaak Streamlit Tester Runbook

This guide is for testers who want to validate Vaak from the Streamlit UI after cloning this repo.

## 1. Clone And Start UI On VM

```bash
cd ~
git clone https://github.com/onkargaikwadai/vaak_project.git
cd vaak_project

chmod +x scripts/run_streamlit_vm.sh
./scripts/run_streamlit_vm.sh
```

The UI starts on:

```text
http://127.0.0.1:8501
```

## 2. Open From Local Laptop

Keep this SSH tunnel open from the local laptop:

```powershell
ssh -p 2222 -L 8501:127.0.0.1:8501 -L 8478:127.0.0.1:8478 onkar@208.115.210.161
```

Open in browser:

```text
http://127.0.0.1:8501
```

## 3. Sidebar Values

Use:

```text
Vaak API base URL: http://127.0.0.1:8478
Bearer token: dev-demo-token
Controller ID: development
Display name: Streamlit Demo User
```

Use a new principal ID for every fresh test, for example:

```text
streamlit-demo-tts-stt-001
```

## 4. Service Check

Open `Service` tab.

Click:

```text
Health Check
Voice Model Spec
Model Quality
```

Pass condition:

```text
Health Check returns ok: true
Voice Model Spec returns contract_version and support flags
```

## 5. Enrollment Flow

Open `Enrollment` tab.

Run this order:

```text
1. Create Principal
2. Open Enrollment
3. Record the challenge phrase with microphone
4. Complete Enrollment
5. Create Default Envelope
6. Open Live Session
```

Pass condition:

```text
Complete Enrollment returns asset_id, voice_model_hash, consent_hash, and enrollment_evidence
Open Live Session returns session_id
```

Keep the generated live session ID. The Speech tab uses it for TTS.

## 6. TTS Test

Open `Speech` tab.

Use this text:

```text
Hello, my name is Onkar Gaikwad and I am an AI engineer.
```

Use style:

```text
Mood: happy
Warmth: 0.70
Urgency: 0.10
Dominance: 0.10
```

Click:

```text
Generate Speech With Vaak
```

Pass condition:

```text
PASS - TTS
Status: 200
Response has audio_b64 and sample_rate
Audio player appears and generated audio can be played
```

## 7. STT Test

In the same `Speech` tab:

```text
Keep "Use latest generated Vaak TTS audio" checked
Select Faster-Whisper model: small
Click "Transcribe With Faster-Whisper"
```

Pass condition:

```text
STT Result shows detected language: en
Transcript is close to the generated TTS text
```

Example expected transcript:

```text
Hello, my name is Onkar Gaikwad, and I'm an AI engineer.
```

## 8. Meaning Of The Test

TTS is verified when Vaak `/v1/sessions/{session_id}/speak` returns generated audio.

STT is verified when Faster-Whisper transcribes either uploaded audio or latest generated TTS audio.

If the UI shows paths like `/home/onkar/...`, Streamlit is running on the VM.

If the UI shows paths like `C:\Users\ADMIN\...`, Streamlit is running locally on Windows.

