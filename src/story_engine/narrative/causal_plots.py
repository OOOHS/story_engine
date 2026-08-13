from copy import deepcopy
from typing import Any, Dict, List

from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.character_lifecycle import (
    CharacterLifecycle,
    CharacterSpawnPlan,
)
from src.story_engine.environment.exchanges import ExchangeDynamics
from src.story_engine.environment.world_object_lifecycle import WorldObjectLifecycle


class CausalPlotEngine:
    """Derives plot clock changes from candidate authoritative state."""

    CONSUMED_RULES_FLAG = "consumed_plot_rules"

    def __init__(self) -> None:
        self.objects = WorldObjectLifecycle()
        self.exchanges = ExchangeDynamics()
        self.characters = CharacterLifecycle()

    def enrich_result(
        self,
        scene_state: Any,
        plot_state: Any,
        scenario: Any,
        result: Dict[str, Any],
        character_spawn_plan: CharacterSpawnPlan | None = None,
        proposal_actors: set[str] | None = None,
    ) -> Dict[str, Any]:
        rules = list(getattr(scenario, "plot_rules", []) or []) if scenario else []
        if not scene_state or not plot_state or not rules:
            return result
        updates = result.get("state_updates", {})
        if not isinstance(updates, dict):
            return result
        if not isinstance(result.get("plot_updates", []), list):
            return result
        scene_updates = updates.get("scene", {})
        if not isinstance(scene_updates, dict):
            return result

        # This bookkeeping flag belongs to the causal engine, not to the
        # resolver model.  Evaluate the candidate without any attempted model
        # write, then restore only the engine-derived value below.
        candidate_updates = deepcopy(updates)
        candidate_updates.get("scene", {}).pop(self.CONSUMED_RULES_FLAG, None)
        try:
            candidate = SceneState(**deepcopy(scene_state.get_snapshot()))
            candidate.apply_updates(candidate_updates)
            if self.characters.stage(candidate, character_spawn_plan):
                return result
            lifecycle_result = deepcopy(result)
            lifecycle_result["state_updates"] = candidate_updates
            if self.exchanges.apply(
                candidate,
                lifecycle_result,
                proposal_actors=set(proposal_actors or set()),
            ):
                return result
            if self.objects.apply(
                candidate,
                lifecycle_result,
                previous_scene_state=scene_state,
            ):
                return result
        except Exception:
            return result

        consumed = set(scene_state.get_scene_flag(self.CONSUMED_RULES_FLAG, []) or [])
        triggered: List[str] = []
        consumed_this_turn: List[str] = []
        derived: List[Dict[str, Any]] = []
        suppressed: List[str] = []
        max_triggers = int(getattr(scenario, "causal_plot_max_triggers_per_turn", 3))
        max_total_advance = int(getattr(scenario, "causal_plot_max_total_advance", 3))
        total_advance = 0
        ordered_rules = sorted(
            enumerate(rules),
            key=lambda item: (-int(getattr(item[1], "priority", 0)), item[0]),
        )
        for _, rule in ordered_rules:
            if rule.one_shot and rule.rule_id in consumed:
                continue
            if rule.plot_id not in plot_state.plots:
                continue
            if not all(
                candidate.matches_condition(condition, plot_state=plot_state)
                for condition in rule.conditions
            ):
                continue
            movement = abs(int(rule.advance))
            if len(triggered) >= max_triggers or total_advance + movement > max_total_advance:
                suppressed.append(rule.rule_id)
                continue
            derived.append(
                {
                    "plot_id": rule.plot_id,
                    "advance": int(rule.advance),
                    "stage_shift": int(rule.stage_shift),
                    "note": rule.reason or f"causal plot rule: {rule.rule_id}",
                    "rule_id": rule.rule_id,
                }
            )
            triggered.append(rule.rule_id)
            if rule.one_shot:
                consumed_this_turn.append(rule.rule_id)
            total_advance += movement

        if not derived:
            if self.CONSUMED_RULES_FLAG in scene_updates:
                scene_updates[self.CONSUMED_RULES_FLAG] = sorted(consumed)
            if suppressed:
                result["causal_plot_suppressed_rules"] = suppressed
            return result
        result["plot_updates"].extend(derived)
        scene_updates[self.CONSUMED_RULES_FLAG] = sorted(
            consumed.union(consumed_this_turn)
        )
        result["causal_plot_rules"] = triggered
        if suppressed:
            result["causal_plot_suppressed_rules"] = suppressed
        return result
