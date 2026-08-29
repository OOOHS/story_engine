from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.story_engine.common.action_features import ACTION_POLICY_TAGS
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.character_lifecycle import (
    CharacterLifecycle,
    CharacterSpawnPlan,
)
from src.story_engine.environment.exchanges import ExchangeDynamics
from src.story_engine.environment.world_object_lifecycle import WorldObjectLifecycle
from src.story_engine.motivation import NeedDynamics
from src.story_engine.social import SocialDynamics


@dataclass
class WorldStateCheckpoint:
    scene_state: Any = field(repr=False, default=None)
    drama_state: Any = field(repr=False, default=None)
    relationship_book: Any = field(repr=False, default=None)
    scene_snapshot: Any = field(repr=False, default=None)
    drama_snapshot: Any = field(repr=False, default=None)
    relationship_snapshot: Any = field(repr=False, default=None)
    drive_states: Any = field(repr=False, default=None)
    drive_snapshots: Any = field(repr=False, default=None)

    def restore(self) -> None:
        if self.scene_state is not None and self.scene_snapshot is not None:
            self.scene_state.description = self.scene_snapshot["description"]
            self.scene_state.world_objects = deepcopy(self.scene_snapshot["world_objects"])
            self.scene_state.actor_states = deepcopy(self.scene_snapshot["actor_states"])
            self.scene_state.scene_flags = deepcopy(self.scene_snapshot["scene_flags"])
            self.scene_state.public_scene_fields = deepcopy(
                self.scene_snapshot.get("public_scene_fields", [])
            )
            self.scene_state.private_scene_fields = deepcopy(
                self.scene_snapshot.get("private_scene_fields", [])
            )
        if self.drama_state is not None and self.drama_snapshot is not None:
            self.drama_state.tension = self.drama_snapshot
        if self.relationship_book is not None and self.relationship_snapshot is not None:
            self.relationship_book.restore_from(self.relationship_snapshot)
        for name, drive in (self.drive_states or {}).items():
            snapshot = (self.drive_snapshots or {}).get(name)
            if drive is not None and snapshot is not None:
                drive.restore_from(snapshot)


@dataclass(frozen=True)
class TransactionResult:
    committed: bool
    errors: List[str] = field(default_factory=list)
    checkpoint: Any = field(default=None, repr=False, compare=False)


