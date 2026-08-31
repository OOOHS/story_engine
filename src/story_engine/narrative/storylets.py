from copy import deepcopy
from typing import Any, Dict, List, Set


class StoryletEngine:
    """Selects state-qualified narrative opportunities.

    A storylet remains content: it can bias what opportunities are salient,
    but it does not make character decisions or mutate the world directly.
    """

    def refresh_situations(
        self,
        scene_state: Any,
        player_name: Any,
        player_pov: Dict[str, Any],
        timeline_packet: Dict[str, Any],
        current_step: int,
    ) -> Dict[str, Any]:
        """Project world and timeline state into ranked situations.

        Purely a same-turn projection used to score storylet relevance;
        it holds no cross-turn state of its own.
        """
        if not scene_state or not player_name:
            return {}
        situations: List[Dict[str, Any]] = []
        frontstage = self._frontstage(scene_state, player_name, player_pov, timeline_packet)
        if frontstage:
            situations.append(frontstage)
        situations.extend(
            self._commitments(scene_state, player_name, timeline_packet, current_step)
        )
        transition = self._transition(player_name, timeline_packet)
        if transition:
            situations.append(transition)
        aftermath = self._aftermath(player_name, timeline_packet)
        if aftermath:
            situations.append(aftermath)

        deduped = {
            str(item.get("situation_id", "")).strip(): item
            for item in situations
            if isinstance(item, dict) and str(item.get("situation_id", "")).strip()
        }
        ranked = sorted(deduped.values(), key=self._situation_sort_key, reverse=True)
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

    def _frontstage(self, scene_state, player_name, player_pov, timeline):
        location = player_pov.get("location")
        if not location:
            return {}
        location_state = scene_state.get_object_state(location)
        phase = str(timeline.get("day_phase", "")).strip()
        actor_states = scene_state.get_actors_in_location(location)
        participants = [str(item).strip() for item in player_pov.get("visible_actors", []) if str(item).strip()]
        tags = self._collect_situation_tags(
            "frontstage",
            phase,
            str(location_state.get("kind", "")).strip(),
            "player_visible",
            location_state.get("tags", []),
        )
        if len(participants) > 1:
            tags.extend(["social", "public"])
        if any(
            isinstance(state, dict)
            and any(state.get(key) for key in ("bias", "framing_style", "territorial", "side_with"))
            for actor, state in actor_states.items()
            if actor != player_name
        ):
            tags.extend(["pressure", "bias"])
        for actor, state in actor_states.items():
            if actor == player_name or not isinstance(state, dict):
                continue
            profile = str(state.get("pressure_profile", "")).strip()
            if profile:
                tags.append(profile)
        return {
            "situation_id": f"frontstage:{location}",
            "kind": "frontstage",
            "status": "active",
            "visibility": "player_visible",
            "location": location,
            "time_window": {"phase": phase, "start_step": int(timeline.get("phase_turn", 0) or 0)},
            "participants": participants,
            "cause": f"玩家当前正在{location}面对眼前局面。",
            "stakes": [],
            "tags": self._dedupe_texts(tags),
            "source": {"type": "player_pov", "id": location},
            "focus_score": 120 + max(0, len(participants) - 1) * 8,
        }

    def _commitments(self, scene_state, player_name, timeline, current_step):
        situations = []
        player_location = scene_state.get_actor_location(player_name)
        due_ids = {
            str(item.get("commitment_id", "")).strip()
            for item in timeline.get("due_commitments", [])
            if isinstance(item, dict)
        }
        seen = set()
        for item in list(timeline.get("due_commitments", [])) + list(timeline.get("upcoming_commitments", [])):
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
                "active" if commitment_id in due_ids else "scheduled"
            )
            participants = [
                str(actor).strip()
                for actor in item.get("participants", [])
                if str(actor).strip()
            ]
            if item.get("player_relevant") and player_name not in participants:
                participants.append(str(player_name))
            visibility = "player_visible" if location and player_location == location else (
                "rumor" if item.get("player_relevant") else "hidden"
            )
            tags = self._collect_situation_tags(
                "commitment",
                phase,
                str(location_state.get("kind", "")).strip(),
                visibility,
                location_state.get("tags", []),
            )
            if item.get("player_relevant"):
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
                    "time_window": {"phase": phase, "due_step": int(item.get("due_step", current_step) or current_step)},
                    "participants": participants,
                    "cause": str(item.get("summary", "")).strip() or str(item.get("title", "")).strip(),
                    "stakes": [],
                    "tags": self._dedupe_texts(tags),
                    "source": {"type": "commitment", "id": commitment_id},
                    "focus_score": 95 if item.get("player_relevant") else 58,
                }
            )
        return situations

    def _transition(self, player_name, timeline):
        pressure = timeline.get("transition_pressure", {}) if isinstance(timeline, dict) else {}
        if not isinstance(pressure, dict) or not pressure.get("active"):
            return {}
        participants = [str(player_name)] + [
            str(actor).strip() for actor in pressure.get("carrier_actors", []) if str(actor).strip()
        ]
        return {
            "situation_id": f"transition:{pressure.get('commitment_id', 'unknown')}",
            "kind": "transition",
            "status": "active",
            "visibility": "player_visible",
            "location": pressure.get("player_location"),
            "time_window": {"phase": str(pressure.get("phase", "")).strip()},
            "participants": self._dedupe_texts(participants),
            "cause": str(pressure.get("title", "")).strip() or str(pressure.get("note", "")).strip(),
            "stakes": ["reputation"],
            "tags": self._dedupe_texts(["transition", "absence", "backlash", pressure.get("phase", "")]),
            "source": {"type": "transition", "id": pressure.get("commitment_id")},
            "focus_score": 165,
        }

    def _aftermath(self, player_name, timeline):
        missed = timeline.get("last_missed_commitment") if isinstance(timeline, dict) else None
        if not isinstance(missed, dict) or not missed.get("commitment_id"):
            return {}
        return {
            "situation_id": f"aftermath:{missed.get('commitment_id')}",
            "kind": "aftermath",
            "status": "active",
            "visibility": "rumor",
            "location": missed.get("location"),
            "time_window": {"phase": str(missed.get("phase", "")).strip()},
            "participants": [str(player_name)],
            "cause": str(missed.get("note", "")).strip(),
            "stakes": ["reputation"],
            "tags": self._dedupe_texts(["aftermath", "absence", missed.get("phase", "")]),
            "source": {"type": "missed_commitment", "id": missed.get("commitment_id")},
            "focus_score": 82,
        }

    def _collect_situation_tags(self, kind, phase, location_kind, visibility, content_tags=None):
        return self._dedupe_texts([kind, visibility, phase, location_kind] + list(content_tags or []))

    def _dedupe_texts(self, items):
        seen = set()
        result = []
        for item in items or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    def resolve(
        self,
        scene_state: Any,
        scenario: Any,
        situation_packet: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        if not scene_state or not scenario:
            return []

        consumed = set(scene_state.scene_flags.get("consumed_storylets", []))
        active: List[Dict[str, Any]] = []
        for storylet in scenario.storylets:
            if storylet.one_shot and storylet.storylet_id in consumed:
                continue
            situation_matches = self.match_situations(storylet, situation_packet or {})
            if self.requires_situation_route(storylet) and not situation_matches:
                continue
            if not all(
                scene_state.matches_condition(condition)
                for condition in storylet.conditions
            ):
                continue

            focus_situation_id = str(
                (situation_packet or {}).get("focus_situation", {}).get("situation_id", "")
            ).strip()
            matched_ids = [
                str(item.get("situation_id", "")).strip()
                for item in situation_matches
                if str(item.get("situation_id", "")).strip()
            ]
            beat = getattr(storylet, "beat", None)
            active.append(
                {
                    "storylet_id": storylet.storylet_id,
                    "intent": storylet.intent,
                    "priority": storylet.priority,
                    "tags": list(storylet.tags),
                    "situation_kinds": list(getattr(storylet, "situation_kinds", []) or []),
                    "situation_tags": list(getattr(storylet, "situation_tags", []) or []),
                    "matched_situation_ids": matched_ids,
                    "focus_situation_match": bool(
                        focus_situation_id and focus_situation_id in matched_ids
                    ),
                    "situation_score": max(
                        [int(item.get("focus_score", 0) or 0) for item in situation_matches]
                        or [0]
                    ),
                    "beat": (
                        beat.model_dump()
                        if beat and hasattr(beat, "model_dump")
                        else beat.dict()
                        if beat
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

    def build_packet(
        self,
        scene_state: Any,
        active_storylets: List[Dict[str, Any]],
        current_step: int,
        situation_packet: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not scene_state or not active_storylets:
            return {}

        priority_storylets = [item for item in active_storylets[:5] if isinstance(item, dict)]
        priority_tags: List[str] = []
        priority_beats: List[Dict[str, Any]] = []
        preferred_template_ids: List[str] = []
        for item in priority_storylets:
            self._extend_unique(priority_tags, item.get("tags", []))
            beat = item.get("beat", {})
            if isinstance(beat, dict) and beat:
                priority_beats.append(beat)
                self._extend_unique(preferred_template_ids, beat.get("preferred_template_ids", []))

        focus = situation_packet.get("focus_situation", {}) if isinstance(situation_packet, dict) else {}
        focus_tags = self._text_set(focus.get("tags", []))
        recent_template_ids = self._text_set(
            scene_state.get_scene_flag("recent_conflict_template_ids", [])
        )
        salient = self.pick_salient(
            priority_storylets=priority_storylets,
            recent_template_ids=recent_template_ids,
            focus_tags=focus_tags,
        )
        return {
            "mode": "advisory_opportunities",
            "priority_storylets": priority_storylets,
            "priority_storylet_ids": [
                str(item.get("storylet_id", "")).strip()
                for item in priority_storylets
                if str(item.get("storylet_id", "")).strip()
            ],
            "priority_tags": priority_tags,
            "priority_beats": priority_beats,
            "preferred_template_ids": preferred_template_ids,
            "focus_situation_id": str(focus.get("situation_id", "")).strip(),
            "focus_situation_kind": str(focus.get("kind", "")).strip(),
            "focus_situation_tags": list(focus.get("tags", []) or []),
            "salient_storylet_id": (
                str(salient.get("storylet_id", "")).strip() if salient else ""
            ),
        }

    def pick_salient(
        self,
        priority_storylets: List[Dict[str, Any]],
        recent_template_ids: Set[str],
        focus_tags: Set[str],
    ) -> Dict[str, Any]:
        if not priority_storylets:
            return {}

        def score(item: Dict[str, Any]) -> int:
            total = int(item.get("priority", 0) or 0) * 10
            beat = item.get("beat", {}) if isinstance(item.get("beat", {}), dict) else {}
            template_ids = self._text_set(beat.get("preferred_template_ids", []))
            storylet_tags = self._text_set(item.get("tags", []))
            if template_ids and not template_ids.issubset(recent_template_ids):
                total += 120
            if focus_tags and storylet_tags.intersection(focus_tags):
                total += 70
            return total

        ranked = sorted(priority_storylets, key=score, reverse=True)
        return ranked[0] if ranked else {}

    def detect_hits(
        self,
        active_storylets: List[Dict[str, Any]],
        result: Dict[str, Any],
    ) -> List[str]:
        """Recognize content opportunities realized by committed candidates.

        The semantic resolver cannot claim or force a hit.  This host pass only
        observes the actions and consequence tags already present in the
        candidate result; consumption still happens only after transaction
        commit.
        """
        hits: List[str] = []
        for storylet in active_storylets or []:
            if not isinstance(storylet, dict):
                continue
            storylet_id = str(storylet.get("storylet_id", "")).strip()
            if storylet_id and self._matches_result(storylet, result):
                hits.append(storylet_id)
        return list(dict.fromkeys(hits))

    def _matches_result(
        self,
        storylet: Dict[str, Any],
        result: Dict[str, Any],
    ) -> bool:
        storylet_id = str(storylet.get("storylet_id", "")).strip()
        if storylet_id and self._cited_by_source_storylet_id(storylet_id, result):
            return True
        beat = storylet.get("beat", {})
        if isinstance(beat, dict) and beat:
            return self._beat_realized(beat, result)
        tags = self._text_set(storylet.get("tags", []))
        flags = self._text_set(result.get("conflict_flags", []))
        if tags and tags.intersection(flags):
            return True
        intent_tokens = [
            token
            for token in str(storylet.get("intent", "")).replace("，", " ")
            .replace("。", " ")
            .replace("、", " ")
            .split()
            if token
        ][:4]
        resolved_text = " ".join(
            str(item.get("result", "")).strip()
            for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        )
        return bool(intent_tokens and any(token in resolved_text for token in intent_tokens))

    def _cited_by_source_storylet_id(
        self,
        storylet_id: str,
        result: Dict[str, Any],
    ) -> bool:
        """The resolver directly settled this storylet as a fact (actor=World
        resolved_action / state_updates / object_lifecycle) and cited it by
        id -- that is an authoritative claim about which opportunity was
        realized, not a heuristic guess, so it short-circuits the fuzzy
        tag/actor matching below.
        """
        for action in result.get("resolved_actions", []) or []:
            if (
                isinstance(action, dict)
                and str(action.get("source_storylet_id", "")).strip() == storylet_id
            ):
                return True
        for operation in result.get("object_lifecycle", []) or []:
            if (
                isinstance(operation, dict)
                and str(operation.get("source_storylet_id", "")).strip() == storylet_id
            ):
                return True
        return False

    def _beat_realized(
        self,
        beat: Dict[str, Any],
        result: Dict[str, Any],
    ) -> bool:
        template_ids = self._text_set(beat.get("preferred_template_ids", []))
        preferred_actors = self._text_set(beat.get("preferred_actors", []))
        required_flags = self._text_set(beat.get("required_flags", []))
        required_visibility = str(beat.get("visibility", "")).strip()
        applied_templates = self._text_set(
            result.get("applied_conflict_templates", [])
        )
        if template_ids and template_ids.intersection(applied_templates):
            return True
        anchored = bool(template_ids or preferred_actors)
        for action in result.get("resolved_actions", []):
            if not isinstance(action, dict):
                continue
            if required_visibility and str(action.get("visibility", "")).strip() != required_visibility:
                continue
            if template_ids and str(action.get("template_id", "")).strip() in template_ids:
                return True
            if (
                preferred_actors
                and str(action.get("actor", "")).strip() in preferred_actors
                and action.get("outcome") in {"blocked", "complication", "partial"}
                and str(action.get("result", "")).strip()
            ):
                return True
        if anchored:
            return False
        return bool(
            required_flags.intersection(
                self._text_set(result.get("conflict_flags", []))
            )
        )

    def requires_situation_route(self, storylet: Any) -> bool:
        return bool(
            list(getattr(storylet, "situation_kinds", []) or [])
            or list(getattr(storylet, "situation_tags", []) or [])
        )

    def match_situations(
        self,
        storylet: Any,
        situation_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not isinstance(situation_packet, dict):
            return []
        active_situations = [
            item
            for item in situation_packet.get("active_situations", [])
            if isinstance(item, dict) and str(item.get("situation_id", "")).strip()
        ]
        if not active_situations:
            return []

        required_kinds = self._text_set(getattr(storylet, "situation_kinds", []) or [])
        required_tags = self._text_set(getattr(storylet, "situation_tags", []) or [])
        if not required_kinds and not required_tags:
            required_tags = self._text_set(getattr(storylet, "tags", []) or [])
            beat = getattr(storylet, "beat", None)
            stake = getattr(beat, "stake", "") if beat else ""
            if str(stake).strip():
                required_tags.add(str(stake).strip())

        focus_id = str(
            situation_packet.get("focus_situation", {}).get("situation_id", "")
        ).strip()
        matches: List[Dict[str, Any]] = []
        for item in active_situations:
            kind = str(item.get("kind", "")).strip()
            tags = self._text_set(item.get("tags", [])).union(
                self._text_set(item.get("stakes", []))
            )
            if required_kinds and kind not in required_kinds:
                continue
            if required_tags and not required_tags.intersection(tags):
                continue
            scored = deepcopy(item)
            if str(scored.get("situation_id", "")).strip() == focus_id:
                scored["focus_score"] = int(scored.get("focus_score", 0) or 0) + 80
            matches.append(scored)

        matches.sort(key=self._situation_sort_key, reverse=True)
        return matches

    def consumable_hits(
        self,
        scenario: Any,
        hits: List[str],
        active_storylets: List[Dict[str, Any]] | None = None,
    ) -> List[str]:
        if not hits:
            return []
        storylet_map = {
            item.storylet_id: item
            for item in (getattr(scenario, "storylets", None) or [])
        }
        consumable: List[str] = []
        for storylet_id in hits:
            storylet = storylet_map.get(storylet_id)
            if storylet and storylet.one_shot and storylet_id not in consumable:
                consumable.append(storylet_id)
                continue
        return consumable

    def _text_set(self, values: Any) -> Set[str]:
        return {str(item).strip() for item in values or [] if str(item).strip()}

    def _extend_unique(self, target: List[str], values: Any) -> None:
        for item in values or []:
            text = str(item).strip()
            if text and text not in target:
                target.append(text)

    def _situation_sort_key(self, item: Dict[str, Any]) -> Any:
        status_rank = {"active": 3, "scheduled": 2, "cooling": 1, "resolved": 0}.get(
            str(item.get("status", "")).strip(), 0
        )
        visibility_rank = {"player_visible": 3, "rumor": 2, "hidden": 1}.get(
            str(item.get("visibility", "")).strip(), 0
        )
        return (
            int(item.get("focus_score", 0) or 0),
            status_rank,
            visibility_rank,
            len(item.get("participants", []) or []),
            str(item.get("situation_id", "")),
        )
