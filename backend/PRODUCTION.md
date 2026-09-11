# Parinita Vaak GA v2.1 production deployment

Vaak v2.1 is the standalone production application/control runtime for VVM voice generation, realtime speech, AI Studio rights, Agency Integrity, identity/authorization and proof. Atma remains an external cognition host.

## 1. Mandatory production services

A GA deployment must provide all of the following. `ProductConfig.validate()` and the runtime boot path fail closed when required dependencies are absent.

1. **Model ensemble**
   - Qwen3-TTS self-hosted/open-weight endpoint.
   - eligible NVIDIA Nemotron Speech TTS open-weight endpoint(s) for TTS redundancy when deployed.
   - NVIDIA Nemotron 3.5 ASR open-weight endpoint.
   - Whisper open-weight ASR endpoint/service.
   - Mistral Voxtral open-weight speech-understanding/ASR endpoint.
   - Meta Llama open-weight Agency-classifier endpoint.
   - DeepSeek open-weight Agency-classifier endpoint.
   - measured quality snapshots for every enabled provider.
   - pinned `model_id`, downloadable `weights_uri`, license identifier and commercial-use approval for every enabled provider.

2. **Media safety gateway**
   - VAD/endpointing.
   - calibrated incoming/outgoing acoustic undertone analysis.
   - stream/audio normalization expected by Vaak.

3. **Identity/evidence service**
   - biometric voice-ownership/liveness/anti-spoof enrollment verification.
   - authenticated presence verification.
   - disclosure-delivery verification.
   - rights-holder/performer identity verification.

4. **HSM/key service**
   - issuer signing key.
   - anchor signing key.
   - voice-vault wrapping key.
   - owner/rightsholder/performer signing keys.
   - private key material never enters the Vaak process.

5. **Chrysalis publication/finality service**
   - durable anchor publication and returned anchor reference.

6. **Robust soft-binding service**
   - receives authorized PCM before egress.
   - returns transformed/marked PCM plus durable binding reference.
   - must meet Parinita codec/re-recording robustness acceptance tests.

7. **Corridor media edge**
   - WebRTC/TURN/SIP/telephony/meeting ingress and egress.
   - returns trusted delivery/presence evidence where required.

8. **Durable local/platform state**
   - product state database.
   - tenant/auth database.
   - quality registry.
   - encrypted voice vault.
   - production HA may place these behind platform-managed durable storage while preserving the contracts.

## 2. Production model catalog

`VAAK_MODEL_CATALOG_JSON` is **open-weight-only**. Every enabled provider must set `open_weights=true`, `commercial_use_allowed=true`, and pin `model_id`, `weights_uri` and `license_id`. GA requires these families:

- Qwen3-TTS (`tts`)
- NVIDIA Nemotron 3.5 ASR (`asr`)
- Whisper (`asr`)
- Mistral Voxtral (`asr` / speech understanding)
- Meta Llama (`agency`)
- DeepSeek (`agency`)

An eligible open-weight NVIDIA Nemotron Speech TTS checkpoint may be enabled as the second TTS path after license/checkpoint review. Mistral Voxtral TTS v26.03 is excluded from the GA production catalog because its current public weights are CC BY-NC 4.0. Mistral participation in GA uses commercially eligible open-weight Voxtral speech-understanding/ASR checkpoints instead.

Every enabled provider must have a corresponding `QualitySnapshot`. A provider may remain configured while being ineligible for traffic because its measured quality is below the role policy.

### Default quality gates

- **TTS:** quality >= 0.86, identity similarity >= 0.84, p95 latency <= 700 ms, availability >= 0.95.
- **ASR:** quality >= 0.84, WER <= 0.16, p95 latency <= 650 ms, availability >= 0.95.
- **Agency:** quality >= 0.90, false-negative rate <= 0.04, p95 latency <= 850 ms, availability >= 0.95.
- snapshots older than 30 days are ineligible by default.

These are release policy defaults, not vendor benchmark claims. Production teams should tighten them as the measured corpus improves.

