from copy import deepcopy
from typing import ClassVar, Dict, Any, Optional
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
    public_scene_fields: list[str] = Field(default_factory=list)
    private_scene_fields: list[str] = Field(default_factory=list)

    DEFAULT_PUBLIC_SCENE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "day_phase",
            "weather",
            "alarm",
            "ambient_condition",
            "public_status",
        }
    )
    SCENE_VISIBILITY_SCHEMA_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"public_scene_fields", "private_scene_fields"}
    )

    PUBLIC_ACTOR_STATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "location",
            "sub_location",
            "stance",
            "posture",
            "expression",
            "appearance",
            "visible_condition",
            "activity",
            "public_status",
            "focus_target",
            "side_with",
            "attitude",
        }
    )
    HOST_ONLY_ACTOR_STATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "dramatic_motive",
            "dramatic_push",
            "pressure_profile",
            "public_lever",
            "signature_templates",
            "bias",
            "framing_style",
            "territorial",
            "family_support",
            "loyalty",
            "pressure",
        }
    )
    ACTOR_VISIBILITY_SCHEMA_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"public_state_fields", "private_state_fields"}
    )
    HOST_ONLY_OBJECT_STATE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"affordances", "policy_tags", "stack_key"}
    )
    OBJECT_VISIBILITY_SCHEMA_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"public_state_fields", "private_state_fields"}
    )

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

    # --- Director signals: soft, per-actor, non-authoritative nudges ----
    #
    # A director signal never becomes a resolved_action and never bypasses
    # _validate_resolved_actions' proposal_actors gate -- it is inbox
    # content only, the character can act on it, reinterpret it, or ignore
    # it entirely. To keep this from turning into a standing directive
    # stream (and eroding character autonomy), the queue depth per actor is
    # capped at 1: a pending, unconsumed signal blocks new ones for that
    # actor until it is popped or expires.
    def queue_director_signal(
        self,
        actor: str,
        suggestion: str,
        *,
        current_step: int,
        source_plot_id: str = "",
        tags: Optional[list] = None,
        expires_after_steps: int = 3,
    ) -> bool:
        actor = str(actor or "").strip()
        suggestion = str(suggestion or "").strip()
        if not actor or not suggestion:
            return False
        queue = self.scene_flags.setdefault("director_signals", {})
        pending = queue.setdefault(actor, [])
        if pending:
            return False
        pending.append(
            {
                "suggestion": suggestion[:280],
                "source_plot_id": str(source_plot_id or ""),
                "tags": [str(tag) for tag in (tags or [])][:6],
                "queued_step": int(current_step),
                "expires_after_steps": max(1, int(expires_after_steps)),
            }
        )
        return True

    def pop_director_signals(self, actor: str, current_step: int) -> list:
        """Read and clear an actor's pending director signals.

        Expired entries (unconsumed past their budget) are dropped silently
        rather than delivered -- an idea the Host never got to surface in
        time is worth less than the character's own read of a stale
        situation.
        """
        queue = self.scene_flags.get("director_signals", {})
        pending = queue.pop(str(actor or ""), [])
        return [
            item
            for item in pending
            if isinstance(item, dict)
            and int(current_step) - int(item.get("queued_step", 0))
            <= int(item.get("expires_after_steps", 3))
        ]

    def public_scene_field_names(self) -> set[str]:
        return (
            set(self.DEFAULT_PUBLIC_SCENE_FIELDS)
            | self._state_field_set(self.public_scene_fields)
        ) - self._state_field_set(self.private_scene_fields)

    def get_public_scene_state(self) -> Dict[str, Any]:
        allowed = self.public_scene_field_names()
        return {
            "description": self.description,
            "flags": {
                key: deepcopy(value)
                for key, value in self.scene_flags.items()
                if key in allowed and not str(key).startswith("_")
            },
        }

    def get_object_state(self, object_name: str) -> Dict[str, Any]:
        return self.world_objects.get(object_name, {})

    def is_location(self, object_name: str) -> bool:
        """Return whether a world-object entry is a spatial graph node.

        Legacy scenarios did not distinguish places from props, so missing
        ``is_location`` remains location-like.  Lifecycle-created tangible
        objects always opt out explicitly.
        """
        state = self.get_object_state(object_name)
        return bool(state.get("is_location", True)) if isinstance(state, dict) else False

    def get_known_locations(self) -> set[str]:
        return {
            name for name in self.world_objects
            if self.is_location(name)
        }

    def get_effective_object_location(self, object_name: str) -> Optional[str]:
        """Resolve an object's physical location through nested containers.

        Container references are authoritative placement, not a denormalized
        copy of the parent's owner/location.  Moving a bag therefore moves all
        of its contents without rewriting every child object.
        """
        current = str(object_name or "").strip()
        visited: set[str] = set()
        while current:
            if current in visited:
                return None
            visited.add(current)
            state = self.get_object_state(current)
            if not isinstance(state, dict) or self.is_location(current):
                return None
            owner = str(state.get("owner") or "").strip()
            if owner:
                return self.get_actor_location(owner)
            location = str(state.get("location") or "").strip()
            if location:
                return location
            current = str(state.get("container") or "").strip()
        return None

    def get_object_container_chain(self, object_name: str) -> list[str]:
        """Return direct-to-outer container ids, stopping safely on bad data."""
        chain: list[str] = []
        current = str(object_name or "").strip()
        visited = {current}
        while current:
            state = self.get_object_state(current)
            if not isinstance(state, dict) or self.is_location(current):
                break
            container = str(state.get("container") or "").strip()
            if not container or container in visited:
                break
            chain.append(container)
            visited.add(container)
            current = container
        return chain

    def is_object_accessible(
        self,
        object_name: str,
        actor_name: Optional[str] = None,
    ) -> bool:
        """Whether the object can be physically manipulated out of its containers."""
        for container in self.get_object_container_chain(object_name):
            state = self.get_object_state(container)
            if not bool(state.get("container_open", True)):
                return False
            if actor_name and bool(state.get("hidden", False)) and str(
                state.get("owner") or ""
            ).strip() != str(actor_name).strip():
                return False
        return True

    def is_object_visible_through_containers(
        self,
        object_name: str,
        viewer_name: Optional[str] = None,
    ) -> bool:
        """Whether every enclosing container permits line of sight to contents."""
        for container in self.get_object_container_chain(object_name):
            state = self.get_object_state(container)
            if bool(state.get("hidden", False)) and str(
                state.get("owner") or ""
            ).strip() != str(viewer_name or "").strip():
                return False
            if bool(state.get("container_open", True)):
                continue
            if bool(state.get("container_opaque", True)):
                return False
        return True

    def get_contained_objects(self, container_name: str) -> Dict[str, Dict[str, Any]]:
        return {
            name: state
            for name, state in self.world_objects.items()
            if isinstance(state, dict)
            and not self.is_location(name)
            and str(state.get("container") or "").strip() == container_name
        }

    def get_visible_objects(self, viewer_name: Optional[str]) -> Dict[str, Dict[str, Any]]:
        if not viewer_name:
            return {}
        viewer_location = self.get_actor_location(viewer_name)
        visible: Dict[str, Dict[str, Any]] = {}
        for name, state in self.world_objects.items():
            if not isinstance(state, dict) or self.is_location(name):
                continue
            owner = str(state.get("owner") or "").strip()
            hidden = bool(state.get("hidden", False))
            if owner == viewer_name:
                visible[name] = state
                continue
            if hidden:
                continue
            if (
                viewer_location
                and self.get_effective_object_location(name) == viewer_location
                and self.is_object_visible_through_containers(name, viewer_name)
            ):
                visible[name] = state
        return visible

    def get_public_object_state(self, object_name: str) -> Dict[str, Any]:
        """Project observable object facts without Host policy metadata."""
        state = self.get_object_state(object_name)
        if not isinstance(state, dict):
            return {}
        private = self._state_field_set(state.get("private_state_fields", []))
        hidden = (
            set(self.HOST_ONLY_OBJECT_STATE_FIELDS)
            | set(self.OBJECT_VISIBILITY_SCHEMA_FIELDS)
            | private
        )
        return {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in hidden and not str(key).startswith("_")
        }

    def get_semantic_snapshot(self) -> Dict[str, Any]:
        """Project facts needed by semantic settlement, excluding Host policy."""
        world_objects = {}
        for name, raw_state in self.world_objects.items():
            state = deepcopy(raw_state) if isinstance(raw_state, dict) else {}
            state.pop("policy_tags", None)
            state.pop("stack_key", None)
            state.pop("public_state_fields", None)
            state.pop("private_state_fields", None)
            affordances = state.get("affordances", [])
            if isinstance(affordances, list):
                state["affordances"] = [
                    {
                        key: deepcopy(value)
                        for key, value in item.items()
                        if key != "policy_tags" and not str(key).startswith("_")
                    }
                    for item in affordances
                    if isinstance(item, dict)
                ]
            world_objects[name] = state
        actor_states = {}
        actor_hidden = set(self.HOST_ONLY_ACTOR_STATE_FIELDS) | set(
            self.ACTOR_VISIBILITY_SCHEMA_FIELDS
        )
        for name, raw_state in self.actor_states.items():
            actor_states[name] = {
                key: deepcopy(value)
                for key, value in raw_state.items()
                if key not in actor_hidden and not str(key).startswith("_")
            } if isinstance(raw_state, dict) else {}
        return {
            "description": self.description,
            "world_objects": world_objects,
            "actor_states": actor_states,
            "scene_flags": deepcopy(self.scene_flags),
        }

    def get_actor_state(self, actor_name: str) -> Dict[str, Any]:
        return self.actor_states.get(actor_name, {})

    def get_public_actor_state(self, actor_name: str) -> Dict[str, Any]:
        """Project one actor into facts another character may directly see."""
        state = self.get_actor_state(actor_name)
        if not isinstance(state, dict):
            return {}
        additional = self._state_field_set(state.get("public_state_fields", []))
        private = self._state_field_set(state.get("private_state_fields", []))
        allowed = (set(self.PUBLIC_ACTOR_STATE_FIELDS) | additional) - private
        allowed -= set(self.ACTOR_VISIBILITY_SCHEMA_FIELDS)
        return {
            key: deepcopy(value)
            for key, value in state.items()
            if key in allowed and not str(key).startswith("_")
        }

    def get_self_actor_state(self, actor_name: str) -> Dict[str, Any]:
        """Project embodied self-state while withholding director controls."""
        state = self.get_actor_state(actor_name)
        if not isinstance(state, dict):
            return {}
        hidden = set(self.HOST_ONLY_ACTOR_STATE_FIELDS) | set(
            self.ACTOR_VISIBILITY_SCHEMA_FIELDS
        )
        return {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in hidden and not str(key).startswith("_")
        }

    @staticmethod
    def _state_field_set(value: Any) -> set[str]:
        if not isinstance(value, (list, tuple, set)):
            return set()
        return {
            str(item).strip()
            for item in value
            if isinstance(item, str) and str(item).strip()
        }

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
                "public_scene": self.get_public_scene_state(),
                "location": None,
                "visible_actors": [],
                "visible_actor_states": {},
                "visible_world": {},
                "visible_objects": [],
                "viewer_zone": None,
                "spatial_layout": {},
                "visible_spatial_facts": [],
            }

        location = self.get_actor_location(viewer_name)
        visible_actor_names = (
            list(self.get_actors_in_location(location))
            if location
            else [viewer_name] if viewer_name in self.actor_states else []
        )
        visible_actor_states = {
            name: self.get_public_actor_state(name)
            for name in visible_actor_names
        }
        raw_visible_objects = self.get_visible_objects(viewer_name)
        projected_visible_objects = {
            name: self.get_public_object_state(name)
            for name in raw_visible_objects
        }
        visible_world = {}
        if location:
            visible_world[location] = self.get_public_object_state(location)
            visible_world.update(projected_visible_objects)
        elif viewer_name:
            visible_world.update(projected_visible_objects)
        elif self.world_objects:
            visible_world = dict(self.world_objects)

        spatial_layout = self._build_spatial_layout(
            location=location,
            visible_actor_states=visible_actor_states,
            viewer_name=viewer_name,
        )
        return {
            "viewer": viewer_name,
            "public_scene": self.get_public_scene_state(),
            "location": location,
            "visible_actors": list(visible_actor_states.keys()),
            "visible_actor_states": visible_actor_states,
            "visible_world": visible_world,
            "visible_objects": list(raw_visible_objects),
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

        unknown_sections = sorted(
            str(key)
            for key in updates
            if key not in {"world_objects", "actor_states", "scene"}
        )
        if unknown_sections:
            raise ValueError(
                "unknown SceneState update sections: "
                + ", ".join(unknown_sections)
            )

    def get_snapshot(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "world_objects": self.world_objects,
            "actor_states": self.actor_states,
            "scene_flags": self.scene_flags,
            "public_scene_fields": list(self.public_scene_fields),
            "private_scene_fields": list(self.private_scene_fields),
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
        if not str(path or "").strip():
            return data if data else None
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
        if operator == "not_exists":
            return actual is None
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
        if isinstance(condition, dict):
            scope = condition.get("scope", "scene")
            target = condition.get("target")
            path = condition.get("path", "")
            operator = condition.get("operator", "eq")
            expected = condition.get("value")
        else:
            scope = condition.scope
            target = condition.target
            path = condition.path
            operator = condition.operator
            expected = condition.value
        source = self._resolve_scope(scope, target, plot_state=plot_state)
        actual = self._get_nested_value(source, path)
        return self._compare_value(actual, operator, expected)

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
