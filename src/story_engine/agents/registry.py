from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from src.story_engine.agents.runtime import CharacterAgentRuntime
from src.story_engine.agents.types import AgentDecision, AgentPerception
from src.story_engine.core.entity import Entity


@dataclass
class RegisteredAgent:
    entity: Entity
    runtime: CharacterAgentRuntime


class AgentRegistry:
    """Session-scoped registry of character entities and their live runtimes."""

    def __init__(self) -> None:
        self._by_entity_id: Dict[str, RegisteredAgent] = {}
        self._entity_id_by_name: Dict[str, str] = {}

    def register(self, entity: Entity, runtime: CharacterAgentRuntime) -> None:
        if entity.get_component("AgentController") is None:
            raise ValueError(
                f"Cannot register entity without AgentController: {entity.name}"
            )
        previous_id = self._entity_id_by_name.get(entity.name)
        if previous_id and previous_id != entity.id:
            previous = self._by_entity_id.pop(previous_id, None)
            close = getattr(previous.runtime, "close", None) if previous else None
            if callable(close):
                close()
        self._by_entity_id[entity.id] = RegisteredAgent(entity=entity, runtime=runtime)
        self._entity_id_by_name[entity.name] = entity.id

    def unregister(self, entity_or_name: Entity | str) -> None:
        entity_id = (
            entity_or_name.id
            if isinstance(entity_or_name, Entity)
            else self._entity_id_by_name.get(entity_or_name)
        )
        if not entity_id:
            return
        registered = self._by_entity_id.pop(entity_id, None)
        if registered:
            self._entity_id_by_name.pop(registered.entity.name, None)
            close = getattr(registered.runtime, "close", None)
            if callable(close):
                close()

    def is_registered(self, entity_or_name: Entity | str) -> bool:
        return self.get(entity_or_name) is not None

    def get(self, entity_or_name: Entity | str) -> Optional[RegisteredAgent]:
        entity_id = (
            entity_or_name.id
            if isinstance(entity_or_name, Entity)
            else self._entity_id_by_name.get(entity_or_name)
        )
        return self._by_entity_id.get(entity_id) if entity_id else None

    def decide(self, entity: Entity, perception: AgentPerception) -> AgentDecision:
        registered = self.get(entity)
        if not registered:
            raise KeyError(f"Entity is not registered as an agent: {entity.name}")
        return registered.runtime.decide(entity, perception)

    def agents(self) -> Iterable[RegisteredAgent]:
        return tuple(self._by_entity_id.values())

    def __len__(self) -> int:
        return len(self._by_entity_id)

    def close(self) -> None:
        for registered in tuple(self._by_entity_id.values()):
            close = getattr(registered.runtime, "close", None)
            if callable(close):
                close()
        self._by_entity_id.clear()
        self._entity_id_by_name.clear()

    def runtime_snapshot(self) -> Dict[str, Any]:
        return {
            registered.entity.name: registered.runtime
            for registered in self._by_entity_id.values()
        }

    def restore_runtimes(
        self,
        snapshot: Dict[str, Any],
        world_entities: Dict[str, Entity],
    ) -> None:
        self._by_entity_id = {}
        self._entity_id_by_name = {}
        for name, runtime in snapshot.items():
            entity = world_entities.get(name)
            if entity is not None:
                self.register(entity, runtime)
