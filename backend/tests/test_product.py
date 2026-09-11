import asyncio
import base64
import importlib.util

import pytest
from fastapi.testclient import TestClient

from vaak.product.bootstrap import build_runtime
from vaak.product.api import create_app
from vaak.product.config import ProductConfig
from vaak.product.principals import PrincipalProfile
from vaak.product.realtime import RealtimeConversation
from vaak.voice_model import VoiceIdentityClass


def dev_runtime():
    return build_runtime(ProductConfig(environment="development", allow_dev_providers=True))


def provision(rt, principal_id="agent_test", *, identity_class=VoiceIdentityClass.DEVELOPER_AGENT):
    rt.register_principal(PrincipalProfile(principal_id, "controller_1", "Test Principal", identity_class, {"tone": "warm"}))
    e = rt.open_voice_enrollment(principal_id)
    rt.complete_voice_enrollment(principal_id, session_id=e["session_id"], pcm16=b"\x01\x00" * 8000, spoken_challenge=e["challenge"])
    rt.set_default_envelope(principal_id)
    return principal_id


def test_production_config_fails_closed_without_required_integrations():
    with pytest.raises(RuntimeError, match="production config missing"):
        build_runtime(ProductConfig(environment="production"))


def test_atma_runtime_is_not_packaged_and_external_atma_is_a_principal_class():
    assert importlib.util.find_spec("vaak.atma") is None
    rt = dev_runtime()
    p = rt.register_principal(PrincipalProfile("atma_external_1", "atma-service", "External Atma", VoiceIdentityClass.ATMA_EXTERNAL, {"register":"professional"}))
    assert p.identity_class == VoiceIdentityClass.ATMA_EXTERNAL


def test_external_agent_voice_flow_and_attestation():
    async def run():
        rt = dev_runtime(); provision(rt)
        live = rt.open_live("agent_test")
        chunks = [c async for c in rt.speak_stream(live.session_id, "hello from external cognition")]
        assert chunks and sum(map(len, chunks)) > 0
        att = rt.attest(live.session_id, "nonce-1")
        assert att["statement"]["twin_id"] == "agent_test"
        manifest = rt.close_live(live.session_id)
        assert manifest["frames_emitted"] > 0
    asyncio.run(run())


def test_product_erasure_kills_open_live_session_and_new_sessions():
    rt = dev_runtime(); provision(rt)
    live = rt.open_live("agent_test")
    out = rt.core.erase("agent_test")
    assert live.session_id in out["terminated_session_ids"]
    assert live.session_id not in rt.core.sessions
    with pytest.raises(Exception):
        rt.open_live("agent_test")


def test_realtime_mode_accepts_external_agent_text():
    async def run():
        rt = dev_runtime(); provision(rt)
        live = rt.open_live("agent_test", mode="external_agent")
        convo = RealtimeConversation(rt, live.session_id, mode="external_agent")
        events = []
        async def send(obj): events.append(obj)
        await convo.handle({"type": "response.create", "text": "Hello from my agent"}, send)
        assert convo.response_task is not None
        await convo.response_task
        assert any(e["type"] == "response.text" for e in events)
        assert any(e["type"] == "response.audio.delta" for e in events)
        assert events[-1]["type"] == "response.done"
    asyncio.run(run())


def test_realtime_input_emits_external_cognition_request_and_undertone_event():
    async def run():
        rt = dev_runtime(); provision(rt)
        live = rt.open_live("agent_test")
        convo = RealtimeConversation(rt, live.session_id)
        events = []
        async def send(obj): events.append(obj)
        await convo.handle({"type": "input_audio_buffer.append", "audio": base64.b64encode(b"hello cognition host").decode()}, send)
        await convo.handle({"type": "input_audio_buffer.commit"}, send)
        assert any(e["type"] == "input_audio.transcript" and e["text"] == "hello cognition host" for e in events)
        assert any(e["type"] == "input_audio.undertone" for e in events)
        assert any(e["type"] == "response.requested" for e in events)
        assert convo.response_task is None
    asyncio.run(run())


def test_rest_api_auth_and_standalone_principal_bootstrap():
    rt = dev_runtime(); app = create_app(rt, api_keys=("secret",)); c = TestClient(app)
    assert c.post("/v1/principals", json={"controller_id":"o", "display_name":"Agent"}).status_code == 401
    h = {"Authorization": "Bearer secret"}
    r = c.post("/v1/principals", json={"principal_id":"api_agent", "controller_id":"o", "display_name":"Agent", "identity_class":"developer_agent"}, headers=h)
    assert r.status_code == 200
    e = c.post("/v1/principals/api_agent/voice/enrollment", headers=h).json()
    r = c.post("/v1/principals/api_agent/voice/enrollment/complete", headers=h, json={"session_id":e["session_id"], "audio_b64":base64.b64encode(b"\x01\x00"*8000).decode(), "spoken_challenge":e["challenge"], "languages":["en"]})
    assert r.status_code == 200
    assert c.post("/v1/principals/api_agent/envelope/default", headers=h).status_code == 200
    r = c.post("/v1/principals/api_agent/live", json={"mode":"external_agent"}, headers=h)
    assert r.status_code == 200 and "session_id" in r.json()


def test_modular_developer_audio_and_trust_endpoints():
    rt = dev_runtime(); provision(rt)
    app = create_app(rt, api_keys=("secret",)); c = TestClient(app); h = {"Authorization": "Bearer secret"}
    audio = base64.b64encode(b"developer audio").decode()
    r = c.post("/v1/audio/transcriptions", json={"audio_b64": audio, "language": "en"}, headers=h)
    assert r.status_code == 200 and r.json()["text"] == "developer audio" and "undertone" in r.json()
    r = c.get("/v1/principals/agent_test/verification-trust-bundle", headers=h)
    assert r.status_code == 200 and r.json()["scheme"].startswith("vaak-reference-trust-bundle")


def test_audio_resolution_api_resolves_closed_anchored_clip():
    async def run():
        rt = dev_runtime(); provision(rt)
        live = rt.open_live("agent_test")
        chunks = [c async for c in rt.speak_stream(live.session_id, "proof me")]
        pcm = b"".join(chunks)
        rt.close_live(live.session_id)
        rt.core.anchor()
        app = create_app(rt, api_keys=("secret",)); client = TestClient(app)
        r = client.post("/v1/audio/resolve", json={"audio_b64": base64.b64encode(pcm).decode()}, headers={"Authorization":"Bearer secret"})
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is True and body["authentic"] is True and "receipt" in body
    asyncio.run(run())
