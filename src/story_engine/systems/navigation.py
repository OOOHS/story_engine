from hashlib import sha256
from typing import Any, Dict, List

from src.story_engine.components.navigation_state import (
    NavigationProblem,
    NavigationState,
)
from src.story_engine.core.entity import Entity
from src.story_engine.rules import LegalityEngine
from src.story_engine.systems.system import System


class NavigationSystem(System):
    """Turn Host route failures into private problems, not prescribed goals."""

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        if not context.get("state_transaction", {}).get("committed"):
            context["navigation_updates"] = []
            return
        scene = self._component(entities, "SceneState")
        result = context.get("simulation_result", {})
        checks = context.get("legality", {}).get("checks", []) or []
        actions = result.get("resolved_actions", []) or []
        action_by_actor = {
            str(item.get("actor", "")).strip(): item
            for item in actions
            if isinstance(item, dict)
        }
        updates = []
        step = int(getattr(context.get("clock"), "current_step", 0) or 0)
        for check in checks:
            if not isinstance(check, dict) or check.get("rule") not in {
                "stale_route", "movement_blocked"
            }:
                continue
            actor = str(check.get("actor", "")).strip()
            action = action_by_actor.get(actor, {})
            if str(action.get("outcome", "")) != "blocked":
                continue
            entity = entities.get(actor)
            state = entity.get_component("NavigationState") if entity else None
            knowledge = entity.get_component("KnowledgeState") if entity else None
            if state is None or knowledge is None or scene is None:
                continue
            origin = str(scene.get_actor_location(actor) or "").strip()
            destination = str(check.get("action_target", "")).strip()
            path = LegalityEngine.find_known_path(
                knowledge.get_map_snapshot(), origin, destination
            )
            route_target = path[1] if len(path) >= 2 else destination
            alternative = self._alternative_path(
                knowledge.get_map_snapshot(), origin, destination,
                blocked_edge=(origin, route_target),
            )
            obligation_id, remaining = self._relevant_obligation(
                entity, destination, step
            )
            seed = f"{actor}|{origin}|{route_target}|{destination}"
            problem_id = f"navigation:{sha256(seed.encode('utf-8')).hexdigest()[:16]}"
            problem = NavigationProblem(
                problem_id=problem_id,
                route_source=origin,
                route_target=route_target,
                destination=destination,
                discovered_at=origin,
                discovered_step=step,
                alternative_path=alternative,
                obligation_id=obligation_id,
                steps_remaining=remaining,
                failure_rule=str(check.get("rule", "")).strip(),
                reason=str(check.get("reason", "")).strip(),
            )
            state.record(problem)
            updates.append(problem.model_dump())
        context["navigation_updates"] = updates

    @staticmethod
    def _alternative_path(
        map_snapshot: Dict[str, Any],
        origin: str,
        destination: str,
        *,
        blocked_edge: tuple[str, str],
    ) -> List[str]:
        routes = {
            source: [
                target for target in targets
                if (source, target) != blocked_edge
            ]
            for source, targets in map_snapshot.get("known_routes", {}).items()
        }
        return LegalityEngine.find_known_path(
            {**map_snapshot, "known_routes": routes}, origin, destination
        )

    @staticmethod
    def _relevant_obligation(
        entity: Entity, destination: str, step: int
    ) -> tuple[str, int | None]:
        obligations = entity.get_component("ObligationState")
        if obligations is None:
            return "", None
        for record in obligations.obligations.values():
            if record.status not in {"scheduled", "due"}:
                continue
            if any(
                condition.get("value") == destination
                for condition in record.completion_conditions
            ):
                return record.obligation_id, record.due_step - int(step)
        return "", None

    @staticmethod
    def _component(entities: Dict[str, Entity], component_name: str) -> Any:
        return next(
            (
                component for entity in entities.values()
                if (component := entity.get_component(component_name)) is not None
            ),
            None,
        )
