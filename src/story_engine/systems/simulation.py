from copy import deepcopy
from typing import Dict, Any, List
from src.story_engine.systems.system import System
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.common.movement_intent import extract_move_target_from_intent
from src.config.config import config


class SimulationSystem(System):
    """
    Resolves collected intents into authoritative state changes.
    """
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        for name, entity in list(entities.items()):
            simulation = entity.get_component("SimulationControl")
            if not simulation:
                continue

            scene_state = entity.get_component("SceneState")
            drama_state = entity.get_component("DramaState")
            plot_state = entity.get_component("PlotState")
            relationship_state = entity.get_component("RelationshipState")
            situation_state = entity.get_component("SituationState")
            scenario = getattr(simulation, "scenario", None)
            player_name = scenario.player_character_name if scenario else None
            current_step = context.get("clock").current_step if context.get("clock") else 0
            timeline_packet = self._refresh_timeline(scene_state, context, player_name=player_name)
            player_pov = scene_state.get_view_pov(player_name) if scene_state else {}
            pre_resolution_location = player_pov.get("location") if isinstance(player_pov, dict) else None
            situation_packet = self._refresh_situations(
                scene_state=scene_state,
                plot_state=plot_state,
                situation_state=situation_state,
                player_name=player_name,
                player_pov=player_pov,
                timeline_packet=timeline_packet,
                current_step=current_step,
            )
            player_intent = next(
                (item for item in context.get("intents", []) if item.get("actor") == player_name),
                None,
            )
            active_storylets = self._resolve_storylets(
                scene_state,
                plot_state,
                scenario,
                situation_packet=situation_packet,
            )
            storylet_packet = self._build_storylet_packet(
                scene_state=scene_state,
                active_storylets=active_storylets,
                current_step=current_step,
                situation_packet=situation_packet,
            )
            social_packet = self._build_social_packet(
                scene_state=scene_state,
                relationship_state=relationship_state,
                player_name=player_name,
                player_pov=player_pov,
            )
            motive_packet = self._build_motive_packet(
                scene_state=scene_state,
                scenario=scenario,
                player_name=player_name,
                player_pov=player_pov,
                social_packet=social_packet,
                timeline_packet=timeline_packet,
            )
            reaction_context = self._build_reaction_context(
                player_name,
                player_pov,
                player_intent,
                social_packet,
                timeline_packet=timeline_packet,
            )
            intent_focus = self._build_intent_focus_packet(
                intents=context.get("intents", []),
                player_name=player_name,
                player_intent=player_intent,
                timeline_packet=timeline_packet,
                reaction_context=reaction_context,
            )
            legality_context = self._build_legality_context(
                scene_state=scene_state,
                scenario=scenario,
                intents=context.get("intents", []),
            )
            plot_packets = plot_state.get_pressure_packets() if plot_state else []
            director_packet = drama_state.build_directive(plot_packets) if drama_state else {}
            conflict_packet = self._build_conflict_packet(
                scene_state=scene_state,
                scenario=scenario,
                current_step=current_step,
                reaction_context=reaction_context,
                storylet_packet=storylet_packet,
                timeline_packet=timeline_packet,
                director_packet=director_packet,
            )
            input_payload = {
                "current_step": current_step,
                "player_name": player_name,
                "player_pov": player_pov,
                "player_intent": player_intent or {},
                "intents": context.get("intents", []),
                "active_storylets": active_storylets,
                "storylet_pressure": storylet_packet,
                "director_packet": director_packet,
                "plot_snapshot": plot_state.get_snapshot() if plot_state else {},
                "timeline": timeline_packet,
                "situations": situation_packet,
                "reaction_context": reaction_context,
                "intent_focus": intent_focus,
                "social": social_packet,
                "motive_pressure": motive_packet,
                "legality": legality_context,
                "conflict": conflict_packet,
            }

            result = simulation.simulate(input_payload)

            if scene_state:
                scene_state.apply_updates(result.get("state_updates"))
                if relationship_state:
                    relationship_state.refresh_from_actor_states(scene_state.actor_states)
                self._apply_relation_drift(scene_state, relationship_state, result, player_name)
                if relationship_state:
                    relationship_state.sync_actor_states(scene_state.actor_states)
                self._record_conflict_result(scene_state, context, result)
                timeline_packet = self._finalize_timeline(scene_state, context, player_name)
                self._consume_storylets(scene_state, scenario, result.get("storylet_hits", []))
                player_pov = scene_state.get_view_pov(player_name) if player_name else {}
                social_packet = self._build_social_packet(
                    scene_state=scene_state,
                    relationship_state=relationship_state,
                    player_name=player_name,
                    player_pov=player_pov,
                )
                situation_packet = self._refresh_situations(
                    scene_state=scene_state,
                    plot_state=plot_state,
                    situation_state=situation_state,
                    player_name=player_name,
                    player_pov=player_pov,
                    timeline_packet=timeline_packet,
                    current_step=current_step,
                )
                motive_packet = self._build_motive_packet(
                    scene_state=scene_state,
                    scenario=scenario,
                    player_name=player_name,
                    player_pov=player_pov,
                    social_packet=social_packet,
                    timeline_packet=timeline_packet,
                )
            visibility_window = self._build_visibility_window(
                pre_resolution_location,
                player_pov.get("location") if isinstance(player_pov, dict) else None,
            )
            if plot_state:
                plot_state.apply_updates(result.get("plot_updates", []))
            if drama_state:
                drama_state.apply_delta(result.get("tension_delta", 0.0))

            spawned = self._spawn_character_if_needed(entities, result.get("spawn_character"))

            context["simulation_result"] = result
            context["director_packet"] = director_packet
            context["active_storylets"] = active_storylets
            context["timeline"] = timeline_packet
            context["situations"] = situation_packet
            context["reaction_context"] = reaction_context
            context["intent_focus"] = intent_focus
            context["social"] = social_packet
            context["motive_pressure"] = motive_packet
            context["legality"] = legality_context
            context["conflict"] = conflict_packet
            context["storylet_pressure"] = storylet_packet
            context["state_snapshot"] = scene_state.get_snapshot() if scene_state else {}
            context["player_pov"] = player_pov
            context["visibility_window"] = visibility_window
            context["spawned_characters"] = spawned

            world_updates = result.get("state_updates", {}).get("world_objects", {})
            actor_updates = result.get("state_updates", {}).get("actor_states", {})
            print(
                f"    -> Structured resolution ready: "
                f"{len(result.get('resolved_actions', []))} actions, "
                f"{len(world_updates)} world updates, {len(actor_updates)} actor updates."
            )
            return

    def _build_visibility_window(
        self,
        before_location: Any,
        after_location: Any,
    ) -> Dict[str, Any]:
        locations: List[str] = []
        for raw in [before_location, after_location]:
            location = str(raw).strip() if raw else ""
            if location and location not in locations:
                locations.append(location)
        return {
            "locations": locations,
            "moved_this_turn": len(locations) > 1,
        }

    def _build_reaction_context(
        self,
        player_name: Any,
        player_pov: Dict[str, Any],
        player_intent: Any,
        social_packet: Dict[str, Any],
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        visible_actor_states = player_pov.get("visible_actor_states", {}) or {}
        transition_pressure = timeline_packet.get("transition_pressure", {}) if isinstance(timeline_packet, dict) else {}
        transition_states = (
            transition_pressure.get("carrier_states", {})
            if isinstance(transition_pressure, dict)
            else {}
        )
        merged_actor_states = dict(visible_actor_states)
        if isinstance(transition_states, dict):
            for name, state in transition_states.items():
                if name == player_name or name in merged_actor_states or not isinstance(state, dict):
                    continue
                merged_actor_states[name] = state
        visible_actors = [
            name for name in player_pov.get("visible_actors", [])
            if name and name != player_name
        ]
        transition_watchers = [
            str(name).strip()
            for name in transition_pressure.get("carrier_actors", [])
            if str(name).strip() and str(name).strip() != str(player_name)
        ] if isinstance(transition_pressure, dict) else []
        for name in transition_watchers:
            if name not in visible_actors:
                visible_actors.append(name)
        player_action = player_intent.get("intent", "") if isinstance(player_intent, dict) else ""
        social_map = {
            str(item.get("actor", "")).strip(): item
            for item in social_packet.get("visible_relations", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        } if isinstance(social_packet, dict) else {}
        hostile_watchers = []
        for name in visible_actors:
            state = merged_actor_states.get(name, {}) if isinstance(merged_actor_states, dict) else {}
            if not isinstance(state, dict):
                continue
            relation = social_map.get(name, {})
            toward_player = relation.get("toward_viewer", {}) if isinstance(relation, dict) else {}
            if not toward_player:
                toward_player = {
                    "favor": self._extract_relation_meter(state, "favor_", player_name),
                    "malice": self._extract_relation_meter(state, "malice_", player_name),
                    "trust": self._extract_relation_meter(state, "trust_", player_name),
                }
            trust_penalty = isinstance(toward_player.get("trust"), (int, float)) and toward_player.get("trust", 0) < 0
            malice_pressure = isinstance(toward_player.get("malice"), (int, float)) and toward_player.get("malice", 0) > 0
            if (
                state.get("territorial")
                or state.get("framing_style")
                or trust_penalty
                or malice_pressure
                or state.get("bias")
            ):
                hostile_watchers.append(name)
        action_pressure = "high"
        if any(token in player_action for token in ["不去", "不肯", "不愿", "拒绝", "不坐", "不回"]):
            action_pressure = "high" if transition_watchers else "medium"
        elif any(token in player_action for token in ["观察", "沉默", "先看", "不说话", "站着", "等等"]):
            action_pressure = "low"
        elif any(token in player_action for token in ["问", "质问", "拒绝", "反驳", "去", "拿", "碰", "坐", "说"]):
            action_pressure = "medium"
        return {
            "location": player_pov.get("location"),
            "visible_watchers": visible_actors,
            "hostile_watchers": hostile_watchers,
            "transition_watchers": transition_watchers,
            "transition_requires_backlash": bool(
                isinstance(transition_pressure, dict)
                and transition_pressure.get("requires_human_backlash")
            ),
            "player_action": player_action,
            "action_pressure": action_pressure,
            "requires_reaction": bool(player_action and visible_actors),
        }

    def _build_legality_context(
        self,
        scene_state: Any,
        scenario: Any,
        intents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        profile = getattr(scenario, "physics_profile", "mundane") if scenario else "mundane"
        checks = [
            self._assess_intent_legality(scene_state, profile, item)
            for item in intents or []
            if isinstance(item, dict) and item.get("actor")
        ]
        return {
            "physics_profile": profile,
            "checks": checks,
        }

    def _build_conflict_packet(
        self,
        scene_state: Any,
        scenario: Any,
        current_step: int,
        reaction_context: Dict[str, Any],
        storylet_packet: Dict[str, Any],
        timeline_packet: Dict[str, Any],
        director_packet: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        if not scene_state or not scenario:
            return {}

        conflict_cfg = getattr(scenario, "conflict", None)
        if not conflict_cfg:
            return {}

        scene_flags = scene_state.scene_flags or {}
        quiet_turns = int(scene_flags.get("quiet_turns_since_conflict", 0))
        visible_conflict_count = int(scene_flags.get("visible_conflict_count", 0))
        day_phase = str(scene_flags.get("day_phase", ""))
        recent_template_ids = [
            str(item)
            for item in scene_flags.get("recent_conflict_template_ids", [])
            if str(item).strip()
        ]
        templates = self._select_conflict_templates(
            getattr(scenario, "conflict_templates", []),
            day_phase,
            current_step,
        )
        immediate_pressure = bool(
            reaction_context.get("requires_reaction")
            and reaction_context.get("action_pressure") != "low"
            and float(conflict_cfg.intensity) >= 0.8
        )
        transition_requires_backlash = bool(
            isinstance(timeline_packet, dict)
            and isinstance(timeline_packet.get("transition_pressure"), dict)
            and timeline_packet.get("transition_pressure", {}).get("requires_human_backlash")
        )
        director_directive = str((director_packet or {}).get("directive", "")).strip()
        director_escalation = director_directive in {"inject_crisis", "raise_pressure"}
        require_visible_conflict = bool(
            reaction_context.get("hostile_watchers")
            and (
                transition_requires_backlash
                or
                immediate_pressure
                or director_escalation
                or
                (current_step <= int(conflict_cfg.force_visible_conflict_before_step) and visible_conflict_count == 0)
                or quiet_turns >= int(conflict_cfg.max_quiet_turns)
            )
        )

        return {
            "current_step": int(current_step),
            "intensity": float(conflict_cfg.intensity),
            "immediate_pressure": immediate_pressure,
            "transition_requires_backlash": transition_requires_backlash,
            "require_visible_conflict": require_visible_conflict,
            "minimum_level_when_forced": (
                "high"
                if director_escalation and float(conflict_cfg.intensity) >= 0.9
                else conflict_cfg.minimum_level_when_forced
            ),
            "antagonist_names": list(conflict_cfg.antagonist_names),
            "preferred_modes": list(conflict_cfg.preferred_modes),
            "surface_style": str(conflict_cfg.surface_style),
            "verbal_directness": float(conflict_cfg.verbal_directness),
            "repetition_window": int(conflict_cfg.repetition_window),
            "director_directive": director_directive,
            "prefer_public_pressure": bool(
                director_escalation
                or (
                    day_phase in {"arrival", "pre_dinner", "dinner"}
                    and visible_conflict_count <= 1
                )
            ),
            "max_forced_actions": 3 if director_escalation and float(conflict_cfg.intensity) >= 0.9 else 2,
            "quiet_turns_since_conflict": quiet_turns,
            "visible_conflict_count": visible_conflict_count,
            "recent_template_ids": recent_template_ids,
            "storylet_ids": list(storylet_packet.get("priority_storylet_ids", [])),
            "storylet_tags": list(storylet_packet.get("priority_tags", [])),
            "storylet_template_ids": list(storylet_packet.get("preferred_template_ids", [])),
            "active_templates": templates,
        }

    def _build_social_packet(
        self,
        scene_state: Any,
        relationship_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not scene_state or not player_name:
            return {}

        visible_actors = [
            name for name in player_pov.get("visible_actors", [])
            if name and name != player_name
        ]
        visible_actor_states = player_pov.get("visible_actor_states", {}) or {}
        if relationship_state and hasattr(relationship_state, "get_visible_relations"):
            relations = relationship_state.get_visible_relations(
                viewer=player_name,
                visible_actors=visible_actors,
                actor_states=visible_actor_states,
            )
        else:
            relations = []
            for actor in visible_actors:
                state = visible_actor_states.get(actor, {})
                if not isinstance(state, dict):
                    continue
                relations.append(
                    {
                        "actor": actor,
                        "bias": state.get("bias"),
                        "framing_style": state.get("framing_style"),
                        "territorial": bool(state.get("territorial")),
                        "toward_viewer": {
                            "favor": self._extract_relation_meter(state, "favor_", player_name),
                            "malice": self._extract_relation_meter(state, "malice_", player_name),
                            "trust": self._extract_relation_meter(state, "trust_", player_name),
                        },
                    }
                )

        return {
            "viewer": player_name,
            "visible_relations": relations,
            "allow_unsignaled_touch": False,
            "prefer_noncontact_signals": True,
            "max_unsignaled_touch_per_turn": 0,
        }

    def _build_motive_packet(
        self,
        scene_state: Any,
        scenario: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        social_packet: Dict[str, Any],
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not scene_state or not scenario or not player_name:
            return {}

        visible_actor_states = dict(player_pov.get("visible_actor_states", {}) or {})
        transition_pressure = timeline_packet.get("transition_pressure", {}) if isinstance(timeline_packet, dict) else {}
        transition_states = (
            transition_pressure.get("carrier_states", {})
            if isinstance(transition_pressure, dict)
            else {}
        )
        if isinstance(transition_states, dict):
            for actor_name, state in transition_states.items():
                if actor_name == player_name or actor_name in visible_actor_states or not isinstance(state, dict):
                    continue
                visible_actor_states[actor_name] = state
        character_map = {
            getattr(character, "name", ""): character
            for character in getattr(scenario, "characters", [])
            if getattr(character, "name", "")
        }
        relation_map = {
            str(item.get("actor", "")).strip(): item
            for item in social_packet.get("visible_relations", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        } if isinstance(social_packet, dict) else {}

        visible_pressures: List[Dict[str, Any]] = []
        for actor_name, state in visible_actor_states.items():
            if actor_name == player_name or not isinstance(state, dict):
                continue
            character = character_map.get(actor_name)
            relation = relation_map.get(actor_name, {})
            toward_viewer = relation.get("toward_viewer", {}) if isinstance(relation, dict) else {}
            if not toward_viewer:
                toward_viewer = {
                    "favor": self._extract_relation_meter(state, "favor_", player_name),
                    "malice": self._extract_relation_meter(state, "malice_", player_name),
                    "trust": self._extract_relation_meter(state, "trust_", player_name),
                }
            pressure_score = self._score_actor_pressure(state, toward_viewer)

            visible_pressures.append(
                {
                    "actor": actor_name,
                    "role": getattr(character, "role", ""),
                    "goals": list(getattr(character, "goals", [])[:3]) if character else [],
                    "dramatic_motive": state.get("dramatic_motive"),
                    "pressure_profile": state.get("pressure_profile"),
                    "public_lever": state.get("public_lever"),
                    "signature_templates": list(state.get("signature_templates", []) or []),
                    "toward_viewer": toward_viewer,
                    "pressure_score": pressure_score,
                    "transitional": actor_name in transition_states and actor_name not in (player_pov.get("visible_actor_states", {}) or {}),
                }
            )

        visible_pressures.sort(key=lambda item: item.get("pressure_score", 0), reverse=True)
        return {
            "viewer": player_name,
            "visible_pressures": visible_pressures,
            "highest_pressure_actor": visible_pressures[0]["actor"] if visible_pressures else None,
            "requires_active_push": bool(visible_pressures and visible_pressures[0].get("pressure_score", 0) >= 4),
        }

    def _build_intent_focus_packet(
        self,
        intents: List[Dict[str, Any]],
        player_name: Any,
        player_intent: Any,
        timeline_packet: Dict[str, Any],
        reaction_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        proposals = []
        for item in intents or []:
            if not isinstance(item, dict) or not item.get("actor"):
                continue
            proposals.append(
                {
                    "actor": item.get("actor"),
                    "intent": item.get("intent", ""),
                    "location": item.get("location"),
                    "role": item.get("proposal_role") or "character_proposal",
                    "priority": float(item.get("proposal_priority", 0.5) or 0.0),
                    "source": item.get("source", ""),
                    "must_reference": bool(item.get("source") in {"manual", "timeline", "injected"}),
                }
            )
        proposals.sort(key=lambda item: item.get("priority", 0.0), reverse=True)
        anchor_intent = {}
        player_proposal = {}
        if isinstance(player_intent, dict) and player_intent.get("intent"):
            player_proposal = {
                "actor": player_name,
                "intent": player_intent.get("intent", ""),
                "priority": float(player_intent.get("proposal_priority", 0.0) or 0.0),
                "role": player_intent.get("proposal_role", "character_proposal"),
                "source": player_intent.get("source", ""),
            }
        if player_proposal and player_proposal.get("source") == "manual":
            anchor_intent = {
                "actor": player_name,
                "intent": player_proposal.get("intent", ""),
                "priority": float(player_proposal.get("priority", 1.0) or 1.0),
                "role": player_proposal.get("role", "player_override"),
            }
        return {
            "anchor_actor": player_name if anchor_intent else None,
            "anchor_intent": anchor_intent,
            "player_proposal": player_proposal,
            "player_override_active": bool(anchor_intent),
            "player_proposal_is_primary": bool(anchor_intent),
            "proposals": proposals[:8],
            "due_commitment_ids": [
                str(item.get("commitment_id"))
                for item in timeline_packet.get("due_commitments", [])
                if isinstance(item, dict) and str(item.get("commitment_id", "")).strip()
            ],
            "requires_same_scene_reaction": bool(reaction_context.get("requires_reaction")),
        }

    def _extract_relation_meter(
        self,
        actor_state: Dict[str, Any],
        prefix: str,
        target_name: Any,
    ) -> Any:
        if not isinstance(actor_state, dict) or not target_name:
            return None
        direct_key = f"{prefix}{target_name}"
        if direct_key in actor_state:
            return actor_state.get(direct_key)
        return None

    def _score_actor_pressure(
        self,
        actor_state: Dict[str, Any],
        toward_viewer: Dict[str, Any],
    ) -> int:
        if not isinstance(actor_state, dict):
            return 0

        malice = toward_viewer.get("malice") if isinstance(toward_viewer.get("malice"), (int, float)) else 0
        trust = toward_viewer.get("trust") if isinstance(toward_viewer.get("trust"), (int, float)) else 0
        pressure_score = int(actor_state.get("dramatic_push", 0) or 0)
        if actor_state.get("bias"):
            pressure_score += 2
        if actor_state.get("framing_style"):
            pressure_score += 2
        if actor_state.get("territorial"):
            pressure_score += 1
        if actor_state.get("side_with"):
            pressure_score += 1
        if malice > 0:
            pressure_score += int(malice)
        if trust < 0:
            pressure_score += abs(int(trust))
        return pressure_score

    def _refresh_situations(
        self,
        scene_state: Any,
        plot_state: Any,
        situation_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        timeline_packet: Dict[str, Any],
        current_step: int,
    ) -> Dict[str, Any]:
        if not scene_state or not player_name:
            return {}

        situations: List[Dict[str, Any]] = []
        frontstage = self._build_frontstage_situation(scene_state, player_name, player_pov, timeline_packet)
        if frontstage:
            situations.append(frontstage)

        situations.extend(
            self._build_commitment_situations(
                scene_state=scene_state,
                player_name=player_name,
                timeline_packet=timeline_packet,
                current_step=current_step,
            )
        )

        transition_situation = self._build_transition_situation(player_name, timeline_packet)
        if transition_situation:
            situations.append(transition_situation)

        aftermath_situation = self._build_aftermath_situation(player_name, timeline_packet)
        if aftermath_situation:
            situations.append(aftermath_situation)

        situations.extend(self._build_plot_situations(plot_state, current_step))

        deduped: Dict[str, Dict[str, Any]] = {}
        for item in situations:
            if not isinstance(item, dict):
                continue
            situation_id = str(item.get("situation_id", "")).strip()
            if not situation_id:
                continue
            deduped[situation_id] = item

        ranked = sorted(deduped.values(), key=self._situation_sort_key, reverse=True)
        focus_situation_id = ranked[0]["situation_id"] if ranked else None

        if situation_state and hasattr(situation_state, "replace_active") and hasattr(situation_state, "build_packet"):
            situation_state.replace_active(ranked, focus_situation_id=focus_situation_id, current_step=current_step)
            return situation_state.build_packet()

        return {
            "focus_situation": deepcopy(ranked[0]) if ranked else {},
            "active_situations": deepcopy(ranked[:8]),
            "player_visible_situations": [
                deepcopy(item)
                for item in ranked
                if str(item.get("visibility", "")).strip() in {"player_visible", "rumor"}
            ][:8],
            "background_situations": [
                deepcopy(item)
                for item in ranked
                if str(item.get("visibility", "")).strip() not in {"player_visible", "rumor"}
            ][:8],
            "resolved_situations": [],
        }

    def _build_frontstage_situation(
        self,
        scene_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        location = player_pov.get("location")
        if not location:
            return {}

        location_state = scene_state.get_object_state(location)
        day_phase = str(timeline_packet.get("day_phase", "")).strip()
        visible_actor_states = player_pov.get("visible_actor_states", {}) or {}
        participants = [
            str(actor).strip()
            for actor in player_pov.get("visible_actors", [])
            if str(actor).strip()
        ]
        tags = self._collect_situation_tags(
            "frontstage",
            day_phase,
            str(location_state.get("kind", "")).strip(),
            "player_visible",
        )
        if len(participants) > 1:
            tags.extend(["social", "public"])
        if any(
            isinstance(state, dict)
            and (
                state.get("bias")
                or state.get("framing_style")
                or state.get("territorial")
                or state.get("side_with")
            )
            for actor, state in visible_actor_states.items()
            if actor != player_name
        ):
            tags.extend(["pressure", "bias"])
        if any(
            isinstance(state, dict) and state.get("pressure_profile") == "white_lotus"
            for actor, state in visible_actor_states.items()
            if actor != player_name
        ):
            tags.append("white_lotus")

        return {
            "situation_id": f"frontstage:{location}",
            "kind": "frontstage",
            "status": "active",
            "visibility": "player_visible",
            "location": location,
            "time_window": {
                "phase": day_phase,
                "start_step": int(timeline_packet.get("phase_turn", 0) or 0),
            },
            "participants": participants,
            "cause": f"玩家当前正在{location}面对眼前局面。",
            "stakes": [],
            "tags": self._dedupe_texts(tags),
            "source": {"type": "player_pov", "id": location},
            "focus_score": 120 + max(0, len(participants) - 1) * 8,
        }

    def _build_commitment_situations(
        self,
        scene_state: Any,
        player_name: Any,
        timeline_packet: Dict[str, Any],
        current_step: int,
    ) -> List[Dict[str, Any]]:
        situations: List[Dict[str, Any]] = []
        player_location = scene_state.get_actor_location(player_name) if scene_state else None
        all_commitments = []
        all_commitments.extend(timeline_packet.get("due_commitments", []))
        all_commitments.extend(timeline_packet.get("upcoming_commitments", []))

        seen = set()
        for item in all_commitments:
            if not isinstance(item, dict):
                continue
            commitment_id = str(item.get("commitment_id", "")).strip()
            if not commitment_id or commitment_id in seen:
                continue
            seen.add(commitment_id)
            location = item.get("location")
            location_state = scene_state.get_object_state(location) if location else {}
            phase = str(item.get("phase", "")).strip()
            status = str(item.get("status", "")).strip() or (
                "active" if commitment_id in {
                    str(entry.get("commitment_id", "")).strip()
                    for entry in timeline_packet.get("due_commitments", [])
                    if isinstance(entry, dict)
                } else "scheduled"
            )
            participants = [
                str(actor_update.get("actor", "")).strip()
                for actor_update in item.get("stage_actors", [])
                if isinstance(actor_update, dict) and str(actor_update.get("actor", "")).strip()
            ]
            if bool(item.get("player_relevant", False)) and player_name not in participants:
                participants.append(str(player_name))
            visibility = "hidden"
            if location and player_location == location:
                visibility = "player_visible"
            elif bool(item.get("player_relevant", False)):
                visibility = "rumor"
            tags = self._collect_situation_tags(
                "commitment",
                phase,
                str(location_state.get("kind", "")).strip(),
                visibility,
            )
            if bool(item.get("player_relevant", False)):
                tags.append("player_relevant")
            if status in {"due", "active"}:
                tags.append("due")
            situations.append(
                {
                    "situation_id": f"commitment:{commitment_id}",
                    "kind": "commitment",
                    "status": "active" if status in {"due", "active"} else "scheduled",
                    "visibility": visibility,
                    "location": location,
                    "time_window": {
                        "phase": phase,
                        "due_step": int(item.get("due_step", current_step) or current_step),
                    },
                    "participants": participants,
                    "cause": str(item.get("summary", "")).strip() or str(item.get("title", "")).strip(),
                    "stakes": [],
                    "tags": self._dedupe_texts(tags),
                    "source": {"type": "commitment", "id": commitment_id},
                    "focus_score": 95 if bool(item.get("player_relevant", False)) else 58,
                }
            )
        return situations

    def _build_transition_situation(
        self,
        player_name: Any,
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        transition_pressure = timeline_packet.get("transition_pressure", {}) if isinstance(timeline_packet, dict) else {}
        if not isinstance(transition_pressure, dict) or not transition_pressure.get("active"):
            return {}

        participants = [str(player_name)] + [
            str(actor).strip()
            for actor in transition_pressure.get("carrier_actors", [])
            if str(actor).strip()
        ]
        return {
            "situation_id": f"transition:{transition_pressure.get('commitment_id', 'unknown')}",
            "kind": "transition",
            "status": "active",
            "visibility": "player_visible",
            "location": transition_pressure.get("player_location"),
            "time_window": {"phase": str(transition_pressure.get("phase", "")).strip()},
            "participants": self._dedupe_texts(participants),
            "cause": str(transition_pressure.get("title", "")).strip() or str(transition_pressure.get("note", "")).strip(),
            "stakes": ["reputation"],
            "tags": self._dedupe_texts(
                [
                    "transition",
                    "absence",
                    "backlash",
                    str(transition_pressure.get("phase", "")).strip(),
                ]
            ),
            "source": {"type": "transition", "id": transition_pressure.get("commitment_id")},
            "focus_score": 165,
        }

    def _build_aftermath_situation(
        self,
        player_name: Any,
        timeline_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        last_missed = timeline_packet.get("last_missed_commitment") if isinstance(timeline_packet, dict) else None
        if not isinstance(last_missed, dict) or not last_missed.get("commitment_id"):
            return {}

        return {
            "situation_id": f"aftermath:{last_missed.get('commitment_id')}",
            "kind": "aftermath",
            "status": "active",
            "visibility": "rumor",
            "location": last_missed.get("location"),
            "time_window": {"phase": str(last_missed.get("phase", "")).strip()},
            "participants": [str(player_name)],
            "cause": str(last_missed.get("note", "")).strip(),
            "stakes": ["reputation"],
            "tags": self._dedupe_texts(
                [
                    "aftermath",
                    "absence",
                    str(last_missed.get("phase", "")).strip(),
                ]
            ),
            "source": {"type": "missed_commitment", "id": last_missed.get("commitment_id")},
            "focus_score": 82,
        }

    def _build_plot_situations(self, plot_state: Any, current_step: int) -> List[Dict[str, Any]]:
        if not plot_state or not hasattr(plot_state, "get_pressure_packets"):
            return []

        situations: List[Dict[str, Any]] = []
        for item in plot_state.get_pressure_packets():
            if not isinstance(item, dict):
                continue
            plot_id = str(item.get("plot_id", "")).strip()
            clock = int(item.get("clock", 0) or 0)
            if not plot_id or clock <= 0:
                continue
            tags = ["plot_pressure"] + [
                str(tag).strip()
                for tag in item.get("tags", [])
                if str(tag).strip()
            ]
            situations.append(
                {
                    "situation_id": f"plot:{plot_id}",
                    "kind": "plot_pressure",
                    "status": "active",
                    "visibility": "hidden",
                    "location": None,
                    "time_window": {"step": current_step, "stage": str(item.get("stage", "")).strip()},
                    "participants": [],
                    "cause": str(item.get("summary", "")).strip(),
                    "stakes": [],
                    "tags": self._dedupe_texts(tags),
                    "source": {"type": "plot", "id": plot_id},
                    "focus_score": 46 + min(clock, 4) * 4,
                }
            )
        return situations

    def _collect_situation_tags(
        self,
        kind: str,
        phase: str,
        location_kind: str,
        visibility: str,
    ) -> List[str]:
        tags = [kind, visibility]
        if phase:
            tags.append(phase)
        if location_kind:
            tags.append(location_kind)
            if location_kind == "library":
                tags.append("library")
            if location_kind == "warehouse":
                tags.append("harbor")
            if location_kind == "study":
                tags.append("study")
            if location_kind == "dining_room":
                tags.append("dinner")
        return self._dedupe_texts(tags)

    def _situation_sort_key(self, item: Dict[str, Any]) -> Any:
        status = str(item.get("status", "")).strip()
        visibility = str(item.get("visibility", "")).strip()
        status_rank = {"active": 3, "scheduled": 2, "cooling": 1, "resolved": 0}.get(status, 0)
        visibility_rank = {"player_visible": 3, "rumor": 2, "hidden": 1}.get(visibility, 0)
        return (
            int(item.get("focus_score", 0) or 0),
            status_rank,
            visibility_rank,
            len(item.get("participants", []) or []),
            str(item.get("situation_id", "")),
        )

    def _dedupe_texts(self, items: List[Any]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for item in items or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered

    def _build_storylet_packet(
        self,
        scene_state: Any,
        active_storylets: List[Dict[str, Any]],
        current_step: int,
        situation_packet: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        if not scene_state or not active_storylets:
            return {}

        priority_storylets = [item for item in active_storylets[:5] if isinstance(item, dict)]
        priority_tags: List[str] = []
        priority_beats: List[Dict[str, Any]] = []
        preferred_template_ids: List[str] = []
        for item in priority_storylets:
            for tag in item.get("tags", []):
                text = str(tag).strip()
                if text and text not in priority_tags:
                    priority_tags.append(text)
            beat = item.get("beat", {})
            if isinstance(beat, dict) and beat:
                priority_beats.append(beat)
                for template_id in beat.get("preferred_template_ids", []):
                    template_text = str(template_id).strip()
                    if template_text and template_text not in preferred_template_ids:
                        preferred_template_ids.append(template_text)

        day_phase = str(scene_state.get_scene_flag("day_phase", ""))
        opening_window = day_phase in {"arrival", "pre_dinner", "dinner"} and int(current_step) <= 2
        focus_situation = situation_packet.get("focus_situation", {}) if isinstance(situation_packet, dict) else {}
        focus_tags = {
            str(item).strip()
            for item in focus_situation.get("tags", [])
            if str(item).strip()
        }
        recent_template_ids = {
            str(item).strip()
            for item in scene_state.get_scene_flag("recent_conflict_template_ids", [])
            if str(item).strip()
        }
        high_pressure_focus = bool(
            focus_tags.intersection({"dinner", "trap", "white_lotus", "absence", "player_visible", "public", "bias"})
            or str(focus_situation.get("kind", "")).strip() in {"transition", "aftermath"}
        )
        forced_storylet = self._pick_forced_storylet(
            priority_storylets=priority_storylets,
            recent_template_ids=recent_template_ids,
            focus_tags=focus_tags,
        )
        require_hit = bool(
            forced_storylet
            and int(forced_storylet.get("priority", 0) or 0) >= 88
            and (opening_window or high_pressure_focus)
        )
        return {
            "priority_storylets": priority_storylets,
            "priority_storylet_ids": [str(item.get("storylet_id", "")).strip() for item in priority_storylets if str(item.get("storylet_id", "")).strip()],
            "priority_tags": priority_tags,
            "priority_beats": priority_beats,
            "preferred_template_ids": preferred_template_ids,
            "focus_situation_id": str(focus_situation.get("situation_id", "")).strip(),
            "focus_situation_kind": str(focus_situation.get("kind", "")).strip(),
            "focus_situation_tags": list(focus_situation.get("tags", []) or []),
            "forced_storylet_id": str(forced_storylet.get("storylet_id", "")).strip() if forced_storylet else "",
            "require_hit": require_hit,
        }

    def _pick_forced_storylet(
        self,
        priority_storylets: List[Dict[str, Any]],
        recent_template_ids: set[str],
        focus_tags: set[str],
    ) -> Dict[str, Any]:
        if not priority_storylets:
            return {}

        def score(item: Dict[str, Any]) -> int:
            total = int(item.get("priority", 0) or 0) * 10
            beat = item.get("beat", {}) if isinstance(item.get("beat", {}), dict) else {}
            template_ids = {
                str(entry).strip()
                for entry in beat.get("preferred_template_ids", [])
                if str(entry).strip()
            }
            storylet_tags = {
                str(entry).strip()
                for entry in item.get("tags", [])
                if str(entry).strip()
            }
            if template_ids and not template_ids.issubset(recent_template_ids):
                total += 120
            if focus_tags and storylet_tags.intersection(focus_tags):
                total += 70
            if storylet_tags.intersection({"trap", "object", "dinner", "white_lotus", "absence"}):
                total += 25
            return total

        ranked = sorted(priority_storylets, key=score, reverse=True)
        if ranked and score(ranked[0]) > 0:
            return ranked[0]
        return priority_storylets[0]

    def _assess_intent_legality(
        self,
        scene_state: Any,
        physics_profile: str,
        intent_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        actor = str(intent_item.get("actor", "Unknown"))
        intent = str(intent_item.get("intent", "")).strip()
        source = str(intent_item.get("source", ""))
        current_location = scene_state.get_actor_location(actor) if scene_state else None
        actor_state = scene_state.get_actor_state(actor) if scene_state else {}

        verdict = "allow"
        reason = ""
        suggested_intent = ""
        rewrite_location = None
        rule = "none"

        if source in {"timeline", "injected"} or actor == "World" or not intent:
            return {
                "actor": actor,
                "intent": intent,
                "verdict": verdict,
                "reason": reason,
                "suggested_intent": suggested_intent,
                "rewrite_location": rewrite_location,
                "rule": rule,
            }

        if physics_profile == "mundane":
            impossible = self._detect_mundane_violation(intent, actor_state)
            if impossible:
                verdict = "block"
                reason = impossible
                rule = "mundane_physics"

        if verdict == "allow":
            movement_check = self._assess_movement_legality(scene_state, actor, intent, current_location)
            if movement_check:
                verdict = movement_check.get("verdict", "allow")
                reason = movement_check.get("reason", "")
                suggested_intent = movement_check.get("suggested_intent", "")
                rewrite_location = movement_check.get("rewrite_location")
                rule = movement_check.get("rule", "movement")

        return {
            "actor": actor,
            "intent": intent,
            "verdict": verdict,
            "reason": reason,
            "suggested_intent": suggested_intent,
            "rewrite_location": rewrite_location,
            "rule": rule,
        }

    def _select_conflict_templates(
        self,
        templates: List[Any],
        day_phase: str,
        current_step: int,
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for template in templates or []:
            phases = list(getattr(template, "phases", []) or [])
            min_step = int(getattr(template, "min_step", 0))
            if phases and day_phase and day_phase not in phases:
                continue
            if current_step < min_step:
                continue
            selected.append(
                {
                    "template_id": getattr(template, "template_id", ""),
                    "instruction": getattr(template, "instruction", ""),
                    "fallback_result": getattr(template, "fallback_result", ""),
                    "fallback_results": list(getattr(template, "fallback_results", []) or []),
                    "preferred_actors": list(getattr(template, "preferred_actors", []) or []),
                    "tags": list(getattr(template, "tags", []) or []),
                }
            )
        return selected

    def _record_conflict_result(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if not scene_state:
            return

        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        flags = scene_state.scene_flags or {}
        conflict_level = str(result.get("conflict_level", "none"))
        visible_conflict = self._is_visible_conflict(result, conflict_level)
        quiet_turns = int(flags.get("quiet_turns_since_conflict", 0))
        conflict_count = int(flags.get("visible_conflict_count", 0))

        if visible_conflict:
            applied_template_ids = [
                str(item)
                for item in result.get("applied_conflict_templates", [])
                if str(item).strip()
            ]
            conflict_packet = context.get("conflict", {}) if isinstance(context.get("conflict", {}), dict) else {}
            repetition_window = max(1, int(conflict_packet.get("repetition_window", 2)))
            recent_template_ids = [
                str(item)
                for item in flags.get("recent_conflict_template_ids", [])
                if str(item).strip()
            ]
            recent_template_ids.extend(applied_template_ids)
            recent_template_ids = recent_template_ids[-repetition_window:]
            scene_state.update_scene_flags(
                {
                    "last_visible_conflict_step": current_step,
                    "quiet_turns_since_conflict": 0,
                    "visible_conflict_count": conflict_count + 1,
                    "recent_conflict_template_ids": recent_template_ids,
                }
            )
            return

        scene_state.update_scene_flags(
            {
                "quiet_turns_since_conflict": quiet_turns + 1,
            }
        )

    def _apply_relation_drift(
        self,
        scene_state: Any,
        relationship_state: Any,
        result: Dict[str, Any],
        player_name: Any,
    ) -> None:
        if not scene_state or not player_name:
            return

        deltas: Dict[str, Dict[str, int]] = {}
        for item in result.get("resolved_actions", []):
            if not isinstance(item, dict):
                continue
            actor = str(item.get("actor", "")).strip()
            if not actor or actor == player_name or actor == "World":
                continue
            if actor not in scene_state.actor_states:
                continue
            if item.get("visibility") != "public":
                continue
            if item.get("outcome") not in {"complication", "blocked"}:
                continue

            actor_state = scene_state.get_actor_state(actor)
            if not isinstance(actor_state, dict):
                continue

            favor_key = f"favor_{player_name}"
            malice_key = f"malice_{player_name}"
            relation_metrics = relationship_state.get_metrics(actor, player_name) if relationship_state else {}
            current_favor = int(relation_metrics.get("favor", actor_state.get(favor_key, 0)) or 0)
            current_malice = int(relation_metrics.get("malice", actor_state.get(malice_key, 0)) or 0)
            next_favor = max(0, current_favor - 1) if current_favor > 0 else current_favor
            next_malice = min(5, current_malice + 1)
            if next_favor == current_favor and next_malice == current_malice:
                continue

            if relationship_state:
                relationship_state.apply_delta(
                    actor,
                    player_name,
                    favor_delta=next_favor - current_favor,
                    malice_delta=next_malice - current_malice,
                )
            scene_state.update_actor_state(
                actor,
                {
                    favor_key: next_favor,
                    malice_key: next_malice,
                },
            )
            deltas[actor] = {
                "favor_delta": next_favor - current_favor,
                "malice_delta": next_malice - current_malice,
            }

        if deltas:
            scene_state.update_scene_flags({"last_relation_deltas": deltas})

    def _is_visible_conflict(self, result: Dict[str, Any], conflict_level: str) -> bool:
        if conflict_level in {"medium", "high"}:
            return True
        flags = result.get("conflict_flags", [])
        if isinstance(flags, list) and flags:
            return True
        for item in result.get("resolved_actions", []):
            if not isinstance(item, dict):
                continue
            if item.get("visibility") != "public":
                continue
            if item.get("outcome") in {"blocked", "complication"}:
                return True
        return False

    def _detect_mundane_violation(self, intent: str, actor_state: Dict[str, Any]) -> str:
        capabilities = actor_state.get("capabilities", []) if isinstance(actor_state, dict) else []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        capabilities = {str(item) for item in capabilities}
        text = intent or ""

        impossible_patterns = [
            (("飞起来", "悬浮", "漂浮在空中", "腾空而起"), "普通人在这个世界里不能突然飞起来。"),
            (("瞬移", "传送到", "闪现到"), "这个世界里没有瞬间移动这种能力。"),
            (("穿墙", "穿过墙"), "普通人不能直接穿墙。"),
            (("隐身", "突然消失"), "普通人不能无痕隐身或凭空消失。"),
            (("凭空变出", "召唤出", "变出一把", "变出一只"), "普通人不能凭空变出物件。"),
            (("放出火球", "打出雷电", "施法", "念咒"), "当前世界不是可随意施法的物理规则。"),
        ]

        for keywords, reason in impossible_patterns:
            if any(keyword in text for keyword in keywords):
                if "supernatural" in capabilities or "magic" in capabilities or "flight" in capabilities:
                    return ""
                return reason
        return ""

    def _assess_movement_legality(
        self,
        scene_state: Any,
        actor: str,
        intent: str,
        current_location: Any,
    ) -> Any:
        if not scene_state or not intent or not current_location:
            return None

        target_location = self._extract_target_location(scene_state, intent, current_location)
        if not target_location or target_location == current_location:
            return None

        connected = {
            str(name)
            for name in scene_state.get_object_state(current_location).get("connected_to", [])
        }
        if target_location in connected:
            return {
                "verdict": "allow",
                "reason": "",
                "suggested_intent": "",
                "rewrite_location": target_location,
                "rule": "movement",
            }

        path = self._find_path(scene_state, current_location, target_location)
        if path and len(path) >= 2:
            next_hop = path[1]
            return {
                "verdict": "rewrite",
                "reason": f"{actor}不能一步直接到达{target_location}，需要按空间连通性移动。",
                "suggested_intent": f"先前往{next_hop}",
                "rewrite_location": next_hop,
                "rule": "movement_path",
            }

        return {
            "verdict": "block",
            "reason": f"{target_location} 目前不是可直接到达的位置。",
            "suggested_intent": "",
            "rewrite_location": None,
            "rule": "movement_blocked",
        }

    def _extract_target_location(self, scene_state: Any, intent: str, current_location: Any) -> Any:
        known_locations = list(scene_state.world_objects.keys())
        connected_locations = scene_state.get_object_state(current_location).get("connected_to", [])
        return extract_move_target_from_intent(
            intent=intent,
            current_location=str(current_location) if current_location else None,
            connected_locations=connected_locations,
            known_locations=known_locations,
        )

    def _find_path(self, scene_state: Any, start: str, target: str) -> List[str]:
        if start == target:
            return [start]
        queue: List[List[str]] = [[start]]
        visited = {start}
        while queue:
            path = queue.pop(0)
            node = path[-1]
            neighbors = scene_state.get_object_state(node).get("connected_to", [])
            for neighbor_raw in neighbors:
                neighbor = str(neighbor_raw)
                if neighbor in visited:
                    continue
                next_path = path + [neighbor]
                if neighbor == target:
                    return next_path
                visited.add(neighbor)
                queue.append(next_path)
        return []

    def _refresh_timeline(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        player_name: Any = None,
    ) -> Dict[str, Any]:
        if not scene_state:
            return {}

        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        scene_flags = scene_state.scene_flags or {}
        phase_schedule = self._normalize_phase_schedule(scene_flags.get("phase_schedule", []))
        commitments = self._normalize_commitments(scene_flags.get("upcoming_commitments", []))
        day_phase = self._resolve_day_phase(current_step, phase_schedule, scene_flags.get("day_phase"))
        phase_turn = self._resolve_phase_turn(current_step, phase_schedule, day_phase)
        transition_pressure = self._build_transition_pressure(
            scene_state=scene_state,
            commitments=commitments,
            current_step=current_step,
            player_name=player_name,
        )
        self._apply_commitment_staging(scene_state, commitments, current_step)

        due_commitments: List[Dict[str, Any]] = []
        upcoming_commitments: List[Dict[str, Any]] = []
        for item in commitments:
            due_step = int(item.get("due_step", 0))
            grace_steps = int(item.get("grace_steps", 0))
            status = item.get("status", "scheduled")
            if status in {"resolved", "missed", "cancelled"}:
                continue
            if current_step < due_step:
                upcoming_commitments.append(item)
                continue
            if current_step <= due_step + grace_steps:
                item["status"] = "due"
                due_commitments.append(item)

        scene_state.update_scene_flags(
            {
                "day_phase": day_phase,
                "phase_turn": phase_turn,
                "phase_schedule": phase_schedule,
                "upcoming_commitments": commitments,
            }
        )
        return {
            "day_phase": day_phase,
            "phase_turn": phase_turn,
            "due_commitments": due_commitments,
            "upcoming_commitments": upcoming_commitments,
            "last_missed_commitment": deepcopy(scene_flags.get("last_missed_commitment")),
            "transition_pressure": transition_pressure,
        }

    def _finalize_timeline(self, scene_state: Any, context: Dict[str, Any], player_name: Any) -> Dict[str, Any]:
        if not scene_state:
            return {}

        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        commitments = self._normalize_commitments(scene_state.get_scene_flag("upcoming_commitments", []))
        player_location = scene_state.get_actor_location(player_name) if player_name else None
        last_missed_commitment = None

        for item in commitments:
            due_step = int(item.get("due_step", 0))
            grace_steps = int(item.get("grace_steps", 0))
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if current_step < due_step:
                item["status"] = "scheduled"
                continue

            required_location = item.get("location")
            player_relevant = bool(item.get("player_relevant", False))
            player_present = not required_location or player_location == required_location

            if player_relevant and not player_present and current_step >= due_step:
                item["status"] = "missed"
                item["resolution_note"] = item.get("absent_consequence", item.get("summary", ""))
                last_missed_commitment = {
                    "commitment_id": item.get("commitment_id"),
                    "title": item.get("title", ""),
                    "phase": item.get("phase", ""),
                    "location": required_location,
                    "note": item.get("resolution_note", ""),
                }
                continue

            if current_step <= due_step + grace_steps:
                item["status"] = "resolved"
                item["resolution_note"] = item.get("present_consequence", item.get("summary", ""))
                continue

            if current_step > due_step + grace_steps:
                item["status"] = "missed"
                item["resolution_note"] = item.get("absent_consequence", item.get("summary", ""))
                last_missed_commitment = {
                    "commitment_id": item.get("commitment_id"),
                    "title": item.get("title", ""),
                    "phase": item.get("phase", ""),
                    "location": required_location,
                    "note": item.get("resolution_note", ""),
                }

        scene_state.update_scene_flags(
            {
                "upcoming_commitments": commitments,
                "last_missed_commitment": last_missed_commitment,
            }
        )
        return {
            "day_phase": scene_state.get_scene_flag("day_phase"),
            "phase_turn": scene_state.get_scene_flag("phase_turn", 0),
            "due_commitments": [
                deepcopy(item)
                for item in commitments
                if item.get("status") == "due"
            ],
            "upcoming_commitments": [
                deepcopy(item)
                for item in commitments
                if item.get("status") == "scheduled"
            ],
            "last_missed_commitment": deepcopy(last_missed_commitment),
        }

    def _apply_commitment_staging(
        self,
        scene_state: Any,
        commitments: List[Dict[str, Any]],
        current_step: int,
    ) -> None:
        if not scene_state:
            return

        for item in commitments:
            if not isinstance(item, dict):
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if bool(item.get("stage_applied")):
                continue
            if current_step < int(item.get("due_step", 0)):
                continue

            staged_any = False
            for actor_update in item.get("stage_actors", []):
                if not isinstance(actor_update, dict) or not actor_update.get("actor"):
                    continue
                actor_name = str(actor_update.get("actor"))
                payload = {
                    key: value
                    for key, value in actor_update.items()
                    if key != "actor"
                }
                if payload:
                    scene_state.update_actor_state(actor_name, payload)
                    staged_any = True
            if staged_any:
                item["stage_applied"] = True

    def _build_transition_pressure(
        self,
        scene_state: Any,
        commitments: List[Dict[str, Any]],
        current_step: int,
        player_name: Any,
    ) -> Dict[str, Any]:
        if not scene_state or not player_name:
            return {}

        player_location = scene_state.get_actor_location(player_name)
        if not player_location:
            return {}

        same_scene_states = scene_state.get_actors_in_location(player_location)
        if not same_scene_states:
            return {}

        for item in commitments:
            if not isinstance(item, dict):
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if bool(item.get("stage_applied")):
                continue
            if not bool(item.get("player_relevant", False)):
                continue
            if current_step < int(item.get("due_step", 0)):
                continue

            target_location = item.get("location")
            if target_location and player_location == target_location:
                continue

            carrier_actors = self._resolve_transition_carriers(
                commitment=item,
                same_scene_states=same_scene_states,
                player_name=player_name,
            )
            if not carrier_actors:
                continue

            carrier_states = {
                actor_name: deepcopy(scene_state.get_actor_state(actor_name))
                for actor_name in carrier_actors
                if isinstance(scene_state.get_actor_state(actor_name), dict)
            }
            if not carrier_states:
                continue

            return {
                "active": True,
                "commitment_id": item.get("commitment_id"),
                "title": item.get("title", ""),
                "phase": item.get("phase", ""),
                "player_location": player_location,
                "target_location": target_location,
                "note": item.get("absent_consequence", item.get("summary", "")),
                "carrier_actors": carrier_actors,
                "carrier_states": carrier_states,
                "requires_human_backlash": True,
            }

        return {}

    def _resolve_transition_carriers(
        self,
        commitment: Dict[str, Any],
        same_scene_states: Dict[str, Dict[str, Any]],
        player_name: Any,
    ) -> List[str]:
        if not isinstance(commitment, dict) or not isinstance(same_scene_states, dict):
            return []

        stage_actor_names = [
            str(item.get("actor", "")).strip()
            for item in commitment.get("stage_actors", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        ]
        candidates: List[Dict[str, Any]] = []
        for actor_name, state in same_scene_states.items():
            if actor_name == player_name or not isinstance(state, dict):
                continue
            toward_viewer = {
                "favor": self._extract_relation_meter(state, "favor_", player_name),
                "malice": self._extract_relation_meter(state, "malice_", player_name),
                "trust": self._extract_relation_meter(state, "trust_", player_name),
            }
            score = self._score_actor_pressure(state, toward_viewer)
            stage_bonus = 4 if actor_name in stage_actor_names else 0
            if score <= 0 and stage_bonus <= 0:
                continue
            candidates.append(
                {
                    "actor": actor_name,
                    "score": score + stage_bonus,
                    "stage_index": stage_actor_names.index(actor_name) if actor_name in stage_actor_names else 99,
                }
            )

        candidates.sort(
            key=lambda item: (
                item.get("stage_index", 99),
                -(int(item.get("score", 0) or 0)),
                str(item.get("actor", "")),
            )
        )
        return [
            str(item.get("actor", "")).strip()
            for item in candidates[:4]
            if str(item.get("actor", "")).strip()
        ]

    def _resolve_storylets(
        self,
        scene_state: Any,
        plot_state: Any,
        scenario: Any,
        situation_packet: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        if not scene_state or not scenario:
            return []

        consumed = set(scene_state.scene_flags.get("consumed_storylets", []))
        active: List[Dict[str, Any]] = []
        for storylet in scenario.storylets:
            if storylet.one_shot and storylet.storylet_id in consumed:
                continue
            situation_matches = self._match_storylet_to_situations(storylet, situation_packet or {})
            if self._storylet_requires_situation_route(storylet) and not situation_matches:
                continue
            if all(scene_state.matches_condition(cond, plot_state=plot_state) for cond in storylet.conditions):
                focus_situation_id = str(
                    (situation_packet or {}).get("focus_situation", {}).get("situation_id", "")
                ).strip()
                matched_situation_ids = [
                    str(item.get("situation_id", "")).strip()
                    for item in situation_matches
                    if str(item.get("situation_id", "")).strip()
                ]
                active.append(
                    {
                        "storylet_id": storylet.storylet_id,
                        "intent": storylet.intent,
                        "priority": storylet.priority,
                        "tags": list(storylet.tags),
                        "situation_kinds": list(getattr(storylet, "situation_kinds", []) or []),
                        "situation_tags": list(getattr(storylet, "situation_tags", []) or []),
                        "matched_situation_ids": matched_situation_ids,
                        "focus_situation_match": bool(
                            focus_situation_id
                            and focus_situation_id in matched_situation_ids
                        ),
                        "situation_score": max(
                            [int(item.get("focus_score", 0) or 0) for item in situation_matches] or [0]
                        ),
                        "beat": (
                            storylet.beat.model_dump()
                            if getattr(storylet, "beat", None) and hasattr(storylet.beat, "model_dump")
                            else storylet.beat.dict()
                            if getattr(storylet, "beat", None)
                            else {}
                        ),
                    }
                )
        active.sort(
            key=lambda item: (
                int(item.get("priority", 0) or 0),
                int(item.get("focus_situation_match", False)),
                int(item.get("situation_score", 0) or 0),
                str(item.get("storylet_id", "")),
            ),
            reverse=True,
        )
        return active

    def _storylet_requires_situation_route(self, storylet: Any) -> bool:
        return bool(
            list(getattr(storylet, "situation_kinds", []) or [])
            or list(getattr(storylet, "situation_tags", []) or [])
        )

    def _match_storylet_to_situations(
        self,
        storylet: Any,
        situation_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not isinstance(situation_packet, dict):
            return []

        active_situations = [
            item for item in situation_packet.get("active_situations", [])
            if isinstance(item, dict) and str(item.get("situation_id", "")).strip()
        ]
        if not active_situations:
            return []

        required_kinds = {
            str(item).strip()
            for item in getattr(storylet, "situation_kinds", []) or []
            if str(item).strip()
        }
        required_tags = {
            str(item).strip()
            for item in getattr(storylet, "situation_tags", []) or []
            if str(item).strip()
        }

        if not required_kinds and not required_tags:
            soft_tags = {
                str(item).strip()
                for item in getattr(storylet, "tags", []) or []
                if str(item).strip()
            }
            beat = getattr(storylet, "beat", None)
            stake = getattr(beat, "stake", "") if beat else ""
            if str(stake).strip():
                soft_tags.add(str(stake).strip())
            required_tags = soft_tags

        focus_situation_id = str(situation_packet.get("focus_situation", {}).get("situation_id", "")).strip()
        matches: List[Dict[str, Any]] = []
        for item in active_situations:
            kind = str(item.get("kind", "")).strip()
            tags = {
                str(tag).strip()
                for tag in item.get("tags", [])
                if str(tag).strip()
            }
            stakes = {
                str(tag).strip()
                for tag in item.get("stakes", [])
                if str(tag).strip()
            }
            if required_kinds and kind not in required_kinds:
                continue
            if required_tags and not required_tags.intersection(tags.union(stakes)):
                continue
            scored = deepcopy(item)
            if str(scored.get("situation_id", "")).strip() == focus_situation_id:
                scored["focus_score"] = int(scored.get("focus_score", 0) or 0) + 80
            matches.append(scored)

        matches.sort(key=self._situation_sort_key, reverse=True)
        return matches

    def _consume_storylets(self, scene_state: Any, scenario: Any, hits: List[str]) -> None:
        if not hits or not scene_state or not scenario:
            return
        storylet_map = {storylet.storylet_id: storylet for storylet in scenario.storylets}
        consumed = list(scene_state.scene_flags.get("consumed_storylets", []))
        consumed_set = set(consumed)
        for storylet_id in hits:
            storylet = storylet_map.get(storylet_id)
            if storylet and storylet.one_shot and storylet_id not in consumed_set:
                consumed.append(storylet_id)
                consumed_set.add(storylet_id)
        if consumed:
            scene_state.update_scene_flags({"consumed_storylets": consumed})

    def _normalize_phase_schedule(self, items: Any) -> List[Dict[str, Any]]:
        schedule: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("phase"):
                continue
            schedule.append(
                {
                    "phase": str(item.get("phase")),
                    "start_step": int(item.get("start_step", 0)),
                    "label": str(item.get("label", item.get("phase"))),
                }
            )
        schedule.sort(key=lambda entry: entry.get("start_step", 0))
        return schedule

    def _normalize_commitments(self, items: Any) -> List[Dict[str, Any]]:
        commitments: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("commitment_id"):
                continue
            normalized = deepcopy(item)
            normalized["title"] = str(normalized.get("title", normalized["commitment_id"]))
            normalized["summary"] = str(normalized.get("summary", ""))
            normalized["phase"] = str(normalized.get("phase", ""))
            normalized["due_step"] = int(normalized.get("due_step", 0))
            normalized["grace_steps"] = int(normalized.get("grace_steps", 0))
            normalized["status"] = str(normalized.get("status", "scheduled"))
            commitments.append(normalized)
        commitments.sort(key=lambda item: (item.get("due_step", 0), item.get("commitment_id", "")))
        return commitments

    def _resolve_day_phase(
        self,
        current_step: int,
        phase_schedule: List[Dict[str, Any]],
        current_phase: Any,
    ) -> str:
        resolved_phase = str(current_phase or "freeplay")
        for item in phase_schedule:
            if current_step >= item.get("start_step", 0):
                resolved_phase = item.get("phase", resolved_phase)
            else:
                break
        return resolved_phase

    def _resolve_phase_turn(
        self,
        current_step: int,
        phase_schedule: List[Dict[str, Any]],
        day_phase: str,
    ) -> int:
        phase_start = 0
        for item in phase_schedule:
            if item.get("phase") == day_phase:
                phase_start = item.get("start_step", 0)
        return max(0, current_step - phase_start)

    def _spawn_character_if_needed(self, entities: Dict[str, Entity], character: Any) -> List[str]:
        if not isinstance(character, dict):
            return []

        new_name = character.get("name", "").strip()
        if not new_name or new_name in entities:
            return []

        base_cfg = config.get_component_config("agent").copy()
        new_entity = create_agent(
            name=new_name,
            role=character.get("role", "路人"),
            personality=character.get("personality", "未知"),
            goals=character.get("goals", []),
            model_config=base_cfg,
        )
        entities[new_name] = new_entity
        print(f"    [New Character] {new_name} ({character.get('role', '路人')}) joined the story.")
        self.logger.info(f"Simulation spawned new character: {new_name}")
        return [new_name]
