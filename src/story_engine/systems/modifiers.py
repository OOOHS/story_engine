from copy import deepcopy
from typing import Any, Dict

from src.story_engine.components.modifier_state import ModifierState
from src.story_engine.core.entity import Entity
from src.story_engine.simulation.modifiers import ModifierDynamics
from src.story_engine.systems.system import System


class ModifierSystem(System):
    """Advance and atomically publish temporary character modifiers."""

    def __init__(self, definitions: Dict[str, Any] | None = None) -> None:
        super().__init__()
        self.dynamics = ModifierDynamics(definitions)

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        live_states = {
            name: state
            for name, entity in entities.items()
            if (state := entity.get_component("ModifierState")) is not None
        }
        expiry_transitions = []
        for name, state in live_states.items():
            for transition in state.advance_to(step):
                expiry_transitions.append({"actor": name, **transition})

        transaction = context.get("state_transaction", {})
        result = context.get("simulation_result", {})
        updates = result.get("modifier_updates", []) if transaction.get("committed") else []
        if not updates:
            context["modifier_updates"] = []
            context["modifier_errors"] = []
            context["modifier_transitions"] = expiry_transitions
            return
        staged = {
            name: ModifierState(**deepcopy(state.model_dump()))
            for name, state in live_states.items()
        }
        applied, errors = self.dynamics.apply(
            modifier_states=staged,
            scene_state=self._get_scene_state(entities),
            result={
                "resolved_actions": result.get("resolved_actions", []),
                "modifier_updates": updates,
            },
            current_step=step,
            observation_windows=context.get("actor_observation_windows", {}),
        )
        if not errors:
            snapshots = {
                name: ModifierState(**deepcopy(state.model_dump()))
                for name, state in live_states.items()
            }
            try:
                for name, state in staged.items():
                    live_states[name].restore_from(state)
            except Exception as exc:
                for name, snapshot in snapshots.items():
                    live_states[name].restore_from(snapshot)
                applied = []
                errors = [f"modifier publication failed: {type(exc).__name__}:{exc}"]
        context["modifier_updates"] = applied if not errors else []
        context["modifier_errors"] = errors
        context["modifier_transitions"] = expiry_transitions

    @staticmethod
    def _get_scene_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("SceneState")
            if state is not None:
                return state
        return None
