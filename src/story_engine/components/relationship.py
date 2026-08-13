from typing import Any, Dict, List

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class RelationshipTrack(BaseModel):
    value: float = 0.0
    minimum: float = -5.0
    maximum: float = 5.0
    decay_per_step: float = 0.0
    updated_step: int = 0
    provenance: Dict[str, Any] = Field(default_factory=dict)
    policy_weights: Dict[str, float] = Field(default_factory=dict)


class RelationshipTracks(Component):
    """Directional continuous tracks aggregated on one pair relationship."""

    directed: Dict[str, Dict[str, RelationshipTrack]] = Field(default_factory=dict)

    @staticmethod
    def direction_key(source: str, target: str) -> str:
        return f"{source}->{target}"

    def get(self, source: str, target: str) -> Dict[str, float]:
        return {
            track_id: track.value
            for track_id, track in self.directed.get(
                self.direction_key(source, target), {}
            ).items()
        }


class RelationshipBit(BaseModel):
    bit_id: str
    roles: Dict[str, str] = Field(default_factory=dict)
    visibility: str = "participants"
    created_step: int = 0
    expires_step: int | None = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


class RelationshipBits(Component):
    """Discrete shared statuses such as acquaintance, kinship, rivalry or employment."""

    bits: Dict[str, RelationshipBit] = Field(default_factory=dict)


class RelationshipTimeline(Component):
    first_met_step: int = 0
    last_interaction_step: int = 0
    important_event_refs: List[str] = Field(default_factory=list)
