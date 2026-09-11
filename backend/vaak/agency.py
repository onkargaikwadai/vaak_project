"""vaak.agency — Agency Integrity and paralinguistic governance.

This module governs *influence*, not truthfulness or model quality.  It is the
policy boundary between a Persona candidate response and audio emission.

Two ideas are deliberately separated:

1. Semantic influence: what the candidate text is trying to make a recipient
   do (recommend, negotiate, solicit consent, etc.).
2. Paralinguistic influence: what the rendered voice is doing acoustically
   (urgency, dominance, intimidation, guilt/fear amplification, dependency
   cues, pressure escalation).

The reference semantic scanner is intentionally conservative and incomplete;
production deployments replace it with the Company's model-based Agency
Integrity classifier.  The reference undertone analyzer consumes requested
prosody metadata so the contract can be tested without pretending a tone
stub can infer human emotion.  Production replaces it with the media team's
acoustic/paralinguistic model and runs the same post-synthesis gate before
emission.

Important invariant: signals of hesitation/distress/uncertainty may be used
to increase care and comprehension, never to increase persuasive force.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
import re
import time
from typing import Protocol

from .util import canon, sha256_hex


class AgencyIntegrityError(Exception):
    """Raised when an utterance would violate the signed Agency policy."""


class InfluenceIntent(str, Enum):
    INFORM = "inform"
    ADVISE = "advise"
    RECOMMEND = "recommend"
    NEGOTIATE = "negotiate"
    PERSUADE_DISCLOSED = "persuade_disclosed"
    SOLICIT_CONSENT = "solicit_consent"


class Beneficiary(str, Enum):
    OWNER = "owner"
    RECIPIENT = "recipient"
    THIRD_PARTY = "third_party"
    PARINITA = "parinita"
    AGENT_PLATFORM = "agent_platform"
    MIXED = "mixed"
    UNKNOWN = "unknown"


_PROSODY_DIMENSIONS = (
    "urgency",
    "dominance",
    "intimidation",
    "seductive_influence",
    "guilt_induction",
    "fear_amplification",
    "dependency_cues",
    "pressure_escalation",
)
_VULNERABILITY_DIMENSIONS = ("hesitation", "distress", "uncertainty")


def _bounded(name: str, value: float) -> float:
    v = float(value)
    if v < 0.0 or v > 1.0:
        raise AgencyIntegrityError(f"{name} must be between 0 and 1")
    return v


@dataclass(frozen=True)
class ProsodyEnvelope:
    """Maximum permitted acoustic influence dimensions (0..1)."""

    urgency: float = 0.35
    dominance: float = 0.35
    intimidation: float = 0.0
    seductive_influence: float = 0.0
    guilt_induction: float = 0.0
    fear_amplification: float = 0.0
    dependency_cues: float = 0.0
    pressure_escalation: float = 0.0
    high_stakes_urgency: float = 0.15
    high_stakes_dominance: float = 0.20

    def __post_init__(self) -> None:
        for name in _PROSODY_DIMENSIONS:
            _bounded(name, getattr(self, name))
        _bounded("high_stakes_urgency", self.high_stakes_urgency)
        _bounded("high_stakes_dominance", self.high_stakes_dominance)

    def body(self) -> dict:
        return {name: getattr(self, name) for name in _PROSODY_DIMENSIONS} | {
            "high_stakes_urgency": self.high_stakes_urgency,
            "high_stakes_dominance": self.high_stakes_dominance,
        }

    @classmethod
    def from_body(cls, body: dict | None) -> "ProsodyEnvelope":
        if not body:
            return cls()
        allowed = set(_PROSODY_DIMENSIONS) | {
            "high_stakes_urgency", "high_stakes_dominance"
        }
        return cls(**{k: float(v) for k, v in body.items() if k in allowed})


@dataclass(frozen=True)
class AgencyPolicy:
    """Owner-signed Agency Integrity policy embedded in the Envelope."""

    allowed_intents: frozenset[InfluenceIntent] = field(default_factory=lambda: frozenset({
        InfluenceIntent.INFORM,
        InfluenceIntent.ADVISE,
        InfluenceIntent.RECOMMEND,
        InfluenceIntent.NEGOTIATE,
        InfluenceIntent.PERSUADE_DISCLOSED,
        InfluenceIntent.SOLICIT_CONSENT,
    }))
    prosody: ProsodyEnvelope = field(default_factory=ProsodyEnvelope)
    vulnerability_threshold: float = 0.60
    vulnerability_influence_ceiling: float = 0.25
    refusal_closes_persuasion: bool = True
    high_stakes_requires_alternatives: bool = True
    high_stakes_requires_uncertainty: bool = True
    conflicts_require_disclosure: bool = True
    permission_escalation_inform_only: bool = True

    def __post_init__(self) -> None:
        _bounded("vulnerability_threshold", self.vulnerability_threshold)
        _bounded("vulnerability_influence_ceiling", self.vulnerability_influence_ceiling)

    def body(self) -> dict:
        return {
            "allowed_intents": sorted(i.value for i in self.allowed_intents),
            "prosody": self.prosody.body(),
            "vulnerability_threshold": self.vulnerability_threshold,
            "vulnerability_influence_ceiling": self.vulnerability_influence_ceiling,
            "refusal_closes_persuasion": self.refusal_closes_persuasion,
            "high_stakes_requires_alternatives": self.high_stakes_requires_alternatives,
            "high_stakes_requires_uncertainty": self.high_stakes_requires_uncertainty,
            "conflicts_require_disclosure": self.conflicts_require_disclosure,
            "permission_escalation_inform_only": self.permission_escalation_inform_only,
        }

    def policy_hash(self) -> str:
        return sha256_hex(canon(self.body()))

    @classmethod
    def from_body(cls, body: dict | None) -> "AgencyPolicy":
        if not body:
            return cls()
        allowed = body.get("allowed_intents")
        return cls(
            allowed_intents=frozenset(
                InfluenceIntent(i) for i in (allowed or [x.value for x in InfluenceIntent])
            ),
            prosody=ProsodyEnvelope.from_body(body.get("prosody")),
            vulnerability_threshold=float(body.get("vulnerability_threshold", 0.60)),
            vulnerability_influence_ceiling=float(
                body.get("vulnerability_influence_ceiling", 0.25)
            ),
            refusal_closes_persuasion=bool(body.get("refusal_closes_persuasion", True)),
            high_stakes_requires_alternatives=bool(
                body.get("high_stakes_requires_alternatives", True)
            ),
            high_stakes_requires_uncertainty=bool(
                body.get("high_stakes_requires_uncertainty", True)
            ),
            conflicts_require_disclosure=bool(
                body.get("conflicts_require_disclosure", True)
            ),
            permission_escalation_inform_only=bool(
                body.get("permission_escalation_inform_only", True)
            ),
        )


@dataclass(frozen=True)
class AgencyContext:
    """Untrusted influence declaration supplied with a candidate utterance.

    These fields are *hints*, not security facts.  AgencyIntegrity independently
    derives the safety-critical parts it can observe from the text and session
    state, and it only lets a declaration make enforcement more restrictive.
    In particular an agent cannot clear high-stakes, conflict, permission-
    escalation, refusal, or manipulation findings by setting these fields to a
    safer value.
    """

    intent: InfluenceIntent = InfluenceIntent.INFORM
    beneficiary: Beneficiary = Beneficiary.OWNER
    influence_intensity: float = 0.0
    high_stakes: bool = False
    alternatives_presented: bool = False
    uncertainty_disclosed: bool = False
    conflict_disclosed: bool = False
    permission_escalation: bool = False
    material_change_since_refusal: bool = False
    declared_goal: str = ""
    rationale_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded("influence_intensity", self.influence_intensity)

    def body(self) -> dict:
        return {
            "intent": self.intent.value,
            "beneficiary": self.beneficiary.value,
            "influence_intensity": self.influence_intensity,
            "high_stakes": self.high_stakes,
            "alternatives_presented": self.alternatives_presented,
            "uncertainty_disclosed": self.uncertainty_disclosed,
            "conflict_disclosed": self.conflict_disclosed,
            "permission_escalation": self.permission_escalation,
            "material_change_since_refusal": self.material_change_since_refusal,
            "declared_goal": self.declared_goal,
            "rationale_refs": list(self.rationale_refs),
        }

    @classmethod
    def from_body(cls, body: dict | None) -> "AgencyContext":
        if not body:
            return cls()
        return cls(
            intent=InfluenceIntent(body.get("intent", "inform")),
            beneficiary=Beneficiary(body.get("beneficiary", "owner")),
            influence_intensity=float(body.get("influence_intensity", 0.0)),
            high_stakes=bool(body.get("high_stakes", False)),
            alternatives_presented=bool(body.get("alternatives_presented", False)),
            uncertainty_disclosed=bool(body.get("uncertainty_disclosed", False)),
            conflict_disclosed=bool(body.get("conflict_disclosed", False)),
            permission_escalation=bool(body.get("permission_escalation", False)),
            material_change_since_refusal=bool(
                body.get("material_change_since_refusal", False)
            ),
            declared_goal=str(body.get("declared_goal", "")),
            rationale_refs=tuple(str(x) for x in body.get("rationale_refs", [])),
        )


@dataclass(frozen=True)
class UndertoneObservation:
    """Observable acoustic/paralinguistic features, never a diagnosis.

    The values are policy signals, not declarations of a person's hidden
    mental state.  Production measurements come from the media analyzer.
    """

    urgency: float = 0.0
    dominance: float = 0.0
    intimidation: float = 0.0
    seductive_influence: float = 0.0
    guilt_induction: float = 0.0
    fear_amplification: float = 0.0
    dependency_cues: float = 0.0
    pressure_escalation: float = 0.0
    hesitation: float = 0.0
    distress: float = 0.0
    uncertainty: float = 0.0
    confidence: float = 1.0
    analyzer: str = "external"

    def __post_init__(self) -> None:
        for name in _PROSODY_DIMENSIONS + _VULNERABILITY_DIMENSIONS + ("confidence",):
            _bounded(name, getattr(self, name))

    def body(self) -> dict:
        names = _PROSODY_DIMENSIONS + _VULNERABILITY_DIMENSIONS
        return {name: getattr(self, name) for name in names} | {
            "confidence": self.confidence,
            "analyzer": self.analyzer,
        }

    @property
    def vulnerability_score(self) -> float:
        return max(self.hesitation, self.distress, self.uncertainty)

    @classmethod
    def from_body(cls, body: dict | None, *, analyzer: str = "external") -> "UndertoneObservation":
        if not body:
            return cls(analyzer=analyzer)
        allowed = set(_PROSODY_DIMENSIONS + _VULNERABILITY_DIMENSIONS + ("confidence",))
        kwargs = {k: float(v) for k, v in body.items() if k in allowed}
        kwargs["analyzer"] = str(body.get("analyzer", analyzer))
        return cls(**kwargs)


class UndertoneAnalyzer(Protocol):
    def analyze_output(self, pcm: bytes, *, prosody: dict | None) -> UndertoneObservation:
        """Return observed acoustic influence features before audio emission."""
        ...


class ReferenceUndertoneAnalyzer:
    """Contract-only analyzer: maps requested prosody metadata to features.

    It intentionally does NOT claim to infer undertone from StubBackend PCM.
    Production must replace this object with an acoustic model that examines
    the rendered audio itself.
    """

    def analyze_output(self, pcm: bytes, *, prosody: dict | None) -> UndertoneObservation:
        del pcm
        return UndertoneObservation.from_body(prosody, analyzer="reference-prosody-metadata")


class SemanticRiskScanner(Protocol):
    def scan(self, text: str) -> set[str]: ...


class ReferenceSemanticRiskScanner:
    """Small deterministic safety net for contract tests, not an NLP claim."""

    _PATTERNS = {
        "artificial_urgency": (
            r"\bact now\b", r"\blast chance\b", r"\bbefore it(?:'| i)s too late\b",
            r"\bonly today\b", r"\bright now or\b",
        ),
        "guilt_pressure": (
            r"\byou owe\b", r"\bif you cared\b", r"\beveryone is counting on you\b",
            r"\bdisappoint(?:ing|ed)?\b", r"\bprove (?:that )?you(?:'| a)re committed\b",
        ),
        "reputation_pressure": (
            r"\bpeople (?:may|might|will) (?:start )?(?:question|doubt|wonder)\b",
            r"\bquestion(?:ing)? whether you(?:'| a)re (?:really )?committed\b",
            r"\bpeople will think less of you\b", r"\bwhat will people think\b",
        ),
        "fear_pressure": (
            r"\byou(?:'| wi)ll regret\b", r"\byou should be afraid\b",
            r"\blose everything\b", r"\bbad things will happen\b",
        ),
        "dependency_pressure": (
            r"\bonly i understand you\b", r"\byou need me\b", r"\bwithout me you\b",
        ),
        "coercion": (
            r"\byou have no choice\b", r"\bmust do this now\b", r"\bdon't tell anyone\b",
        ),
        "privilege_escalation": (
            r"\bgrant me access\b", r"\bgive me access\b", r"\bturn off (?:the )?restriction\b",
            r"\bdisable (?:the )?(?:guardrail|restriction)\b", r"\bremove (?:the )?guardrail\b",
        ),
        "alternatives_suppression": (
            r"\bdon't consider alternatives\b", r"\bno need to compare\b",
        ),
    }

    def scan(self, text: str) -> set[str]:
        normalized = " ".join(text.lower().split())
        risks: set[str] = set()
        for tag, patterns in self._PATTERNS.items():
            if any(re.search(p, normalized) for p in patterns):
                risks.add(tag)
        return risks


@dataclass(frozen=True)
class AgencyAssessment:
    """Independent reference classification of a candidate utterance.

    Production replaces the deterministic classifier with the Company's
    independently operated Agency Integrity model.  The assessment is always
    combined conservatively with the caller declaration: a caller may request
    stricter handling, but it cannot downgrade what the classifier observes.
    """

    intent: InfluenceIntent = InfluenceIntent.INFORM
    influence_intensity: float = 0.0
    high_stakes: bool = False
    alternatives_presented: bool = False
    uncertainty_disclosed: bool = False
    conflict_disclosed: bool = False
    permission_escalation: bool = False
    risk_tags: tuple[str, ...] = ()
    classifier: str = "reference"

    def __post_init__(self) -> None:
        _bounded("influence_intensity", self.influence_intensity)

    def body(self) -> dict:
        return {
            "intent": self.intent.value,
            "influence_intensity": self.influence_intensity,
            "high_stakes": self.high_stakes,
            "alternatives_presented": self.alternatives_presented,
            "uncertainty_disclosed": self.uncertainty_disclosed,
            "conflict_disclosed": self.conflict_disclosed,
            "permission_escalation": self.permission_escalation,
            "risk_tags": list(self.risk_tags),
            "classifier": self.classifier,
        }


class AgencyClassifier(Protocol):
    def classify(self, text: str) -> AgencyAssessment: ...


class ReferenceAgencyClassifier:
    """Deterministic independent classifier for the reference contract.

    This is intentionally a policy test double, not a claim of production NLP
    quality.  It demonstrates the trust boundary: the Persona cannot mark its
    own utterance low-risk and thereby bypass Agency Integrity.
    """

    _HIGH_STAKES = (
        r"\bwire\b", r"\btransfer (?:the )?(?:funds|money)\b", r"\bpayment\b",
        r"\bsign (?:the |this )?(?:agreement|contract|release|waiver)\b",
        r"\blegal right", r"\bmedical (?:treatment|procedure|decision)\b",
        r"\bterminate (?:the )?(?:employee|employment)\b", r"\bfire (?:the )?employee\b",
        r"\bgrant (?:me |the agent )?(?:access|permission)\b",
        r"\bcredential(?:s)?\b", r"\bpassword\b", r"\baccount access\b",
    )
    _ALTERNATIVES = (
        r"\balternative(?:s)?\b", r"\boption [a-z0-9]\b", r"\banother (?:option|choice)\b",
        r"\bother (?:option|choice|approach)\b",
    )
    _UNCERTAINTY = (
        r"\buncertain(?:ty)?\b", r"\brisk(?:s|y)?\b", r"\bmay\b", r"\bmight\b",
        r"\bcould\b", r"\bconfidence\b", r"\bi (?:do not|don't) know\b",
        r"\bnot guaranteed\b",
    )
    _CONFLICT = (
        r"\bparinita benefits\b", r"\b(?:we|i|our company) benefit(?:s)?\b",
        r"\bfinancial(?:ly)? benefit", r"\bcommission\b", r"\baffiliate\b",
        r"\bconflict of interest\b",
    )
    _PERSUADE = (
        r"\bi want to persuade you\b", r"\blet me convince you\b",
        r"\byou need to\b", r"\byou have to\b",
    )
    _RECOMMEND = (
        r"\bi recommend\b", r"\bmy recommendation\b", r"\byou should consider\b",
        r"\bi suggest\b",
    )
    _NEGOTIATE = (
        r"\bcounteroffer\b", r"\bour offer\b", r"\bmy offer\b", r"\bdeal terms\b",
        r"\bnegotiate\b",
    )
    _CONSENT = (
        r"\bdo you consent\b", r"\bplease approve\b", r"\bauthorize (?:this|the)\b",
        r"\bgrant (?:me |the agent )?(?:access|permission)\b",
    )

    def __init__(self, scanner: SemanticRiskScanner | None = None) -> None:
        self.scanner = scanner or ReferenceSemanticRiskScanner()

    @staticmethod
    def _any(patterns: tuple[str, ...], text: str) -> bool:
        return any(re.search(p, text) for p in patterns)

    def classify(self, text: str) -> AgencyAssessment:
        normalized = " ".join(text.lower().split())
        risks = self.scanner.scan(normalized)

        intent = InfluenceIntent.INFORM
        if self._any(self._RECOMMEND, normalized):
            intent = InfluenceIntent.RECOMMEND
        if self._any(self._NEGOTIATE, normalized):
            intent = InfluenceIntent.NEGOTIATE
        if self._any(self._CONSENT, normalized):
            intent = InfluenceIntent.SOLICIT_CONSENT
        if self._any(self._PERSUADE, normalized) or risks & {
            "guilt_pressure", "reputation_pressure", "fear_pressure",
            "dependency_pressure", "coercion", "artificial_urgency",
        }:
            intent = InfluenceIntent.PERSUADE_DISCLOSED

        intensity = 0.0
        if intent in {InfluenceIntent.ADVISE, InfluenceIntent.RECOMMEND}:
            intensity = 0.20
        elif intent in {InfluenceIntent.NEGOTIATE, InfluenceIntent.SOLICIT_CONSENT}:
            intensity = 0.35
        elif intent == InfluenceIntent.PERSUADE_DISCLOSED:
            intensity = 0.65
        if risks:
            intensity = max(intensity, 0.70)

        return AgencyAssessment(
            intent=intent,
            influence_intensity=intensity,
            high_stakes=self._any(self._HIGH_STAKES, normalized),
            alternatives_presented=self._any(self._ALTERNATIVES, normalized),
            uncertainty_disclosed=self._any(self._UNCERTAINTY, normalized),
            conflict_disclosed=self._any(self._CONFLICT, normalized),
            permission_escalation="privilege_escalation" in risks
            or self._any(self._CONSENT, normalized),
            risk_tags=tuple(sorted(risks)),
            classifier="reference-independent-v1",
        )


@dataclass(frozen=True)
class AgencyEvent:
    phase: str
    decision: str
    reasons: tuple[str, ...]
    context: dict
    observation: dict | None
    assessment: dict | None = None
    at: float = field(default_factory=time.time)

    def body(self) -> dict:
        return {
            "phase": self.phase,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "context": self.context,
            "observation": self.observation,
            "assessment": self.assessment,
            "at": self.at,
        }


class AgencyIntegrity:
    """Stateful manipulation firewall for one authorized voice session."""

    _PERSUASIVE = {
        InfluenceIntent.RECOMMEND,
        InfluenceIntent.NEGOTIATE,
        InfluenceIntent.PERSUADE_DISCLOSED,
        InfluenceIntent.SOLICIT_CONSENT,
    }

    def __init__(
        self,
        policy: AgencyPolicy,
        *,
        scanner: SemanticRiskScanner | None = None,
        classifier: AgencyClassifier | None = None,
        analyzer: UndertoneAnalyzer | None = None,
        default_beneficiary: Beneficiary = Beneficiary.UNKNOWN,
        output_window_ms: int = 300,
    ) -> None:
        self.policy = policy
        self.scanner = scanner or ReferenceSemanticRiskScanner()
        self.classifier = classifier or ReferenceAgencyClassifier(self.scanner)
        self.analyzer = analyzer or ReferenceUndertoneAnalyzer()
        self.default_beneficiary = default_beneficiary
        self.output_window_ms = max(50, int(output_window_ms))
        self.events: list[AgencyEvent] = []
        self.incoming_undertone = UndertoneObservation(analyzer="none")
        self.recipient_refused = False
        self._material_change_evidence: str | None = None
        self._recent_output = deque[bytes]()
        # 16 kHz mono, signed 16-bit PCM.  The session may batch at this same
        # horizon; retaining a rolling window here makes the analyzer contract
        # explicit even when a backend emits differently sized chunks.
        self._output_window_bytes = int(16_000 * 2 * self.output_window_ms / 1000)

    def observe_incoming(self, observation: UndertoneObservation) -> None:
        self.incoming_undertone = observation
        self._record("incoming_undertone", "observe", (), AgencyContext(), observation)

    def record_refusal(self) -> None:
        self.recipient_refused = True
        self._material_change_evidence = None
        self._record("recipient_state", "closed_after_refusal", (), AgencyContext(), None)

    def record_material_change(self, evidence_ref: str) -> None:
        """Trusted host signal that a refusal may be revisited once.

        Candidate text cannot self-declare this.  The reference daemon keeps
        the method off the public HTTP surface; production maps it to a
        policy-plane event whose provenance is independently authenticated.
        """
        evidence_ref = str(evidence_ref).strip()
        if not evidence_ref:
            raise AgencyIntegrityError("material change requires evidence_ref")
        self._material_change_evidence = evidence_ref
        self._record(
            "recipient_state", "material_change_recorded", (),
            AgencyContext(rationale_refs=(evidence_ref,)), None,
        )

    @staticmethod
    def _intent_rank(intent: InfluenceIntent) -> int:
        return {
            InfluenceIntent.INFORM: 0,
            InfluenceIntent.ADVISE: 1,
            InfluenceIntent.RECOMMEND: 2,
            InfluenceIntent.NEGOTIATE: 3,
            InfluenceIntent.SOLICIT_CONSENT: 3,
            InfluenceIntent.PERSUADE_DISCLOSED: 4,
        }[intent]

    def _effective_context(
        self,
        declared: AgencyContext,
        assessment: AgencyAssessment,
    ) -> AgencyContext:
        """Combine declaration and independent assessment conservatively."""
        intent = declared.intent
        if self._intent_rank(assessment.intent) > self._intent_rank(intent):
            intent = assessment.intent

        # The caller may disclose a conflict beneficiary, but may not turn an
        # unknown/non-owner session into an owner-benefit claim.  Session
        # topology supplies the default beneficiary outside the Persona.
        conflict_beneficiaries = {
            Beneficiary.THIRD_PARTY,
            Beneficiary.PARINITA,
            Beneficiary.AGENT_PLATFORM,
            Beneficiary.MIXED,
            Beneficiary.UNKNOWN,
        }
        beneficiary = self.default_beneficiary
        if declared.beneficiary in conflict_beneficiaries:
            beneficiary = declared.beneficiary

        return AgencyContext(
            intent=intent,
            beneficiary=beneficiary,
            influence_intensity=max(
                declared.influence_intensity, assessment.influence_intensity
            ),
            high_stakes=declared.high_stakes or assessment.high_stakes,
            # Presence of these disclosures is derived from the actual text;
            # self-report cannot satisfy the requirement.
            alternatives_presented=assessment.alternatives_presented,
            uncertainty_disclosed=assessment.uncertainty_disclosed,
            conflict_disclosed=assessment.conflict_disclosed,
            permission_escalation=(
                declared.permission_escalation or assessment.permission_escalation
            ),
            # Self-declared material change is intentionally ignored.  Only
            # record_material_change() can reopen persuasion after refusal.
            material_change_since_refusal=bool(self._material_change_evidence),
            declared_goal=declared.declared_goal,
            rationale_refs=declared.rationale_refs,
        )

    def inspect_pre_synthesis(
        self,
        text: str,
        context: AgencyContext,
        prosody: dict | None,
    ) -> AgencyContext:
        assessment = self.classifier.classify(text)
        context = self._effective_context(context, assessment)
        reasons: list[str] = []
        if context.intent not in self.policy.allowed_intents:
            reasons.append("intent_not_granted")

        risks = set(assessment.risk_tags)
        reasons.extend(sorted(risks))

        if (
            self.policy.permission_escalation_inform_only
            and (context.permission_escalation or "privilege_escalation" in risks)
            and (
                context.intent != InfluenceIntent.INFORM
                or context.influence_intensity > 0.0
            )
        ):
            reasons.append("permission_escalation_must_use_neutral_consent_surface")

        if (
            self.policy.refusal_closes_persuasion
            and self.recipient_refused
            and context.intent in self._PERSUASIVE
            and not context.material_change_since_refusal
        ):
            reasons.append("recipient_refusal_closed_persuasion")

        if context.high_stakes and context.intent in self._PERSUASIVE:
            if context.intent == InfluenceIntent.PERSUADE_DISCLOSED:
                reasons.append("high_stakes_persuasion_not_permitted")
            if self.policy.high_stakes_requires_alternatives and not context.alternatives_presented:
                reasons.append("high_stakes_alternatives_required")
            if self.policy.high_stakes_requires_uncertainty and not context.uncertainty_disclosed:
                reasons.append("high_stakes_uncertainty_required")

        conflict_beneficiaries = {
            Beneficiary.THIRD_PARTY,
            Beneficiary.PARINITA,
            Beneficiary.AGENT_PLATFORM,
            Beneficiary.MIXED,
            Beneficiary.UNKNOWN,
        }
        if (
            self.policy.conflicts_require_disclosure
            and context.intent in self._PERSUASIVE
            and context.beneficiary in conflict_beneficiaries
            and not context.conflict_disclosed
        ):
            reasons.append("beneficiary_conflict_disclosure_required")

        if (
            self.incoming_undertone.vulnerability_score
            >= self.policy.vulnerability_threshold
            and context.intent in self._PERSUASIVE
            and context.influence_intensity > self.policy.vulnerability_influence_ceiling
        ):
            reasons.append("vulnerability_signal_cannot_increase_influence")

        requested = UndertoneObservation.from_body(
            prosody, analyzer="requested-prosody"
        )
        reasons.extend(self._prosody_violations(requested, high_stakes=context.high_stakes))

        normalized = " ".join(text.lower().split())
        if (
            ("no pressure" in normalized or "take all the time you need" in normalized)
            and max(requested.urgency, requested.dominance, requested.pressure_escalation) > 0.30
        ):
            reasons.append("semantic_prosody_conflict")

        if reasons:
            self._record(
                "pre_synthesis", "block", tuple(sorted(set(reasons))),
                context, requested, assessment,
            )
            raise AgencyIntegrityError("; ".join(sorted(set(reasons))))
        self._record("pre_synthesis", "allow", (), context, requested, assessment)
        # A trusted material-change event authorizes one renewed persuasive
        # attempt; it is consumed only after a successful pre-synthesis gate.
        if context.intent in self._PERSUASIVE and self._material_change_evidence:
            self._material_change_evidence = None
            self.recipient_refused = False
        return context

    def inspect_output(
        self,
        pcm: bytes,
        *,
        prosody: dict | None,
        context: AgencyContext,
        record_allow: bool = True,
    ) -> UndertoneObservation:
        self._recent_output.append(bytes(pcm))
        total = sum(len(x) for x in self._recent_output)
        while self._recent_output and total > self._output_window_bytes:
            first = self._recent_output[0]
            excess = total - self._output_window_bytes
            if excess >= len(first):
                total -= len(self._recent_output.popleft())
            else:
                self._recent_output[0] = first[excess:]
                total -= excess
                break
        window = b"".join(self._recent_output)
        observation = self.analyzer.analyze_output(window, prosody=prosody)
        # Record how much acoustic history was actually considered without
        # changing the external analyzer protocol.
        if observation.analyzer and "window=" not in observation.analyzer:
            observation = replace(
                observation,
                analyzer=f"{observation.analyzer};window={len(window)//32}ms",
            )
        reasons = self._prosody_violations(observation, high_stakes=context.high_stakes)
        if reasons:
            self._record("post_synthesis_undertone", "block", tuple(reasons), context, observation)
            raise AgencyIntegrityError("; ".join(reasons))
        if record_allow:
            self._record("post_synthesis_undertone", "allow", (), context, observation)
        return observation

    def _prosody_violations(
        self,
        observation: UndertoneObservation,
        *,
        high_stakes: bool,
    ) -> list[str]:
        p = self.policy.prosody
        reasons = []
        for name in _PROSODY_DIMENSIONS:
            limit = getattr(p, name)
            if high_stakes and name == "urgency":
                limit = min(limit, p.high_stakes_urgency)
            if high_stakes and name == "dominance":
                limit = min(limit, p.high_stakes_dominance)
            if getattr(observation, name) > limit:
                reasons.append(f"prosody_{name}_exceeds_envelope")
        return reasons

    def _record(
        self,
        phase: str,
        decision: str,
        reasons: tuple[str, ...],
        context: AgencyContext,
        observation: UndertoneObservation | None,
        assessment: AgencyAssessment | None = None,
    ) -> None:
        self.events.append(AgencyEvent(
            phase=phase,
            decision=decision,
            reasons=reasons,
            context=context.body(),
            observation=observation.body() if observation else None,
            assessment=assessment.body() if assessment else None,
        ))

    def event_digest(self) -> str:
        return sha256_hex(canon([e.body() for e in self.events]))

    def report(self) -> dict:
        last = self.events[-1].body() if self.events else None
        return {
            "policy_hash": self.policy.policy_hash(),
            "recipient_refused": self.recipient_refused,
            "material_change_pending": bool(self._material_change_evidence),
            "incoming_undertone": self.incoming_undertone.body(),
            "output_window_ms": self.output_window_ms,
            "event_count": len(self.events),
            "events_digest": self.event_digest(),
            "last_event": last,
        }
