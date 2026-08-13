from copy import deepcopy
from typing import Any, Dict

from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.core.entity import Entity
from src.story_engine.systems.system import System


class RouteKnowledgeSystem(System):
    """Publish Host-validated route reports into private, possibly stale maps."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        if not context.get("state_transaction", {}).get("committed"):
            context["route_knowledge_updates"] = []
            return
        updates = context.get("simulation_result", {}).get(
            "route_knowledge_updates", []
        )
        if not isinstance(updates, list) or not updates:
            context["route_knowledge_updates"] = []
            return
        states = {
            name: state
            for name, entity in entities.items()
            if (state := entity.get_component("KnowledgeState")) is not None
        }
        staged = {
            name: KnowledgeState(**deepcopy(state.model_dump()))
            for name, state in states.items()
        }
        applied = []
        step = int(getattr(context.get("clock"), "current_step", 0) or 0)
        for item in updates[:12]:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            path = [
                str(node).strip()
                for node in item.get("route_path", []) or []
                if str(node).strip()
            ]
            if source not in staged or target not in staged or source == target:
                continue
            if len(path) < 2 or any(
                route_target not in staged[source].known_routes.get(route_source, [])
                for route_source, route_target in zip(path, path[1:])
            ):
                continue
            for route_source, route_target in zip(path, path[1:]):
                staged[target].learn_reported_route(
                    route_source, route_target, reporter=source, step=step
                )
            applied.append(dict(item))
        for name, state in staged.items():
            states[name].restore_from(state)
        context["route_knowledge_updates"] = applied
