from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class ClaimSystem(System):
    """Synchronize objective Claim truth with authoritative world conditions."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        registry = context.get("claim_registry")
        if registry is None:
            context["claim_transitions"] = []
            context["claim_errors"] = []
            return
        clock = context.get("clock")
        transitions, errors = registry.advance_to(
            step=clock.current_step if clock else 0,
            scene_state=self._component(entities, "SceneState"),
            plot_state=self._component(entities, "PlotState"),
        )
        context["claim_transitions"] = transitions
        context["claim_errors"] = errors

    @staticmethod
    def _component(entities: Dict[str, Entity], component_name: str) -> Any:
        for entity in entities.values():
            component = entity.get_component(component_name)
            if component is not None:
                return component
        return None
