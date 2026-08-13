from copy import deepcopy
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class SentimentRecord(BaseModel):
    sentiment_id: str
    toward: str
    kind: str
    intensity: float = Field(ge=0.0, le=1.0)
    valence: float = Field(ge=-1.0, le=1.0)
    reason: str
    created_step: int = 0
    updated_step: int = 0
    expires_step: int | None = None
    decay_per_step: float = Field(default=0.0, ge=0.0, le=1.0)
    source_event: str = ""
    policy_weights: Dict[str, float] = Field(default_factory=dict)


class SentimentState(Component):
    """Host social-response proxy used by legacy policy and relationship rules.

    It is not authoritative evidence of what a persistent Hermes subject
    consciously feels. Hermes receives the underlying observable events and
    owns appraisal/emotion internally; this proxy is not projected into its
    subject ledger.
    """

    sentiments: Dict[str, SentimentRecord] = Field(default_factory=dict)
    max_sentiments: int = Field(default=32, ge=0, le=100)

    def upsert(
        self,
        *,
        toward: str,
        kind: str,
        magnitude: float,
        valence: float,
        reason: str,
        current_step: int,
        duration_steps: int,
        decay_per_step: float,
        policy_weights: Dict[str, float],
        source_event: str = "",
    ) -> SentimentRecord:
        sentiment_id = f"{toward}:{kind}"
        magnitude = min(1.0, max(0.0, float(magnitude)))
        record = self.sentiments.get(sentiment_id)
        if record is None:
            if len(self.sentiments) >= self.max_sentiments:
                oldest = min(
                    self.sentiments.values(),
                    key=lambda item: (item.updated_step, item.sentiment_id),
                )
                self.sentiments.pop(oldest.sentiment_id, None)
            record = SentimentRecord(
                sentiment_id=sentiment_id,
                toward=toward,
                kind=kind,
                intensity=magnitude,
                valence=valence,
                reason=reason,
                created_step=int(current_step),
                updated_step=int(current_step),
                expires_step=int(current_step) + max(1, int(duration_steps)),
                decay_per_step=decay_per_step,
                source_event=source_event,
                policy_weights=deepcopy(policy_weights),
            )
            self.sentiments[sentiment_id] = record
            return record

        # Repeated compatible experiences saturate rather than add without bound.
        record.intensity = min(
            1.0,
            1.0 - (1.0 - record.intensity) * (1.0 - magnitude),
        )
        record.valence = valence
        record.reason = reason
        record.updated_step = int(current_step)
        record.expires_step = int(current_step) + max(1, int(duration_steps))
        record.decay_per_step = decay_per_step
        record.source_event = source_event
        record.policy_weights = deepcopy(policy_weights)
        return record

    def advance_to(self, step: int) -> list[Dict[str, Any]]:
        transitions = []
        target_step = int(step)
        for sentiment_id, record in list(self.sentiments.items()):
            elapsed = target_step - int(record.updated_step)
            before = record.intensity
            if elapsed > 0 and record.decay_per_step > 0:
                record.intensity = max(
                    0.0, before - record.decay_per_step * elapsed
                )
                record.updated_step = target_step
            expired = (
                record.expires_step is not None
                and target_step > record.expires_step
            ) or record.intensity <= 0.001
            if expired:
                self.sentiments.pop(sentiment_id, None)
                transitions.append(
                    {
                        "sentiment_id": sentiment_id,
                        "status": "expired",
                        "before": before,
                    }
                )
            elif record.intensity != before:
                transitions.append(
                    {
                        "sentiment_id": sentiment_id,
                        "status": "decayed",
                        "before": before,
                        "after": record.intensity,
                    }
                )
        return transitions

    def score_tags(
        self, toward: str, tags: set[str]
    ) -> tuple[float, Dict[str, float]]:
        total = 0.0
        contributions: Dict[str, float] = {}
        for sentiment_id, record in self.sentiments.items():
            if record.toward != toward:
                continue
            contribution = record.intensity * sum(
                record.policy_weights.get(tag, 0.0) for tag in tags
            )
            if contribution:
                contributions[sentiment_id] = round(contribution, 6)
                total += contribution
        return total, contributions

    def get_private_snapshot(self) -> Dict[str, Any]:
        ordered = sorted(
            self.sentiments.values(),
            key=lambda item: (-item.intensity, item.updated_step, item.sentiment_id),
        )
        return {
            "active": [
                {
                    "sentiment_id": item.sentiment_id,
                    "toward": item.toward,
                    "kind": item.kind,
                    "intensity": item.intensity,
                    "valence": item.valence,
                    "reason": item.reason,
                    "created_step": item.created_step,
                    "updated_step": item.updated_step,
                    "expires_step": item.expires_step,
                    "source_event": item.source_event,
                }
                for item in ordered
            ]
        }

    def restore_from(self, snapshot: "SentimentState") -> None:
        self.sentiments = deepcopy(snapshot.sentiments)
        self.max_sentiments = snapshot.max_sentiments
