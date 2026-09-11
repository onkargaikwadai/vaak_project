from __future__ import annotations

import base64
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..envelope import ChannelClass, Register
from ..studio import DirectorIntent, RightsScope, StudioCharacter, StudioSessionRequest, VoiceOrigin
from ..voice_model import VaakVoiceModelSpec, VoiceIdentityClass
from .principals import PrincipalProfile
from .realtime import RealtimeConversation
from .runtime import DEFAULT_CALL_DISCLOSURE, ProductRuntime
from .security import AuthContext, AuthorizationError, TenantAuthStore


class PrincipalCreate(BaseModel):
    principal_id: str | None = None
    controller_id: str
    display_name: str
    identity_class: str = "developer_agent"
    persona: dict[str, Any] = Field(default_factory=dict)


class EnrollmentComplete(BaseModel):
    session_id: str
    audio_b64: str
    spoken_challenge: str
    languages: list[str] = Field(default_factory=lambda: ["en"])


class LiveCreate(BaseModel):
    mode: str = "external_agent"
    channel: str = "local"
    register_name: str = Field(default="private", alias="register")
    language: str = "en"
    presence_evidence: str | None = None


class AudioBody(BaseModel):
    audio_b64: str
    language: str | None = None


class StudioCharacterCreate(BaseModel):
    character_id: str | None = None
    rights_holder_id: str
    display_name: str
    persona: dict[str, Any] = Field(default_factory=dict)
    voice_origin: str = "synthetic"
    performer_id: str | None = None
    canonical_voice_version: str = "1"
    performer_identity_evidence: str | None = None


class StudioRightsCreate(BaseModel):
    rights_holder_id: str
    production_ids: list[str]
    languages: list[str] = Field(default_factory=lambda: ["en"])
    territories: list[str] = Field(default_factory=lambda: ["worldwide"])
    channels: list[str] = Field(default_factory=lambda: ["local", "call", "stream"])
    usage_classes: list[str] = Field(default_factory=lambda: ["dialogue"])
    allow_localization: bool = True
    allow_voice_transformation: bool = False
    ttl_seconds: float = 31536000.0
    rights_holder_identity_evidence: str | None = None


class StudioLiveCreate(BaseModel):
    grant_id: str
    production_id: str
    usage_class: str = "dialogue"
    language: str = "en"
    territory: str = "worldwide"
    channel: str = "local"
    register_name: str = Field(default="stage", alias="register")
    presence_evidence: str | None = None
    director: dict[str, Any] = Field(default_factory=dict)


class RightsRevoke(BaseModel):
    reason: str


class SpeakBody(BaseModel):
    text: str
    prosody: dict[str, Any] | None = None
    agency: dict[str, Any] | None = None


class DisclosureRender(BaseModel):
    text: str = DEFAULT_CALL_DISCLOSURE


class DisclosureConfirm(BaseModel):
    delivery_evidence: str


