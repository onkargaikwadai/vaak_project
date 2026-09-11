from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass
class VaakClient:
    base_url: str
    api_key: str
    timeout_s: float = 30.0

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def create_principal(self, *, controller_id: str, display_name: str, identity_class: str = "developer_agent", persona: dict | None = None, principal_id: str | None = None) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals", json={
                "controller_id": controller_id, "display_name": display_name,
                "identity_class": identity_class, "persona": persona or {}, "principal_id": principal_id,
            }, headers=self._headers)
            r.raise_for_status(); return r.json()

    def open_voice_enrollment(self, principal_id: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/voice/enrollment", headers=self._headers)
            r.raise_for_status(); return r.json()

    def complete_voice_enrollment(self, principal_id: str, *, session_id: str, pcm16: bytes, spoken_challenge: str, languages=("en",)) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/voice/enrollment/complete", json={
                "session_id": session_id,
                "audio_b64": base64.b64encode(pcm16).decode(),
                "spoken_challenge": spoken_challenge,
                "languages": list(languages),
            }, headers=self._headers)
            r.raise_for_status(); return r.json()

    def set_default_envelope(self, principal_id: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/envelope/default", headers=self._headers)
            r.raise_for_status(); return r.json()

    def open_live(self, principal_id: str, *, mode="external_agent", channel="local", register="private", language="en", presence_evidence: str | None = None) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/live", json={
                "mode": mode, "channel": channel, "register": register,
                "language": language, "presence_evidence": presence_evidence,
            }, headers=self._headers)
            r.raise_for_status(); return r.json()

    def render_disclosure(self, session_id: str, *, text: str | None = None) -> dict:
        import httpx
        body = {} if text is None else {"text": text}
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/sessions/{session_id}/disclosure/render", json=body, headers=self._headers)
            r.raise_for_status(); return r.json()

    def confirm_disclosure(self, session_id: str, delivery_evidence: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/sessions/{session_id}/disclosure/confirm", json={"delivery_evidence": delivery_evidence}, headers=self._headers)
            r.raise_for_status(); return r.json()

    def model_quality(self) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.get(f"{self.base_url.rstrip('/')}/v1/models/quality", headers=self._headers)
            r.raise_for_status(); return r.json()

    def create_studio_character(self, studio_id: str, *, rights_holder_id: str, display_name: str, character_id: str | None = None, persona: dict | None = None, voice_origin: str = "synthetic", performer_id: str | None = None, canonical_voice_version: str = "1", performer_identity_evidence: str | None = None) -> dict:
        import httpx
        body = {"rights_holder_id": rights_holder_id, "display_name": display_name, "character_id": character_id, "persona": persona or {}, "voice_origin": voice_origin, "performer_id": performer_id, "canonical_voice_version": canonical_voice_version, "performer_identity_evidence": performer_identity_evidence}
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/studios/{studio_id}/characters", json=body, headers=self._headers)
            r.raise_for_status(); return r.json()

    def issue_studio_rights(self, studio_id: str, character_id: str, **body) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/studios/{studio_id}/characters/{character_id}/rights", json=body, headers=self._headers)
            r.raise_for_status(); return r.json()

    def open_studio_live(self, studio_id: str, character_id: str, **body) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/studios/{studio_id}/characters/{character_id}/live", json=body, headers=self._headers)
            r.raise_for_status(); return r.json()

    def revoke_studio_rights(self, grant_id: str, reason: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/studio-rights/{grant_id}/revoke", json={"reason": reason}, headers=self._headers)
            r.raise_for_status(); return r.json()


    def speak(self, session_id: str, text: str, *, prosody: dict | None = None, agency: dict | None = None) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/sessions/{session_id}/speak", json={"text": text, "prosody": prosody, "agency": agency}, headers=self._headers)
            r.raise_for_status(); return r.json()

    def attest(self, session_id: str, nonce: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/sessions/{session_id}/attest", json={"nonce": nonce}, headers=self._headers)
            r.raise_for_status(); return r.json()

    def erase(self, principal_id: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/erase", headers=self._headers)
            r.raise_for_status(); return r.json()

    def transcribe(self, pcm16: bytes, *, language: str | None = None) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/audio/transcriptions", json={"audio_b64": base64.b64encode(pcm16).decode(), "language": language}, headers=self._headers)
            r.raise_for_status(); return r.json()

    def resolve_audio(self, pcm16: bytes) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.post(f"{self.base_url.rstrip('/')}/v1/audio/resolve", json={"audio_b64": base64.b64encode(pcm16).decode()}, headers=self._headers)
            r.raise_for_status(); return r.json()

    def verification_trust_bundle(self, principal_id: str) -> dict:
        import httpx
        with httpx.Client(timeout=self.timeout_s) as c:
            r = c.get(f"{self.base_url.rstrip('/')}/v1/principals/{principal_id}/verification-trust-bundle", headers=self._headers)
            r.raise_for_status(); return r.json()
