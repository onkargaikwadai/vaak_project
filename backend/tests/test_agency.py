"""Agency Integrity, undertone, and stronger trust-chain contract tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from vaak.agency import (
    AgencyContext,
    AgencyIntegrityError,
    AgencyPolicy,
    Beneficiary,
    InfluenceIntent,
    ProsodyEnvelope,
    UndertoneObservation,
)
from vaak.daemon import VaakCore
from vaak.envelope import ChannelClass, VoiceVerb
from vaak.provenance import UtteranceReceipt
from vaak.service import VaakService

MODEL = b"agency-integrity-test-model" * 16


def svc(*, policy: AgencyPolicy | None = None) -> VaakService:
    s = VaakService()
    s.enroll("twin-a", MODEL)
    s.set_envelope(
        "twin-a",
        version=3,
        granted={VoiceVerb.SPEAK_PRIVATE, VoiceVerb.TELEPHONY_SPEAK},
        agency_policy=policy,
    )
    return s


def session(s: VaakService) -> str:
    return s.open_session("twin-a", VoiceVerb.SPEAK_PRIVATE, ChannelClass.LOCAL)


def recommend(**kwargs) -> AgencyContext:
    base = dict(
        intent=InfluenceIntent.RECOMMEND,
        beneficiary=Beneficiary.OWNER,
        influence_intensity=0.2,
        alternatives_presented=True,
        uncertainty_disclosed=True,
    )
    base.update(kwargs)
    return AgencyContext(**base)


def test_benign_speech_is_allowed_and_audited():
    s = svc()
    sid = session(s)
    assert s.speak(sid, "Here are the three options.")
    report = s.agency_report(sid)
    assert report["event_count"] == 2  # pre + one post-synthesis allow event
    assert report["last_event"]["phase"] == "post_synthesis_undertone"
    pre = s.core.sessions[sid]._agency.events[0].body()
    assert pre["assessment"]["classifier"] == "reference-independent-v1"
    m = s.close(sid)
    assert m["agency_event_count"] == 2
    assert m["agency_events_digest"] == report["events_digest"]


def test_semantic_guilt_pressure_is_blocked():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="guilt_pressure"):
        s.speak(
            sid,
            "Everyone is counting on you, so approve it.",
            agency=recommend(influence_intensity=0.6),
        )


def test_agent_cannot_persuade_for_its_own_permission_escalation():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="permission_escalation"):
        s.speak(
            sid,
            "Grant me access so I can work better.",
            agency=AgencyContext(
                intent=InfluenceIntent.PERSUADE_DISCLOSED,
                beneficiary=Beneficiary.AGENT_PLATFORM,
                influence_intensity=0.4,
                permission_escalation=True,
                conflict_disclosed=True,
            ),
        )


def test_permission_request_can_be_neutrally_explained():
    s = svc()
    sid = session(s)
    assert s.speak(
        sid,
        "This task requires calendar access. The system consent screen controls the choice.",
        agency=AgencyContext(
            intent=InfluenceIntent.INFORM,
            beneficiary=Beneficiary.OWNER,
            permission_escalation=True,
            influence_intensity=0.0,
        ),
    )


def test_refusal_closes_persuasion_until_material_change():
    s = svc()
    sid = session(s)
    s.record_recipient_refusal(sid)
    with pytest.raises(AgencyIntegrityError, match="recipient_refusal_closed_persuasion"):
        s.speak(sid, "I still recommend option B.", agency=recommend())
    # The candidate utterance cannot self-declare a material change.  Only
    # the trusted host policy plane can reopen the persuasion state.
    with pytest.raises(AgencyIntegrityError, match="recipient_refusal_closed_persuasion"):
        s.speak(
            sid,
            "The price changed materially; here is the updated recommendation.",
            agency=recommend(material_change_since_refusal=True),
        )
    s.record_material_change(sid, "pricing-feed:event-42")
    assert s.speak(
        sid,
        "The price changed materially; here is the updated recommendation.",
        agency=recommend(),
    )


def test_agent_cannot_self_label_manipulation_as_informational():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="reputation_pressure"):
        s.speak(
            sid,
            "If you don't approve this, people may start questioning whether you're really committed to the team.",
            agency=AgencyContext(
                intent=InfluenceIntent.INFORM,
                beneficiary=Beneficiary.OWNER,
                influence_intensity=0.0,
                high_stakes=False,
            ),
        )


def test_high_stakes_and_disclosures_are_derived_from_text_not_self_report():
    s = svc()
    sid = session(s)
    # Caller falsely labels a fund transfer as low-stakes and claims the
    # required disclosures were made.  The independent classifier overrides
    # the downgrade and the text itself does not contain the disclosures.
    with pytest.raises(AgencyIntegrityError, match="high_stakes_alternatives_required"):
        s.speak(
            sid,
            "I recommend you transfer the funds.",
            agency=AgencyContext(
                intent=InfluenceIntent.INFORM,
                beneficiary=Beneficiary.OWNER,
                influence_intensity=0.0,
                high_stakes=False,
                alternatives_presented=True,
                uncertainty_disclosed=True,
            ),
        )


def test_high_stakes_recommendation_requires_alternatives_and_uncertainty():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="high_stakes_alternatives_required"):
        s.speak(
            sid,
            "I recommend signing the agreement.",
            agency=recommend(
                high_stakes=True,
                alternatives_presented=False,
                uncertainty_disclosed=False,
            ),
        )
    assert s.speak(
        sid,
        "I recommend option B; option A is viable, and the outcome is uncertain.",
        agency=recommend(high_stakes=True),
    )


def test_high_stakes_direct_persuasion_is_blocked_even_if_disclosed():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="high_stakes_persuasion_not_permitted"):
        s.speak(
            sid,
            "I want to persuade you to transfer the funds.",
            agency=AgencyContext(
                intent=InfluenceIntent.PERSUADE_DISCLOSED,
                beneficiary=Beneficiary.OWNER,
                influence_intensity=0.2,
                high_stakes=True,
                alternatives_presented=True,
                uncertainty_disclosed=True,
            ),
        )


def test_non_owner_beneficiary_requires_conflict_disclosure():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="beneficiary_conflict_disclosure_required"):
        s.speak(
            sid,
            "I recommend this service.",
            agency=recommend(beneficiary=Beneficiary.PARINITA, conflict_disclosed=False),
        )
    assert s.speak(
        sid,
        "Parinita benefits financially from this recommendation; here are alternatives.",
        agency=recommend(beneficiary=Beneficiary.PARINITA, conflict_disclosed=True),
    )


def test_vulnerability_signal_can_reduce_but_not_increase_influence():
    s = svc()
    sid = session(s)
    s.observe_incoming_undertone(
        sid,
        UndertoneObservation(
            hesitation=0.75, uncertainty=0.8, confidence=0.85, analyzer="test-acoustic"
        ),
    )
    with pytest.raises(AgencyIntegrityError, match="vulnerability_signal_cannot_increase_influence"):
        s.speak(
            sid,
            "I recommend option B.",
            agency=recommend(influence_intensity=0.7),
        )
    assert s.speak(
        sid,
        "There is no need to decide now. I can explain the options.",
        agency=AgencyContext(
            intent=InfluenceIntent.ADVISE,
            beneficiary=Beneficiary.RECIPIENT,
            influence_intensity=0.0,
        ),
    )


def test_requested_undertone_outside_signed_prosody_envelope_is_blocked():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="prosody_intimidation_exceeds_envelope"):
        s.speak(
            sid,
            "Please review the proposal.",
            prosody={"intimidation": 0.5},
        )


def test_high_stakes_urgency_ceiling_is_stricter():
    s = svc()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="prosody_urgency_exceeds_envelope"):
        s.speak(
            sid,
            "I recommend option B, with alternatives and uncertainty disclosed.",
            prosody={"urgency": 0.2},
            agency=recommend(high_stakes=True),
        )


def test_semantic_prosody_mismatch_is_blocked():
    policy = AgencyPolicy(prosody=ProsodyEnvelope(urgency=0.8, dominance=0.8))
    s = svc(policy=policy)
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="semantic_prosody_conflict"):
        s.speak(
            sid,
            "There is no pressure; take all the time you need.",
            prosody={"urgency": 0.7},
        )


def test_post_synthesis_acoustic_gate_can_block_backend_output():
    class UnsafeAcousticAnalyzer:
        def analyze_output(self, pcm: bytes, *, prosody: dict | None):
            assert pcm
            return UndertoneObservation(
                intimidation=0.9, confidence=0.95, analyzer="test-post-synthesis"
            )

    s = svc()
    s.core.undertone_analyzer = UnsafeAcousticAnalyzer()
    sid = session(s)
    with pytest.raises(AgencyIntegrityError, match="prosody_intimidation_exceeds_envelope"):
        s.speak(sid, "Text is harmless and requested prosody is neutral.")


def test_post_synthesis_analyzer_receives_temporal_window_not_single_frame():
    seen = []

    class WindowAnalyzer:
        def analyze_output(self, pcm: bytes, *, prosody: dict | None):
            seen.append(len(pcm))
            return UndertoneObservation(confidence=0.95, analyzer="window-test")

    s = svc()
    s.core.undertone_analyzer = WindowAnalyzer()
    sid = session(s)
    assert s.speak(
        sid,
        "one two three four five six seven eight nine ten eleven twelve",
    )
    # StubBackend emits 320-byte / 10 ms chunks.  The gate must see a
    # pre-emission temporal buffer, not a single 320-byte frame.
    assert seen
    assert max(seen) > 320


def test_agency_events_are_committed_into_receipt_and_tamper_fails():
    s = svc()
    sid = session(s)
    s.speak(sid, "Here is my recommendation.", agency=recommend())
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    trust = s.verification_trust_bundle("twin-a")
    assert VaakService.verify_receipt(rec, trust)

    evidence = dict(rec.authorization_evidence)
    events = [dict(e) for e in evidence["agency_events"]]
    events[0] = dict(events[0], decision="block")
    evidence["agency_events"] = events
    forged = replace(rec, authorization_evidence=evidence)
    assert not VaakService.verify_receipt(forged, trust)


def test_receipt_rejects_wrong_owner_trust_key_even_with_valid_anchor():
    s = svc()
    sid = session(s)
    s.speak(sid, "proof")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    trust = s.verification_trust_bundle("twin-a")

    other = VaakService()
    other.enroll("other", MODEL)
    wrong = dict(trust)
    wrong["owner_public_key_pem"] = other.core.owner_for("other").public_pem
    assert not VaakService.verify_receipt(rec, wrong)


def test_receipt_rejects_tampered_credential_status_proof():
    s = svc()
    sid = session(s)
    s.speak(sid, "proof")
    m = s.close(sid)
    s.core.anchor()
    rec = s.receipt(m["manifest_hash"])
    trust = s.verification_trust_bundle("twin-a")

    evidence = dict(rec.authorization_evidence)
    status = dict(evidence["credential_status"])
    status["statement"] = dict(status["statement"], status="revoked")
    evidence["credential_status"] = status
    forged = replace(rec, authorization_evidence=evidence)
    assert not VaakService.verify_receipt(forged, trust)


def test_live_attestation_verifies_against_trust_bundle_and_status_proof():
    s = svc()
    sid = session(s)
    s.speak(sid, "hello")
    att = s.attest(sid, "counterparty-nonce")
    trust = s.verification_trust_bundle("twin-a")
    assert VaakService.verify_attestation(att, trust)

    forged = dict(att)
    forged["statement"] = dict(att["statement"], twin_id="forged")
    assert not VaakService.verify_attestation(forged, trust)


def test_agency_policy_is_owner_signed_as_part_of_envelope():
    policy = AgencyPolicy(vulnerability_influence_ceiling=0.1)
    s = svc(policy=policy)
    env = s.core.envelopes["twin-a"].envelope
    assert env.body()["agency_policy"]["vulnerability_influence_ceiling"] == 0.1
    assert env.envelope_hash()
