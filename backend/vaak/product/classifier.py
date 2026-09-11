from __future__ import annotations

from dataclasses import dataclass

from ..agency import AgencyAssessment, InfluenceIntent


@dataclass
class AgencyClassifierConfig:
    base_url: str
    api_key: str | None = None
    timeout_s: float = 4.0
    fail_closed: bool = True
    classifier_name: str = "agency-http"


class HTTPAgencyClassifier:
    """Independent production Agency Integrity classifier adapter."""
    def __init__(self, config: AgencyClassifierConfig):
        self.config = config

    def classify(self, text: str) -> AgencyAssessment:
        import httpx
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        try:
            with httpx.Client(timeout=self.config.timeout_s) as client:
                r = client.post(f"{self.config.base_url.rstrip('/')}/v1/classify", json={"text": text}, headers=headers)
                r.raise_for_status()
                obj = r.json()
            return AgencyAssessment(
                intent=InfluenceIntent(obj.get("intent", "inform")),
                influence_intensity=float(obj.get("influence_intensity", 0.0)),
                high_stakes=bool(obj.get("high_stakes", False)),
                alternatives_presented=bool(obj.get("alternatives_presented", False)),
                uncertainty_disclosed=bool(obj.get("uncertainty_disclosed", False)),
                conflict_disclosed=bool(obj.get("conflict_disclosed", False)),
                permission_escalation=bool(obj.get("permission_escalation", False)),
                risk_tags=tuple(str(x) for x in obj.get("risk_tags", [])),
                classifier=str(obj.get("classifier", self.config.classifier_name)),
            )
        except Exception:
            if not self.config.fail_closed:
                raise
            return AgencyAssessment(
                intent=InfluenceIntent.PERSUADE_DISCLOSED,
                influence_intensity=1.0,
                high_stakes=True,
                risk_tags=("classifier_unavailable_fail_closed",),
                classifier=f"{self.config.classifier_name}-fail-closed",
            )


class EnsembleAgencyClassifier:
    """Conservative multi-model Agency classifier.

    Intended production families include independently operated Meta Llama and
    DeepSeek classifiers. No member can vote a risk away: risk is merged by OR,
    maximum influence intensity and union of risk tags. This keeps the
    independent-classifier invariant while reducing single-model blind spots.
    """

    _RANK = {
        InfluenceIntent.INFORM: 0,
        InfluenceIntent.ADVISE: 1,
        InfluenceIntent.RECOMMEND: 2,
        InfluenceIntent.NEGOTIATE: 3,
        InfluenceIntent.SOLICIT_CONSENT: 4,
        InfluenceIntent.PERSUADE_DISCLOSED: 5,
    }

    def __init__(self, classifiers):
        self.classifiers = tuple(classifiers)
        if not self.classifiers:
            raise ValueError("at least one Agency classifier is required")

    def classify(self, text: str) -> AgencyAssessment:
        results = [c.classify(text) for c in self.classifiers]
        intent = max((r.intent for r in results), key=lambda x: self._RANK.get(x, 99))
        return AgencyAssessment(
            intent=intent,
            influence_intensity=max(r.influence_intensity for r in results),
            high_stakes=any(r.high_stakes for r in results),
            alternatives_presented=all(r.alternatives_presented for r in results),
            uncertainty_disclosed=all(r.uncertainty_disclosed for r in results),
            conflict_disclosed=all(r.conflict_disclosed for r in results),
            permission_escalation=any(r.permission_escalation for r in results),
            risk_tags=tuple(sorted({t for r in results for t in r.risk_tags})),
            classifier="ensemble:" + "+".join(r.classifier for r in results),
        )
