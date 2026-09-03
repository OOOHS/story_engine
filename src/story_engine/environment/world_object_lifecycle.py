import json
from typing import Any, Dict, List, Optional

from .narrative_candidates import CandidateLedger, record_candidate_audit

DYNAMIC_NAMES_FLAG = "dynamic_world_object_names"


class WorldObjectLifecycle:
    """Stages evidence-backed lifecycle changes for tangible world objects."""

    OPERATIONS = {
        "spawn",
        "relocate",
        "set_visibility",
        "set_container_state",
        "use",
        "destroy",
    }
    RESERVED_PROPERTIES = {
        "is_location",
        "kind",
        "location",
        "owner",
        "container",
        "sub_location",
        "hidden",
        "portable",
        "quantity",
        "stack_key",
        "affordances",
        "is_container",
        "container_capacity",
        "container_size",
        "container_open",
        "container_opaque",
    }
    WORLD_ACTOR = "World"

    def apply(
        self,
        scene_state: Any,
        result: Dict[str, Any],
        *,
        previous_scene_state: Any = None,
        current_step: int = 0,
    ) -> List[str]:
        operations = result.get("object_lifecycle", [])
        if not isinstance(operations, list):
            return ["object_lifecycle must be a list"]
        if not operations:
            return []
        if not scene_state:
            return ["object_lifecycle requires scene state"]

        unresolved_contests = self._find_unresolved_contests(scene_state, operations)
        if unresolved_contests:
            return unresolved_contests

        resolved_actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        errors: List[str] = []
        for index, request in enumerate(operations):
            prefix = f"object_lifecycle[{index}]"
            if not isinstance(request, dict):
                errors.append(f"{prefix} must be an object")
                continue
            operation = str(request.get("operation", "")).strip()
            object_id = self._text(request.get("object_id"), 120)
            actor = self._text(request.get("actor"), 120)
            reason = self._text(request.get("reason"), 500)
            if operation not in self.OPERATIONS:
                errors.append(f"{prefix} has unknown operation: {operation}")
                continue
            if not object_id:
                errors.append(f"{prefix} requires object_id")
                continue
            if actor != self.WORLD_ACTOR and actor not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown actor: {actor}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            if not self._has_action_evidence(actor, resolved_actions):
                errors.append(f"{prefix} is not supported by a resolved action")
            if errors and any(error.startswith(prefix) for error in errors):
                continue

            operation_errors: List[str] = []
            if operation == "spawn":
                self._spawn(
                    scene_state,
                    request,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
                record_candidate_audit(
                    scene_state,
                    kind="object",
                    source="gm",
                    accepted=not operation_errors,
                    reason="; ".join(operation_errors),
                    candidate_id=object_id,
                    step=current_step,
                )
            elif operation == "relocate":
                self._relocate(
                    scene_state,
                    request,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
            elif operation == "set_visibility":
                self._set_visibility(
                    scene_state,
                    request,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
            elif operation == "set_container_state":
                self._set_container_state(
                    scene_state,
                    request,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
            elif operation == "use":
                self._use(
                    scene_state,
                    request,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
            elif operation == "destroy":
                self._destroy(
                    scene_state,
                    object_id,
                    actor,
                    prefix,
                    operation_errors,
                    previous_scene_state,
                )
            errors.extend(operation_errors)
        return errors

    def _find_unresolved_contests(
        self,
        scene_state: Any,
        operations: List[Any],
    ) -> List[str]:
        """Reject order-sensitive claims that bypassed the contest resolver."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            operation_name = str(operation.get("operation", "")).strip()
            object_id = self._text(operation.get("object_id"), 120)
            if operation_name not in {
                "relocate",
                "set_visibility",
                "set_container_state",
                "use",
                "destroy",
            }:
                continue
            if (
                not object_id
                or object_id not in scene_state.world_objects
                or scene_state.is_location(object_id)
            ):
                continue
            grouped.setdefault(object_id, []).append(operation)

        errors: List[str] = []
        for object_id, claims in grouped.items():
            if len(claims) < 2:
                continue
            operation_names = {
                str(operation.get("operation", "")).strip()
                for operation in claims
            }
            actors = {
                self._text(operation.get("actor"), 120)
                for operation in claims
                if self._text(operation.get("actor"), 120)
            }
            if operation_names == {"set_visibility"}:
                hidden_values = [operation.get("hidden") for operation in claims]
                if hidden_values and all(
                    value == hidden_values[0] for value in hidden_values[1:]
                ):
                    continue
            elif operation_names == {"set_container_state"}:
                open_values = [operation.get("open") for operation in claims]
                if open_values and all(
                    value == open_values[0] for value in open_values[1:]
                ):
                    continue
            elif operation_names == {"use"}:
                state = scene_state.get_object_state(object_id)
                parsed_affordances = []
                malformed = False
                for operation in claims:
                    affordance = self.get_affordance(
                        state,
                        self._text(operation.get("affordance_id"), 120),
                    )
                    if affordance is None:
                        malformed = True
                        break
                    consumes = affordance.get("consumes", False)
                    exclusive = affordance.get("exclusive", False)
                    if not isinstance(consumes, bool) or not isinstance(exclusive, bool):
                        malformed = True
                        break
                    parsed_affordances.append(affordance)
                if malformed:
                    continue
                if all(
                    not affordance.get("consumes", False)
                    and not affordance.get("exclusive", False)
                    for affordance in parsed_affordances
                ):
                    continue
                if all(
                    affordance.get("consumes", False)
                    and not affordance.get("exclusive", False)
                    for affordance in parsed_affordances
                ):
                    quantity = state.get("quantity", 1)
                    if (
                        not isinstance(quantity, bool)
                        and isinstance(quantity, int)
                        and quantity >= len(claims)
                    ):
                        continue
            elif len(actors) < 2:
                # A single actor may intentionally sequence relocate, hide and
                # destroy operations within one resolved action.
                continue

            errors.append(
                f"unresolved simultaneous object contest: {object_id}"
            )
        return errors

    def _spawn(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        if object_id in scene_state.world_objects or object_id in scene_state.actor_states:
            errors.append(f"{prefix} cannot spawn existing object: {object_id}")
            return
        dynamic_names = self._dynamic_names(scene_state)
        cap_error = CandidateLedger.check_cap(
            scene_state,
            names_flag=DYNAMIC_NAMES_FLAG,
            cap_flag="max_dynamic_world_objects",
            default_cap=32,
        )
        if cap_error == "max_dynamic_world_objects must be an integer":
            errors.append(f"{prefix} has invalid max_dynamic_world_objects")
            return
        if cap_error:
            errors.append(f"{prefix} exceeds max_dynamic_world_objects")
            return

        kind = self._text(request.get("object_kind"), 80) or "item"
        if kind.lower() in {"location", "room", "place", "area", "building", "zone"}:
            errors.append(f"{prefix} cannot create spatial graph nodes")
        properties = request.get("properties", {})
        if not isinstance(properties, dict):
            errors.append(f"{prefix}.properties must be an object")
            properties = {}
        if len(properties) > 32:
            errors.append(f"{prefix}.properties has too many fields")
        if any(not isinstance(key, str) or not key.strip() for key in properties):
            errors.append(f"{prefix}.properties keys must be non-empty text")
        reserved = self.RESERVED_PROPERTIES.intersection(properties)
        if reserved:
            errors.append(
                f"{prefix}.properties contains reserved fields: {', '.join(sorted(reserved))}"
            )
        try:
            serialized = json.dumps(properties, ensure_ascii=False)
        except (TypeError, ValueError):
            serialized = ""
            errors.append(f"{prefix}.properties must be JSON serializable")
        if len(serialized) > 8000:
            errors.append(f"{prefix}.properties is too large")
        for key in ("portable", "hidden"):
            if key in request and not isinstance(request.get(key), bool):
                errors.append(f"{prefix}.{key} must be boolean")

        placement = self._validate_placement(
            scene_state,
            request,
            actor,
            prefix,
            errors,
            previous_scene_state,
            object_id=object_id,
        )
        if errors:
            return
        state = dict(properties)
        state.update(
            {
                "is_location": False,
                "kind": kind,
                "portable": bool(request.get("portable", True)),
                "hidden": bool(request.get("hidden", False)),
                **placement,
            }
        )
        scene_state.world_objects[object_id] = state
        CandidateLedger.append_name(scene_state, DYNAMIC_NAMES_FLAG, object_id)

    def _relocate(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        state = self._require_tangible(scene_state, object_id, prefix, errors)
        if state is None:
            return
        if not bool(state.get("portable", True)) and actor != self.WORLD_ACTOR:
            errors.append(f"{prefix} cannot relocate non-portable object: {object_id}")
            return
        self._require_actor_at_object(
            scene_state, object_id, actor, prefix, errors, previous_scene_state
        )
        placement = self._validate_placement(
            scene_state,
            request,
            actor,
            prefix,
            errors,
            previous_scene_state,
            object_id=object_id,
            object_state=state,
        )
        if errors:
            return
        state.update(placement)
        if "hidden" in request:
            if not isinstance(request.get("hidden"), bool):
                errors.append(f"{prefix}.hidden must be boolean")
                return
            state["hidden"] = request["hidden"]

    def _set_visibility(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        state = self._require_tangible(scene_state, object_id, prefix, errors)
        if state is None:
            return
        if not isinstance(request.get("hidden"), bool):
            errors.append(f"{prefix}.hidden must be boolean")
            return
        self._require_actor_at_object(
            scene_state, object_id, actor, prefix, errors, previous_scene_state
        )
        if not errors:
            state["hidden"] = request["hidden"]

    def _set_container_state(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        state = self._require_tangible(scene_state, object_id, prefix, errors)
        if state is None:
            return
        if not bool(state.get("is_container", False)):
            errors.append(f"{prefix} object is not a container: {object_id}")
            return
        if not isinstance(request.get("open"), bool):
            errors.append(f"{prefix}.open must be boolean")
            return
        self._require_actor_at_object(
            scene_state, object_id, actor, prefix, errors, previous_scene_state
        )
        if not errors:
            state["container_open"] = request["open"]

    def _destroy(
        self,
        scene_state: Any,
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        state = self._require_tangible(scene_state, object_id, prefix, errors)
        if state is None:
            return
        self._require_actor_at_object(
            scene_state, object_id, actor, prefix, errors, previous_scene_state
        )
        if scene_state.get_contained_objects(object_id):
            errors.append(f"{prefix} cannot destroy non-empty container: {object_id}")
        if errors:
            return
        self._remove_object(scene_state, object_id)

    def _use(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        state = self._require_tangible(scene_state, object_id, prefix, errors)
        if state is None:
            return
        affordance_id = self._text(request.get("affordance_id"), 120)
        if not affordance_id:
            errors.append(f"{prefix} requires affordance_id")
            return
        affordance = self.get_affordance(state, affordance_id)
        if affordance is None:
            errors.append(
                f"{prefix} references unknown affordance: {object_id}->{affordance_id}"
            )
            return
        required_capabilities = affordance.get("requires_capabilities", [])
        if not isinstance(required_capabilities, list) or any(
            not isinstance(item, str) or not item.strip()
            for item in required_capabilities
        ):
            errors.append(
                f"{prefix} affordance requires_capabilities must be a list of non-empty strings"
            )
            return
        requires_owner = affordance.get("requires_owner", False)
        if not isinstance(requires_owner, bool):
            errors.append(f"{prefix} affordance requires_owner must be boolean")
            return
        exclusive = affordance.get("exclusive", False)
        if not isinstance(exclusive, bool):
            errors.append(f"{prefix} affordance exclusive must be boolean")
            return
        self._require_actor_at_object(
            scene_state, object_id, actor, prefix, errors, previous_scene_state
        )
        if actor != self.WORLD_ACTOR:
            actor_capabilities = self._actor_capabilities(
                scene_state,
                previous_scene_state,
                actor,
            )
            missing = sorted(
                {
                    item.strip()
                    for item in required_capabilities
                    if item.strip() not in actor_capabilities
                }
            )
            if missing:
                errors.append(
                    f"{prefix} actor lacks required capabilities: {', '.join(missing)}"
                )
            if requires_owner and str(state.get("owner") or "").strip() != actor:
                errors.append(
                    f"{prefix} affordance requires actor to own object: {actor}->{object_id}"
                )
        consumes = affordance.get("consumes", False)
        if not isinstance(consumes, bool):
            errors.append(f"{prefix} affordance consumes must be boolean")
            return
        if errors or not consumes:
            return
        if scene_state.get_contained_objects(object_id):
            errors.append(f"{prefix} cannot consume non-empty container: {object_id}")
            return
        quantity = state.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            errors.append(f"{prefix} object quantity must be a positive integer")
            return
        if quantity > 1:
            state["quantity"] = quantity - 1
        else:
            self._remove_object(scene_state, object_id)

    def _remove_object(self, scene_state: Any, object_id: str) -> None:
        scene_state.world_objects.pop(object_id, None)
        dynamic_names = [name for name in self._dynamic_names(scene_state) if name != object_id]
        scene_state.update_scene_flags({"dynamic_world_object_names": dynamic_names})

    @staticmethod
    def get_affordance(state: Any, affordance_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(state, dict):
            return None
        affordances = state.get("affordances", [])
        if not isinstance(affordances, list):
            return None
        for item in affordances:
            if (
                isinstance(item, dict)
                and str(item.get("id", "")).strip() == str(affordance_id).strip()
            ):
                return item
        return None

    def _validate_placement(
        self,
        scene_state: Any,
        request: Dict[str, Any],
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
        *,
        object_id: str,
        object_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        owner = self._text(request.get("owner"), 120)
        location = self._text(request.get("location"), 120)
        container = self._text(request.get("container"), 120)
        if (owner or container) and request.get("sub_location"):
            errors.append(f"{prefix}.sub_location is only valid with location")
        if sum(bool(value) for value in (owner, location, container)) != 1:
            errors.append(
                f"{prefix} requires exactly one of owner, location or container"
            )
            return {}
        placement: Dict[str, Any] = {
            "owner": None,
            "location": None,
            "container": None,
            "sub_location": None,
        }
        proof_locations: set[str] = set()
        if owner:
            if owner not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown owner: {owner}")
                return {}
            placement["owner"] = owner
            if not scene_state.get_actor_location(owner):
                errors.append(f"{prefix} owner has no location: {owner}")
            proof_locations.update(
                self._actor_locations(scene_state, previous_scene_state, owner)
            )
        elif location:
            if location not in scene_state.get_known_locations():
                errors.append(f"{prefix} has unknown location: {location}")
                return {}
            placement["location"] = location
            proof_locations.add(location)
            sub_location = self._text(request.get("sub_location"), 120)
            if sub_location:
                zones = scene_state.get_object_state(location).get("zones", {})
                normalized_zones = scene_state._normalize_zones(zones)
                if normalized_zones and sub_location not in normalized_zones:
                    errors.append(
                        f"{prefix} has unknown sub_location: {location}/{sub_location}"
                    )
                placement["sub_location"] = sub_location
        else:
            if container == object_id:
                errors.append(f"{prefix} cannot place object inside itself: {object_id}")
                return {}
            target = self._require_tangible(scene_state, container, prefix, errors)
            if target is None:
                return {}
            if not bool(target.get("is_container", False)):
                errors.append(f"{prefix} destination is not a container: {container}")
                return {}
            if actor != self.WORLD_ACTOR and not bool(
                target.get("container_open", True)
            ):
                errors.append(f"{prefix} destination container is closed: {container}")
            if actor != self.WORLD_ACTOR and not scene_state.is_object_accessible(
                container, actor
            ):
                errors.append(
                    f"{prefix} destination container is inaccessible: {container}"
                )
            if object_id in scene_state.get_object_container_chain(container):
                errors.append(
                    f"{prefix} would create container cycle: {object_id}->{container}"
                )
            capacity = target.get("container_capacity")
            if (
                isinstance(capacity, bool)
                or not isinstance(capacity, int)
                or capacity < 1
            ):
                errors.append(f"{prefix} destination container has invalid capacity: {container}")
            else:
                load = sum(
                    self._object_size(child)
                    for child_id, child in scene_state.get_contained_objects(container).items()
                    if child_id != object_id
                )
                item_size = self._object_size(object_state or {})
                if load + item_size > capacity:
                    errors.append(
                        f"{prefix} exceeds container capacity: {container} "
                        f"({load + item_size} > {capacity})"
                    )
            placement["container"] = container
            destination = scene_state.get_effective_object_location(container)
            if not destination:
                errors.append(f"{prefix} destination container has no effective location: {container}")
            else:
                proof_locations.add(destination)
        if actor != self.WORLD_ACTOR:
            actor_locations = self._actor_locations(
                scene_state, previous_scene_state, actor
            )
            if not actor_locations.intersection(proof_locations):
                errors.append(f"{prefix} destination is not co-located with actor: {actor}")
        return placement

    def _require_tangible(
        self,
        scene_state: Any,
        object_id: str,
        prefix: str,
        errors: List[str],
    ) -> Optional[Dict[str, Any]]:
        if object_id not in scene_state.world_objects:
            errors.append(f"{prefix} references unknown object: {object_id}")
            return None
        if scene_state.is_location(object_id):
            errors.append(f"{prefix} cannot operate on location: {object_id}")
            return None
        state = scene_state.get_object_state(object_id)
        if not isinstance(state, dict):
            errors.append(f"{prefix} object state must be an object: {object_id}")
            return None
        return state

    def _require_actor_at_object(
        self,
        scene_state: Any,
        object_id: str,
        actor: str,
        prefix: str,
        errors: List[str],
        previous_scene_state: Any,
    ) -> None:
        if actor == self.WORLD_ACTOR:
            return
        if not scene_state.is_object_accessible(object_id, actor):
            errors.append(
                f"{prefix} object is inaccessible through its container chain: {object_id}"
            )
        actor_locations = self._actor_locations(scene_state, previous_scene_state, actor)
        object_locations = {
            location
            for location in (
                scene_state.get_effective_object_location(object_id),
                previous_scene_state.get_effective_object_location(object_id)
                if previous_scene_state
                else None,
            )
            if location
        }
        if not actor_locations.intersection(object_locations):
            errors.append(f"{prefix} actor is not co-located with object: {actor}->{object_id}")

    @staticmethod
    def _object_size(state: Dict[str, Any]) -> int:
        raw_size = state.get("container_size", 1) if isinstance(state, dict) else 1
        raw_quantity = state.get("quantity", 1) if isinstance(state, dict) else 1
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 1:
            return 1
        if (
            isinstance(raw_quantity, bool)
            or not isinstance(raw_quantity, int)
            or raw_quantity < 1
        ):
            raw_quantity = 1
        return raw_size * raw_quantity

    @staticmethod
    def _actor_locations(
        scene_state: Any,
        previous_scene_state: Any,
        actor: str,
    ) -> set[str]:
        return {
            location
            for location in (
                scene_state.get_actor_location(actor) if scene_state else None,
                previous_scene_state.get_actor_location(actor)
                if previous_scene_state
                else None,
            )
            if location
        }

    @staticmethod
    def _actor_capabilities(
        scene_state: Any,
        previous_scene_state: Any,
        actor: str,
    ) -> set[str]:
        source = previous_scene_state if (
            previous_scene_state
            and actor in getattr(previous_scene_state, "actor_states", {})
        ) else scene_state
        state = source.get_actor_state(actor) if source else {}
        raw = state.get("capabilities", []) if isinstance(state, dict) else []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return set()
        return {
            str(item).strip()
            for item in raw
            if isinstance(item, str) and str(item).strip()
        }

    def _has_action_evidence(self, actor: str, resolved_actions: List[Dict[str, Any]]) -> bool:
        return any(
            self._text(action.get("actor"), 120) == actor
            and str(action.get("outcome", "")).strip().lower()
            in {"success", "partial", "complication"}
            for action in resolved_actions
        )

    @staticmethod
    def _dynamic_names(scene_state: Any) -> List[str]:
        return CandidateLedger.normalized_names(scene_state, DYNAMIC_NAMES_FLAG)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
