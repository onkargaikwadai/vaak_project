# Vaak API - Postman cURL Collection

Use these cURL requests in Postman by clicking:

```text
Import -> Raw text -> paste cURL -> Import
```

## Common Variables

For terminal testing:

```bash
export BASE_URL="http://127.0.0.1:8478"
export TOKEN="dev-demo-token"
export PRINCIPAL_ID="postman-demo-user"
export STUDIO_ID="postman-demo-studio"
export CHARACTER_ID="postman-demo-character"
export GRANT_ID="replace-with-grant-id"
export SESSION_ID="replace-with-session-id"
export ENROLL_SESSION_ID="replace-with-enrollment-session-id"
export CHALLENGE="replace-with-challenge-phrase"
```

In Postman, create environment variables with the same names:

```text
BASE_URL
TOKEN
PRINCIPAL_ID
STUDIO_ID
CHARACTER_ID
GRANT_ID
SESSION_ID
ENROLL_SESSION_ID
CHALLENGE
```

For Postman URLs, use:

```text
{{BASE_URL}}
{{TOKEN}}
{{PRINCIPAL_ID}}
{{SESSION_ID}}
```

## 1. Health Check

```bash
curl -sS "$BASE_URL/healthz"
```

## 2. Voice Model Spec

```bash
curl -sS "$BASE_URL/v1/voice-model/spec" \
  -H "Authorization: Bearer $TOKEN"
```

## 3. Model Quality

```bash
curl -sS "$BASE_URL/v1/models/quality" \
  -H "Authorization: Bearer $TOKEN"
```

## 4. Create Principal

```bash
curl -sS -X POST "$BASE_URL/v1/principals" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "principal_id": "postman-demo-user",
    "controller_id": "development",
    "display_name": "Postman Demo User",
    "identity_class": "developer_agent",
    "persona": {
      "role": "demo",
      "tone": "calm"
    }
  }'
```

## 5. Open Voice Enrollment

```bash
curl -sS -X POST "$BASE_URL/v1/principals/$PRINCIPAL_ID/voice/enrollment" \
  -H "Authorization: Bearer $TOKEN"
```

Save the returned values:

```text
session_id -> ENROLL_SESSION_ID
challenge -> CHALLENGE
```

## 6. Complete Voice Enrollment

For simple dev testing, this sends small placeholder audio. For real testing, replace `audio_b64` with base64 of recorded microphone audio.

```bash
curl -sS -X POST "$BASE_URL/v1/principals/$PRINCIPAL_ID/voice/enrollment/complete" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$ENROLL_SESSION_ID\",
    \"audio_b64\": \"AQABAAEAAQABAAEAAQABAAEAAQABAAEAAQABAAEAAQABAAEAAQABAAEAAQABAAE=\",
    \"spoken_challenge\": \"$CHALLENGE\",
    \"languages\": [\"en\"]
  }"
```

## 7. Create Default Envelope

```bash
curl -sS -X POST "$BASE_URL/v1/principals/$PRINCIPAL_ID/envelope/default" \
  -H "Authorization: Bearer $TOKEN"
```

## 8. Open Live Session

```bash
curl -sS -X POST "$BASE_URL/v1/principals/$PRINCIPAL_ID/live" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "external_agent",
    "channel": "local",
    "register": "private",
    "language": "en"
  }'
```

Save the returned value:

```text
session_id -> SESSION_ID
```

## 9. Create Studio Character

```bash
curl -sS -X POST "$BASE_URL/v1/studios/$STUDIO_ID/characters" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "character_id": "postman-demo-character",
    "rights_holder_id": "development",
    "display_name": "Demo Character",
    "persona": {
      "tone": "warm",
      "style": "clear"
    },
    "voice_origin": "synthetic",
    "canonical_voice_version": "1"
  }'
```

## 10. Issue Studio Character Rights

```bash
curl -sS -X POST "$BASE_URL/v1/studios/$STUDIO_ID/characters/$CHARACTER_ID/rights" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "rights_holder_id": "development",
    "production_ids": ["demo-production"],
    "languages": ["en"],
    "territories": ["worldwide"],
    "channels": ["local", "call", "stream"],
    "usage_classes": ["dialogue"],
    "allow_localization": true,
    "allow_voice_transformation": false,
    "ttl_seconds": 31536000
  }'
```