def create_app(
    runtime: ProductRuntime,
    *,
    auth: TenantAuthStore | None = None,
    api_keys: tuple[str, ...] = (),
    rate_limit_per_minute: int = 1200,
) -> FastAPI:
    app = FastAPI(title="Parinita Vaak", version="2.1.0")

    def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
        if not authorization or not authorization.startswith("Bearer "):
            if auth is None and not api_keys:
                return AuthContext("default", "development", ("*",), "dev")
            raise HTTPException(401, "missing bearer token")
        token = authorization[7:]
        if auth is not None:
            try:
                ctx = auth.authenticate(token)
                auth.check_rate(ctx, limit_per_minute=rate_limit_per_minute)
                return ctx
            except AuthorizationError as exc:
                if "rate limit" in str(exc):
                    raise HTTPException(429, str(exc)) from exc
                pass
        if any(secrets.compare_digest(token, key) for key in api_keys):
            return AuthContext("default", "legacy", ("*",), "legacy")
        raise HTTPException(403, "invalid bearer token")

    def require_scope(ctx: AuthContext, scope: str) -> None:
        if not ctx.allows(scope):
            raise HTTPException(403, f"missing scope: {scope}")

    def own(ctx: AuthContext, kind: str, resource_id: str, scope: str) -> None:
        if auth is None:
            require_scope(ctx, scope)
            return
        try:
            auth.require_resource(ctx, kind, resource_id, scope)
        except AuthorizationError as exc:
            raise HTTPException(403, str(exc)) from exc

    def bind(ctx: AuthContext, kind: str, resource_id: str) -> None:
        if auth is not None:
            try:
                auth.bind_resource(ctx.tenant_id, kind, resource_id, ctx.subject_id)
            except AuthorizationError as exc:
                raise HTTPException(403, str(exc)) from exc

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "product": "vaak", "version": "2.1.0", "cognition": "external", "release": "GA"}

    @app.get("/v1/voice-model/spec")
    async def voice_model_spec(ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "models:read")
        return VaakVoiceModelSpec().__dict__

    @app.get("/v1/models/quality")
    async def model_quality(ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "models:read")
        if runtime.quality_router is None:
            return {"router": "not-configured"}
        out = {}
        for role in ("tts", "asr", "agency"):
            snapshots = runtime.quality_router.store.list_role(role)
            out[role] = runtime.quality_router.status(role, [s.provider_id for s in snapshots])
        return out

    @app.post("/v1/principals")
    async def create_principal(body: PrincipalCreate, ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "principals:create")
        try:
            if body.controller_id != ctx.subject_id and not ctx.allows("principals:admin") and ctx.subject_id not in {"legacy", "development"}:
                raise HTTPException(403, "controller_id must match authenticated subject")
            principal_id = body.principal_id or f"principal_{secrets.token_hex(8)}"
            bind(ctx, "principal", principal_id)
            profile = runtime.register_principal(PrincipalProfile(
                principal_id=principal_id,
                controller_id=body.controller_id,
                display_name=body.display_name,
                tenant_id=ctx.tenant_id,
                identity_class=VoiceIdentityClass(body.identity_class),
                persona=body.persona,
            ))
            return profile.body()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/principals/{principal_id}/voice/enrollment")
    async def open_enrollment(principal_id: str, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "voice:enroll")
        try:
            return runtime.open_voice_enrollment(principal_id, tenant_id=ctx.tenant_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/principals/{principal_id}/voice/enrollment/complete")
    async def complete_enrollment(principal_id: str, body: EnrollmentComplete, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "voice:enroll")
        try:
            return runtime.complete_voice_enrollment(
                principal_id,
                session_id=body.session_id,
                pcm16=base64.b64decode(body.audio_b64),
                spoken_challenge=body.spoken_challenge,
                languages=tuple(body.languages),
                tenant_id=ctx.tenant_id,
            )
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/principals/{principal_id}/envelope/default")
    async def set_default_envelope(principal_id: str, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "policy:write")
        try:
            return runtime.set_default_envelope(principal_id, tenant_id=ctx.tenant_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/principals/{principal_id}/live")
    async def open_live(principal_id: str, body: LiveCreate, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "sessions:create")
        try:
            s = runtime.open_live(
                principal_id,
                mode=body.mode,
                channel=ChannelClass(body.channel),
                register=Register(body.register_name),
                language=body.language,
                tenant_id=ctx.tenant_id,
                presence_evidence=body.presence_evidence,
            )
            bind(ctx, "session", s.session_id)
            return {"session_id": s.session_id, "realtime_url": f"/v1/realtime/{s.session_id}", "mode": s.mode, "disclosure_required": s.channel == ChannelClass.CALL}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{session_id}/disclosure/render")
    async def render_disclosure(session_id: str, body: DisclosureRender, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "session", session_id, "sessions:speak")
        try:
            pcm, meta = runtime.render_disclosure(session_id, text=body.text)
            return {"audio_b64": base64.b64encode(pcm).decode(), "sample_rate": 16000, **meta}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{session_id}/disclosure/confirm")
    async def confirm_disclosure(session_id: str, body: DisclosureConfirm, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "session", session_id, "sessions:speak")
        try:
            return runtime.confirm_disclosure(session_id, body.delivery_evidence)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/studios/{studio_id}/characters")
    async def create_studio_character(studio_id: str, body: StudioCharacterCreate, ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "studio:write")
        bind(ctx, "studio", studio_id)
        if runtime.studio is None:
            raise HTTPException(503, "Vaak Studio runtime unavailable")
        try:
            if body.rights_holder_id != ctx.subject_id and not ctx.allows("studio:admin") and "*" not in ctx.scopes:
                raise HTTPException(403, "rights_holder_id must match authenticated subject")
            character_id = body.character_id or f"character_{secrets.token_hex(8)}"
            bind(ctx, "principal", character_id)
            ch = StudioCharacter(
                character_id=character_id,
                studio_id=studio_id,
                display_name=body.display_name,
                persona=body.persona,
                voice_origin=VoiceOrigin(body.voice_origin),
                performer_id=body.performer_id,
                canonical_voice_version=body.canonical_voice_version,
            )
            runtime.studio.create_character(
                ch, rights_holder_id=body.rights_holder_id, tenant_id=ctx.tenant_id,
                performer_identity_evidence=body.performer_identity_evidence,
            )
            runtime.set_studio_envelope(character_id, tenant_id=ctx.tenant_id)
            return ch.body()
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/studios/{studio_id}/characters/{character_id}/rights")
    async def issue_studio_rights(studio_id: str, character_id: str, body: StudioRightsCreate, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "studio", studio_id, "studio:write")
        own(ctx, "principal", character_id, "studio:write")
        if runtime.studio is None:
            raise HTTPException(503, "Vaak Studio runtime unavailable")
        try:
            character = runtime.studio.characters.get(character_id)
            if character is None or character.studio_id != studio_id:
                raise ValueError("unknown character for studio")
            profile = runtime.get_principal(character_id, tenant_id=ctx.tenant_id)
            if body.rights_holder_id != profile.controller_id:
                raise ValueError("rights holder does not control character")
            if body.rights_holder_id != ctx.subject_id and not ctx.allows("studio:admin") and "*" not in ctx.scopes:
                raise HTTPException(403, "rights_holder_id must match authenticated subject")
            scope = RightsScope(
                production_ids=tuple(body.production_ids),
                languages=tuple(body.languages),
                territories=tuple(body.territories),
                channels=tuple(body.channels),
                usage_classes=tuple(body.usage_classes),
                allow_localization=body.allow_localization,
                allow_voice_transformation=body.allow_voice_transformation,
            )
            grant = runtime.studio.issue_rights(
                rights_holder_id=body.rights_holder_id,
                character_id=character_id,
                scope=scope,
                ttl_seconds=body.ttl_seconds,
                rights_holder_identity_evidence=body.rights_holder_identity_evidence,
            )
            bind(ctx, "studio_rights", grant.grant_id)
            return grant.body() | {"rights_grant_hash": grant.grant_hash()}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/studio-rights/{grant_id}/revoke")
    async def revoke_studio_rights(grant_id: str, body: RightsRevoke, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "studio_rights", grant_id, "studio:write")
        if runtime.studio is None:
            raise HTTPException(503, "Vaak Studio runtime unavailable")
        try:
            grant = runtime.studio.revoke_rights(grant_id, body.reason)
            return {"grant_id": grant.grant_id, "revoked_at": grant.revoked_at, "reason": grant.revocation_reason}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/studios/{studio_id}/characters/{character_id}/live")
    async def open_studio_live(studio_id: str, character_id: str, body: StudioLiveCreate, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "studio", studio_id, "sessions:create")
        own(ctx, "principal", character_id, "sessions:create")
        own(ctx, "studio_rights", body.grant_id, "sessions:create")
        if runtime.studio is None:
            raise HTTPException(503, "Vaak Studio runtime unavailable")
        try:
            character = runtime.studio.characters.get(character_id)
            if character is None or character.studio_id != studio_id:
                raise ValueError("unknown character for studio")
            live = runtime.studio.open_session(character_id, StudioSessionRequest(
                grant_id=body.grant_id,
                production_id=body.production_id,
                usage_class=body.usage_class,
                language=body.language,
                territory=body.territory,
                channel=ChannelClass(body.channel),
                register=Register(body.register_name),
                director=DirectorIntent(**body.director),
                presence_evidence=body.presence_evidence,
            ))
            bind(ctx, "session", live.session_id)
            return {"session_id": live.session_id, "realtime_url": f"/v1/realtime/{live.session_id}", "mode": "studio", "disclosure_required": live.channel == ChannelClass.CALL}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{session_id}/speak")
    async def speak(session_id: str, body: SpeakBody, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "session", session_id, "sessions:speak")
        chunks = []
        try:
            async for pcm in runtime.speak_stream(session_id, body.text, prosody=body.prosody, agency=body.agency):
                chunks.append(pcm)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"audio_b64": base64.b64encode(b"".join(chunks)).decode(), "sample_rate": 16000}

    @app.post("/v1/sessions/{session_id}/attest")
    async def attest(session_id: str, body: dict, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "session", session_id, "sessions:attest")
        try:
            return runtime.attest(session_id, str(body.get("nonce") or ""))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/audio/transcriptions")
    async def transcribe_audio(body: AudioBody, ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "audio:transcribe")
        try:
            pcm = base64.b64decode(body.audio_b64)
            text, undertone = await runtime.transcribe(pcm, language=body.language)
            return {"text": text, "undertone": undertone, "sample_rate": 16000}
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/audio/resolve")
    async def resolve_audio(body: AudioBody, ctx: AuthContext = Depends(require_auth)):
        require_scope(ctx, "proof:verify")
        try:
            return runtime.core.resolve_clip(base64.b64decode(body.audio_b64))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/principals/{principal_id}/verification-trust-bundle")
    async def verification_trust_bundle(principal_id: str, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "proof:read")
        try:
            return runtime.core.verification_trust_bundle(principal_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/principals/{principal_id}/erase")
    async def erase(principal_id: str, ctx: AuthContext = Depends(require_auth)):
        own(ctx, "principal", principal_id, "voice:erase")
        try:
            return runtime.erase_voice(principal_id, tenant_id=ctx.tenant_id)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.websocket("/v1/realtime/{session_id}")
    async def realtime(ws: WebSocket, session_id: str):
        token = ws.query_params.get("token")
        if not token:
            bearer = ws.headers.get("authorization", "")
            token = bearer[7:] if bearer.startswith("Bearer ") else None
        ctx = None
        if token and auth is not None:
            try:
                ctx = auth.authenticate(token)
                auth.require_resource(ctx, "session", session_id, "sessions:speak")
            except AuthorizationError:
                ctx = None
        if ctx is None and token and any(secrets.compare_digest(token, key) for key in api_keys):
            ctx = AuthContext("default", "legacy", ("*",), "legacy")
        if ctx is None and auth is None and not api_keys:
            ctx = AuthContext("default", "development", ("*",), "dev")
        if ctx is None:
            await ws.close(code=4403)
            return
        live = runtime.live.get(session_id)
        if live is None or live.tenant_id != ctx.tenant_id:
            await ws.close(code=4404)
            return
        await ws.accept()
        convo = RealtimeConversation(runtime, session_id, mode=live.mode, language=live.language)
        await ws.send_json({
            "type": "session.created",
            "session_id": session_id,
            "mode": live.mode,
            "sample_rate": 16000,
            "cognition": "external",
            "tenant_id": live.tenant_id,
            "disclosure_required": live.channel == ChannelClass.CALL,
        })

        async def send(obj: dict):
            await ws.send_json(obj)

        try:
            while True:
                event = await ws.receive_json()
                try:
                    await convo.handle(event, send)
                except Exception as exc:
                    await send({"type": "error", "error": type(exc).__name__, "message": str(exc)})
        except WebSocketDisconnect:
            if session_id in runtime.core.sessions:
                runtime.interrupt(session_id)

    return app
