"""In-process rollback checkpoint for the authoritative Runner phase chain."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Tuple, Type

from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity


@dataclass(frozen=True)
class ComponentRow:
    key: str
    component_type: Type[Component]
    component: Component
    payload: Dict[str, Any]


@dataclass(frozen=True)
class EntityRow:
    name: str
    entity_id: str
    entity: Entity
    components: Tuple[ComponentRow, ...]


@dataclass(frozen=True)
class RunnerStepCheckpoint:
    entities: Tuple[EntityRow, ...]
    agent_runtimes: Dict[str, Any]
    relation_bindings: Dict[str, str]
    claim_bindings: Dict[str, str]
    action_queue: Any
    clock_step: int
    clock_time: datetime

    @classmethod
    def capture(cls, runner: Any) -> "RunnerStepCheckpoint":
        entity_rows = []
        for name, entity in runner.entities.items():
            components = tuple(
                ComponentRow(
                    key=key,
                    component_type=component.__class__,
                    component=component,
                    payload=deepcopy(component.model_dump()),
                )
                for key, component in entity.components.items()
            )
            entity_rows.append(
                EntityRow(
                    name=name,
                    entity_id=entity.id,
                    entity=entity,
                    components=components,
                )
            )
        return cls(
            entities=tuple(entity_rows),
            agent_runtimes=runner.agent_registry.runtime_snapshot(),
            relation_bindings=runner.relation_registry.binding_snapshot(),
            claim_bindings=runner.claim_registry.binding_snapshot(),
            action_queue=runner.action_queue.checkpoint(),
            clock_step=int(runner.clock.current_step),
            clock_time=runner.clock.current_time,
        )

    def restore(self, runner: Any) -> None:
        restored_entities: Dict[str, Entity] = {}
        for row in self.entities:
            entity = row.entity
            entity.name = row.name
            entity.id = row.entity_id
            restored_components: Dict[str, Component] = {}
            for component_row in row.components:
                component = component_row.component
                restored = component_row.component_type(
                    **deepcopy(component_row.payload)
                )
                for field_name in component_row.component_type.model_fields:
                    setattr(
                        component,
                        field_name,
                        deepcopy(getattr(restored, field_name)),
                    )
                component.entity = entity
                restored_components[component_row.key] = component
            entity.components = restored_components
            restored_entities[row.name] = entity

        runner.entities.clear()
        runner.entities.update(restored_entities)
        runner.action_queue.restore(self.action_queue)
        runner.clock.current_step = int(self.clock_step)
        runner.clock.current_time = self.clock_time
        runner.relation_registry.restore_bindings(
            self.relation_bindings,
            runner.entities,
        )
        runner.claim_registry.restore_bindings(
            self.claim_bindings,
            runner.entities,
        )
        runner.agent_registry.restore_runtimes(
            self.agent_runtimes,
            runner.entities,
        )
