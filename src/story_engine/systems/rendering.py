from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.components.scene_state import SceneState


class RenderingSystem(System):
    """
    Converts resolved simulation output into player-facing narration.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        overrides = context.get("overrides", {})
        simulation_result = context.get("simulation_result", {})
        if not simulation_result:
            return
        player_pov = context.get("player_pov", {})
        visibility_window = context.get("visibility_window", {})
        visible_locations = visibility_window.get("locations", []) if isinstance(visibility_window, dict) else []
        visible_simulation = self._build_visible_simulation(
            simulation_result,
            player_pov,
            visible_locations=visible_locations,
        )
        timeline = context.get("timeline", {})
        public_timeline = self._public_timeline(timeline, player_pov)
        social = self._public_social(context.get("social", {}))

        for name, entity in entities.items():
            renderer = entity.get_component("NarrativeRenderer")
            if not renderer:
                continue
            scene_state = entity.get_component("SceneState")
            continuity = {}
            if scene_state:
                continuity = scene_state.get_scene_flag("last_player_visible_snapshot", {}) or {}
            visible_fact_lines = self._collect_visible_fact_lines(visible_simulation)

            render_payload = {
                "player_pov": player_pov,
                "simulation_result": visible_simulation,
                "timeline": public_timeline,
                "social": social,
                "continuity": continuity,
                "current_visible_facts": visible_fact_lines,
                "visible_spatial_facts": list(player_pov.get("visible_spatial_facts", [])),
                "spatial_layout": player_pov.get("spatial_layout", {}),
            }

            if name in overrides:
                narration = overrides[name]
                print(f"\n> World Engine (MANUAL RENDER): {narration}\n")
            else:
                narration = renderer.render(render_payload)
                print(f"\n> World Engine: {narration}\n")

            if scene_state:
                scene_state.update_scene_flags(
                    {
                        "last_player_visible_snapshot": {
                            "narration": narration,
                            "visible_facts": visible_fact_lines,
                        }
                    }
                )

            context["rendered_text"] = narration
            context["visible_simulation_result"] = visible_simulation
            context["visible_timeline"] = public_timeline
            return

    def _build_visible_simulation(
        self,
        simulation_result: Dict[str, Any],
        player_pov: Dict[str, Any],
        visible_locations: List[str] = None,
    ) -> Dict[str, Any]:
        allowed_locations = [
            str(item).strip()
            for item in (visible_locations or [])
            if str(item).strip()
        ]
        if not allowed_locations:
            visible_location = str(player_pov.get("location", "")).strip()
            if visible_location:
                allowed_locations.append(visible_location)
        allowed_location_set = set(allowed_locations)
        visible_actions: List[Dict[str, Any]] = []
        for item in simulation_result.get("resolved_actions", []):
            visibility = item.get("visibility", "public")
            item_location = item.get("location")
            if visibility != "public":
                continue
            if allowed_location_set and item_location and str(item_location).strip() not in allowed_location_set:
                continue
            visible_actions.append(item)
        visible_actions = [
            {key: value for key, value in item.items() if key != "private_result"}
            for item in visible_actions
        ]

        visible_object_ids = {
            str(item).strip()
            for item in player_pov.get("visible_world", {})
            if str(item).strip()
        }
        visible_action_actors = {
            str(item.get("actor", "")).strip()
            for item in visible_actions
            if str(item.get("actor", "")).strip()
        }
        visible_lifecycle = [
            item
            for item in simulation_result.get("object_lifecycle", [])
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip() in visible_action_actors
            and str(item.get("object_id", "")).strip() in visible_object_ids
        ]
        state_updates = simulation_result.get("state_updates", {})
        visible_state_updates = state_updates
        if isinstance(state_updates, dict):
            world_updates = state_updates.get("world_objects", {})
            actor_updates = state_updates.get("actor_states", {})
            public_object_states = player_pov.get("visible_world", {})
            public_actor_states = player_pov.get("visible_actor_states", {})
            public_scene_flags = (
                player_pov.get("public_scene", {}).get("flags", {})
                if isinstance(player_pov.get("public_scene", {}), dict)
                else {}
            )
            scene_updates = state_updates.get("scene", {})
            visible_actor_ids = set(player_pov.get("visible_actors", [])) | set(
                visible_action_actors
            )
            visible_state_updates = {
                **state_updates,
                "world_objects": {
                    object_id: {
                        field: value
                        for field, value in props.items()
                        if field in (public_object_states.get(object_id, {}) or {})
                        and field not in SceneState.HOST_ONLY_OBJECT_STATE_FIELDS
                        and field not in SceneState.OBJECT_VISIBILITY_SCHEMA_FIELDS
                        and not str(field).startswith("_")
                    }
                    for object_id, props in world_updates.items()
                    if object_id in visible_object_ids and isinstance(props, dict)
                } if isinstance(world_updates, dict) else {},
                "actor_states": {
                    actor: {
                        field: value
                        for field, value in props.items()
                        if field
                        in (
                            set(SceneState.PUBLIC_ACTOR_STATE_FIELDS)
                            | set(
                                (public_actor_states.get(actor, {}) or {}).keys()
                            )
                        )
                        and field
                        not in SceneState.ACTOR_VISIBILITY_SCHEMA_FIELDS
                    }
                    for actor, props in actor_updates.items()
                    if actor in visible_actor_ids and isinstance(props, dict)
                } if isinstance(actor_updates, dict) else {},
                "scene": {
                    field: value
                    for field, value in scene_updates.items()
                    if field == "description" or field in public_scene_flags
                } if isinstance(scene_updates, dict) else {},
            }

        visible_movements = [
            item
            for item in simulation_result.get("actor_movements", [])
            if isinstance(item, dict)
            and str(item.get("visibility", "local")) != "hidden"
            and (
                str(item.get("actor", "")) in visible_action_actors
                or str(item.get("origin", "")) in allowed_location_set
                or str(item.get("destination", "")) in allowed_location_set
            )
        ]
        visible_object_changes = [
            item
            for item in simulation_result.get("object_state_changes", [])
            if isinstance(item, dict)
            and str(item.get("visibility", "local")) != "hidden"
            and str(item.get("object_id", "")) in visible_object_ids
        ]
        visible_host_object_changes = [
            {
                key: item.get(key)
                for key in (
                    "change_id",
                    "object_id",
                    "paths",
                    "location",
                    "visibility",
                    "occurred_step",
                    "statement",
                )
            }
            for item in simulation_result.get("host_object_state_changes", [])
            if isinstance(item, dict)
            and str(item.get("visibility", "local")) != "hidden"
            and (
                not allowed_location_set
                or str(item.get("location", "")).strip() in allowed_location_set
            )
        ]
        visible_scene_changes = [
            item
            for item in simulation_result.get("scene_state_changes", [])
            if isinstance(item, dict)
            and str(item.get("visibility", "public")) == "public"
            and str(item.get("path", "")) in public_scene_flags
        ]
        viewer_location = str(player_pov.get("location", "")).strip()
        visible_topology_changes = [
            {
                key: item.get(key)
                for key in (
                    "change_id",
                    "operation",
                    "source",
                    "target",
                    "bidirectional",
                    "visibility",
                    "occurred_step",
                    "statement",
                )
            }
            for item in simulation_result.get("topology_changes", [])
            if isinstance(item, dict)
            and str(item.get("visibility", "local")) != "hidden"
            and (
                str(item.get("visibility", "local")) == "public"
                or viewer_location
                in {
                    str(item.get("source", "")).strip(),
                    str(item.get("target", "")).strip(),
                }
            )
        ]

        return {
            **simulation_result,
            "resolved_actions": visible_actions,
            "uncertain_outcomes": [],
            "state_updates": visible_state_updates,
            "actor_movements": visible_movements,
            "object_state_changes": visible_object_changes,
            "host_object_state_changes": visible_host_object_changes,
            "scene_state_changes": visible_scene_changes,
            "topology_changes": visible_topology_changes,
            "plot_updates": [],
            "storylet_hits": [],
            "conflict_flags": [],
            "applied_conflict_templates": [],
            "causal_plot_rules": [],
            "causal_plot_suppressed_rules": [],
            "tension_delta": 0.0,
            "spawn_character": None,
            "simulation_notes": [],
            "object_lifecycle": visible_lifecycle,
            "exchanges": [],
            "resource_contests": [],
            "action_feedback": [],
            "drive_updates": [],
            "social_impacts": [],
            "modifier_updates": [],
            "knowledge_updates": [],
            "claim_discoveries": [],
        }

    @staticmethod
    def _public_social(social: Any) -> Dict[str, Any]:
        if not isinstance(social, dict):
            return {}
        relations = []
        for item in social.get("visible_relations", []) or []:
            if not isinstance(item, dict):
                continue
            relations.append(
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"bias", "framing_style", "territorial"}
                }
            )
        return {
            "viewer": social.get("viewer"),
            "visible_relations": relations,
            "allow_unsignaled_touch": bool(
                social.get("allow_unsignaled_touch", False)
            ),
            "prefer_noncontact_signals": bool(
                social.get("prefer_noncontact_signals", True)
            ),
        }

    @staticmethod
    def _public_timeline(
        timeline: Any,
        player_pov: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(timeline, dict):
            return {}
        public_flags = (
            player_pov.get("public_scene", {}).get("flags", {})
            if isinstance(player_pov.get("public_scene", {}), dict)
            else {}
        )
        packet: Dict[str, Any] = {}
        if "day_phase" in public_flags:
            packet["day_phase"] = timeline.get("day_phase")
        transition = timeline.get("phase_transition", {})
        if isinstance(transition, dict) and transition.get("from") and transition.get("to"):
            packet["phase_transition"] = {
                "from": transition.get("from"),
                "to": transition.get("to"),
            }
        missed = timeline.get("last_missed_commitment")
        player = str(player_pov.get("viewer", "")).strip()
        if (
            isinstance(missed, dict)
            and player
            and player in set(missed.get("missing_participants", []) or [])
        ):
            packet["last_missed_commitment"] = {
                key: missed.get(key)
                for key in ("commitment_id", "title", "location", "note")
                if missed.get(key) not in (None, "")
            }
        return packet

    def _collect_visible_fact_lines(self, simulation_result: Dict[str, Any]) -> List[str]:
        visible_facts: List[str] = []
        for item in simulation_result.get("resolved_actions", []):
            if item.get("visibility", "public") != "public":
                continue
            actor = item.get("actor", "某人")
            result = item.get("result", "")
            if result:
                visible_facts.append(f"{actor}: {result}")
        for item in simulation_result.get("topology_changes", []):
            statement = str(item.get("statement", "")).strip()
            if statement:
                visible_facts.append(statement)
        for item in simulation_result.get("host_object_state_changes", []):
            statement = str(item.get("statement", "")).strip()
            if statement:
                visible_facts.append(statement)
        return visible_facts
