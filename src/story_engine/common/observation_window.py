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
