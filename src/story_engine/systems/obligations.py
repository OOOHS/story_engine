from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.motivation import ObligationConflictAnalyzer
from src.story_engine.systems.system import System


class ObligationSystem(System):
    """Settles deterministic due/breach transitions after action resolution."""

    def __init__(self) -> None:
        super().__init__()
        self.conflicts = ObligationConflictAnalyzer()

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        scene_state, plot_state = self._world_components(entities)
        transitions = {}
        conflicts = {}
        for name, entity in entities.items():
            obligations = entity.get_component("ObligationState")
            if not obligations or not hasattr(obligations, "advance_to"):
                continue
            drive = entity.get_component("DriveState")
            changed = obligations.advance_to(
                step,
                drive_state=drive,
                scene_state=scene_state,
                plot_state=plot_state,
            )
            if changed:
                transitions[name] = changed
            current_conflicts = self.conflicts.analyze(
                obligations,
                actor_name=name,
                scene_state=scene_state,
                plot_state=plot_state,
                current_step=step,
            )
            if current_conflicts:
                conflicts[name] = current_conflicts
        context["obligation_transitions"] = transitions
        context["obligation_conflicts"] = conflicts

    @staticmethod
    def _world_components(entities: Dict[str, Entity]):
        for entity in entities.values():
            if entity.get_component("SimulationControl"):
                return (
                    entity.get_component("SceneState"),
                    entity.get_component("PlotState"),
                )
        return None, None
