from typing import Any, Dict, Iterable

from src.story_engine.core.entity import Entity


def reactivate_relevant_agent_goal(
    entity: Entity,
    *,
    event_id: str,
    references: Iterable[Any],
    impacts: Iterable[Any] = (),
    step: int,
    reason: str,
) -> Dict[str, Any] | None:
    """Reset continuation backoff only for a POV-delivered relevant change."""

    controller = entity.get_component("AgentController")
    if not controller:
        return None
    clean_event = _text(event_id, 180)
    refs = {_text(item, 180) for item in references}
    refs.discard("")
    normalized_impacts = _normalize_impacts(impacts)
    matched = _find_relevant_goal(
        entity,
        clean_event,
        refs,
        normalized_impacts,
        agent_only=True,
    )
    if matched is None:
        return None
    record, match_basis = matched
    previous_repeat = int(controller.repeated_goal_action_count)
    controller.last_goal_wakeup_step = int(step)
    controller.last_goal_wakeup_id = record.goal_id
    controller.repeated_goal_action_count = 0
    controller.last_goal_action_signature = ""
    controller.goal_reactivation_count += 1
    return {
        "actor": entity.name,
        "goal_id": record.goal_id,
        "event_id": clean_event,
        "step": int(step),
        "previous_repeated_action_count": previous_repeat,
        "reason": _text(reason, 300),
        "match_basis": match_basis,
    }


def relevant_goal_match(
    entity: Entity,
    *,
    event_id: str,
    references: Iterable[Any],
    impacts: Iterable[Any] = (),
) -> tuple[str, str] | None:
    """Pure host relevance query used by bounded attention routing."""

    clean_event = _text(event_id, 180)
    refs = {_text(item, 180) for item in references}
    refs.discard("")
    matched = _find_relevant_goal(
        entity,
        clean_event,
        refs,
        _normalize_impacts(impacts),
        agent_only=False,
    )
    if matched is None:
        return None
    record, match_basis = matched
    return record.goal_id, match_basis


def _find_relevant_goal(
    entity: Entity,
    event_id: str,
    references: set[str],
    impacts: set[tuple[str, str, str]],
    *,
    agent_only: bool,
) -> tuple[Any, str] | None:
    goals = entity.get_component("GoalState")
    if not goals or not hasattr(goals, "active_records"):
        return None
    for record in goals.active_records():
        if agent_only and record.origin != "agent":
            continue
        if not record.completion_conditions:
            continue
        match_basis = _match_basis(record, event_id, references, impacts)
        if match_basis:
            return record, match_basis
    return None


def _match_basis(
    record: Any,
    event_id: str,
    references: set[str],
    impacts: set[tuple[str, str, str]],
) -> str:
    if _matches_dependency(record, impacts):
        return "state_dependency"
    if event_id and _text(record.source_ref, 180) == event_id:
        return "source_event"
    if _text(record.source_ref, 180) in references:
        return "source_reference"
    for condition in list(record.completion_conditions) + list(
        record.failure_conditions
    ):
        if not isinstance(condition, dict):
            continue
        if _text(condition.get("target"), 180) in references:
            return "condition_reference"
        value = condition.get("value")
        if isinstance(value, (list, tuple, set)):
            if {_text(item, 180) for item in value}.intersection(references):
                return "condition_value"
        elif _text(value, 180) in references:
            return "condition_value"
    return ""


def _matches_dependency(
    record: Any,
    impacts: set[tuple[str, str, str]],
) -> bool:
    if not impacts:
        return False
    for condition in list(record.completion_conditions) + list(
        record.failure_conditions
    ):
        if not isinstance(condition, dict):
            continue
        scope = _text(condition.get("scope", "scene"), 40)
        target = _text(condition.get("target"), 180)
        if scope == "scene" and not target:
            target = "scene"
        path = _text(condition.get("path"), 120) or "*"
        if not scope or not target:
            continue
        for impact_scope, impact_target, impact_path in impacts:
            if scope != impact_scope or target != impact_target:
                continue
            if _paths_overlap(scope, path, impact_path):
                return True
    return False


def _paths_overlap(scope: str, dependency_path: str, impact_path: str) -> bool:
    if "*" in {dependency_path, impact_path} or dependency_path == impact_path:
        return True
    # An object goal is an endpoint query (for example owner == actor), but
    # availability changes determine whether an Agent should try again.  These
    # paths form one host-defined causal family rather than free-form tags.
    if scope == "world_object":
        availability = {
            "existence",
            "owner",
            "location",
            "container",
            "hidden",
            "accessibility",
            "visibility",
            "container_open",
        }
        return dependency_path in availability and impact_path in availability
    return False


def _normalize_impacts(values: Iterable[Any]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for value in values or ():
        raw = value.model_dump() if hasattr(value, "model_dump") else value
        if not isinstance(raw, dict):
            continue
        scope = _text(raw.get("scope"), 40)
        target = _text(raw.get("target"), 180)
        path = _text(raw.get("path"), 120) or "*"
        if scope and target:
            result.add((scope, target, path))
    return result


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]
