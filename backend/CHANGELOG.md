# Changelog

## 2.1.0 - Open-weight-only GA

- Made open-weight-only model selection a production invariant.
- Production rejects any enabled model without downloadable/pinned weights, license metadata, or commercial-use approval.
- Qwen3-TTS remains the primary open-weight VVM/TTS acoustic foundation.
- NVIDIA Nemotron 3.5 ASR, Whisper and Mistral Voxtral form the open-weight speech-recognition/understanding pool.
- Meta Llama and DeepSeek remain independent open-weight Agency Integrity families.
- Added optional commercially eligible NVIDIA Nemotron Speech TTS as an open-weight TTS redundancy family.
- Mistral Voxtral TTS v26.03 is excluded from the GA production catalog because the current public weights are CC BY-NC 4.0; Mistral remains represented by commercially eligible open-weight Voxtral speech-understanding/ASR checkpoints.
- Added production regression tests for closed-weight rejection, commercial-rights rejection and weight/license pinning.

## 2.0.0 - GA

- Promoted standalone Vaak to the GA application/control runtime.
- Added durable tenant + controller-subject authentication/authorization with rate limiting; production rejects legacy shared API keys.
- Added `vaak-admin` operator CLI for tenant-token and resource administration.
- Added independently verified presence evidence and removed caller-controlled presence assertions from the public API.
- Added rendered call disclosure plus trusted transport-delivery evidence bound to the exact disclosure-audio digest.
- Added durable encrypted voice vault with per-asset AES-GCM DEKs and external HSM/key-service wrapping; crypto-shred survives restart.
- Added restart-durable principals, voice bindings, Delegation Envelopes, terminal lifecycle state, Studio characters and Studio rights.
- Added production HSM signing/wrapping facade and Chrysalis-compatible automatic provenance publication.
- Added mandatory production robust soft-binding boundary before audio digest/egress; failures fail speech closed.
- Added authoritative performer/rightsholder identity-evidence contracts for licensed Studio workflows.
- Added Production Model Ensemble & Quality Router:
  - Qwen3-TTS as the primary VVM/TTS foundation, with eligible open-weight Nemotron Speech TTS checkpoints for redundancy;
  - NVIDIA Nemotron 3.5 ASR + Whisper for ASR;
  - Meta Llama + DeepSeek as independently configurable Agency Integrity classifier families.
- Added durable quality snapshots, freshness checks, role-specific quality floors, quality-first scoring and no mid-utterance TTS provider switching.
- Production configuration requires all six model families plus measured snapshots for every enabled provider.
- Fixed Studio REST forwarding of performer identity evidence.
- Fixed Studio rights rehydration after restart.
- Added same-tenant controller isolation in addition to tenant isolation.
- Expanded exact-package regression suite to 94 tests.

## 1.2.0

- Corrected the product boundary: Atma removed from Vaak and external cognition contract made canonical.

## 1.1.0

- Added Vaak Studio character/rights workflows and native VVM model-plan/training contracts.

## 1.0.0

- Added realtime API, developer SDKs and production adapter contracts around the v0.4 governance kernel.
