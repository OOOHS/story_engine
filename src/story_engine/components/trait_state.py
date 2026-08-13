from typing import Any, Dict, Iterable

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class TraitProfile(BaseModel):
    """A data-defined personality tendency used by the host policy."""

    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str = ""
    policy_weights: Dict[str, float] = Field(default_factory=dict)


class TraitState(Component):
    """Stable predisposition parameters, not a live stream of mental state.

    Legacy Host policy consumes ``policy_weights``. Persistent subjects receive
    the bounded trait profile only at identity bootstrap and own subsequent
    appraisal and expression.
    """

    traits: Dict[str, TraitProfile] = Field(default_factory=dict)

    @classmethod
    def from_initial(cls, traits: Iterable[Any] = ()) -> "TraitState":
        profiles: Dict[str, TraitProfile] = {}
        for raw in traits or []:
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if not isinstance(raw, dict):
                continue
            trait_id = " ".join(
                str(raw.get("trait_id") or raw.get("name") or "").split()
            ).strip()[:80]
            if not trait_id or trait_id in profiles:
                continue
            weights = {}
            for tag, value in dict(raw.get("policy_weights", {}) or {}).items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                tag_key = " ".join(str(tag).split()).strip()[:80]
                if tag_key:
                    weights[tag_key] = max(-5.0, min(5.0, float(value)))
            profiles[trait_id] = TraitProfile(
                intensity=raw.get("intensity", 1.0),
                description=" ".join(str(raw.get("description", "")).split()).strip()[:300],
                policy_weights=weights,
            )
        return cls(traits=profiles)

    def score_tags(self, tags: Iterable[str]) -> tuple[float, Dict[str, float]]:
        tag_set = {str(tag).strip() for tag in tags if str(tag).strip()}
        contributions: Dict[str, float] = {}
        total = 0.0
        for trait_id, profile in self.traits.items():
            contribution = profile.intensity * sum(
                profile.policy_weights.get(tag, 0.0) for tag in tag_set
            )
            if contribution:
                contributions[trait_id] = round(contribution, 6)
                total += contribution
        return total, contributions

    def get_private_snapshot(self) -> Dict[str, Any]:
        return {
            trait_id: {
                "intensity": profile.intensity,
                "description": profile.description,
            }
            for trait_id, profile in self.traits.items()
        }
