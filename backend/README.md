# Parinita Vaak GA v2.1.0

**Vaak is Parinita's standalone production voice, realtime presence, AI Studio, governance and proof platform.** Atma is built separately and integrates through the same external cognition contract used by third-party agents.

## What Vaak ships

- **Vaak Voice Model (VVM)** conditioning/training contract and production acoustic-backbone routing.
- **Vaak Live** realtime speech input/output, external-cognition events, barge-in, transcript/undertone events and governed response creation.
- **Vaak Studio** synthetic characters, licensed-performer voices, rights-holder controls, director intent, scoped rights and revocation.
- **Agency Integrity** independent semantic classification, refusal state, high-stakes controls and manipulation/influence governance.
- **Acoustic governance** pre-emission temporal undertone/prosody gate.
- **Identity and authorization** voice enrollment, short-lived credentials, signed Delegation Envelopes, presence evidence and atomic revocation/erasure.
- **Proof** live attestation, exact-content provenance, mandatory production soft binding, HSM signing interfaces and Chrysalis publication hooks.
- **Developer platform** FastAPI/OpenAPI, WebSocket realtime protocol, Python client, TypeScript SDK and operator CLI.

Vaak does **not** contain Atma cognition, memory, Self Graph, goals, model orchestration or tool planning.

## External cognition contract

```text
audio in
  -> Vaak ASR + undertone
  -> response.requested
  -> Atma or developer cognition host
  -> response.create
  -> credential + Delegation Envelope
  -> Agency Integrity
  -> VVM / acoustic backbone
  -> output acoustic gate
  -> soft binding + provenance
  -> audio out
```

Atma receives no privileged bypass.

## Production Model Ensemble & Quality Router

Vaak GA is **open-weight-only**. Every enabled production model must have downloadable weights, pinned checkpoint identity, explicit license metadata and commercial-use approval. The following families are represented in the production catalog and quality registry:

| Role | Required families | Vaak use |
|---|---|---|
| Voice/TTS | Qwen3-TTS; eligible NVIDIA Nemotron Speech TTS checkpoints | VVM acoustic foundations / voice serving |
| ASR | NVIDIA Nemotron 3.5 ASR; Whisper; Mistral Voxtral | Streaming recognition / speech-understanding candidates |
| Agency Integrity | Meta Llama; DeepSeek | Independent semantic safety/risk ensemble |

The router does not select by brand. Every configured provider has a durable `QualitySnapshot`. Role-specific gates cover quality score, p95 latency and availability; TTS also requires identity similarity, ASR has a WER ceiling, and Agency Integrity has a false-negative ceiling. Stale benchmark snapshots are ineligible.

A faster or cheaper model cannot win if it falls below the quality floor. TTS failover is permitted only **before** first audio; switching acoustic models mid-utterance is prohibited because it can break voice identity and provenance continuity.

The public model families are replaceable acoustic/semantic engines. The VVM conditioning contract, rights, authorization, Agency Integrity and proof semantics do not change when a provider changes.

### Open-weight and licensing posture

Vaak production rejects opaque/hosted-only model dependencies. Each enabled model must declare `open_weights=true`, a pinned `model_id`, `weights_uri`, `license_id`, and `commercial_use_allowed=true`. The runtime fails closed if any of those conditions is missing.

Mistral Voxtral TTS v26.03 is **not part of the GA production catalog** because the current public weights are CC BY-NC 4.0. Mistral remains in Vaak through commercially eligible open-weight Voxtral speech-understanding/ASR checkpoints such as the Apache-2.0 realtime family.

## VVM: identity-, authority-, influence- and rights-conditioned voice

Vaak is not defined as `text + speaker embedding -> audio`. `vaak.voice_model.VoiceConditioning` and `vaak.modeling.VaakModelPlan` define a canonical model plan containing:

`identity + Persona/register + relationship + authority + influence posture + incoming undertone + channel/locale + Studio direction + production context + performance version + rights grant`

The VVM training contract includes objectives for identity retention, semantic fidelity, Persona/register continuity, authority adherence, influence-policy adherence, undertone response safety, conversational continuity, Studio direction fidelity, rights-scope adherence and provenance alignment.

Qwen3-TTS and eligible commercially usable open-weight Nemotron Speech TTS checkpoints are acoustic foundations/backends. Mistral Voxtral TTS v26.03 is excluded from GA; Mistral participation is through eligible open-weight Voxtral speech-understanding/ASR checkpoints. None replaces the VVM rights/governance model.

## Vaak Studio

Studio identities can represent:

- fully synthetic characters;
- studio/rightsholder-owned voices;
- licensed performer voices;
- enterprise/brand characters.

A signed `StudioRightsGrant` scopes performance to productions, languages, territories, channels, usage classes and voice/performance versions. Licensed performers require independently verified performer identity evidence in production. Rights holders require independent identity evidence before production rights issuance. Grant expiry is rechecked before emission, and revocation terminates active sessions tied to the grant.

Director controls may include scene/take, emotion direction, pacing, energy, emphasis, accent and age presentation. Direction cannot override authorization, rights scope, Agency Integrity or prosody ceilings.

## GA security boundaries

### Tenant + controller authorization

Production API access uses durable bearer tokens bound to a tenant, subject and scopes. Resources are bound to both tenant and controlling subject. A scoped user cannot laterally operate another subject's resources inside the same tenant unless the credential has explicit `tenant:admin` or wildcard authority.

Legacy shared API keys are rejected in production.

### Enrollment

Production enrollment requires an external verifier that proves the submitted media against the fresh challenge and returns trusted liveness/speaker-ownership/anti-spoof evidence. Development has a deterministic contract verifier; production refuses that provider.

### Presence and disclosure

High-consequence/call sessions consume independently verified presence evidence. A caller cannot set a Boolean `presence_checked` field through the public API.

For calls, Vaak renders the disclosure audio and blocks normal speech until a trusted transport/evidence service returns delivery evidence bound to the exact disclosure audio hash, session, channel and principal.

### Durable voice death

The production vault persists encrypted voice assets. Each asset has its own AES-GCM DEK; the DEK is wrapped through the configured HSM/key-service boundary. Erasure destroys the wrapped DEK, revokes every issued credential for the voice, terminates active sessions and prevents new sessions. Historical proof remains verifiable as formerly authorized.

### Provenance

Production audio is sent through the configured robust soft-binding service before it is digested and emitted. Failure of the binding service fails speech closed. Session evidence records binding references and their digest. Signed provenance roots are published through the Chrysalis-compatible publisher.

## Core API

- `GET /healthz`
- `GET /v1/voice-model/spec`
- `GET /v1/models/quality`
- `POST /v1/principals`
- `POST /v1/principals/{id}/voice/enrollment`
- `POST /v1/principals/{id}/voice/enrollment/complete`
- `POST /v1/principals/{id}/envelope/default`
- `POST /v1/principals/{id}/live`
- `POST /v1/studios/{studio}/characters`
- `POST /v1/studios/{studio}/characters/{character}/rights`
- `POST /v1/studio-rights/{grant}/revoke`
- `POST /v1/studios/{studio}/characters/{character}/live`
- `POST /v1/sessions/{id}/disclosure/render`
- `POST /v1/sessions/{id}/disclosure/confirm`
- `POST /v1/sessions/{id}/speak`
- `POST /v1/sessions/{id}/attest`
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/resolve`
- `GET /v1/principals/{id}/verification-trust-bundle`
- `POST /v1/principals/{id}/erase`
- `WS /v1/realtime/{session_id}`

## Operator bootstrap

Create tenant-scoped credentials without editing SQLite by hand:

```bash
vaak-admin --auth-db /var/lib/vaak/auth.db issue-token \
  --tenant studio-a --subject service-studio-a --scope '*'
```

For least privilege, repeat `--scope` with only the required scopes rather than using `*`.

## Deployment

See `PRODUCTION.md` and `.env.example`. The source runtime intentionally consumes external production services for GPU inference, biometric/identity evidence, HSM custody, robust media binding, Chrysalis publication and Corridor/WebRTC/SIP transport. Those are deployable dependencies, not mocked claims inside the source package.

## Verification

```bash
python -m pytest -q
python -m compileall -q vaak
```

Expected GA suite: **97 passed**.

## Product principle

**Vaak is the governed voice model and embodied-presence platform. Atma is a separate Digital Twin that uses it. Vaak Studio applies the same identity, rights, quality and proof architecture to synthetic characters and licensed performers.**

## Public model references

- Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS
- Mistral Voxtral TTS (open-weight, public weights non-commercial): https://docs.mistral.ai/models/voxtral-tts-26-03
- Mistral Voxtral speech understanding: https://mistral.ai/news/voxtral/
- NVIDIA Nemotron ASR Streaming: https://docs.nvidia.com/nim/speech/
- OpenAI Whisper: https://github.com/openai/whisper
- Meta Llama: https://github.com/meta-llama/llama-models
- DeepSeek: https://github.com/deepseek-ai