Save the returned value:

```text
grant_id -> GRANT_ID
```

## 11. Revoke Studio Rights

```bash
curl -sS -X POST "$BASE_URL/v1/studio-rights/$GRANT_ID/revoke" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Demo revoke test"
  }'
```

## 12. Open Studio Live Session

```bash
curl -sS -X POST "$BASE_URL/v1/studios/$STUDIO_ID/characters/$CHARACTER_ID/live" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "grant_id": "replace-with-grant-id",
    "production_id": "demo-production",
    "usage_class": "dialogue",
    "language": "en",
    "territory": "worldwide",
    "channel": "local",
    "register": "stage",
    "director": {
      "intent": "dialogue"
    }
  }'
```

## 13. Render Disclosure

```bash
curl -sS -X POST "$BASE_URL/v1/sessions/$SESSION_ID/disclosure/render" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "You are speaking with an AI-generated voice operating under authorized Vaak identity and policy controls."
  }'
```

## 14. Confirm Disclosure

```bash
curl -sS -X POST "$BASE_URL/v1/sessions/$SESSION_ID/disclosure/confirm" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "delivery_evidence": "demo-disclosure-confirmed"
  }'
```

## 15. Speak

```bash
curl -sS -X POST "$BASE_URL/v1/sessions/$SESSION_ID/speak" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, my name is Onkar Gaikwad and I am an AI engineer.",
    "prosody": {
      "warmth": 0.7,
      "urgency": 0.1,
      "dominance": 0.1
    },
    "agency": {
      "intent": "inform",
      "influence_intensity": 0.0
    }
  }'
```

The response returns:

```text
audio_b64
sample_rate
```

## 16. Session Attestation

```bash
curl -sS -X POST "$BASE_URL/v1/sessions/$SESSION_ID/attest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "nonce": "postman-demo-nonce"
  }'
```

## 17. Audio Transcription

Replace `audio_b64` with base64 audio content.

```bash
curl -sS -X POST "$BASE_URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_b64": "replace-with-base64-audio",
    "language": "en"
  }'
```

## 18. Audio Resolve

Replace `audio_b64` with base64 audio generated by Vaak.

```bash
curl -sS -X POST "$BASE_URL/v1/audio/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_b64": "replace-with-base64-audio"
  }'
```

## 19. Verification Trust Bundle

```bash
curl -sS "$BASE_URL/v1/principals/$PRINCIPAL_ID/verification-trust-bundle" \
  -H "Authorization: Bearer $TOKEN"
```

## 20. Erase Voice

```bash
curl -sS -X POST "$BASE_URL/v1/principals/$PRINCIPAL_ID/erase" \
  -H "Authorization: Bearer $TOKEN"
```

## 21. Realtime WebSocket

This is not a normal cURL request. Use Postman WebSocket request.

URL:

```text
ws://127.0.0.1:8478/v1/realtime/{{SESSION_ID}}?token={{TOKEN}}
```

First message after connection should look like:

```json
{
  "type": "session.created",
  "session_id": "your-session-id",
  "mode": "external_agent",
  "sample_rate": 16000,
  "cognition": "external",
  "tenant_id": "default",
  "disclosure_required": false
}
```

Send text response event:

```json
{
  "type": "response.create",
  "text": "Hello, this is a realtime Vaak response.",
  "prosody": {
    "warmth": 0.7,
    "urgency": 0.1,
    "dominance": 0.1
  },
  "agency": {
    "intent": "inform",
    "influence_intensity": 0.0
  }
}
```

## Recommended Postman Test Order

Run in this order:

```text
1. Health Check
2. Voice Model Spec
3. Create Principal
4. Open Voice Enrollment
5. Complete Voice Enrollment
6. Create Default Envelope
7. Open Live Session
8. Speak
9. Session Attestation
10. Verification Trust Bundle
11. Erase Voice
```

Studio APIs should be tested separately:

```text
1. Create Studio Character
2. Issue Studio Character Rights
3. Open Studio Live Session
4. Revoke Studio Rights
```

