from copy import deepcopy
from typing import Any, Dict, List


class SituationEngine:
    """Projects world, timeline and plot state into ranked situations."""

    def refresh(
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
        situations.extend(self._plots(plot_state, current_step))

        deduped = {
            str(item.get("situation_id", "")).strip(): item
            for item in situations
            if isinstance(item, dict) and str(item.get("situation_id", "")).strip()
        }
        ranked = sorted(deduped.values(), key=self.sort_key, reverse=True)
        focus_id = ranked[0]["situation_id"] if ranked else None
        if situation_state and hasattr(situation_state, "replace_active"):
            situation_state.replace_active(ranked, focus_situation_id=focus_id, current_step=current_step)
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

    def _frontstage(self, scene_state, player_name, player_pov, timeline):
        location = player_pov.get("location")
        if not location:
            return {}
        location_state = scene_state.get_object_state(location)
        phase = str(timeline.get("day_phase", "")).strip()
        actor_states = scene_state.get_actors_in_location(location)
        participants = [str(item).strip() for item in player_pov.get("visible_actors", []) if str(item).strip()]
        tags = self.collect_tags(
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
            "tags": self.dedupe(tags),
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
            tags = self.collect_tags(
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
                    "tags": self.dedupe(tags),
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
            "participants": self.dedupe(participants),
            "cause": str(pressure.get("title", "")).strip() or str(pressure.get("note", "")).strip(),
            "stakes": ["reputation"],
            "tags": self.dedupe(["transition", "absence", "backlash", pressure.get("phase", "")]),
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
            "tags": self.dedupe(["aftermath", "absence", missed.get("phase", "")]),
            "source": {"type": "missed_commitment", "id": missed.get("commitment_id")},
            "focus_score": 82,
        }

    def _plots(self, plot_state, current_step):
        if not plot_state or not hasattr(plot_state, "get_pressure_packets"):
            return []
        situations = []
        for item in plot_state.get_pressure_packets():
            if not isinstance(item, dict):
                continue
            plot_id = str(item.get("plot_id", "")).strip()
            clock = int(item.get("clock", 0) or 0)
            if not plot_id or clock <= 0:
                continue
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
                    "tags": self.dedupe(["plot_pressure"] + list(item.get("tags", []))),
                    "source": {"type": "plot", "id": plot_id},
                    "focus_score": 46 + min(clock, 4) * 4,
                }
            )
        return situations

    def collect_tags(self, kind, phase, location_kind, visibility, content_tags=None):
        return self.dedupe([kind, visibility, phase, location_kind] + list(content_tags or []))

    def dedupe(self, items):
        seen = set()
        result = []
        for item in items or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    def sort_key(self, item):
        status = {"active": 3, "scheduled": 2, "cooling": 1, "resolved": 0}.get(str(item.get("status", "")).strip(), 0)
        visibility = {"player_visible": 3, "rumor": 2, "hidden": 1}.get(str(item.get("visibility", "")).strip(), 0)
        return (int(item.get("focus_score", 0) or 0), status, visibility, len(item.get("participants", []) or []), str(item.get("situation_id", "")))
