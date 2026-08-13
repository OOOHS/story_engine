from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class DriveSystem(System):
    """Advances private need pressure once per completed world step."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        changes = {}
        for name, entity in entities.items():
            drive = entity.get_component("DriveState")
            if not drive or not hasattr(drive, "advance_to"):
                continue
            changed = drive.advance_to(step)
            if changed:
                changes[name] = changed
        context["drive_drift"] = changes
