"""Simplified text parsing exclusively for the explicitly selected offline baseline."""
from src.story_engine.agents.actions import AgentAction
from src.story_engine.rules.offline_targets import bind_action_target

def interpret_offline_action(intent, actor, perception):
    text = intent.casefold()
    kind = "interact"
    for candidate, words in (("observe", ("观察", "查看", "检查", "搜索", "环顾", "inspect", "observe", "search")),
                             ("move", ("前往", "走到", "进入", "离开", "移动", "move", "go to", "leave")),
                             ("communicate", ("说", "问", "告诉", "回答", "喊", "communicate", "speak", "ask", "tell")),
                             ("wait", ("等待", "停留", "休息", "wait", "rest"))):
        if any(word in text for word in words):
            kind = candidate
            break
    return bind_action_target(AgentAction(kind, intent), actor_name=actor, perception=perception).action

from typing import Iterable, Mapping, Optional


MOVE_VERBS = ("去", "前往", "走向", "走去", "走到", "来到", "进入", "回到", "上楼", "下楼", "回", "进", "入席", "落座")
NEGATED_MOVE_PATTERNS = (
    "不去",
    "别去",
    "不要去",
    "先不去",
    "不想去",
    "不往",
    "不进",
    "别进",
    "不回",
    "别回",
)


def _aliases_for_location(
    location: str,
    location_aliases: Optional[Mapping[str, Iterable[str]]] = None,
) -> list[str]:
    aliases = []
    full_name = str(location or "").strip()
    if not full_name:
        return aliases
    aliases.append(full_name)
    for raw_alias in (location_aliases or {}).get(full_name, []):
        alias = str(raw_alias).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def _is_negated_move(intent: str, alias: str) -> bool:
    normalized = " ".join(str(intent or "").split())
    if not normalized or not alias:
        return False
    return any(f"{pattern}{alias}" in normalized for pattern in NEGATED_MOVE_PATTERNS)


def _location_is_affirmed(
    intent: str,
    location: str,
    location_aliases: Optional[Mapping[str, Iterable[str]]] = None,
) -> bool:
    normalized = " ".join(str(intent or "").split())
    if not normalized:
        return False
    for alias in _aliases_for_location(location, location_aliases):
        if alias not in normalized:
            continue
        if _is_negated_move(normalized, alias):
            continue
        return True
    return False


def extract_move_target_from_intent(
    intent: str,
    current_location: Optional[str],
    connected_locations: Iterable[str],
    known_locations: Iterable[str],
    location_aliases: Optional[Mapping[str, Iterable[str]]] = None,
) -> Optional[str]:
    normalized = " ".join(str(intent or "").split())
    if not normalized or not any(verb in normalized for verb in MOVE_VERBS):
        return None

    connected = [
        str(name).strip()
        for name in connected_locations or []
        if str(name).strip() and str(name).strip() != str(current_location or "").strip()
    ]
    all_locations = [
        str(name).strip()
        for name in known_locations or []
        if str(name).strip() and str(name).strip() != str(current_location or "").strip()
    ]

    for pool in (connected, all_locations):
        for location in pool:
            if _location_is_affirmed(normalized, location, location_aliases):
                return location
    return None
