"""POV-bounded target binding for natural-language action proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from src.story_engine.agents.actions import AgentAction


@dataclass(frozen=True)
class TargetBinding:
    """Outcome of attempting to bind an omitted natural-language target."""

    action: AgentAction
    status: str  # bound | absent | ambiguous | not_needed
    candidates: tuple[str, ...] = ()


def bind_action_target(
    action: AgentAction,
    *,
    actor_name: str = "",
    perception: Any = None,
) -> TargetBinding:
    """Bind a unique visible target without consulting authoritative secrets.

    The parser intentionally does not guess from the whole world.  Candidates
    come only from the actor's current ``AgentPerception`` (visible actors,
    visible objects and visible locations).  If two candidates match, the
    original action is returned unchanged so the Host can report a blocked or
    clarification-needed result rather than silently choosing one.
    """

    if not isinstance(action, AgentAction):
        raise ValueError("action target binding requires an AgentAction")
    if action.target or action.kind == "wait":
        return TargetBinding(action=action, status="not_needed")
    if action.kind not in {"observe", "move", "interact", "communicate"}:
        return TargetBinding(action=action, status="not_needed")

    text = _normalize(action.detail)
    if not text:
        return TargetBinding(action=action, status="absent")
    candidates = _visible_candidates(perception, action.kind, actor_name)
    matches: list[tuple[int, str]] = []
    for candidate, aliases in candidates:
        terms = [candidate, *aliases]
        score = max(
            (len(_normalize(term)) for term in terms if _normalize(term) and _normalize(term) in text),
            default=0,
        )
        if score:
            matches.append((score, candidate))
    if not matches:
        return TargetBinding(action=action, status="absent")
    best_score = max(score for score, _ in matches)
    best = tuple(sorted({name for score, name in matches if score == best_score}))
    if len(best) != 1:
        return TargetBinding(action=action, status="ambiguous", candidates=best)
    return TargetBinding(
        action=replace(action, target=best[0]),
        status="bound",
        candidates=best,
    )


def _visible_candidates(
    perception: Any,
    kind: str,
    actor_name: str,
) -> list[tuple[str, tuple[str, ...]]]:
    if perception is None:
        return []
    world_view = getattr(perception, "world_view", {}) or {}
    if not isinstance(world_view, dict):
        world_view = {}
    result: list[tuple[str, tuple[str, ...]]] = []
    if kind in {"observe", "move", "interact"}:
        visible_world = world_view.get("visible_world", {})
        if isinstance(visible_world, dict):
            for name, state in visible_world.items():
                if not isinstance(state, dict):
                    state = {}
                aliases = tuple(
                    str(item).strip()
                    for item in state.get("aliases", []) or []
                    if str(item).strip()
                )
                result.append((str(name).strip(), aliases))
    if kind in {"observe", "communicate", "interact"}:
        for name in world_view.get("visible_actors", []) or []:
            value = str(name).strip()
            if value and value != str(actor_name).strip():
                result.append((value, ()))
    # Deduplicate while keeping the first POV projection.
    seen: set[str] = set()
    deduped: list[tuple[str, tuple[str, ...]]] = []
    for item in result:
        if not item[0] or item[0] in seen:
            continue
        seen.add(item[0])
        deduped.append(item)
    return deduped


def _normalize(value: Any) -> str:
    return "".join(str(value or "").casefold().split())


__all__ = ["TargetBinding", "bind_action_target"]