class WorldStateTransaction:
    """Validates simulation writes on component copies, then commits atomically."""

    ALLOWED_STATE_KEYS = {"scene", "world_objects", "actor_states"}
    OBJECT_LIFECYCLE_FIELDS = {
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
    SPATIAL_TOPOLOGY_FIELDS = {
        "is_location",
        "connected_to",
        "zones",
        "default_zone",
        "aliases",
    }
    ENGINE_MANAGED_FLAGS = {
        "dynamic_character_names",
        "max_dynamic_characters",
        "dynamic_world_object_names",
        "max_dynamic_world_objects",
        "agent_goal_wakeup_interval",
        "agent_open_goal_review_interval",
        "public_event_attention_budget",
        "world_version",
        "consumed_character_entry_authorizations",
        "consumed_storylets",
        "consumed_plot_rules",
    }

    def __init__(self) -> None:
        self.social = SocialDynamics()
        self.objects = WorldObjectLifecycle()
        self.exchanges = ExchangeDynamics()
        self.characters = CharacterLifecycle()
        self.needs = NeedDynamics()

    def commit(
        self,
        scene_state: Any,
        plot_state: Any,
        drama_state: Any,
        result: Dict[str, Any],
        relationship_book: Any = None,
        character_spawn_plan: CharacterSpawnPlan | None = None,
        drive_states: Dict[str, Any] | None = None,
        current_step: int = 0,
        proposal_actors: set[str] | None = None,
        consumed_storylet_ids: List[str] | None = None,
        emergent_meter_budget: int = 0,
    ) -> TransactionResult:
        # ``plot_state`` and ``consumed_storylet_ids`` are accepted only for
        # call-site compatibility. Plot clocks, storylet consumption, new
        # plot_beat_proposals, and director_signals are no longer
        # staged/committed here: they are narrative derivations of
        # already-committed world facts, produced by NarrativeDirector and
        # settled by ``CausalPlotEngine.settle`` after this transaction
        # succeeds, not rehearsed against a guess of what it will produce.
        errors: List[str] = []
        updates = result.get("state_updates", {})
        self._validate_scene_updates(scene_state, updates, errors)
        tension_delta = self._validate_tension_delta(result.get("tension_delta", 0.0), errors)
        if errors:
            return TransactionResult(False, errors)

        checkpoint = WorldStateCheckpoint(
            scene_state=scene_state,
            drama_state=drama_state,
            relationship_book=relationship_book,
            scene_snapshot=deepcopy(scene_state.get_snapshot()) if scene_state else None,
            drama_snapshot=deepcopy(drama_state.tension) if drama_state else None,
            relationship_snapshot=(
                relationship_book.__class__(**deepcopy(relationship_book.model_dump()))
                if relationship_book
                else None
            ),
            drive_states=dict(drive_states or {}),
            drive_snapshots={
                name: drive.__class__(**deepcopy(drive.model_dump()))
                for name, drive in (drive_states or {}).items()
                if drive is not None
            },
        )

        staged_scene = SceneState(**deepcopy(scene_state.get_snapshot())) if scene_state else None
        staged_drama = (
            drama_state.__class__(**deepcopy(drama_state.model_dump()))
            if drama_state
            else None
        )
        staged_relationships = (
            relationship_book.__class__(**deepcopy(relationship_book.model_dump()))
            if relationship_book
            else None
        )
        staged_drives = {
            name: drive.__class__(**deepcopy(drive.model_dump()))
            for name, drive in (drive_states or {}).items()
            if drive is not None
        }
        working_result = result
        try:
            if staged_scene:
                staged_scene.apply_updates(deepcopy(updates))
                errors.extend(
                    self.characters.stage(staged_scene, character_spawn_plan)
                )
                errors.extend(
                    self.exchanges.apply(
                        staged_scene,
                        deepcopy(working_result),
                        proposal_actors=set(proposal_actors or set()),
                    )
                )
                errors.extend(
                    self.needs.apply_object_affordances(
                        staged_drives,
                        staged_scene,
                        working_result,
                        current_step=int(current_step),
                    )
                )
                errors.extend(
                    self.needs.apply_explicit_updates(
                        staged_drives,
                        staged_scene,
                        working_result,
                        current_step=int(current_step),
                        previous_scene_state=scene_state,
                    )
                )
                errors.extend(
                    self.needs.apply_creations(
                        staged_drives,
                        staged_scene,
                        working_result,
                        current_step=int(current_step),
                        budget=int(emergent_meter_budget),
                    )
                )
                errors.extend(
                    self.objects.apply(
                        staged_scene,
                        deepcopy(working_result),
                        previous_scene_state=scene_state,
                    )
                )
                errors.extend(
                    self.social.validate_relation_updates(staged_scene, working_result)
                )
                self.social.apply_relation_updates(
                    staged_scene,
                    staged_relationships,
                    deepcopy(working_result),
                    current_step=int(current_step),
                )
                self.social.record_interactions(
                    staged_scene,
                    staged_relationships,
                    working_result,
                    current_step=int(current_step),
                )
                if working_result.get("relationship_updates") and staged_relationships is None:
                    errors.append("relationship_updates require a RelationshipBook")
                if staged_relationships:
                    self._validate_relationship_invariants(
                        staged_scene, staged_relationships, errors
                    )
                self._validate_resolved_actions(
                    scene_state,
                    working_result.get("resolved_actions", []),
                    set(proposal_actors or set()),
                    errors,
                )
                self._validate_scene_invariants(staged_scene, errors)
                for actor in staged_drives:
                    if actor not in staged_scene.actor_states:
                        errors.append(f"drive state references missing actor: {actor}")
            if staged_drama:
                staged_drama.apply_delta(tension_delta)
        except Exception as exc:
            errors.append(f"staging_failed:{type(exc).__name__}:{exc}")
        if errors:
            return TransactionResult(False, errors)

        if staged_scene:
            try:
                previous_version = int(
                    staged_scene.get_scene_flag("world_version", 0) or 0
                )
            except (TypeError, ValueError):
                previous_version = 0
            staged_scene.update_scene_flags({"world_version": previous_version + 1})

        if scene_state and staged_scene:
            scene_state.description = staged_scene.description
            scene_state.world_objects = staged_scene.world_objects
            scene_state.actor_states = staged_scene.actor_states
            scene_state.scene_flags = staged_scene.scene_flags
        if drama_state and staged_drama:
            drama_state.tension = staged_drama.tension
        if relationship_book and staged_relationships:
            relationship_book.restore_from(staged_relationships)
        for name, staged_drive in staged_drives.items():
            drive = (drive_states or {}).get(name)
            if drive is not None:
                drive.restore_from(staged_drive)
        if working_result is not result:
            result.clear()
            result.update(working_result)
        return TransactionResult(True, [], checkpoint=checkpoint)

    def sanitize_rejected_result(
        self,
        result: Dict[str, Any],
        errors: List[str],
    ) -> Dict[str, Any]:
        sanitized = dict(result)
        sanitized["resolved_actions"] = []
        sanitized["uncertain_outcomes"] = []
        sanitized["state_updates"] = {
            "scene": {},
            "world_objects": {},
            "actor_states": {},
        }
        sanitized["plot_updates"] = []
        sanitized["relationship_updates"] = []
        sanitized["social_impacts"] = []
        sanitized["modifier_updates"] = []
        sanitized["knowledge_updates"] = []
        sanitized["claim_discoveries"] = []
        sanitized["object_lifecycle"] = []
        sanitized["exchanges"] = []
        sanitized["resource_contests"] = []
        sanitized["drive_updates"] = []
        sanitized["drive_creations"] = []
        sanitized["director_signals"] = []
        sanitized["plot_beat_proposals"] = []
        sanitized["storylet_hits"] = []
        sanitized["tension_delta"] = 0.0
        sanitized["spawn_character"] = None
        sanitized["conflict_level"] = "none"
        sanitized["conflict_flags"] = []
        notes = list(sanitized.get("simulation_notes", []) or [])
        notes.append("权威状态事务拒绝了本轮结算：" + "；".join(errors))
        sanitized["simulation_notes"] = notes
        sanitized["transaction_rejected"] = True
        return sanitized

    def _validate_resolved_actions(
        self,
        scene_state: Any,
        actions: Any,
        proposal_actors: set[str],
        errors: List[str],
    ) -> None:
        if not isinstance(actions, list):
            errors.append("resolved_actions must be a list")
            return
        known_actors = set(scene_state.actor_states) if scene_state else set()
        for index, action in enumerate(actions):
            label = f"resolved_actions[{index}]"
            if not isinstance(action, dict):
                errors.append(f"{label} must be an object")
                continue
            actor = str(action.get("actor", "")).strip()
            if not actor:
                errors.append(f"{label} requires actor")
                continue
            if actor == "World":
                continue
            if actor not in known_actors:
                errors.append(f"{label} references unknown actor: {actor}")
            if actor not in proposal_actors:
                errors.append(
                    f"{label} actor has no current-turn proposal: {actor}"
                )

    def _validate_scene_updates(self, scene_state, updates, errors):
        if not isinstance(updates, dict):
            errors.append("state_updates must be an object")
            return
        unknown = set(updates).difference(self.ALLOWED_STATE_KEYS)
        if unknown:
            errors.append("unknown state_update sections: " + ", ".join(sorted(unknown)))
        for section in self.ALLOWED_STATE_KEYS:
            value = updates.get(section, {})
            if not isinstance(value, dict):
                errors.append(f"state_updates.{section} must be an object")
        if not scene_state:
            return
        for name, props in updates.get("world_objects", {}).items():
            if name not in scene_state.world_objects:
                errors.append(f"unknown world object: {name}")
            if not isinstance(props, dict):
                errors.append(f"world object update must be an object: {name}")
                continue
            topology = self.SPATIAL_TOPOLOGY_FIELDS.intersection(props)
            if topology:
                errors.append(
                    "spatial topology fields require a host world-building API: "
                    f"{name} -> {', '.join(sorted(topology))}"
                )
            if name in scene_state.world_objects and not scene_state.is_location(name):
                protected = self.OBJECT_LIFECYCLE_FIELDS.intersection(props)
                if protected:
                    errors.append(
                        "object placement fields require object_lifecycle: "
                        f"{name} -> {', '.join(sorted(protected))}"
                    )
        for name, props in updates.get("actor_states", {}).items():
            if name not in scene_state.actor_states:
                errors.append(f"unknown actor: {name}")
            if not isinstance(props, dict):
                errors.append(f"actor update must be an object: {name}")
                continue
            visibility_schema = SceneState.ACTOR_VISIBILITY_SCHEMA_FIELDS.intersection(
                props
            )
            if visibility_schema:
                errors.append(
                    "actor visibility schema is host-authored: "
                    f"{name} -> {', '.join(sorted(visibility_schema))}"
                )
            location = props.get("location")
            if location is not None and location not in scene_state.get_known_locations():
                errors.append(f"unknown actor location for {name}: {location}")
        scene_section = updates.get("scene", {})
        if isinstance(scene_section, dict) and "description" in scene_section:
            if not isinstance(scene_section["description"], str):
                errors.append("scene.description must be text")
        if isinstance(scene_section, dict):
            visibility_schema = SceneState.SCENE_VISIBILITY_SCHEMA_FIELDS.intersection(
                scene_section
            )
            if visibility_schema:
                errors.append(
                    "scene visibility schema is host-authored: "
                    + ", ".join(sorted(visibility_schema))
                )
            protected_flags = self.ENGINE_MANAGED_FLAGS.intersection(scene_section)
            if protected_flags:
                errors.append(
                    "engine-managed flags cannot be written through state_updates: "
                    + ", ".join(sorted(protected_flags))
                )

    def _validate_scene_invariants(self, scene_state, errors):
        known_locations = scene_state.get_known_locations()
        for actor, state in scene_state.actor_states.items():
            if not isinstance(state, dict):
                errors.append(f"actor state is not an object: {actor}")
                continue
            location = state.get("location")
            if location is not None and location not in known_locations:
                errors.append(f"actor references missing location: {actor}->{location}")
            sub_location = state.get("sub_location")
            if location and sub_location:
                zones = scene_state.get_object_state(location).get("zones", {})
                if isinstance(zones, dict) and zones and sub_location not in zones:
                    errors.append(f"actor references missing zone: {actor}->{location}/{sub_location}")
        for location, state in scene_state.world_objects.items():
            if not isinstance(state, dict):
                errors.append(f"world object state is not an object: {location}")
                continue
            if not scene_state.is_location(location):
                self._validate_tangible_object(scene_state, location, state, errors)
                continue
            for neighbor in state.get("connected_to", []) or []:
                if str(neighbor) not in known_locations:
                    errors.append(f"location graph references missing object: {location}->{neighbor}")
        dynamic_names = scene_state.get_scene_flag("dynamic_world_object_names", [])
        if not isinstance(dynamic_names, list):
            errors.append("dynamic_world_object_names must be a list")
        else:
            normalized = [str(item).strip() for item in dynamic_names if str(item).strip()]
            if len(normalized) != len(set(normalized)):
                errors.append("dynamic_world_object_names contains duplicates")
            for object_id in normalized:
                if object_id not in scene_state.world_objects:
                    errors.append(f"dynamic world object is missing: {object_id}")
                elif scene_state.is_location(object_id):
                    errors.append(f"dynamic world object cannot be a location: {object_id}")
        dynamic_characters = scene_state.get_scene_flag("dynamic_character_names", [])
        if not isinstance(dynamic_characters, list):
            errors.append("dynamic_character_names must be a list")
        else:
            normalized_characters = [
                str(item).strip() for item in dynamic_characters if str(item).strip()
            ]
            if len(normalized_characters) != len(set(normalized_characters)):
                errors.append("dynamic_character_names contains duplicates")
            for actor in normalized_characters:
                if actor not in scene_state.actor_states:
                    errors.append(f"dynamic character is missing actor state: {actor}")
            try:
                character_limit = max(
                    0, int(scene_state.get_scene_flag("max_dynamic_characters", 6) or 0)
                )
                if len(normalized_characters) > character_limit:
                    errors.append("dynamic_character_names exceeds max_dynamic_characters")
            except (TypeError, ValueError):
                errors.append("max_dynamic_characters must be an integer")
        consumed_entries = scene_state.get_scene_flag(
            "consumed_character_entry_authorizations", []
        )
        if not isinstance(consumed_entries, list):
            errors.append(
                "consumed_character_entry_authorizations must be a list"
            )
        else:
            normalized_entries = [
                str(item).strip() for item in consumed_entries if str(item).strip()
            ]
            if len(normalized_entries) != len(set(normalized_entries)):
                errors.append(
                    "consumed_character_entry_authorizations contains duplicates"
                )
        consumed_storylets = scene_state.get_scene_flag("consumed_storylets", [])
        if not isinstance(consumed_storylets, list):
            errors.append("consumed_storylets must be a list")
        else:
            normalized_storylets = [
                str(item).strip() for item in consumed_storylets if str(item).strip()
            ]
            if len(normalized_storylets) != len(set(normalized_storylets)):
                errors.append("consumed_storylets contains duplicates")

    def _validate_tangible_object(self, scene_state, object_id, state, errors):
        owner = str(state.get("owner") or "").strip()
        location = str(state.get("location") or "").strip()
        container = str(state.get("container") or "").strip()
        if sum(bool(value) for value in (owner, location, container)) != 1:
            errors.append(
                "tangible object requires exactly one owner, location or container: "
                f"{object_id}"
            )
        if owner and owner not in scene_state.actor_states:
            errors.append(f"object references missing owner: {object_id}->{owner}")
        if location and location not in scene_state.get_known_locations():
            errors.append(f"object references missing location: {object_id}->{location}")
        if container:
            if container == object_id:
                errors.append(f"object cannot contain itself: {object_id}")
            elif container not in scene_state.world_objects or scene_state.is_location(container):
                errors.append(f"object references missing container: {object_id}->{container}")
            elif not bool(scene_state.get_object_state(container).get("is_container", False)):
                errors.append(f"object references non-container: {object_id}->{container}")
            visited = {object_id}
            current = container
            while current:
                if current in visited:
                    errors.append(f"container cycle detected from object: {object_id}")
                    break
                visited.add(current)
                parent = scene_state.get_object_state(current)
                if not isinstance(parent, dict) or scene_state.is_location(current):
                    break
                current = str(parent.get("container") or "").strip()
        sub_location = str(state.get("sub_location") or "").strip()
        if sub_location and not location:
            errors.append(f"object sub_location requires direct location: {object_id}")
        if sub_location and location:
            zones = scene_state._normalize_zones(
                scene_state.get_object_state(location).get("zones", {})
            )
            if zones and sub_location not in zones:
                errors.append(
                    f"object references missing zone: {object_id}->{location}/{sub_location}"
                )
        forbidden = {"connected_to", "zones", "default_zone"}.intersection(state)
        if forbidden:
            errors.append(
                f"tangible object contains spatial graph fields: {object_id} -> "
                + ", ".join(sorted(forbidden))
            )
        if not isinstance(state.get("hidden", False), bool):
            errors.append(f"object hidden flag must be boolean: {object_id}")
        if not isinstance(state.get("portable", True), bool):
            errors.append(f"object portable flag must be boolean: {object_id}")
        is_container = state.get("is_container", False)
        if not isinstance(is_container, bool):
            errors.append(f"object is_container flag must be boolean: {object_id}")
            is_container = False
        container_size = state.get("container_size", 1)
        if (
            isinstance(container_size, bool)
            or not isinstance(container_size, int)
            or container_size < 1
        ):
            errors.append(f"object container_size must be a positive integer: {object_id}")
        if is_container:
            capacity = state.get("container_capacity")
            if (
                isinstance(capacity, bool)
                or not isinstance(capacity, int)
                or capacity < 1
            ):
                errors.append(
                    f"container capacity must be a positive integer: {object_id}"
                )
            else:
                load = 0
                for child in scene_state.get_contained_objects(object_id).values():
                    size = child.get("container_size", 1)
                    quantity = child.get("quantity", 1)
                    if (
                        not isinstance(size, bool)
                        and isinstance(size, int)
                        and size >= 1
                        and not isinstance(quantity, bool)
                        and isinstance(quantity, int)
                        and quantity >= 1
                    ):
                        load += size * quantity
                if load > capacity:
                    errors.append(
                        f"container capacity exceeded: {object_id} ({load} > {capacity})"
                    )
            if not isinstance(state.get("container_open", True), bool):
                errors.append(f"container open flag must be boolean: {object_id}")
            if not isinstance(state.get("container_opaque", True), bool):
                errors.append(f"container opaque flag must be boolean: {object_id}")
        elif any(
            field in state
            for field in ("container_capacity", "container_open", "container_opaque")
        ):
            errors.append(
                f"non-container object declares container-only fields: {object_id}"
            )
        quantity = state.get("quantity")
        if quantity is not None and (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 1
        ):
            errors.append(f"object quantity must be a positive integer: {object_id}")
        stack_key = state.get("stack_key")
        if stack_key is not None and (
            not isinstance(stack_key, str)
            or not stack_key.strip()
            or len(stack_key.strip()) > 120
        ):
            errors.append(f"object stack_key must be non-empty text: {object_id}")
        affordances = state.get("affordances", [])
        if not isinstance(affordances, list):
            errors.append(f"object affordances must be a list: {object_id}")
            return
        affordance_ids = []
        for index, affordance in enumerate(affordances):
            if not isinstance(affordance, dict):
                errors.append(f"object affordance must be an object: {object_id}[{index}]")
                continue
            affordance_id = str(affordance.get("id", "")).strip()
            if not affordance_id:
                errors.append(f"object affordance requires id: {object_id}[{index}]")
            elif affordance_id.startswith("engine:"):
                errors.append(
                    f"object affordance uses reserved engine id: {object_id}[{index}]"
                )
            else:
                affordance_ids.append(affordance_id)
            if not isinstance(affordance.get("consumes", False), bool):
                errors.append(f"object affordance consumes must be boolean: {object_id}[{index}]")
            if not isinstance(affordance.get("exclusive", False), bool):
                errors.append(f"object affordance exclusive must be boolean: {object_id}[{index}]")
            if not isinstance(affordance.get("requires_owner", False), bool):
                errors.append(f"object affordance requires_owner must be boolean: {object_id}[{index}]")
            required_capabilities = affordance.get("requires_capabilities", [])
            if not isinstance(required_capabilities, list):
                errors.append(
                    f"object affordance requires_capabilities must be a list: {object_id}[{index}]"
                )
            else:
                normalized_capabilities = []
                if len(required_capabilities) > 16:
                    errors.append(
                        f"object affordance has too many capability requirements: {object_id}[{index}]"
                    )
                for capability in required_capabilities:
                    if (
                        not isinstance(capability, str)
                        or not capability.strip()
                        or len(capability.strip()) > 80
                    ):
                        errors.append(
                            f"object affordance capability must be non-empty text: {object_id}[{index}]"
                        )
                        continue
                    normalized_capabilities.append(capability.strip())
                if len(normalized_capabilities) != len(set(normalized_capabilities)):
                    errors.append(
                        f"object affordance capabilities must be unique: {object_id}[{index}]"
                    )
            effects = affordance.get("need_effects", {})
            if not isinstance(effects, dict):
                errors.append(f"object affordance need_effects must be an object: {object_id}[{index}]")
                continue
            for need, delta in effects.items():
                if not str(need).strip():
                    errors.append(f"object affordance has empty need: {object_id}[{index}]")
                if (
                    isinstance(delta, bool)
                    or not isinstance(delta, (int, float))
                    or not -1.0 <= float(delta) <= 1.0
                ):
                    errors.append(
                        f"object affordance need effect out of range: {object_id}[{index}]"
                    )
            policy_tags = affordance.get("policy_tags", [])
            if not isinstance(policy_tags, list):
                errors.append(
                    f"object affordance policy_tags must be a list: {object_id}[{index}]"
                )
            else:
                normalized_tags = []
                if len(policy_tags) > 16:
                    errors.append(
                        f"object affordance has too many policy_tags: {object_id}[{index}]"
                    )
                for tag in policy_tags:
                    if (
                        not isinstance(tag, str)
                        or tag.strip() not in ACTION_POLICY_TAGS
                    ):
                        errors.append(
                            f"object affordance has unsupported policy tag: {object_id}[{index}]"
                        )
                        continue
                    normalized_tags.append(tag.strip())
                if len(normalized_tags) != len(set(normalized_tags)):
                    errors.append(
                        f"object affordance policy_tags must be unique: {object_id}[{index}]"
                    )
        if len(affordance_ids) != len(set(affordance_ids)):
            errors.append(f"object affordance ids must be unique: {object_id}")

    def _validate_relationship_invariants(self, scene_state, relationship_book, errors):
        known_actors = set(scene_state.actor_states)
        for relation_id, record in relationship_book.relationships.items():
            for participant in record.participants:
                if participant not in known_actors:
                    errors.append(
                        f"relationship has unknown participant actor: {participant}"
                    )
            if relation_id != relationship_book.relation_id(*record.participants):
                errors.append(f"relationship id does not match participants: {relation_id}")
            valid_directions = {
                relationship_book.direction_key(source, target)
                for source in record.participants
                for target in record.participants
                if source != target
            }
            for direction, tracks in record.directed_tracks.items():
                if direction not in valid_directions:
                    errors.append(f"invalid relationship direction: {relation_id}.{direction}")
                for track_id, track in tracks.items():
                    if track.minimum > track.maximum:
                        errors.append(
                            f"relationship track has invalid bounds: {direction}.{track_id}"
                        )
                    if not track.minimum <= track.value <= track.maximum:
                        errors.append(
                            f"relationship track out of range: {direction}.{track_id}"
                        )

    def _validate_tension_delta(self, value, errors) -> float:
        try:
            delta = float(value)
        except (TypeError, ValueError):
            errors.append("tension_delta must be numeric")
            return 0.0
        if not -1.0 <= delta <= 1.0:
            errors.append("tension_delta must be between -1 and 1")
        return delta
