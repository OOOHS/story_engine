from typing import Dict, Any, Optional
from pydantic import Field
from src.story_engine.core.component import Component


class SceneState(Component):
    """
    Holds the authoritative snapshot of the world.
    This is not a transcript. It represents what is true right now.
    """
    description: str = "Initial State"
    world_objects: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    actor_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    scene_flags: Dict[str, Any] = Field(default_factory=dict)

    def update_description(self, new_description: str):
        self.description = new_description

    def update_object_state(self, object_name: str, state: Dict[str, Any]):
        if object_name not in self.world_objects:
            self.world_objects[object_name] = {}
        self.world_objects[object_name].update(state)

    def update_actor_state(self, actor_name: str, state: Dict[str, Any]):
        if actor_name not in self.actor_states:
            self.actor_states[actor_name] = {}
        self.actor_states[actor_name].update(state)

    def update_scene_flags(self, state: Dict[str, Any]):
        self.scene_flags.update(state)

    def get_scene_flag(self, key: str, default: Any = None) -> Any:
        return self.scene_flags.get(key, default)

    def get_object_state(self, object_name: str) -> Dict[str, Any]:
        return self.world_objects.get(object_name, {})

    def get_actor_state(self, actor_name: str) -> Dict[str, Any]:
        return self.actor_states.get(actor_name, {})

    def get_actor_location(self, actor_name: str) -> Optional[str]:
        state = self.get_actor_state(actor_name)
        location = state.get("location")
        return str(location) if location else None

    def get_actors_in_location(self, location: Optional[str]) -> Dict[str, Dict[str, Any]]:
        if not location:
            return {}
        return {
            name: props
            for name, props in self.actor_states.items()
            if props.get("location") == location
        }

    def get_view_pov(self, viewer_name: Optional[str]) -> Dict[str, Any]:
        if not viewer_name:
            return {
                "viewer": None,
                "location": None,
                "visible_actors": [],
                "visible_actor_states": {},
                "visible_world": {},
                "viewer_zone": None,
                "spatial_layout": {},
                "visible_spatial_facts": [],
            }

        location = self.get_actor_location(viewer_name)
        visible_actor_states = self.get_actors_in_location(location) if location else dict(self.actor_states)
        visible_world = {}
        if location:
            visible_world[location] = self.get_object_state(location)
        elif self.world_objects:
            visible_world = dict(self.world_objects)

        spatial_layout = self._build_spatial_layout(
            location=location,
            visible_actor_states=visible_actor_states,
            viewer_name=viewer_name,
        )
        return {
            "viewer": viewer_name,
            "location": location,
            "visible_actors": list(visible_actor_states.keys()),
            "visible_actor_states": visible_actor_states,
            "visible_world": visible_world,
            "viewer_zone": spatial_layout.get("viewer_zone"),
            "spatial_layout": spatial_layout,
            "visible_spatial_facts": list(spatial_layout.get("facts", [])),
        }

    def get_player_pov(self, player_name: Optional[str]) -> Dict[str, Any]:
        return self.get_view_pov(player_name)

    def apply_updates(self, updates: Optional[Dict[str, Any]]) -> None:
        if not isinstance(updates, dict):
            return

        for obj_name, props in updates.get("world_objects", {}).items():
            if isinstance(props, dict):
                self.update_object_state(obj_name, props)

        for actor_name, props in updates.get("actor_states", {}).items():
            if isinstance(props, dict):
                normalized_props = dict(props)
                if normalized_props.get("location") and "sub_location" not in normalized_props:
                    default_zone = self._default_zone_for_location(str(normalized_props["location"]))
                    if default_zone:
                        normalized_props["sub_location"] = default_zone
                self.update_actor_state(actor_name, normalized_props)

        scene_flags = updates.get("scene", {})
        if isinstance(scene_flags, dict):
            scene_flags = dict(scene_flags)
            description = scene_flags.pop("description", None)
            if description:
                self.update_description(description)
            self.update_scene_flags(scene_flags)

        # Backward-compatible fallback: unknown top-level keys are treated as world objects.
        for key, value in updates.items():
            if key in {"world_objects", "actor_states", "scene"}:
                continue
            if isinstance(value, dict):
                self.update_object_state(key, value)

    def get_snapshot(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "world_objects": self.world_objects,
            "actor_states": self.actor_states,
            "scene_flags": self.scene_flags,
        }

    def _default_zone_for_location(self, location: str) -> Optional[str]:
        state = self.get_object_state(location)
        if not isinstance(state, dict):
            return None
        default_zone = state.get("default_zone")
        if default_zone:
            return str(default_zone)
        zones = state.get("zones", {})
        if isinstance(zones, dict) and zones:
            first_key = next(iter(zones.keys()), None)
            return str(first_key) if first_key else None
        if isinstance(zones, list) and zones:
            first_item = zones[0]
            if isinstance(first_item, dict) and first_item.get("id"):
                return str(first_item.get("id"))
            if first_item:
                return str(first_item)
        return None

    def _build_spatial_layout(
        self,
        location: Optional[str],
        visible_actor_states: Dict[str, Dict[str, Any]],
        viewer_name: Optional[str],
    ) -> Dict[str, Any]:
        if not location:
            return {
                "location": None,
                "viewer_zone": None,
                "zones": {},
                "actors": [],
                "facts": [],
            }

        location_state = self.get_object_state(location)
        zones = self._normalize_zones(location_state.get("zones", {}))
        default_zone = self._default_zone_for_location(location)
        actors = []
        facts = []
        viewer_zone = None
        for actor_name, state in visible_actor_states.items():
            if not isinstance(state, dict):
                continue
            zone = str(state.get("sub_location") or default_zone or "")
            stance = str(state.get("stance") or "standing")
            focus_target = state.get("focus_target")
            side_with = state.get("side_with")
            actor_entry = {
                "actor": actor_name,
                "zone": zone or None,
                "zone_label": zones.get(zone, zone or location),
                "stance": stance,
                "focus_target": focus_target,
                "side_with": side_with,
            }
            actors.append(actor_entry)
            facts.append(self._build_spatial_fact(actor_entry))
            if actor_name == viewer_name:
                viewer_zone = zone or None

        return {
            "location": location,
            "viewer_zone": viewer_zone,
            "zones": zones,
            "actors": actors,
            "facts": [fact for fact in facts if fact],
        }

    @staticmethod
    def _normalize_zones(raw_zones: Any) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if isinstance(raw_zones, dict):
            for zone_id, payload in raw_zones.items():
                zone_key = str(zone_id).strip()
                if not zone_key:
                    continue
                if isinstance(payload, dict):
                    label = payload.get("label", zone_key)
                else:
                    label = payload
                normalized[zone_key] = str(label).strip() or zone_key
            return normalized
        if isinstance(raw_zones, list):
            for item in raw_zones:
                if isinstance(item, dict):
                    zone_id = str(item.get("id", "")).strip()
                    if not zone_id:
                        continue
                    normalized[zone_id] = str(item.get("label", zone_id)).strip() or zone_id
                elif item:
                    zone_key = str(item).strip()
                    normalized[zone_key] = zone_key
        return normalized

    @staticmethod
    def _build_spatial_fact(actor_entry: Dict[str, Any]) -> str:
        actor = actor_entry.get("actor", "某人")
        zone_label = actor_entry.get("zone_label") or "原地"
        stance = actor_entry.get("stance", "standing")
        focus_target = actor_entry.get("focus_target")
        side_with = actor_entry.get("side_with")
        if stance == "seated":
            base = f"{actor}坐在{zone_label}。"
        elif stance == "leaning":
            base = f"{actor}倚在{zone_label}。"
        else:
            base = f"{actor}站在{zone_label}。"
        if focus_target:
            base = base.rstrip("。") + f" 视线主要落在{focus_target}身上。"
        if side_with:
            base = base.rstrip("。") + f" 立场明显偏向{side_with}。"
        return base

    @staticmethod
    def _get_nested_value(data: Dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _resolve_scope(self, scope: str, target: Optional[str], plot_state: Optional[Any] = None) -> Dict[str, Any]:
        if scope == "scene":
            return self.get_snapshot()
        if scope == "world_object" and target:
            return self.get_object_state(target)
        if scope == "actor" and target:
            return self.get_actor_state(target)
        if scope == "plot" and plot_state and target:
            return plot_state.get_snapshot().get(target, {})
        return {}

    @staticmethod
    def _compare_value(actual: Any, operator: str, expected: Any) -> bool:
        if operator == "exists":
            return actual is not None
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "gt":
            return actual is not None and actual > expected
        if operator == "gte":
            return actual is not None and actual >= expected
        if operator == "lt":
            return actual is not None and actual < expected
        if operator == "lte":
            return actual is not None and actual <= expected
        if operator == "contains":
            if isinstance(actual, (list, tuple, set, str)):
                return expected in actual
            return False
        if operator == "in":
            if isinstance(expected, (list, tuple, set, str)):
                return actual in expected
            return False
        return False

    def matches_condition(self, condition: Any, plot_state: Optional[Any] = None) -> bool:
        source = self._resolve_scope(condition.scope, condition.target, plot_state=plot_state)
        actual = self._get_nested_value(source, condition.path)
        return self._compare_value(actual, condition.operator, condition.value)

    def get_state_string(self) -> str:
        parts = [f"当前场景：{self.description}"]

        if self.scene_flags:
            scene_flags = ", ".join([f"{k}={v}" for k, v in self.scene_flags.items()])
            parts.append(f"场景标记：{scene_flags}")

        if self.world_objects:
            lines = ["当前物体状态 (World Objects):"]
            for name, props in self.world_objects.items():
                props_str = ", ".join([f"{k}={v}" for k, v in props.items()])
                lines.append(f"- {name}: {props_str}")
            parts.append("\n".join(lines))

        if self.actor_states:
            lines = ["当前角色状态 (Actor States):"]
            for name, props in self.actor_states.items():
                props_str = ", ".join([f"{k}={v}" for k, v in props.items()])
                lines.append(f"- {name}: {props_str}")
            parts.append("\n".join(lines))

        return "\n".join(parts)
