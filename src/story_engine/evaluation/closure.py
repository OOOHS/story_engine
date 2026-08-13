from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable

from src.story_engine.motivation.needs import NeedDynamics


@dataclass(frozen=True)
class EpisodeClosurePolicy:
    """Host-owned conditions for ending an Episode, not the world simulation."""

    stable_steps: int = 2
    minimum_steps: int = 0
    require_goal_anchor: bool = False
    require_no_active_verifiable_goals: bool = True
    require_no_active_obligations: bool = True
    require_no_open_agreements: bool = True
    require_empty_action_queue: bool = True
    require_resolved_plots: bool = False
    require_no_active_agent_goals: bool = True
    require_no_active_navigation_problems: bool = True
    require_no_active_timeline_commitments: bool = True
    require_no_pending_world_events: bool = True
    require_no_pending_event_responses: bool = True
    require_all_autonomous_agents_exercised: bool = True
    require_no_actionable_critical_needs: bool = True
    require_stable_material_state: bool = True

    def normalized(self) -> "EpisodeClosurePolicy":
        return EpisodeClosurePolicy(
            stable_steps=max(1, int(self.stable_steps)),
            minimum_steps=max(0, int(self.minimum_steps)),
            require_goal_anchor=bool(self.require_goal_anchor),
            require_no_active_verifiable_goals=bool(
                self.require_no_active_verifiable_goals
            ),
            require_no_active_obligations=bool(
                self.require_no_active_obligations
            ),
            require_no_open_agreements=bool(self.require_no_open_agreements),
            require_empty_action_queue=bool(self.require_empty_action_queue),
            require_resolved_plots=bool(self.require_resolved_plots),
            require_no_active_agent_goals=bool(
                self.require_no_active_agent_goals
            ),
            require_no_active_navigation_problems=bool(
                self.require_no_active_navigation_problems
            ),
            require_no_active_timeline_commitments=bool(
                self.require_no_active_timeline_commitments
            ),
            require_no_pending_world_events=bool(
                self.require_no_pending_world_events
            ),
            require_no_pending_event_responses=bool(
                self.require_no_pending_event_responses
            ),
            require_all_autonomous_agents_exercised=bool(
                self.require_all_autonomous_agents_exercised
            ),
            require_no_actionable_critical_needs=bool(
                self.require_no_actionable_critical_needs
            ),
            require_stable_material_state=bool(
                self.require_stable_material_state
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self.normalized())


@dataclass(frozen=True)
class EpisodeClosureStatus:
    eligible: bool
    blockers: tuple[str, ...]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EpisodeClosureEvaluator:
    """Derive closure eligibility from authoritative ECS/registry state."""

    CLOSURE_MATERIAL_KINDS = frozenset({
        "scene",
        "plots",
        "relationships",
        "agreements",
        "obligations",
        "goals",
        "knowledge",
        "navigation",
        "claims",
        "world_events",
    })

    TERMINAL_OBLIGATION_STATUSES = {
        "fulfilled",
        "breached",
        "cancelled",
        "delegated",
    }

    def evaluate(
        self,
        session: Any,
        policy: EpisodeClosurePolicy,
        material_change_kinds: Iterable[str] = (),
    ) -> EpisodeClosureStatus:
        policy = policy.normalized()
        runner = session.runner
        blockers = []
        verifiable_goals = 0
        active_verifiable_goals = 0
        active_agent_goals = 0
        active_obligations = 0
        active_navigation_problems = 0
        active_timeline_commitments = 0
        dormant_navigation_problems = 0
        pending_world_events = 0
        pending_event_responses = 0
        dormant_pending_world_events = 0
        dormant_pending_event_responses = 0
        unexercised_autonomous_agents = 0
        actionable_critical_needs = 0
        unactionable_critical_needs = 0
        dormant_actionable_critical_needs = 0
        scene_state_for_needs = next(
            (
                entity.get_component("SceneState")
                for entity in runner.entities.values()
                if entity.get_component("SceneState") is not None
            ),
            None,
        )
        need_dynamics = NeedDynamics()
        current_material_changes = tuple(dict.fromkeys(
            str(item).strip()
            for item in material_change_kinds
            if str(item).strip() in self.CLOSURE_MATERIAL_KINDS
        ))
        seen_scene_states = set()
        for entity in runner.entities.values():
            goal_state = entity.get_component("GoalState")
            if goal_state is not None:
                for record in goal_state.goals.values():
                    if not (
                        record.completion_conditions or record.failure_conditions
                    ):
                        continue
                    verifiable_goals += 1
                    if record.status == "active":
                        active_verifiable_goals += 1
            if goal_state is not None:
                active_agent_goals += sum(
                    record.status == "active" and record.origin == "agent"
                    for record in goal_state.goals.values()
                )
            obligation_state = entity.get_component("ObligationState")
            if obligation_state is not None:
                active_obligations += sum(
                    record.status not in self.TERMINAL_OBLIGATION_STATUSES
                    for record in obligation_state.obligations.values()
                )
            scene_state = entity.get_component("SceneState")
            if scene_state is not None and id(scene_state) not in seen_scene_states:
                seen_scene_states.add(id(scene_state))
                commitments = scene_state.get_scene_flag(
                    "upcoming_commitments", []
                )
                if isinstance(commitments, list):
                    active_timeline_commitments += sum(
                        isinstance(record, dict)
                        and bool(str(record.get("commitment_id", "")).strip())
                        and str(record.get("status", "scheduled")).strip().lower()
                        not in {"resolved", "missed", "cancelled"}
                        for record in commitments
                    )
            cognition = entity.get_component("Cognition")
            controller = entity.get_component("AgentController")
            dormant = bool(
                controller
                and (
                    not controller.autonomous
                    or str(controller.activation_policy) == "dormant"
                )
            )
            if (
                controller is not None
                and controller.autonomous
                and str(controller.activation_policy) != "dormant"
                and int(controller.decision_count) == 0
            ):
                unexercised_autonomous_agents += 1
            drive = entity.get_component("DriveState")
            if drive is not None and controller is not None:
                opportunities = need_dynamics.build_opportunities(
                    scene_state_for_needs,
                    str(getattr(entity, "name", "") or ""),
                    drive,
                )
                for need, meter in drive.needs.items():
                    if meter.pressure < meter.critical_threshold:
                        continue
                    actionable = any(
                        item.get("available", False)
                        and float(
                            (item.get("need_effects", {}) or {}).get(
                                need, 0.0
                            ) or 0.0
                        ) < 0
                        for item in opportunities
                        if isinstance(item, dict)
                    )
                    if actionable and dormant:
                        dormant_actionable_critical_needs += 1
                    elif actionable:
                        actionable_critical_needs += 1
                    else:
                        unactionable_critical_needs += 1
            navigation = entity.get_component("NavigationState")
            if navigation is not None:
                navigation_count = sum(
                    problem.status == "active"
                    for problem in navigation.problems.values()
                )
                if dormant:
                    dormant_navigation_problems += navigation_count
                else:
                    active_navigation_problems += navigation_count
            if cognition is not None:
                world_count = len(
                    getattr(cognition, "pending_world_events", []) or []
                )
                response_count = len(
                    getattr(cognition, "pending_event_responses", []) or []
                )
                if dormant:
                    dormant_pending_world_events += world_count
                    dormant_pending_event_responses += response_count
                else:
                    pending_world_events += world_count
                    pending_event_responses += response_count
        if policy.require_goal_anchor and verifiable_goals == 0:
            blockers.append("no_verifiable_goal_anchor")
        if (
            policy.require_no_active_verifiable_goals
            and active_verifiable_goals
        ):
            blockers.append("active_verifiable_goals")
        if policy.require_no_active_agent_goals and active_agent_goals:
            blockers.append("active_agent_goals")
        if (
            policy.require_no_active_navigation_problems
            and active_navigation_problems
        ):
            blockers.append("active_navigation_problems")
        if (
            policy.require_no_active_timeline_commitments
            and active_timeline_commitments
        ):
            blockers.append("active_timeline_commitments")
        if policy.require_no_pending_world_events and pending_world_events:
            blockers.append("pending_world_events")
        if (
            policy.require_no_pending_event_responses
            and pending_event_responses
        ):
            blockers.append("pending_event_responses")
        if (
            policy.require_all_autonomous_agents_exercised
            and unexercised_autonomous_agents
        ):
            blockers.append("unexercised_autonomous_agents")
        if (
            policy.require_no_actionable_critical_needs
            and actionable_critical_needs
        ):
            blockers.append("actionable_critical_needs")
        if policy.require_no_active_obligations and active_obligations:
            blockers.append("active_obligations")
        if policy.require_stable_material_state and current_material_changes:
            blockers.append("material_state_changed")

        agreement_book = runner.agreement_registry.to_book()
        pending_agreements = 0
        pending_performance = 0
        for record in agreement_book.agreements.values():
            if record.status == "pending":
                pending_agreements += 1
            if record.status == "settled" and record.performance_status == "pending":
                pending_performance += 1
        if policy.require_no_open_agreements:
            if pending_agreements:
                blockers.append("pending_agreements")
            if pending_performance:
                blockers.append("pending_agreement_performance")

        queue_snapshot = runner.action_queue.snapshot()
        pending_actions = len(queue_snapshot.get("pending", []))
        if policy.require_empty_action_queue and pending_actions:
            blockers.append("pending_actions")

        unresolved_plots = 0
        for entity in runner.entities.values():
            plot_state = entity.get_component("PlotState")
            if plot_state is None:
                continue
            unresolved_plots += sum(
                int(plot.get("clock", 0) or 0)
                < int(plot.get("max_clock", 0) or 0)
                for plot in plot_state.plots.values()
            )
        if policy.require_resolved_plots and unresolved_plots:
            blockers.append("unresolved_plots")

        return EpisodeClosureStatus(
            eligible=not blockers,
            blockers=tuple(blockers),
            details={
                "verifiable_goal_count": verifiable_goals,
                "active_verifiable_goal_count": active_verifiable_goals,
                "active_agent_goal_count": active_agent_goals,
                "active_obligation_count": active_obligations,
                "active_navigation_problem_count": active_navigation_problems,
                "active_timeline_commitment_count": active_timeline_commitments,
                "dormant_navigation_problem_count": dormant_navigation_problems,
                "pending_world_event_count": pending_world_events,
                "pending_event_response_count": pending_event_responses,
                "dormant_pending_world_event_count": dormant_pending_world_events,
                "dormant_pending_event_response_count": dormant_pending_event_responses,
                "unexercised_autonomous_agent_count": unexercised_autonomous_agents,
                "actionable_critical_need_count": actionable_critical_needs,
                "unactionable_critical_need_count": unactionable_critical_needs,
                "dormant_actionable_critical_need_count": (
                    dormant_actionable_critical_needs
                ),
                "pending_agreement_count": pending_agreements,
                "pending_agreement_performance_count": pending_performance,
                "pending_action_count": pending_actions,
                "unresolved_plot_count": unresolved_plots,
                "material_change_kinds": list(current_material_changes),
            },
        )
