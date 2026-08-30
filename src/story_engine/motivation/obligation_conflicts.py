from collections import deque
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple


class ObligationConflictAnalyzer:
    """Find deadline conflicts implied by authoritative completion conditions.

    The analyzer is deliberately conservative.  It only reasons about spatial
    requirements that can be derived from existing structured conditions and
    the world's location graph.  It does not infer conflicts from prose titles
    or invent story-specific semantics.
    """

    TERMINAL_STATUSES = {"fulfilled", "breached", "cancelled", "delegated"}

    def analyze(
        self,
        obligation_state: Any,
        *,
        actor_name: str,
        scene_state: Any,
        current_step: int = 0,
    ) -> List[Dict[str, Any]]:
        if not obligation_state or not scene_state or not actor_name:
            return []
        actor_location = scene_state.get_actor_location(actor_name)
        if not actor_location:
            return []

        tasks = []
        for record in obligation_state.obligations.values():
            if record.status in self.TERMINAL_STATUSES:
                continue
            effective_status = obligation_state.effective_status(record, current_step)
            if effective_status in self.TERMINAL_STATUSES:
                continue
            if self._is_already_satisfied(record, scene_state):
                continue
            locations = self._required_locations(
                record,
                actor_name=actor_name,
                scene_state=scene_state,
            )
            if not locations:
                continue
            tasks.append(
                {
                    "record": record,
                    "locations": sorted(locations),
                    "deadline": int(record.due_step) + int(record.grace_steps),
                }
            )

        tasks.sort(key=lambda item: item["record"].obligation_id)
        conflicts: List[Dict[str, Any]] = []
        for first, second in combinations(tasks, 2):
            # A conjunction with several spatial conditions may describe a
            # delivery rather than requiring the debtor's body in all places.
            # Without action-cost semantics it has no single safe waypoint, so
            # remain conservative and do not guess.
            if len(first["locations"]) != 1 or len(second["locations"]) != 1:
                continue
            conflict = self._pair_conflict(
                actor_location,
                first,
                second,
                scene_state=scene_state,
                current_step=current_step,
            )
            if conflict:
                conflicts.append(conflict)

        severity_rank = {"hard": 0, "constrained": 1}
        conflicts.sort(
            key=lambda item: (
                severity_rank.get(item.get("severity"), 9),
                item.get("earliest_deadline", 10**9),
                tuple(item.get("obligation_ids", [])),
            )
        )
        return conflicts

    def _pair_conflict(
        self,
        actor_location: str,
        first: Dict[str, Any],
        second: Dict[str, Any],
        *,
        scene_state: Any,
        current_step: int,
    ) -> Optional[Dict[str, Any]]:
        first_id = first["record"].obligation_id
        second_id = second["record"].obligation_id
        first_location = first["locations"][0]
        second_location = second["locations"][0]
        if first_location == second_location:
            return None

        orders = [
            (first, second),
            (second, first),
        ]
        feasible_orders = []
        route_failures = []
        for leading, trailing in orders:
            leading_location = leading["locations"][0]
            trailing_location = trailing["locations"][0]
            first_distance = self._distance(
                scene_state,
                actor_location,
                leading_location,
            )
            second_distance = self._distance(
                scene_state,
                leading_location,
                trailing_location,
            )
            if first_distance is None or second_distance is None:
                route_failures.append(True)
                continue
            leading_finish = int(current_step) + first_distance
            trailing_finish = leading_finish + second_distance
            if (
                leading_finish <= leading["deadline"]
                and trailing_finish <= trailing["deadline"]
            ):
                feasible_orders.append(
                    {
                        "order": [
                            leading["record"].obligation_id,
                            trailing["record"].obligation_id,
                        ],
                        "finish_steps": {
                            leading["record"].obligation_id: leading_finish,
                            trailing["record"].obligation_id: trailing_finish,
                        },
                        "minimum_slack": min(
                            leading["deadline"] - leading_finish,
                            trailing["deadline"] - trailing_finish,
                        ),
                    }
                )

        if len(feasible_orders) == 2:
            return None
        severity = "constrained" if feasible_orders else "hard"
        reason_code = (
            "forced_order"
            if feasible_orders
            else "unreachable_route"
            if route_failures
            else "deadline_collision"
        )
        deadlines = {
            first_id: first["deadline"],
            second_id: second["deadline"],
        }
        locations = {
            first_id: first_location,
            second_id: second_location,
        }
        return {
            "conflict_id": self._conflict_id([first_id, second_id]),
            "obligation_ids": [first_id, second_id],
            "severity": severity,
            "reason_code": reason_code,
            "required_locations": locations,
            "deadlines": deadlines,
            "earliest_deadline": min(deadlines.values()),
            "steps_until_earliest_deadline": min(deadlines.values()) - int(current_step),
            "feasible_orders": feasible_orders,
            "choice_required": not feasible_orders,
            "analysis_scope": "debtor_solo_spatial_route",
            "delegation_may_resolve": True,
            "summary": self._summary(
                severity,
                first["record"].title,
                second["record"].title,
            ),
        }

    def _required_locations(
        self,
        record: Any,
        *,
        actor_name: str,
        scene_state: Any,
    ) -> set[str]:
        locations: set[str] = set()
        known_locations = scene_state.get_known_locations()
        for condition in record.completion_conditions or []:
            if not isinstance(condition, dict):
                continue
            if str(condition.get("operator", "eq")) != "eq":
                continue
            scope = str(condition.get("scope", "scene"))
            target = str(condition.get("target") or "").strip()
            path = str(condition.get("path", "")).strip()
            expected = condition.get("value")
            if (
                scope == "actor"
                and target == actor_name
                and path == "location"
                and str(expected) in known_locations
            ):
                locations.add(str(expected))
                continue
            if scope != "world_object" or target not in scene_state.world_objects:
                continue
            if path == "location" and str(expected) in known_locations:
                locations.add(str(expected))
                continue
            if path != "owner":
                continue
            expected_owner = str(expected or "").strip()
            if expected_owner == actor_name:
                object_location = scene_state.get_effective_object_location(target)
                if object_location:
                    locations.add(object_location)
            elif expected_owner in scene_state.actor_states:
                owner_location = scene_state.get_actor_location(expected_owner)
                if owner_location:
                    locations.add(owner_location)
        return locations

    @staticmethod
    def _is_already_satisfied(record: Any, scene_state: Any) -> bool:
        return bool(
            record.completion_conditions
            and all(
                scene_state.matches_condition(condition)
                for condition in record.completion_conditions
            )
        )

    def _distance(
        self,
        scene_state: Any,
        start: str,
        target: str,
    ) -> Optional[int]:
        if start == target:
            return 0
        known_locations = scene_state.get_known_locations()
        queue = deque([(start, 0)])
        visited = {start}
        while queue:
            location, distance = queue.popleft()
            state = scene_state.get_object_state(location)
            neighbors = state.get("connected_to", []) if isinstance(state, dict) else []
            for raw_neighbor in sorted(str(item) for item in neighbors):
                neighbor = str(raw_neighbor)
                if neighbor in visited or neighbor not in known_locations:
                    continue
                if neighbor == target:
                    return distance + 1
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
        return None

    @staticmethod
    def _conflict_id(obligation_ids: List[str]) -> str:
        return "obligation_conflict:" + ":".join(sorted(obligation_ids))

    @staticmethod
    def _summary(severity: str, first_title: str, second_title: str) -> str:
        if severity == "hard":
            return f"当前路线和期限不足以同时完成“{first_title}”与“{second_title}”。"
        return f"“{first_title}”与“{second_title}”只有特定先后顺序仍可同时完成。"
