from typing import Any, Dict, Iterable

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class TraitProfile(BaseModel):
    """A data-defined personality tendency, authored once at identity bootstrap."""

    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str = ""


class TraitState(Component):
    """Stable predisposition parameters, not a live stream of mental state.

    Nothing on the Host turns these into a preference over a character's
    options; a persistent subject receives the bounded trait profile once at
    identity bootstrap and owns every appraisal and expression after that.
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
            profiles[trait_id] = TraitProfile(
                intensity=raw.get("intensity", 1.0),
                description=" ".join(str(raw.get("description", "")).split()).strip()[:300],
            )
        return cls(traits=profiles)

    def get_private_snapshot(self) -> Dict[str, Any]:
        return {
            trait_id: {
                "intensity": profile.intensity,
                "description": profile.description,
            }
            for trait_id, profile in self.traits.items()
        }
