from dataclasses import dataclass
from typing import Any, Dict, List, Literal

from src.story_engine.common.observation_window import (
    actor_observation_locations,
    shares_action_location,
)


@dataclass(frozen=True)
class ModifierDefinition:
    kind: str
    description: str
    duration_steps: int
    stacking: Literal["refresh", "stack", "replace"]
    max_stacks: int
    policy_weights: Dict[str, float]


MODIFIER_DEFINITIONS = {
    "exhausted": ModifierDefinition(
        "exhausted",
        "体力和注意力暂时透支，更倾向休息并回避冒险。",
        4,
        "stack",
        3,
        {"rest": 0.7, "patient": 0.25, "risk": -0.4, "confront": -0.2},
    ),
    "injured": ModifierDefinition(
        "injured",
        "伤势暂时限制行动，使角色更谨慎。物理伤势事实仍应写入 SceneState。",
        8,
        "refresh",
        1,
        {"retreat": 0.5, "cautious": 0.45, "rest": 0.35, "risk": -0.65},
    ),
    "focused": ModifierDefinition(
        "focused",
        "注意力暂时集中，更倾向调查和审慎行动。",
        4,
        "refresh",
        1,
        {"information": 0.65, "cautious": 0.25, "patient": 0.15},
    ),
    "inspired": ModifierDefinition(
        "inspired",
        "暂时受到鼓舞，更愿意主动协作或承担行动。",
        5,
        "stack",
        2,
        {"aid": 0.4, "social": 0.3, "risk": 0.2, "patient": 0.1},
    ),
    "shaken": ModifierDefinition(
        "shaken",
        "受到冲击但不指向特定人物，暂时更谨慎和退缩。",
        3,
        "refresh",
        1,
        {"retreat": 0.45, "cautious": 0.4, "risk": -0.35, "confront": -0.2},
    ),
}


