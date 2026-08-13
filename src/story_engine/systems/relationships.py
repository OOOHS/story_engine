from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class RelationshipSystem(System):
    """Advance host-owned relationship track decay and timed bits."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        registry = context.get("relation_registry")
        if registry is None:
            context["relationship_transitions"] = []
            return
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        book = registry.to_relationship_book()
        transitions = book.advance_to(step)
        if transitions:
            registry.apply_relationship_book(book, entities)
        context["relationship_transitions"] = transitions