## 3. Authentication and tenancy

Production rejects legacy shared API keys.

Provision tokens with:

```bash
vaak-admin --auth-db "$VAAK_AUTH_DB" issue-token \
  --tenant <tenant> --subject <controller> \
  --scope principals:create --scope voice:enroll --scope policy:write \
  --scope sessions:create --scope sessions:speak --scope sessions:attest \
  --scope proof:read --scope proof:verify --scope audio:transcribe
```

Resources are bound to tenant + controller subject. A different subject in the same tenant requires `tenant:admin` (or an operator wildcard credential) to cross that controller boundary.

## 4. Call/presence sequence

For a call/high-consequence session:

1. Corridor/identity service produces trusted presence evidence.
2. Client opens the Vaak session with that evidence.
3. Vaak renders the mandatory disclosure audio.
4. Transport delivers it.
5. Evidence service returns delivery evidence bound to the exact audio SHA-256 and session.
6. Client confirms disclosure.
7. Normal governed speech becomes available.

A client cannot unlock speech by setting its own `presence_checked` or `disclosed` Boolean.

## 5. Voice enrollment

1. Open enrollment and receive the fresh challenge.
2. Capture owner/performer media.
3. Submit the audio plus claimed challenge.
4. External verifier must establish challenge presence, liveness, speaker ownership and anti-spoof posture.
5. Only verified evidence permits the encrypted voice asset to be enrolled.

## 6. Studio identity and rights

Licensed performer characters require independent performer identity evidence at character creation. Production rights issuance requires independent rights-holder identity evidence. The evidence hashes are included in the signed Studio rights contract.

A `StudioRightsGrant` scopes production IDs, language, territory, channel, usage class and voice/performance version. Rights expiry is checked before emission. Revocation terminates active sessions tied to the grant.

## 7. HSM and voice-vault behavior

Production uses the HSM/key-service facade for signing, wrapping and unwrapping. The voice vault stores only encrypted media and wrapped per-asset DEKs. Crypto-erasure deletes the wrapped DEK. The process cannot recover the voice asset after erasure even though ciphertext and historical verification material can remain.

## 8. Soft binding and proof

Production audio is soft-bound before final digest/emission. If binding fails, no audio is emitted. Session manifests bind authorization evidence, presence/disclosure evidence, VVM plan hash, model provider, Studio rights context and the digest of soft-binding references.

The provenance log signs roots through the HSM-backed anchor key and publishes them to Chrysalis-compatible finality.

## 9. Realtime and external cognition

Vaak emits `response.requested`; Atma or another cognition host returns `response.create`. The cognition host never receives authority to bypass the Vaak authorization/Agency/media path.

The WebSocket protocol is the application/session contract. Production browser/mobile/telephony transport should use Corridor/WebRTC/SIP rather than exposing the internal WebSocket directly to untrusted networks without an edge layer.

## 10. Operations gates

A production environment should additionally enforce:

- TLS/mTLS between internal services where appropriate;
- network policies restricting HSM, evidence, model and Chrysalis endpoints;
- database backups and recovery drills;
- alerting on authentication failures, Agency blocks, credential anomalies, rights revocations and binding failures;
- token rotation and revocation procedures;
- HSM compromise/rotation procedures;
- deployment rollback that never restores an erased voice DEK or revoked rights state;
- capacity and latency SLOs measured on the actual target hardware.

## 11. Model quality and ChatGPT Voice-class benchmark

ChatGPT Voice-class interaction quality is the **experience benchmark**, not an automatic claim of parity. GA should continuously measure time-to-first-audio, mouth-to-ear latency, endpointing, barge-in recovery, naturalness, identity similarity, long-session stability, multilingual pronunciation, WER and task completion. Public superiority/parity claims require the corresponding measured evidence.

## 12. Atma boundary

Atma is not installed, imported, stored or reasoned inside Vaak. Atma connects as an external cognition host and uses the same governed speech contracts as developer agents.