class ModifierDynamics:
    """Validate semantic modifier requests against committed action evidence."""

    MAX_UPDATES = 12
    FORBIDDEN_FIELDS = {
        "duration_steps",
        "expires_step",
        "policy_weights",
        "stacking",
        "max_stacks",
        "stacks",
    }

    def __init__(self, definitions: Dict[str, Any] | None = None) -> None:
        self.definitions = dict(MODIFIER_DEFINITIONS)
        for kind, raw in (definitions or {}).items():
            if isinstance(raw, ModifierDefinition):
                definition = raw
            elif isinstance(raw, dict):
                definition = ModifierDefinition(
                    kind=str(raw.get("kind") or kind),
                    description=str(raw.get("description", "")),
                    duration_steps=max(1, int(raw.get("duration_steps", 1))),
                    stacking=str(raw.get("stacking", "refresh")),
                    max_stacks=max(1, min(8, int(raw.get("max_stacks", 1)))),
                    policy_weights={
                        str(tag): float(weight)
                        for tag, weight in dict(raw.get("policy_weights", {})).items()
                    },
                )
            else:
                continue
            if definition.stacking not in {"refresh", "stack", "replace"}:
                raise ValueError(f"invalid modifier stacking rule: {definition.kind}")
            self.definitions[str(kind)] = definition

    def public_catalog(self) -> List[Dict[str, Any]]:
        return [
            {"kind": kind, "description": definition.description}
            for kind, definition in sorted(self.definitions.items())
        ]

    def apply(
        self,
        *,
        modifier_states: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
        current_step: int,
        observation_windows: Any = None,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        updates = result.get("modifier_updates", [])
        if not isinstance(updates, list):
            return [], ["modifier_updates must be a list"]
        if len(updates) > self.MAX_UPDATES:
            return [], [f"modifier_updates cannot exceed {self.MAX_UPDATES} per turn"]
        actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        applied: List[Dict[str, Any]] = []
        errors: List[str] = []
        known_actors = set(scene_state.actor_states) if scene_state else set()
        for index, update in enumerate(updates):
            prefix = f"modifier_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            forbidden = sorted(self.FORBIDDEN_FIELDS.intersection(update))
            if forbidden:
                errors.append(
                    f"{prefix} contains host-owned fields: {', '.join(forbidden)}"
                )
            operation = self._text(update.get("operation") or "apply", 20).lower()
            target = self._text(update.get("target"), 120)
            source = self._text(update.get("source"), 120)
            kind = self._text(update.get("kind"), 60).lower()
            reason = self._text(update.get("reason"), 500)
            definition = self.definitions.get(kind)
            if operation not in {"apply", "remove"}:
                errors.append(f"{prefix} has invalid operation: {operation}")
            if target not in known_actors or target not in modifier_states:
                errors.append(f"{prefix} target has no character ModifierState: {target}")
            if source not in known_actors and source != "World":
                errors.append(f"{prefix} source must be an existing actor or World")
            if definition is None:
                errors.append(f"{prefix} has unknown modifier kind: {kind}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            try:
                magnitude = float(update.get("magnitude", 0.5))
                if not 0.05 <= magnitude <= 1.0:
                    errors.append(f"{prefix}.magnitude must be between 0.05 and 1")
            except (TypeError, ValueError):
                magnitude = 0.5
                errors.append(f"{prefix}.magnitude must be numeric")
            evidence = self._find_evidence(
                actions,
                scene_state=scene_state,
                source=source,
                target=target,
                observation_windows=observation_windows,
            )
            if evidence is None:
                errors.append(f"{prefix} lacks a committed source action")
            if any(error.startswith(prefix) for error in errors):
                continue
            state = modifier_states[target]
            hidden_source = (
                source != target
                and str(evidence.get("visibility", "public")) == "hidden"
            )
            disclosed_source = "" if hidden_source else source
            disclosed_reason = (
                "发生了一项暂时无法明确归因的变化。"
                if hidden_source
                else reason
            )
            if operation == "remove":
                removed = state.remove(kind)
                if removed is None:
                    errors.append(f"{prefix} cannot remove inactive modifier: {kind}")
                    continue
                applied.append(
                    {
                        "operation": "remove",
                        "target": target,
                        "kind": kind,
                        "reason": disclosed_reason,
                    }
                )
                continue
            record = state.apply(
                kind=kind,
                description=definition.description,
                magnitude=magnitude,
                current_step=current_step,
                duration_steps=definition.duration_steps,
                stacking=definition.stacking,
                max_stacks=definition.max_stacks,
                policy_weights=definition.policy_weights,
                reason=disclosed_reason,
                source=disclosed_source,
                source_event=(
                    ""
                    if hidden_source
                    else f"resolved_action:step:{int(current_step)}:actor:{source}"
                ),
                provenance={
                    "source_kind": "resolved_action",
                    "source_ref": f"step:{int(current_step)}:actor:{source}",
                },
            )
            applied.append(
                {
                    "operation": "apply",
                    "target": target,
                    "kind": kind,
                    "intensity": record.intensity,
                    "stacks": record.stacks,
                    "expires_step": record.expires_step,
                    "reason": disclosed_reason,
                }
            )
        return applied, errors

    @staticmethod
    def _find_evidence(
        actions: List[Dict[str, Any]],
        *,
        scene_state: Any,
        source: str,
        target: str,
        observation_windows: Any = None,
    ) -> Dict[str, Any] | None:
        target_locations = actor_observation_locations(
            target,
            scene_state,
            observation_windows,
        )
        return next(
            (
                action
                for action in actions
                if str(action.get("actor", "")).strip() == source
                and str(action.get("outcome", ""))
                in {"success", "partial", "complication"}
                and (
                    source == target
                    or (
                        source == "World"
                        and str(action.get("location", "")).strip()
                        in target_locations
                    )
                    or (
                        source != "World"
                        and shares_action_location(
                            source,
                            target,
                            str(action.get("location", "")).strip(),
                            scene_state,
                            observation_windows,
                        )
                    )
                )
            ),
            None,
        )

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
