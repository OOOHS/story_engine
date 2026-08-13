from typing import Any, Dict, List

from src.story_engine.components.relationship import RelationshipBit


class SocialDynamics:
    """Builds social context and applies explicit directed relation changes."""

    def build_social_packet(self, scene_state, relationship_book, viewer, pov):
        if not scene_state or not viewer:
            return {}
        visible = [name for name in pov.get("visible_actors", []) if name and name != viewer]
        states = {
            name: scene_state.get_actor_state(name)
            for name in pov.get("visible_actors", [])
            if name in scene_state.actor_states
        }
        relations = (
            relationship_book.get_visible_relations(viewer, visible, states)
            if relationship_book
            else []
        )
        return {
            "viewer": viewer,
            "visible_relations": relations,
            "allow_unsignaled_touch": False,
            "prefer_noncontact_signals": True,
            "max_unsignaled_touch_per_turn": 0,
        }

    def build_reaction_context(self, player_name, pov, player_intent, social, timeline):
        states = dict(pov.get("visible_actor_states", {}) or {})
        transition = timeline.get("transition_pressure", {}) if isinstance(timeline, dict) else {}
        transition_states = transition.get("carrier_states", {}) if isinstance(transition, dict) else {}
        for name, state in transition_states.items() if isinstance(transition_states, dict) else []:
            if name != player_name and name not in states and isinstance(state, dict):
                states[name] = state
        visible = [name for name in pov.get("visible_actors", []) if name and name != player_name]
        transition_watchers = [
            str(name).strip() for name in transition.get("carrier_actors", [])
            if str(name).strip() and str(name).strip() != str(player_name)
        ] if isinstance(transition, dict) else []
        for name in transition_watchers:
            if name not in visible:
                visible.append(name)
        relation_map = {
            str(item.get("actor", "")).strip(): item
            for item in social.get("visible_relations", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        } if isinstance(social, dict) else {}
        hostile = []
        for name in visible:
            state = states.get(name, {})
            if not isinstance(state, dict):
                continue
            relation_item = relation_map.get(name, {})
            relation_states = set(
                relation_item.get("toward_viewer_states", []) or []
            )
            if (
                relation_item.get("territorial")
                or relation_item.get("framing_style")
                or relation_item.get("bias")
                or state.get("territorial")
                or state.get("framing_style")
                or state.get("bias")
                or bool({"hostile", "wary"}.intersection(relation_states))
            ):
                hostile.append(name)
        action = player_intent.get("intent", "") if isinstance(player_intent, dict) else ""
        pressure = self.classify_action_pressure(action, bool(transition_watchers))
        return {
            "location": pov.get("location"),
            "visible_watchers": visible,
            "hostile_watchers": hostile,
            "transition_watchers": transition_watchers,
            "transition_requires_backlash": bool(transition.get("requires_human_backlash")),
            "player_action": action,
            "action_pressure": pressure,
            "requires_reaction": bool(action and visible),
        }

    def build_motive_packet(
        self,
        scene_state,
        scenario,
        viewer,
        pov,
        social,
        timeline,
        entities=None,
        relationship_book=None,
    ):
        if not scene_state or not scenario or not viewer:
            return {}
        states = {
            name: scene_state.get_actor_state(name)
            for name in pov.get("visible_actors", [])
            if name in scene_state.actor_states
        }
        transition = timeline.get("transition_pressure", {}) if isinstance(timeline, dict) else {}
        transition_states = transition.get("carrier_states", {}) if isinstance(transition, dict) else {}
        if isinstance(transition_states, dict):
            for name, state in transition_states.items():
                if name != viewer and name not in states and isinstance(state, dict):
                    states[name] = state
        characters = {
            getattr(item, "name", ""): item
            for item in getattr(scenario, "characters", [])
            if getattr(item, "name", "")
        }
        relations = {
            str(item.get("actor", "")).strip(): item
            for item in social.get("visible_relations", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        } if isinstance(social, dict) else {}
        pressures = []
        original_visible = set(pov.get("visible_actors", []) or [])
        for name, state in states.items():
            if name == viewer or not isinstance(state, dict):
                continue
            relation_item = relations.get(name, {})
            relation_states = list(
                relation_item.get("toward_viewer_states", []) or []
            )
            relation_metrics = (
                relationship_book.get_metrics(name, viewer)
                if relationship_book is not None
                else {}
            )
            character = characters.get(name)
            entity = (entities or {}).get(name)
            goal_state = entity.get_component("GoalState") if entity else None
            active_goals = (
                [record.title for record in goal_state.active_records()]
                if goal_state is not None and hasattr(goal_state, "active_records")
                else list(getattr(character, "goals", [])[:3]) if character else []
            )
            pressures.append(
                {
                    "actor": name,
                    "role": getattr(character, "role", ""),
                    "goals": active_goals[:3],
                    "dramatic_motive": state.get("dramatic_motive"),
                    "pressure_profile": state.get("pressure_profile"),
                    "public_lever": state.get("public_lever"),
                    "signature_templates": list(state.get("signature_templates", []) or []),
                    "toward_viewer_states": relation_states,
                    "pressure_score": self.score_pressure(state, relation_metrics),
                    "transitional": name in transition_states and name not in original_visible,
                }
            )
        pressures.sort(key=lambda item: item["pressure_score"], reverse=True)
        return {
            "viewer": viewer,
            "visible_pressures": pressures,
            "highest_pressure_actor": pressures[0]["actor"] if pressures else None,
            "requires_active_push": bool(pressures and pressures[0]["pressure_score"] >= 4),
        }

    def apply_relation_updates(
        self, scene_state, relationship_book, result, *, current_step: int = 0
    ):
        if not scene_state:
            return
        applied = {}
        resolved_actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        for update in result.get("relationship_updates", []) or []:
            if not isinstance(update, dict):
                continue
            source = str(update.get("source", "")).strip()
            target = str(update.get("target", "")).strip()
            if source not in scene_state.actor_states or target not in scene_state.actor_states:
                continue
            reason = str(update.get("reason", "")).strip()
            evidence = next(
                (
                    action
                    for action in resolved_actions
                    if str(action.get("actor", "")).strip() in {source, target}
                ),
                None,
            )
            if not reason or evidence is None:
                continue
            deltas = {}
            for metric in ("favor", "malice", "trust"):
                raw = update.get(f"{metric}_delta")
                if isinstance(raw, (int, float)):
                    deltas[f"{metric}_delta"] = max(-5, min(5, int(raw)))
            if not deltas:
                continue
            if relationship_book is None:
                continue
            relationship_book.apply_delta(
                source,
                target,
                current_step=current_step,
                reason=reason,
                provenance={
                    "source_kind": "resolved_action",
                    "source_ref": (
                        f"step:{int(current_step)}:actor:"
                        f"{str(evidence.get('actor', '')).strip()}"
                    ),
                },
                **deltas,
            )
            applied[f"{source}->{target}"] = {**deltas, "reason": reason}
        if applied:
            scene_state.update_scene_flags({"last_relation_deltas": applied})

    def record_interactions(
        self,
        scene_state,
        relationship_book,
        result,
        *,
        current_step: int = 0,
    ) -> None:
        """Lazily materialize a pair when two actors actually interact."""
        if not scene_state or relationship_book is None:
            return
        known_actors = set(scene_state.actor_states)
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            source = str(action.get("actor", "")).strip()
            target = str(action.get("action_target", "")).strip()
            if (
                source not in known_actors
                or target not in known_actors
                or source == target
                or str(action.get("visibility", "public")) == "hidden"
            ):
                continue
            record = relationship_book.ensure(
                source,
                target,
                created_step=int(current_step),
                provenance={
                    "source_kind": "resolved_action",
                    "source_ref": f"step:{int(current_step)}:actor:{source}",
                },
            )
            record.last_interaction_step = max(
                record.last_interaction_step, int(current_step)
            )
            if "acquainted" not in record.bits:
                record.bits["acquainted"] = RelationshipBit(
                    bit_id="acquainted",
                    roles={"participant_0": source, "participant_1": target},
                    created_step=int(current_step),
                    provenance={
                        "source_kind": "resolved_action",
                        "source_ref": f"step:{int(current_step)}:actor:{source}",
                    },
                )

    def validate_relation_updates(self, scene_state, result):
        """Return hard validation errors for authoritative relationship writes.

        ``apply_relation_updates`` intentionally remains tolerant when used as a
        standalone helper.  The world transaction calls this stricter boundary
        before staging so a malformed social write cannot partially coexist
        with otherwise committed world changes.
        """
        updates = result.get("relationship_updates", [])
        if not isinstance(updates, list):
            return ["relationship_updates must be a list"]
        if not scene_state:
            return ["relationship_updates require scene state"] if updates else []

        resolved_actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        errors = []
        for index, update in enumerate(updates):
            prefix = f"relationship_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            source = str(update.get("source", "")).strip()
            target = str(update.get("target", "")).strip()
            if source not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown source actor: {source}")
            if target not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown target actor: {target}")
            if not str(update.get("reason", "")).strip():
                errors.append(f"{prefix} requires a reason")
            if not any(
                str(action.get("actor", "")).strip() in {source, target}
                for action in resolved_actions
            ):
                errors.append(f"{prefix} is not supported by a resolved action")

            present_metrics = 0
            for metric in ("favor", "malice", "trust"):
                key = f"{metric}_delta"
                if key not in update:
                    continue
                present_metrics += 1
                value = update.get(key)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or int(value) != value
                ):
                    errors.append(f"{prefix}.{key} must be an integer")
                    continue
                if not -5 <= int(value) <= 5:
                    errors.append(f"{prefix}.{key} must be between -5 and 5")
            if not present_metrics:
                errors.append(f"{prefix} requires at least one relation delta")
        return errors

    def score_pressure(self, state, relation):
        if not isinstance(state, dict):
            return 0
        malice = relation.get("malice") if isinstance(relation.get("malice"), (int, float)) else 0
        trust = relation.get("trust") if isinstance(relation.get("trust"), (int, float)) else 0
        score = int(state.get("dramatic_push", 0) or 0)
        score += 2 if state.get("bias") else 0
        score += 2 if state.get("framing_style") else 0
        score += 1 if state.get("territorial") else 0
        score += 1 if state.get("side_with") else 0
        score += int(malice) if malice > 0 else 0
        score += abs(int(trust)) if trust < 0 else 0
        return score

    def classify_action_pressure(self, action: str, has_transition_watchers: bool) -> str:
        if any(token in action for token in ["不去", "不肯", "不愿", "拒绝", "不坐", "不回"]):
            return "high" if has_transition_watchers else "medium"
        if any(token in action for token in ["观察", "沉默", "先看", "不说话", "站着", "等等"]):
            return "low"
        if any(token in action for token in ["问", "质问", "反驳", "去", "拿", "碰", "坐", "说"]):
            return "medium"
        return "high"
