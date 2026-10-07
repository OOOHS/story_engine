from typing import Any


def actor_observation_locations(
    actor: str,
    scene_state: Any,
    observation_windows: Any,
) -> set[str]:
    packet = (
        observation_windows.get(actor, {})
        if isinstance(observation_windows, dict)
        else {}
    )
    if packet.get("present_during_step") is False:
        return set()
    locations = {
        str(item).strip()
        for item in packet.get("locations", [])
        if str(item).strip()
    }
    current = (
        scene_state.get_actor_location(actor)
        if scene_state is not None
        else None
    )
    if not locations and current:
        locations.add(str(current).strip())
    return locations


def shares_action_location(
    source: str,
    target: str,
    action_location: str,
    scene_state: Any,
    observation_windows: Any,
) -> bool:
    location = str(action_location or "").strip()
    return bool(
        location
        and location
        in actor_observation_locations(source, scene_state, observation_windows)
        and location
        in actor_observation_locations(target, scene_state, observation_windows)
    )


def receives_communication(action: Any, target: str, scene_state: Any, observation_windows: Any) -> bool:
    """Explicit listeners were semantically validated before world commit.

    Older and offline actions use the existing co-location projection.
    An explicit empty list represents a message that reached nobody.
    """
    if not isinstance(action, dict) or action.get("action_kind") != "communicate":
        return False
    if action.get("outcome") not in {"success", "partial", "complication"}:
        return False
    if "recipients" in action:
        return isinstance(action["recipients"], list) and target in action["recipients"]
    return shares_action_location(str(action.get("actor", "")), target,
                                  str(action.get("location", "")), scene_state, observation_windows)
