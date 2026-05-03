from typing import Iterable, Optional


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


def _aliases_for_location(location: str) -> list[str]:
    aliases = []
    full_name = str(location or "").strip()
    if not full_name:
        return aliases
    aliases.append(full_name)
    short_name = full_name.replace("沈宅", "").strip()
    if short_name and short_name not in aliases:
        aliases.append(short_name)
    if "餐厅" in full_name:
        for alias in ["餐桌", "饭桌", "长桌", "桌边", "席上", "入席", "餐桌前"]:
            if alias not in aliases:
                aliases.append(alias)
    if "客厅" in full_name:
        for alias in ["沙发边", "客厅里", "客厅那边"]:
            if alias not in aliases:
                aliases.append(alias)
    if "二楼走廊" in full_name:
        for alias in ["楼上", "楼梯口", "走廊里"]:
            if alias not in aliases:
                aliases.append(alias)
    if "书房" in full_name:
        for alias in ["书桌前", "书架边", "书房里"]:
            if alias not in aliases:
                aliases.append(alias)
    if "客房" in full_name:
        for alias in ["房间里", "卧室", "客房里"]:
            if alias not in aliases:
                aliases.append(alias)
    return aliases


def _is_negated_move(intent: str, alias: str) -> bool:
    normalized = " ".join(str(intent or "").split())
    if not normalized or not alias:
        return False
    return any(f"{pattern}{alias}" in normalized for pattern in NEGATED_MOVE_PATTERNS)


def _location_is_affirmed(intent: str, location: str) -> bool:
    normalized = " ".join(str(intent or "").split())
    if not normalized:
        return False
    for alias in _aliases_for_location(location):
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
            if _location_is_affirmed(normalized, location):
                return location
    return None
