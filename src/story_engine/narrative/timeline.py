from copy import deepcopy
from typing import Any, Dict, List


class TimelineEngine:
    """Advances phases and world commitments independently of action resolution."""

    def refresh(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        player_name: Any = None,
    ) -> Dict[str, Any]:
        if not scene_state:
            return {}
        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        flags = scene_state.scene_flags or {}
        schedule = self.normalize_phase_schedule(flags.get("phase_schedule", []))
        commitments = self.normalize_commitments(flags.get("upcoming_commitments", []))
        previous_phase = str(flags.get("day_phase", "") or "").strip()
        phase = self.resolve_day_phase(current_step, schedule, flags.get("day_phase"))
        phase_turn = self.resolve_phase_turn(current_step, schedule, phase)
        relation_registry = context.get("relation_registry")
        relationship_book = (
            relation_registry.to_relationship_book() if relation_registry else None
        )
        transition = self.build_transition_pressure(
            scene_state, commitments, current_step, player_name, relationship_book
        )
        due, upcoming = [], []
        for item in commitments:
            due_step = int(item.get("due_step", 0))
            grace = int(item.get("grace_steps", 0))
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if current_step < due_step:
                upcoming.append(item)
            elif current_step <= due_step + grace:
                item["status"] = "due"
                due.append(item)

        scene_state.update_scene_flags(
            {
                "day_phase": phase,
                "phase_turn": phase_turn,
                "phase_schedule": schedule,
                "upcoming_commitments": commitments,
            }
        )
        return {
            "day_phase": phase,
            "phase_transition": (
                {"from": previous_phase, "to": phase}
                if previous_phase and phase and previous_phase != phase
                else {}
            ),
            "phase_turn": phase_turn,
            "due_commitments": due,
            "upcoming_commitments": upcoming,
            "last_missed_commitment": deepcopy(flags.get("last_missed_commitment")),
            "transition_pressure": transition,
        }

    def finalize(
        self,
        scene_state: Any,
        context: Dict[str, Any],
        player_name: Any,
    ) -> Dict[str, Any]:
        if not scene_state:
            return {}
        clock = context.get("clock")
        current_step = clock.current_step if clock else 0
        commitments = self.normalize_commitments(
            scene_state.get_scene_flag("upcoming_commitments", [])
        )
        last_missed = None
        attendance_events = []

        for item in commitments:
            due_step = int(item.get("due_step", 0))
            grace = int(item.get("grace_steps", 0))
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if current_step < due_step:
                item["status"] = "scheduled"
                continue

            required_location = item.get("location")
            expected = {
                str(actor).strip()
                for actor in item.get("participants", [])
                if str(actor).strip()
            }
            if item.get("player_relevant") and player_name:
                expected.add(str(player_name))
            present = sorted(
                actor
                for actor in expected
                if not required_location
                or scene_state.get_actor_location(actor) == required_location
            )
            missing = sorted(expected.difference(present))
            item["present_participants"] = present
            item["missing_participants"] = missing
            if missing and current_step >= due_step + grace:
                last_missed = self._mark_missed(
                    item,
                    required_location,
                    missing_participants=missing,
                )
                attendance_events.append(
                    self._attendance_event(
                        scene_state,
                        item,
                        current_step=current_step,
                        status="missed",
                        expected=sorted(expected),
                        present=present,
                        missing=missing,
                    )
                )
            elif missing:
                item["status"] = "due"
            elif current_step <= due_step + grace:
                item["status"] = "resolved"
                item["resolution_note"] = item.get(
                    "present_consequence", item.get("summary", "")
                )
                if expected:
                    attendance_events.append(
                        self._attendance_event(
                            scene_state,
                            item,
                            current_step=current_step,
                            status="resolved",
                            expected=sorted(expected),
                            present=present,
                            missing=[],
                        )
                    )
            else:
                last_missed = self._mark_missed(item, required_location)

        scene_state.update_scene_flags(
            {
                "upcoming_commitments": commitments,
                "last_missed_commitment": last_missed,
            }
        )
        return {
            "day_phase": scene_state.get_scene_flag("day_phase"),
            "phase_turn": scene_state.get_scene_flag("phase_turn", 0),
            "due_commitments": [deepcopy(item) for item in commitments if item.get("status") == "due"],
            "upcoming_commitments": [
                deepcopy(item) for item in commitments if item.get("status") == "scheduled"
            ],
            "last_missed_commitment": deepcopy(last_missed),
            "attendance_events": attendance_events,
        }

    def private_schedule(
        self,
        scene_state: Any,
        actor_name: str,
        current_step: int,
        include_player_relevant: bool = False,
    ) -> Dict[str, Any]:
        if not scene_state or not actor_name:
            return {"active": [], "due": [], "upcoming": []}
        commitments = self.normalize_commitments(
            scene_state.get_scene_flag("upcoming_commitments", [])
        )
        active = []
        for item in commitments:
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            participants = {
                str(name).strip()
                for name in item.get("participants", [])
                if str(name).strip()
            }
            if actor_name not in participants and not (
                include_player_relevant and item.get("player_relevant")
            ):
                continue
            due_step = int(item.get("due_step", 0))
            active.append(
                {
                    "commitment_id": item.get("commitment_id"),
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "location": item.get("location"),
                    "due_step": due_step,
                    "grace_steps": int(item.get("grace_steps", 0)),
                    "steps_until_due": due_step - int(current_step),
                    "status": item.get("status", "scheduled"),
                    "attendance_is_voluntary": True,
                }
            )
        return {
            "active": active,
            "due": [item for item in active if item["steps_until_due"] <= 0],
            "upcoming": [item for item in active if item["steps_until_due"] > 0],
        }

    def build_transition_pressure(
        self,
        scene_state,
        commitments,
        current_step,
        player_name,
        relationship_book=None,
    ):
        if not scene_state or not player_name:
            return {}
        player_location = scene_state.get_actor_location(player_name)
        if not player_location:
            return {}
        same_scene = scene_state.get_actors_in_location(player_location)
        for item in commitments:
            if not isinstance(item, dict):
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            if not item.get("player_relevant"):
                continue
            if current_step < int(item.get("due_step", 0)):
                continue
            target = item.get("location")
            if target and player_location == target:
                continue
            carriers = self.resolve_transition_carriers(
                item, same_scene, player_name, relationship_book
            )
            carrier_states = {
                name: deepcopy(scene_state.get_actor_state(name))
                for name in carriers
                if isinstance(scene_state.get_actor_state(name), dict)
            }
            if carrier_states:
                return {
                    "active": True,
                    "commitment_id": item.get("commitment_id"),
                    "title": item.get("title", ""),
                    "phase": item.get("phase", ""),
                    "player_location": player_location,
                    "target_location": target,
                    "note": item.get("absent_consequence", item.get("summary", "")),
                    "carrier_actors": carriers,
                    "carrier_states": carrier_states,
                    "requires_human_backlash": True,
                }
        return {}

    def resolve_transition_carriers(
        self,
        commitment,
        same_scene_states,
        player_name,
        relationship_book=None,
    ):
        if not isinstance(commitment, dict) or not isinstance(same_scene_states, dict):
            return []
        participants = [
            str(actor).strip()
            for actor in commitment.get("participants", [])
            if str(actor).strip()
        ]
        candidates = []
        for actor, state in same_scene_states.items():
            if actor == player_name or not isinstance(state, dict):
                continue
            pressure = self._score_actor_pressure(
                state,
                relationship_book.get_metrics(actor, player_name)
                if relationship_book
                else {},
            )
            bonus = 4 if actor in participants else 0
            if pressure <= 0 and bonus <= 0:
                continue
            candidates.append(
                {
                    "actor": actor,
                    "score": pressure + bonus,
                    "participant_index": (
                        participants.index(actor) if actor in participants else 99
                    ),
                }
            )
        candidates.sort(
            key=lambda item: (
                item["participant_index"],
                -int(item["score"]),
                str(item["actor"]),
            )
        )
        return [str(item["actor"]).strip() for item in candidates[:4]]

    def normalize_phase_schedule(self, items):
        schedule = [
            {
                "phase": str(item.get("phase")),
                "start_step": int(item.get("start_step", 0)),
                "label": str(item.get("label", item.get("phase"))),
            }
            for item in items or []
            if isinstance(item, dict) and item.get("phase")
        ]
        schedule.sort(key=lambda item: item["start_step"])
        return schedule

    def normalize_commitments(self, items):
        result = []
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
            raw_participants = normalized.get("participants", [])
            if not isinstance(raw_participants, list):
                raw_participants = []
            normalized["participants"] = list(
                dict.fromkeys(
                    str(actor).strip()
                    for actor in raw_participants
                    if str(actor).strip()
                )
            )
            normalized["wake_before_steps"] = max(
                0, int(normalized.get("wake_before_steps", 1) or 0)
            )
            result.append(normalized)
        result.sort(key=lambda item: (item["due_step"], item["commitment_id"]))
        return result

    def resolve_day_phase(self, current_step, schedule, current_phase):
        phase = str(current_phase or "freeplay")
        for item in schedule:
            if current_step < item["start_step"]:
                break
            phase = item["phase"]
        return phase

    def resolve_phase_turn(self, current_step, schedule, phase):
        phase_start = 0
        for item in schedule:
            if item["phase"] == phase:
                phase_start = item["start_step"]
        return max(0, current_step - phase_start)

    def _mark_missed(
        self,
        item,
        required_location,
        missing_participants=None,
    ):
        item["status"] = "missed"
        item["resolution_note"] = item.get("absent_consequence", item.get("summary", ""))
        return {
            "commitment_id": item.get("commitment_id"),
            "title": item.get("title", ""),
            "phase": item.get("phase", ""),
            "location": required_location,
            "note": item.get("resolution_note", ""),
            "missing_participants": list(missing_participants or []),
        }

    def _attendance_event(
        self,
        scene_state: Any,
        item: Dict[str, Any],
        *,
        current_step: int,
        status: str,
        expected: List[str],
        present: List[str],
        missing: List[str],
    ) -> Dict[str, Any]:
        commitment_id = str(item.get("commitment_id", "")).strip()
        title = str(item.get("title", commitment_id)).strip()
        location = str(item.get("location", "")).strip()
        if status == "missed":
            statement = (
                f"日程“{title}”到期时，{self._join_names(missing)}"
                f"没有出现在{location or '约定地点'}。"
            )
            kind = "timeline_attendance_missed"
        else:
            statement = (
                f"日程“{title}”到期时，{self._join_names(present)}"
                f"出现在{location or '约定地点'}。"
            )
            kind = "timeline_attendance_resolved"
        local_witnesses = sorted(
            scene_state.get_actors_in_location(location)
            if scene_state and location
            else []
        )
        direct = [actor for actor in local_witnesses if actor not in missing]
        return {
            "event_id": f"timeline:{commitment_id}:{status}",
            "kind": kind,
            "title": title,
            "statement": statement,
            "occurred_step": int(current_step),
            "location": location,
            "subjects": expected,
            "present_participants": list(present),
            "missing_participants": list(missing),
            "direct_witnesses": direct,
            "self_witnesses": list(missing),
            "source_type": "timeline_resolution",
            "source_ref": f"{commitment_id}:{status}",
        }

    @staticmethod
    def _join_names(names: List[str]) -> str:
        values = [str(name).strip() for name in names if str(name).strip()]
        return "、".join(values) if values else "相关参与者"

    def _score_actor_pressure(self, state, relation):
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
