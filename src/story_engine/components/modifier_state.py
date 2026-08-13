from copy import deepcopy
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class ModifierRecord(BaseModel):
    modifier_id: str
    kind: str
    description: str = ""
    intensity: float = Field(ge=0.0, le=1.0)
    stacks: int = Field(default=1, ge=1, le=8)
    created_step: int = 0
    updated_step: int = 0
    expires_step: Optional[int] = None
    reason: str = ""
    source: str = ""
    source_event: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)
    policy_weights: Dict[str, float] = Field(default_factory=dict)


class ModifierState(Component):
    """Private, temporary, non-social conditions affecting character policy."""

    modifiers: Dict[str, ModifierRecord] = Field(default_factory=dict)
    max_modifiers: int = Field(default=24, ge=0, le=64)

    def apply(
        self,
        *,
        kind: str,
        description: str,
        magnitude: float,
        current_step: int,
        duration_steps: int,
        stacking: Literal["refresh", "stack", "replace"],
        max_stacks: int,
        policy_weights: Dict[str, float],
        reason: str,
        source: str = "",
        source_event: str = "",
        provenance: Dict[str, Any] | None = None,
    ) -> ModifierRecord:
        modifier_id = str(kind)
        magnitude = min(1.0, max(0.0, float(magnitude)))
        expires_step = int(current_step) + max(1, int(duration_steps))
        record = self.modifiers.get(modifier_id)
        if record is None:
            if len(self.modifiers) >= self.max_modifiers:
                oldest = min(
                    self.modifiers.values(),
                    key=lambda item: (item.updated_step, item.modifier_id),
                )
                self.modifiers.pop(oldest.modifier_id, None)
            record = ModifierRecord(
                modifier_id=modifier_id,
                kind=kind,
                description=description,
                intensity=magnitude,
                stacks=1,
                created_step=int(current_step),
                updated_step=int(current_step),
                expires_step=expires_step,
                reason=reason,
                source=source,
                source_event=source_event,
                provenance=deepcopy(provenance or {}),
                policy_weights=deepcopy(policy_weights),
            )
            self.modifiers[modifier_id] = record
            return record

        if stacking == "replace":
            record.intensity = magnitude
            record.stacks = 1
        elif stacking == "stack":
            record.intensity = min(
                1.0,
                1.0 - (1.0 - record.intensity) * (1.0 - magnitude),
            )
            record.stacks = min(max(1, int(max_stacks)), record.stacks + 1)
        else:
            record.intensity = max(record.intensity, magnitude)
        record.description = description
        record.updated_step = int(current_step)
        record.expires_step = max(int(record.expires_step or 0), expires_step)
        record.reason = reason
        record.source = source
        record.source_event = source_event
        record.provenance = deepcopy(provenance or {})
        record.policy_weights = deepcopy(policy_weights)
        return record

    def remove(self, kind: str) -> Optional[ModifierRecord]:
        return self.modifiers.pop(str(kind), None)

    def advance_to(self, step: int) -> list[Dict[str, Any]]:
        transitions = []
        target = int(step)
        for modifier_id, record in list(self.modifiers.items()):
            if record.expires_step is None or target < int(record.expires_step):
                continue
            self.modifiers.pop(modifier_id, None)
            transitions.append(
                {
                    "modifier_id": modifier_id,
                    "kind": record.kind,
                    "status": "expired",
                    "intensity": record.intensity,
                    "stacks": record.stacks,
                }
            )
        return transitions

    def score_tags(self, tags: set[str]) -> tuple[float, Dict[str, float]]:
        total = 0.0
        contributions: Dict[str, float] = {}
        for modifier_id, record in self.modifiers.items():
            stack_scale = 1.0 + 0.25 * max(0, record.stacks - 1)
            contribution = record.intensity * stack_scale * sum(
                float(record.policy_weights.get(tag, 0.0) or 0.0)
                for tag in tags
            )
            if contribution:
                contributions[modifier_id] = round(contribution, 6)
                total += contribution
        return total, contributions

    def get_private_snapshot(self) -> Dict[str, Any]:
        ordered = sorted(
            self.modifiers.values(),
            key=lambda item: (-item.intensity, item.expires_step or 10**12, item.kind),
        )
        return {
            "active": [
                {
                    "modifier_id": item.modifier_id,
                    "kind": item.kind,
                    "description": item.description,
                    "intensity": item.intensity,
                    "stacks": item.stacks,
                    "created_step": item.created_step,
                    "updated_step": item.updated_step,
                    "expires_step": item.expires_step,
                    "reason": item.reason,
                    "source": item.source,
                    "source_event": item.source_event,
                }
                for item in ordered
            ],
            "active_count": len(ordered),
        }

    def restore_from(self, snapshot: "ModifierState") -> None:
        self.modifiers = deepcopy(snapshot.modifiers)
        self.max_modifiers = snapshot.max_modifiers
