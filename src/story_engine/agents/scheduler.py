from typing import Any, Dict, Iterable

from src.story_engine.agents.types import AgentActivation
from src.story_engine.core.entity import Entity


class AgentScheduler:
    """Decides which character minds need a turn.

    Foreground characters react every step. Off-screen characters run at a
    lower, deterministically staggered frequency or wake when a world signal
    reaches their location. The scheduler never changes world state itself.
    """

    def __init__(self) -> None:
        self._obligation_conflicts = None

    def activation_for(
        self,
        entity: Entity,
        *,
        step: int,
        actor_location: Any,
        player_location: Any,
        proposals: Iterable[Dict[str, Any]],
        is_player: bool,
        has_manual_override: bool,
        scene_state: Any = None,
        agreement_registry: Any = None,
    ) -> AgentActivation:
        controller = entity.get_component("AgentController")
        if has_manual_override:
            return AgentActivation(True, "foreground", "manual_override")
        if controller and not controller.autonomous:
            return AgentActivation(False, "dormant", "autonomy_disabled")

        policy = str(getattr(controller, "activation_policy", "auto"))
        if policy == "dormant":
            return AgentActivation(False, "dormant", "policy_dormant")
        if is_player or policy == "foreground":
            return AgentActivation(True, "foreground", "player_or_foreground_policy")
        if player_location is None:
            return AgentActivation(True, "foreground", "no_player_viewpoint")
        if actor_location and player_location and actor_location == player_location:
            return AgentActivation(True, "foreground", "shares_player_location")

        if self._has_local_world_signal(actor_location, proposals):
            return AgentActivation(True, "background", "local_world_signal")

        attention_kind, attention_id = self._pending_attention(entity, step)
        if policy in {"auto", "background"} and attention_id:
            return AgentActivation(
                True,
                "background",
                f"{attention_kind}:{attention_id}",
            )

        navigation_problem = self._navigation_problem(entity)
        if policy in {"auto", "background"} and navigation_problem:
            return AgentActivation(
                True,
                "background",
                f"navigation_problem:{navigation_problem}",
            )

        urgent_schedule = self._urgent_schedule(entity.name, step, scene_state)
        if policy in {"auto", "background"} and urgent_schedule:
            return AgentActivation(
                True,
                "background",
                f"schedule_due:{urgent_schedule}",
            )

        urgent_agreement = self._urgent_agreement(
            entity, step, agreement_registry, scene_state
        )
        if policy in {"auto", "background"} and urgent_agreement:
            return AgentActivation(
                True,
                "background",
                f"agreement_due:{urgent_agreement}",
            )

        urgent_conflict = self._urgent_obligation_conflict(
            entity,
            step,
            scene_state=scene_state,
        )
        if policy in {"auto", "background"} and urgent_conflict:
            return AgentActivation(
                True,
                "background",
                urgent_conflict,
            )

        urgent_obligation = self._urgent_obligation(entity, step)
        if policy in {"auto", "background"} and urgent_obligation:
            return AgentActivation(
                True,
                "background",
                f"obligation_due:{urgent_obligation}",
            )

        critical_need = self._critical_need(entity)
        if policy in {"auto", "background"} and critical_need:
            return AgentActivation(
                True,
                "background",
                f"critical_need:{critical_need}",
            )

        continuation_goal = self._continuation_goal(entity, step, scene_state)
        if policy in {"auto", "background"} and continuation_goal:
            return AgentActivation(
                True,
                "background",
                f"agent_goal:{continuation_goal}",
            )

        interval = max(1, int(getattr(controller, "background_interval", 3) or 3))
        if policy in {"auto", "background"} and self._is_scheduled(entity.name, step, interval):
            return AgentActivation(True, "background", "background_tick")
        return AgentActivation(False, "dormant", "not_scheduled")

    def _has_local_world_signal(
        self,
        actor_location: Any,
        proposals: Iterable[Dict[str, Any]],
    ) -> bool:
        if not actor_location:
            return False
        for proposal in proposals or []:
            if not isinstance(proposal, dict):
                continue
            if proposal.get("actor") != "World":
                continue
            if proposal.get("location") == actor_location:
                return True
        return False

    def _critical_need(self, entity: Entity) -> str:
        drive = entity.get_component("DriveState")
        if not drive:
            return ""
        candidates = [
            (name, meter.pressure)
            for name, meter in drive.needs.items()
            if meter.pressure >= meter.critical_threshold
        ]
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (-item[1], item[0]))
        return str(candidates[0][0])

    @staticmethod
    def _pending_world_event(entity: Entity) -> str:
        cognition = entity.get_component("Cognition")
        if not cognition or not hasattr(cognition, "next_pending_world_event"):
            return ""
        return str(cognition.next_pending_world_event() or "")

    @staticmethod
    def _pending_event_response(entity: Entity) -> str:
        cognition = entity.get_component("Cognition")
        if not cognition or not hasattr(cognition, "next_pending_event_response"):
            return ""
        return str(cognition.next_pending_event_response() or "")

    @staticmethod
    def _pending_attention(entity: Entity, current_step: int) -> tuple[str, str]:
        cognition = entity.get_component("Cognition")
        if cognition and hasattr(cognition, "next_pending_attention"):
            kind, attention_id = cognition.next_pending_attention(current_step)
            return str(kind or ""), str(attention_id or "")
        if event_id := AgentScheduler._pending_world_event(entity):
            return "world_event", event_id
        if response_id := AgentScheduler._pending_event_response(entity):
            return "event_response", response_id
        return "", ""

    @staticmethod
    def _navigation_problem(entity: Entity) -> str:
        state = entity.get_component("NavigationState")
        return str(state.next_wakeup() or "") if state else ""

    @staticmethod
    def _continuation_goal(
        entity: Entity,
        step: int,
        scene_state: Any,
    ) -> str:
        state = entity.get_component("GoalState")
        controller = entity.get_component("AgentController")
        if not state or not controller or not hasattr(state, "active_records"):
            return ""
        agent_goals = [
            record
            for record in state.active_records()
            if record.origin == "agent"
        ]
        candidates = [
            record for record in agent_goals if record.completion_conditions
        ] or [
            record for record in agent_goals if not record.completion_conditions
        ]
        if not candidates:
            return ""
        record = candidates[0]
        verifiable = bool(record.completion_conditions)
        interval_flag = (
            "agent_goal_wakeup_interval"
            if verifiable
            else "agent_open_goal_review_interval"
        )
        default_interval = 2 if verifiable else 12
        raw_interval = (
            scene_state.get_scene_flag(interval_flag, default_interval)
            if scene_state
            else default_interval
        )
        try:
            lower_bound, upper_bound = (1, 20) if verifiable else (4, 80)
            base_interval = max(
                lower_bound,
                min(upper_bound, int(raw_interval)),
            )
        except (TypeError, ValueError):
            base_interval = default_interval
        repeated = (
            int(controller.repeated_goal_action_count)
            if controller.last_goal_wakeup_id == record.goal_id
            else 0
        )
        backoff = 2 ** min(3, max(0, repeated - 1))
        interval = min(80, base_interval * backoff)
        last_step = (
            int(controller.last_goal_wakeup_step)
            if controller.last_goal_wakeup_id == record.goal_id
            else int(record.created_step)
        )
        return record.goal_id if int(step) - last_step >= interval else ""

    def _urgent_schedule(
        self,
        actor_name: str,
        step: int,
        scene_state: Any,
    ) -> str:
        if not scene_state:
            return ""
        for item in scene_state.get_scene_flag("upcoming_commitments", []):
            if not isinstance(item, dict):
                continue
            if item.get("status") in {"resolved", "missed", "cancelled"}:
                continue
            participants = {
                str(actor).strip()
                for actor in item.get("participants", [])
                if str(actor).strip()
            }
            if actor_name not in participants:
                continue
            try:
                due_step = int(item.get("due_step", 0))
                wake_before = max(
                    0, int(item.get("wake_before_steps", 1) or 0)
                )
            except (TypeError, ValueError):
                continue
            if int(step) >= due_step - wake_before:
                return str(item.get("commitment_id", "")).strip()
        return ""

    def _urgent_obligation(self, entity: Entity, step: int) -> str:
        state = entity.get_component("ObligationState")
        if not state or not hasattr(state, "next_wakeup"):
            return ""
        record = state.next_wakeup(step)
        return str(record.obligation_id) if record else ""

    def _urgent_agreement(
        self,
        entity: Entity,
        step: int,
        agreement_registry: Any,
        scene_state: Any,
    ) -> str:
        if not agreement_registry or not hasattr(agreement_registry, "next_wakeup"):
            return ""
        raw_horizon = (
            scene_state.get_scene_flag("agreement_wakeup_horizon", 1)
            if scene_state
            else 1
        )
        try:
            horizon = max(0, min(20, int(raw_horizon)))
        except (TypeError, ValueError):
            horizon = 1
        return str(agreement_registry.next_wakeup(entity.name, step, horizon) or "")

    def _urgent_obligation_conflict(
        self,
        entity: Entity,
        step: int,
        *,
        scene_state: Any = None,
    ) -> str:
        state = entity.get_component("ObligationState")
        if not state or not scene_state:
            return ""
        # Import lazily so the low-level agents package does not acquire the
        # environment -> systems -> agents cycle during module initialization.
        if self._obligation_conflicts is None:
            from src.story_engine.motivation.obligation_conflicts import (
                ObligationConflictAnalyzer,
            )

            self._obligation_conflicts = ObligationConflictAnalyzer()
        conflicts = self._obligation_conflicts.analyze(
            state,
            actor_name=entity.name,
            scene_state=scene_state,
            current_step=step,
        )
        if not conflicts:
            return ""
        raw_horizon = scene_state.get_scene_flag("obligation_conflict_horizon", 6)
        try:
            horizon = max(0, min(50, int(raw_horizon)))
        except (TypeError, ValueError):
            horizon = 6
        for conflict in conflicts:
            if conflict.get("severity") not in {"hard", "constrained"}:
                continue
            if int(conflict.get("steps_until_earliest_deadline", 10**9)) <= horizon:
                return str(conflict.get("conflict_id", ""))
        return ""

    def _is_scheduled(self, actor_name: str, step: int, interval: int) -> bool:
        # Python's hash is process-randomized. This stable offset keeps replay
        # and branch evaluation deterministic.
        offset = sum(ord(char) for char in str(actor_name)) % interval
        return int(step) % interval == offset
