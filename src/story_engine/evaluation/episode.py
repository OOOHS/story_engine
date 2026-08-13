import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.story_engine.evaluation.closure import (
    EpisodeClosureEvaluator,
    EpisodeClosurePolicy,
    EpisodeClosureStatus,
)


@dataclass(frozen=True)
class PolicyDecisionAudit:
    actor: str
    mode: str
    selected_source: str = ""
    selected_action_kind: str = ""
    runtime_candidate_count: int = 0
    environment_candidate_count: int = 0
    runtime_action_kind_count: int = 0
    runtime_target_count: int = 0
    attention_motive_available_count: int = 0
    urgent_attention_motive_available_count: int = 0
    validated_motive_ref_count: int = 0
    rejected_motive_ref_count: int = 0
    validated_event_motive_ref_count: int = 0
    selected_validated_motive_ref_count: int = 0
    selected_event_motive_ref_count: int = 0
    continuity_supported: bool = False
    motivated: bool = False


@dataclass(frozen=True)
class EpisodeStepTrace:
    index: int
    simulation_time_before: int
    simulation_time_after: int
    world_hash_before: str
    world_hash_after: str
    character_hash_before: str
    character_hash_after: str
    proposal_actors: tuple[str, ...]
    resolved_actors: tuple[str, ...]
    action_kinds: tuple[str, ...]
    committed: bool
    relationship_count: int
    sentiment_count: int
    agreement_count: int
    modifier_count: int
    claim_count: int
    known_claim_count: int
    world_event_count: int = 0
    material_change_kinds: tuple[str, ...] = ()
    irreversible_changes: tuple[str, ...] = ()
    causal_handoffs: tuple[str, ...] = ()
    causal_rule_ids: tuple[str, ...] = ()
    goal_engaged_actors: tuple[str, ...] = ()
    sampled_policy_actors: tuple[str, ...] = ()
    goal_continuation_actors: tuple[str, ...] = ()
    goal_reactivation_actors: tuple[str, ...] = ()
    policy_selections: tuple[tuple[str, str], ...] = ()
    policy_audits: tuple[PolicyDecisionAudit, ...] = ()
    actor_actions: tuple[tuple[str, str, str], ...] = ()
    changed_subjects: tuple[str, ...] = ()
    closure_eligible: bool = False
    closure_blockers: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    narrative_text: str = ""


@dataclass(frozen=True)
class EpisodeReport:
    random_seed: int | str
    steps: tuple[EpisodeStepTrace, ...]
    metrics: Dict[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    requested_steps: int = 0
    closure_reached: bool = False
    termination_reason: str = "step_limit"
    closure_policy: Dict[str, Any] = field(default_factory=dict)
    final_closure_status: Dict[str, Any] = field(default_factory=dict)

    @property
    def authoritative(self) -> bool:
        return not self.violations

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target


class EpisodeRunner:
    """Runs and audits multi-turn emergence without judging prose quality."""

    _DERIVED_SCENE_FLAGS = {
        "world_version",
        "last_action_batch",
        "day_phase",
        "phase_turn",
        "phase_schedule",
        "upcoming_commitments",
        "quiet_turns_since_conflict",
        "last_missed_commitment",
        "last_player_visible_snapshot",
        "last_relation_deltas",
        "recent_conflict_template_ids",
        "consumed_storylets",
    }

    def run(
        self,
        session: Any,
        *,
        steps: int,
        step_inputs: Optional[
            Callable[[int, Any], Dict[str, Any] | None]
        ] = None,
        closure_policy: EpisodeClosurePolicy | None = None,
    ) -> EpisodeReport:
        traces: List[EpisodeStepTrace] = []
        requested_steps = max(0, int(steps))
        normalized_policy = closure_policy.normalized() if closure_policy else None
        closure_evaluator = EpisodeClosureEvaluator()
        closure_status: EpisodeClosureStatus | None = None
        closure_streak = 0
        closure_reached = False
        authoritative_failure = False
        for index in range(requested_steps):
            before = self._snapshot(session)
            kwargs = step_inputs(index, session) if step_inputs else {}
            context = session.run_step(**dict(kwargs or {}))
            if getattr(session, "delivery_pending", False):
                context = session.retry_delivery()
                context["episode_delivery_retry"] = True
            after = self._snapshot(session)
            violations = self._audit_step(session, context)
            intents = [
                item for item in context.get("intents", [])
                if isinstance(item, dict)
            ]
            actions = [
                item
                for item in context.get("simulation_result", {}).get(
                    "resolved_actions", []
                )
                if isinstance(item, dict)
            ]
            material_change_kinds = self._material_change_kinds(before, after)
            irreversible_changes = self._irreversible_changes(before, after)
            causal_handoffs = self._causal_handoffs(before, after)
            causal_handoffs.extend(
                self._policy_causal_handoffs(context, actions)
            )
            causal_handoffs = sorted(dict.fromkeys(causal_handoffs))
            goal_engaged_actors = self._goal_engaged_actors(context)
            sampled_policy_actors = self._sampled_policy_actors(context)
            goal_continuation_actors = sorted(
                str(actor)
                for actor, activation in (
                    context.get("agent_activations", {}) or {}
                ).items()
                if isinstance(activation, dict)
                and str(activation.get("reason", "")).startswith("agent_goal:")
            )
            goal_reactivation_actors = sorted(
                {
                    str(item.get("actor", ""))
                    for item in context.get("goal_reactivations", []) or []
                    if isinstance(item, dict) and str(item.get("actor", "")).strip()
                }
            )
            changed_subjects = self._changed_subjects(context)
            if normalized_policy is not None:
                closure_status = closure_evaluator.evaluate(
                    session,
                    normalized_policy,
                    material_change_kinds=material_change_kinds,
                )
                closure_streak = closure_streak + 1 if closure_status.eligible else 0
            traces.append(
                EpisodeStepTrace(
                    index=index,
                    simulation_time_before=before["simulation_time"],
                    simulation_time_after=after["simulation_time"],
                    world_hash_before=before["world_hash"],
                    world_hash_after=after["world_hash"],
                    character_hash_before=before["character_hash"],
                    character_hash_after=after["character_hash"],
                    proposal_actors=tuple(
                        str(item.get("actor", "")) for item in intents
                    ),
                    resolved_actors=tuple(
                        str(item.get("actor", "")) for item in actions
                    ),
                    action_kinds=tuple(
                        str(item.get("action_kind", "")) for item in actions
                    ),
                    committed=bool(
                        context.get("state_transaction", {}).get("committed")
                    ),
                    relationship_count=after["relationship_count"],
                    sentiment_count=after["sentiment_count"],
                    agreement_count=after["agreement_count"],
                    modifier_count=after["modifier_count"],
                    claim_count=after["claim_count"],
                    known_claim_count=after["known_claim_count"],
                    world_event_count=after["world_event_count"],
                    material_change_kinds=tuple(material_change_kinds),
                    irreversible_changes=tuple(irreversible_changes),
                    causal_handoffs=tuple(causal_handoffs),
                    causal_rule_ids=tuple(
                        str(rule_id)
                        for rule_id in context.get("simulation_result", {}).get(
                            "causal_plot_rules", []
                        )
                        if str(rule_id).strip()
                    ),
                    goal_engaged_actors=tuple(goal_engaged_actors),
                    sampled_policy_actors=tuple(sampled_policy_actors),
                    goal_continuation_actors=tuple(goal_continuation_actors),
                    goal_reactivation_actors=tuple(goal_reactivation_actors),
                    policy_selections=tuple(
                        sorted(
                            (
                                str(actor),
                                str(trace.get("selected_candidate_id", "")),
                            )
                            for actor, trace in (
                                context.get("policy_traces", {}) or {}
                            ).items()
                            if isinstance(trace, dict)
                        )
                    ),
                    policy_audits=tuple(self._policy_audits(context)),
                    actor_actions=tuple(
                        (
                            str(item.get("actor", "")),
                            str(item.get("action_kind", "")),
                            str(item.get("action_target", "")),
                        )
                        for item in actions
                    ),
                    changed_subjects=tuple(changed_subjects),
                    closure_eligible=bool(
                        closure_status and closure_status.eligible
                    ),
                    closure_blockers=(
                        tuple(closure_status.blockers) if closure_status else ()
                    ),
                    violations=tuple(violations),
                    narrative_text=self._bounded_narrative(
                        context.get("rendered_text", "")
                    ),
                )
            )
            if context.get("authoritative_step_failed"):
                authoritative_failure = True
                break
            if (
                normalized_policy is not None
                and len(traces) >= normalized_policy.minimum_steps
                and closure_streak >= normalized_policy.stable_steps
            ):
                closure_reached = True
                break
        if normalized_policy is not None and closure_status is None:
            closure_status = closure_evaluator.evaluate(session, normalized_policy)
        return self._report(
            session,
            traces,
            requested_steps=requested_steps,
            closure_policy=normalized_policy,
            closure_status=closure_status,
            closure_reached=closure_reached,
            authoritative_failure=authoritative_failure,
        )

    def _report(
        self,
        session: Any,
        traces: List[EpisodeStepTrace],
        *,
        requested_steps: int | None = None,
        closure_policy: EpisodeClosurePolicy | None = None,
        closure_status: EpisodeClosureStatus | None = None,
        closure_reached: bool = False,
        authoritative_failure: bool = False,
    ) -> EpisodeReport:
        proposal_actors = {
            actor
            for trace in traces
            for actor in trace.proposal_actors
            if actor and actor != "World"
        }
        resolved_actors = {
            actor
            for trace in traces
            for actor in trace.resolved_actors
            if actor and actor != "World"
        }
        action_kinds = {
            kind for trace in traces for kind in trace.action_kinds if kind
        }
        world_change_steps = sum(bool(trace.material_change_kinds) for trace in traces)
        character_change_steps = sum(
            trace.character_hash_before != trace.character_hash_after
            for trace in traces
        )
        irreversible_change_steps = sum(
            bool(trace.irreversible_changes) for trace in traces
        )
        causal_transition_steps = sum(bool(trace.causal_rule_ids) for trace in traces)
        goal_engagement_steps = sum(bool(trace.goal_engaged_actors) for trace in traces)
        goal_continuation_steps = sum(
            bool(trace.goal_continuation_actors) for trace in traces
        )
        goal_continuation_actors = {
            actor
            for trace in traces
            for actor in trace.goal_continuation_actors
        }
        goal_reactivation_steps = sum(
            bool(trace.goal_reactivation_actors) for trace in traces
        )
        goal_reactivation_actors = {
            actor
            for trace in traces
            for actor in trace.goal_reactivation_actors
        }
        interaction_chain_steps, longest_interaction_chain = self._interaction_chains(
            traces
        )
        actor_differentiation = self._actor_differentiation(traces)
        commitment_resolutions = self._commitment_resolutions(traces)
        goal_resolutions = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("goal_resolved:")
        ]
        goal_adoptions = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("goal_adopted:")
        ]
        goal_refinements = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("goal_refined:")
        ]
        agreement_creations = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("agreement_created:")
        ]
        obligation_creations = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("obligation_created:")
        ]
        claim_knowledge_changes = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("claim_knowledge_")
        ]
        modifier_changes = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith(("modifier_created:", "modifier_updated:"))
        ]
        world_event_creations = [
            change
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith("world_event_created:")
        ]
        causal_handoffs = [
            handoff
            for trace in traces
            for handoff in trace.causal_handoffs
        ]
        causal_handoff_steps = sum(bool(trace.causal_handoffs) for trace in traces)
        max_causal_chain_depth = self._causal_chain_depth(causal_handoffs)
        temporal_causality = self._temporal_causal_metrics(traces)
        policy_motive_handoffs = [
            item
            for item in causal_handoffs
            if item.startswith("resolved_action:")
            and "<-" in item
            and item.split("<-", 1)[1].startswith(
                (
                    "goal:",
                    "sentiment:",
                    "relationship_track:",
                    "obligation:",
                    "navigation_problem:",
                    "action_failure:",
                    "claim_knowledge:",
                    "agreement:",
                    "world_event:",
                    "event_response:",
                    "modifier:",
                    "drive_need:",
                    "timeline_commitment:",
                )
            )
        ]
        committed_steps = sum(trace.committed for trace in traces)
        narrative_texts = [
            trace.narrative_text for trace in traces if trace.narrative_text
        ]
        normalized_narrative_list = [
            " ".join(text.split()).casefold()
            for text in narrative_texts
            if " ".join(text.split()).strip()
        ]
        normalized_narratives = set(normalized_narrative_list)
        max_narrative_repetition = self._max_normalized_repetition(
            narrative_texts
        )
        violations = tuple(
            violation
            for trace in traces
            for violation in trace.violations
        )
        longest_repetition = self._longest_repetition(
            [self._action_batch_signature(trace) for trace in traces]
        )
        material_stability_blocked_steps = sum(
            "material_state_changed" in trace.closure_blockers
            for trace in traces
        )
        actionable_need_blocked_steps = sum(
            "actionable_critical_needs" in trace.closure_blockers
            for trace in traces
        )
        final = self._snapshot(session)
        flags = []
        if traces and world_change_steps == 0:
            flags.append("stagnant_episode")
        if len(proposal_actors) > 1 and len(resolved_actors) <= 1:
            flags.append("single_actor_monopoly")
        if len(traces) >= 4 and longest_repetition >= 4:
            flags.append("repetitive_actions")
        if final["max_repeated_policy_action_count"] >= 4:
            flags.append("repetitive_policy_choices")
        if traces and committed_steps * 2 < len(traces):
            flags.append("mostly_rejected_transactions")
        if committed_steps and not narrative_texts:
            flags.append("missing_narrative_output")
        if max_narrative_repetition >= 4:
            flags.append("repetitive_narration")
        if (
            len(resolved_actors) > 1
            and final["relationship_count"] == 0
            and final["sentiment_count"] == 0
        ):
            flags.append("no_social_state_growth")
        if self._deadlocked(traces):
            flags.append("deadlocked_episode")
        if (
            len(resolved_actors) > 1
            and all(
                sum(actor == name for trace in traces for actor, _, _ in trace.actor_actions)
                >= 3
                for name in resolved_actors
            )
            and actor_differentiation < 0.15
        ):
            flags.append("undifferentiated_actor_behavior")
        if closure_policy is not None and not closure_reached:
            flags.append("unclosed_episode")
            if (
                traces
                and "material_state_changed" in traces[-1].closure_blockers
            ):
                flags.append("materially_active_at_step_limit")
            if (
                traces
                and "actionable_critical_needs" in traces[-1].closure_blockers
            ):
                flags.append("actionable_critical_needs_at_step_limit")
        if goal_adoptions and sum(
            "<-" in item and item.startswith("goal:")
            for item in causal_handoffs
        ) < len(goal_adoptions):
            flags.append("unattributed_agent_goal")
        if agreement_creations and sum(
            "<-" in item and item.startswith("agreement:")
            for item in causal_handoffs
        ) < len(agreement_creations):
            flags.append("unattributed_agreement")
        if obligation_creations and sum(
            "<-" in item and item.startswith("obligation:")
            for item in causal_handoffs
        ) < len(obligation_creations):
            flags.append("unattributed_obligation")
        if claim_knowledge_changes and sum(
            "<-" in item and item.startswith("claim_knowledge:")
            for item in causal_handoffs
        ) < len(claim_knowledge_changes):
            flags.append("unattributed_claim_knowledge")
        if modifier_changes and sum(
            "<-" in item and item.startswith("modifier:")
            for item in causal_handoffs
        ) < len(modifier_changes):
            flags.append("unattributed_modifier")
        if world_event_creations and sum(
            "<-" in item and item.startswith("world_event:")
            for item in causal_handoffs
        ) < len(world_event_creations):
            flags.append("unattributed_world_event")
        sampled_policy_steps = sum(
            bool(trace.sampled_policy_actors) for trace in traces
        )
        policy_audits = [
            audit for trace in traces for audit in trace.policy_audits
        ]
        sampled_policy_audits = [
            audit for audit in policy_audits if audit.mode == "host_sampled"
        ]
        runtime_candidate_counts = [
            audit.runtime_candidate_count for audit in sampled_policy_audits
        ]
        attention_motive_opportunity_audits = [
            audit
            for audit in sampled_policy_audits
            if audit.attention_motive_available_count > 0
        ]
        urgent_attention_motive_opportunity_audits = [
            audit
            for audit in sampled_policy_audits
            if audit.urgent_attention_motive_available_count > 0
        ]
        event_motive_reference_audits = [
            audit
            for audit in sampled_policy_audits
            if audit.validated_event_motive_ref_count > 0
        ]
        selected_event_motive_audits = [
            audit
            for audit in sampled_policy_audits
            if audit.selected_event_motive_ref_count > 0
        ]
        return EpisodeReport(
            random_seed=session.random_seed,
            steps=tuple(traces),
            metrics={
                "step_count": len(traces),
                "requested_step_count": (
                    len(traces) if requested_steps is None else requested_steps
                ),
                "closure_reached": closure_reached,
                "steps_to_closure": len(traces) if closure_reached else None,
                "committed_steps": committed_steps,
                "narrative_step_count": len(narrative_texts),
                "narrative_character_count": sum(
                    len(text) for text in narrative_texts
                ),
                "unique_narrative_step_count": len(normalized_narratives),
                "max_narrative_repetition": max_narrative_repetition,
                "narrative_repetition_rate": (
                    round(
                        1.0 - len(normalized_narratives) / len(narrative_texts),
                        6,
                    )
                    if narrative_texts
                    else None
                ),
                "proposal_actor_count": len(proposal_actors),
                "resolved_actor_count": len(resolved_actors),
                "action_kind_count": len(action_kinds),
                "world_change_steps": world_change_steps,
                "material_stability_blocked_steps": (
                    material_stability_blocked_steps
                ),
                "actionable_critical_need_blocked_steps": (
                    actionable_need_blocked_steps
                ),
                "terminal_actionable_critical_need_count": (
                    int(
                        closure_status.details.get(
                            "actionable_critical_need_count", 0
                        ) or 0
                    )
                    if closure_status is not None
                    else 0
                ),
                "terminal_material_change_count": (
                    len(closure_status.details.get("material_change_kinds", []))
                    if closure_status is not None
                    else 0
                ),
                "character_change_steps": character_change_steps,
                "irreversible_change_steps": irreversible_change_steps,
                "irreversible_change_count": sum(
                    len(trace.irreversible_changes) for trace in traces
                ),
                "causal_transition_steps": causal_transition_steps,
                "causal_handoff_steps": causal_handoff_steps,
                "causal_handoff_count": len(causal_handoffs),
                "policy_motive_handoff_count": len(policy_motive_handoffs),
                "policy_motivated_action_count": len({
                    item.split("<-", 1)[0]
                    for item in policy_motive_handoffs
                }),
                "causal_consequence_node_count": len({
                    item.split("<-", 1)[0]
                    for item in causal_handoffs
                    if "<-" in item
                }),
                "max_causal_chain_depth": max_causal_chain_depth,
                "cross_step_causal_handoff_count": temporal_causality[
                    "cross_step_handoff_count"
                ],
                "cross_step_causal_step_count": temporal_causality[
                    "cross_step_count"
                ],
                "max_causal_span_steps": temporal_causality[
                    "max_span_steps"
                ],
                "causal_arc_present": bool(
                    temporal_causality["max_span_steps"] >= 2
                ),
                "resolved_causal_arc": bool(
                    closure_reached
                    and temporal_causality["max_span_steps"] >= 2
                    and irreversible_change_steps
                ),
                "causal_source_kind_count": len({
                    item.split("<-", 1)[1].split(":", 1)[0]
                    for item in causal_handoffs
                    if "<-" in item
                }),
                "interaction_chain_steps": interaction_chain_steps,
                "longest_interaction_chain": longest_interaction_chain,
                "goal_engagement_steps": goal_engagement_steps,
                "goal_continuation_steps": goal_continuation_steps,
                "goal_continuation_actor_count": len(goal_continuation_actors),
                "goal_reactivation_steps": goal_reactivation_steps,
                "goal_reactivation_actor_count": len(goal_reactivation_actors),
                "goal_continuation_attempt_count": final[
                    "goal_continuation_attempt_count"
                ],
                "max_repeated_goal_action_count": final[
                    "max_repeated_goal_action_count"
                ],
                "max_repeated_policy_action_count": final[
                    "max_repeated_policy_action_count"
                ],
                "goal_reactivation_count": final["goal_reactivation_count"],
                "sampled_policy_steps": sampled_policy_steps,
                "policy_decision_count": len(policy_audits),
                "sampled_policy_decision_count": len(sampled_policy_audits),
                "runtime_candidate_count": sum(runtime_candidate_counts),
                "minimum_runtime_candidate_count": (
                    min(runtime_candidate_counts)
                    if runtime_candidate_counts
                    else None
                ),
                "maximum_runtime_candidate_count": (
                    max(runtime_candidate_counts)
                    if runtime_candidate_counts
                    else None
                ),
                "mean_runtime_candidate_count": (
                    round(
                        sum(runtime_candidate_counts)
                        / len(runtime_candidate_counts),
                        6,
                    )
                    if runtime_candidate_counts
                    else None
                ),
                "selected_runtime_candidate_count": sum(
                    audit.selected_source == "runtime"
                    for audit in sampled_policy_audits
                ),
                "continuity_supported_selection_count": sum(
                    audit.continuity_supported for audit in sampled_policy_audits
                ),
                "motivated_selection_count": sum(
                    audit.motivated for audit in sampled_policy_audits
                ),
                "attention_motive_available_decision_count": len(
                    attention_motive_opportunity_audits
                ),
                "urgent_attention_motive_available_decision_count": len(
                    urgent_attention_motive_opportunity_audits
                ),
                "validated_candidate_motive_ref_count": sum(
                    audit.validated_motive_ref_count
                    for audit in sampled_policy_audits
                ),
                "rejected_candidate_motive_ref_count": sum(
                    audit.rejected_motive_ref_count
                    for audit in sampled_policy_audits
                ),
                "validated_event_motive_ref_count": sum(
                    audit.validated_event_motive_ref_count
                    for audit in sampled_policy_audits
                ),
                "selected_candidate_motive_ref_count": sum(
                    audit.selected_validated_motive_ref_count
                    for audit in sampled_policy_audits
                ),
                "selected_event_motive_ref_count": sum(
                    audit.selected_event_motive_ref_count
                    for audit in sampled_policy_audits
                ),
                "event_motive_reference_decision_count": len(
                    event_motive_reference_audits
                ),
                "event_motive_selected_decision_count": len(
                    selected_event_motive_audits
                ),
                "event_motive_reference_rate": (
                    round(
                        len(event_motive_reference_audits)
                        / len(attention_motive_opportunity_audits),
                        6,
                    )
                    if attention_motive_opportunity_audits
                    else None
                ),
                "event_motive_selection_rate": (
                    round(
                        len(selected_event_motive_audits)
                        / len(attention_motive_opportunity_audits),
                        6,
                    )
                    if attention_motive_opportunity_audits
                    else None
                ),
                "goal_engagement_rate": round(
                    goal_engagement_steps / sampled_policy_steps, 6
                )
                if sampled_policy_steps
                else None,
                "actor_differentiation": round(actor_differentiation, 6),
                "commitment_resolution_count": commitment_resolutions,
                "agreement_creation_count": len(agreement_creations),
                "obligation_creation_count": len(obligation_creations),
                "claim_knowledge_change_count": len(claim_knowledge_changes),
                "modifier_change_count": len(modifier_changes),
                "drive_need_cause_count": sum(
                    item.startswith("drive_need:") and "<-" in item
                    for item in causal_handoffs
                ),
                "goal_resolution_count": len(goal_resolutions),
                "agent_goal_adoption_count": len(goal_adoptions),
                "agent_goal_refinement_count": len(goal_refinements),
                "goal_achievement_count": sum(
                    change.endswith(":achieved") for change in goal_resolutions
                ),
                "goal_failure_count": sum(
                    change.endswith(":failed") for change in goal_resolutions
                ),
                "active_goal_count": final["active_goal_count"],
                "verifiable_goal_count": final["verifiable_goal_count"],
                "active_verifiable_goal_count": final[
                    "active_verifiable_goal_count"
                ],
                "active_agent_goal_count": final["active_agent_goal_count"],
                "active_open_agent_goal_count": final[
                    "active_open_agent_goal_count"
                ],
                "relationship_count": final["relationship_count"],
                "sentiment_count": final["sentiment_count"],
                "agreement_count": final["agreement_count"],
                "modifier_count": final["modifier_count"],
                "claim_count": final["claim_count"],
                "known_claim_count": final["known_claim_count"],
                "world_event_count": final["world_event_count"],
                "world_event_creation_count": len(world_event_creations),
                "longest_action_repetition": longest_repetition,
            },
            quality_flags=tuple(flags),
            violations=violations,
            requested_steps=(
                len(traces) if requested_steps is None else requested_steps
            ),
            closure_reached=closure_reached,
            termination_reason=(
                "authoritative_failure"
                if authoritative_failure
                else "closure_reached" if closure_reached else "step_limit"
            ),
            closure_policy=(closure_policy.to_dict() if closure_policy else {}),
            final_closure_status=(
                closure_status.to_dict() if closure_status else {}
            ),
        )

    @staticmethod
    def _bounded_narrative(value: Any) -> str:
        text = str(value or "").replace("\x00", "").strip()
        return text[:12_000]

    @staticmethod
    def _max_normalized_repetition(values: Iterable[Any]) -> int:
        normalized = [
            " ".join(str(value or "").split()).casefold()
            for value in values
            if " ".join(str(value or "").split()).strip()
        ]
        return max(Counter(normalized).values(), default=0)

    def _audit_step(self, session: Any, context: Dict[str, Any]) -> List[str]:
        violations = []
        if context.get("step_aborted"):
            violations.append(
                f"step_aborted:{context.get('step_abort_reason', 'unknown')}"
            )
        if context.get("authoritative_step_failed"):
            violations.append("authoritative_step_failed")
        if context.get("step_failed") and context.get("step_committed"):
            violations.append("delivery_step_failed")
        if context.get("delivery_pending"):
            violations.append("delivery_retry_pending")
        intent_actors = {
            str(item.get("actor", "")).strip()
            for item in context.get("intents", [])
            if isinstance(item, dict) and str(item.get("actor", "")).strip()
        }
        result = context.get("simulation_result", {})
        for action in result.get("resolved_actions", []) if isinstance(result, dict) else []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            if actor and actor != "World" and actor not in intent_actors:
                violations.append(f"resolved_actor_without_proposal:{actor}")
        if result.get("uncertain_outcomes"):
            violations.append("unresolved_uncertain_outcomes_reached_episode_boundary")
        visible = context.get("visible_simulation_result", {})
        for action in visible.get("resolved_actions", []) if isinstance(visible, dict) else []:
            if isinstance(action, dict) and action.get("private_result"):
                violations.append("private_result_leaked_to_visible_simulation")
        if not context.get("state_transaction", {}).get("committed") and result.get(
            "resolved_actions"
        ):
            violations.append("rejected_transaction_retained_resolved_actions")
        transaction = context.get("state_transaction", {})
        if transaction.get("committed") and transaction.get("errors"):
            violations.append("committed_transaction_retained_errors")
        for actor in context.get("agent_registration_errors", []) or []:
            violations.append(f"missing_agent_runtime:{actor}")
        for error in context.get("outcome_check_errors", []) or []:
            violations.append(f"outcome_check_error:{error}")
        for error in context.get("sentiment_errors", []) or []:
            violations.append(f"sentiment_error:{error}")
        for error in context.get("goal_errors", []) or []:
            violations.append(f"goal_error:{error}")
        for error in context.get("modifier_errors", []) or []:
            violations.append(f"modifier_error:{error}")
        for error in context.get("claim_errors", []) or []:
            violations.append(f"claim_error:{error}")
        for error in context.get("claim_knowledge_errors", []) or []:
            violations.append(f"claim_knowledge_error:{error}")
        for error in context.get("world_event_errors", []) or []:
            violations.append(f"world_event_error:{error}")
        policy_traces = context.get("policy_traces", {})
        for intent in context.get("intents", []) or []:
            if not isinstance(intent, dict):
                continue
            actor = str(intent.get("actor", "")).strip()
            candidate_id = str(intent.get("policy_candidate_id", "")).strip()
            if not actor or not candidate_id:
                continue
            if actor not in policy_traces:
                # A long-running action may complete from an earlier proposal
                # while the actor is busy and therefore has no new policy trace.
                continue
            selected = str(
                policy_traces.get(actor, {}).get("selected_candidate_id", "")
            ).strip()
            if selected != candidate_id:
                violations.append(f"policy_trace_mismatch:{actor}")
        registry = session.runner.relation_registry
        agent_registry = session.runner.agent_registry
        scene = next(
            (
                entity.get_component("SceneState")
                for entity in session.runner.entities.values()
                if entity.get_component("SceneState") is not None
            ),
            None,
        )
        for actor_name in sorted(scene.actor_states if scene else {}):
            actor_entity = session.runner.entities.get(actor_name)
            if actor_entity is None:
                violations.append(f"scene_actor_without_entity:{actor_name}")
                continue
            if actor_entity.get_component("AgentController") is None:
                violations.append(
                    f"scene_actor_without_agent_controller:{actor_name}"
                )
                continue
            if not agent_registry.is_registered(actor_entity):
                violations.append(f"scene_actor_without_runtime:{actor_name}")
        for entity in registry.entities():
            if agent_registry.is_registered(entity):
                violations.append(f"relation_entity_registered_as_agent:{entity.name}")
        for entity in session.runner.claim_registry.entities():
            if agent_registry.is_registered(entity):
                violations.append(f"claim_entity_registered_as_agent:{entity.name}")
        return violations

    def _snapshot(self, session: Any) -> Dict[str, Any]:
        runner = session.runner
        scene = next(
            (
                entity.get_component("SceneState")
                for entity in runner.entities.values()
                if entity.get_component("SceneState") is not None
            ),
            None,
        )
        plots = next(
            (
                entity.get_component("PlotState")
                for entity in runner.entities.values()
                if entity.get_component("PlotState") is not None
            ),
            None,
        )
        relationship_book = runner.relation_registry.to_relationship_book()
        agreement_book = runner.agreement_registry.to_book()
        characters = {}
        sentiment_count = 0
        active_goal_count = 0
        verifiable_goal_count = 0
        active_verifiable_goal_count = 0
        active_agent_goal_count = 0
        active_open_agent_goal_count = 0
        modifier_count = 0
        known_claim_count = 0
        goal_continuation_attempt_count = 0
        max_repeated_goal_action_count = 0
        max_repeated_policy_action_count = 0
        goal_reactivation_count = 0
        world_events = {}
        for name, entity in sorted(runner.entities.items()):
            world_event = entity.get_component("WorldEventFact")
            if world_event is not None:
                witnesses = entity.get_component("WorldEventWitnesses")
                responses = entity.get_component("WorldEventResponses")
                world_events[world_event.event_id] = {
                    "fact": world_event.model_dump(mode="json"),
                    "witnesses": (
                        witnesses.model_dump(mode="json")
                        if witnesses is not None
                        else {}
                    ),
                    "responses": (
                        responses.model_dump(mode="json")
                        if responses is not None
                        else {}
                    ),
                }
            if entity.get_component("AgentController") is None:
                continue
            controller = entity.get_component("AgentController")
            goal_continuation_attempt_count += int(
                controller.goal_continuation_attempts
            )
            max_repeated_goal_action_count = max(
                max_repeated_goal_action_count,
                int(controller.repeated_goal_action_count),
            )
            max_repeated_policy_action_count = max(
                max_repeated_policy_action_count,
                int(controller.max_repeated_policy_action_count),
            )
            goal_reactivation_count += int(controller.goal_reactivation_count)
            components = {}
            for component_name in (
                "DriveState",
                "ObligationState",
                "SentimentState",
                "GoalState",
                "ModifierState",
                "KnowledgeState",
                "NavigationState",
                "Cognition",
                "Planning",
            ):
                component = entity.get_component(component_name)
                if component is not None:
                    components[component_name] = component.model_dump(mode="json")
            sentiment = entity.get_component("SentimentState")
            sentiment_count += len(sentiment.sentiments) if sentiment else 0
            goal_state = entity.get_component("GoalState")
            active_goal_count += (
                len(goal_state.active_records())
                if goal_state is not None and hasattr(goal_state, "active_records")
                else 0
            )
            if goal_state is not None:
                for record in goal_state.goals.values():
                    if record.status == "active" and record.origin == "agent":
                        active_agent_goal_count += 1
                        if not (
                            record.completion_conditions
                            or record.failure_conditions
                        ):
                            active_open_agent_goal_count += 1
                    if not (
                        record.completion_conditions or record.failure_conditions
                    ):
                        continue
                    verifiable_goal_count += 1
                    if record.status == "active":
                        active_verifiable_goal_count += 1
            modifier_state = entity.get_component("ModifierState")
            modifier_count += (
                len(modifier_state.modifiers) if modifier_state is not None else 0
            )
            knowledge_state = entity.get_component("KnowledgeState")
            known_claim_count += (
                len(knowledge_state.claims) if knowledge_state is not None else 0
            )
            characters[name] = components
        world_payload = {
            "scene": scene.get_snapshot() if scene else {},
            "plots": plots.get_snapshot() if plots else {},
            "relationships": relationship_book.model_dump(mode="json"),
            "agreements": agreement_book.model_dump(mode="json"),
            "world_events": world_events,
        }
        scene_snapshot = scene.get_snapshot() if scene else {}
        material_scene = deepcopy_json(scene_snapshot)
        for flag in self._DERIVED_SCENE_FLAGS:
            material_scene.get("scene_flags", {}).pop(flag, None)
        obligations = {}
        drives = {}
        cognitions = {}
        sentiments = {}
        goals = {}
        modifiers = {}
        knowledge = {}
        navigation = {}
        for name, entity in sorted(runner.entities.items()):
            if entity.get_component("AgentController") is None:
                continue
            obligation = entity.get_component("ObligationState")
            drive = entity.get_component("DriveState")
            cognition = entity.get_component("Cognition")
            sentiment = entity.get_component("SentimentState")
            goal_state = entity.get_component("GoalState")
            modifier_state = entity.get_component("ModifierState")
            knowledge_state = entity.get_component("KnowledgeState")
            navigation_state = entity.get_component("NavigationState")
            obligations[name] = (
                obligation.model_dump(mode="json") if obligation is not None else {}
            )
            drive_payload = drive.model_dump(mode="json") if drive is not None else {}
            drive_payload.pop("last_advanced_step", None)
            drives[name] = drive_payload
            cognition_payload = (
                cognition.model_dump(mode="json") if cognition is not None else {}
            )
            cognition_payload.pop("experiences", None)
            cognitions[name] = cognition_payload
            sentiments[name] = (
                sentiment.model_dump(mode="json") if sentiment is not None else {}
            )
            goals[name] = (
                goal_state.model_dump(mode="json") if goal_state is not None else {}
            )
            modifiers[name] = (
                modifier_state.model_dump(mode="json")
                if modifier_state is not None
                else {}
            )
            knowledge[name] = (
                knowledge_state.model_dump(mode="json")
                if knowledge_state is not None
                else {}
            )
            navigation[name] = (
                navigation_state.model_dump(mode="json")
                if navigation_state is not None
                else {}
            )
        claim_catalog = runner.claim_registry.gm_catalog()
        material_parts = {
            "scene": material_scene,
            "plots": plots.get_snapshot() if plots else {},
            "relationships": relationship_book.model_dump(mode="json"),
            "agreements": agreement_book.model_dump(mode="json"),
            "obligations": obligations,
            "drives": drives,
            "cognitions": cognitions,
            "sentiments": sentiments,
            "goals": goals,
            "modifiers": modifiers,
            "knowledge": knowledge,
            "navigation": navigation,
            "claims": claim_catalog,
            "world_events": world_events,
        }
        return {
            "simulation_time": session.simulation_time,
            "world_hash": self._hash(world_payload),
            "character_hash": self._hash(characters),
            "relationship_count": len(relationship_book.relationships),
            "sentiment_count": sentiment_count,
            "agreement_count": len(agreement_book.agreements),
            "active_goal_count": active_goal_count,
            "verifiable_goal_count": verifiable_goal_count,
            "active_verifiable_goal_count": active_verifiable_goal_count,
            "active_agent_goal_count": active_agent_goal_count,
            "active_open_agent_goal_count": active_open_agent_goal_count,
            "modifier_count": modifier_count,
            "claim_count": len(claim_catalog),
            "known_claim_count": known_claim_count,
            "goal_continuation_attempt_count": goal_continuation_attempt_count,
            "max_repeated_goal_action_count": max_repeated_goal_action_count,
            "max_repeated_policy_action_count": (
                max_repeated_policy_action_count
            ),
            "goal_reactivation_count": goal_reactivation_count,
            "world_event_count": len(world_events),
            "material_parts": material_parts,
        }

    @staticmethod
    def _material_change_kinds(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        before_parts = before.get("material_parts", {})
        after_parts = after.get("material_parts", {})
        return [
            name
            for name in (
                "scene",
                "plots",
                "relationships",
                "agreements",
                "obligations",
                "drives",
                "cognitions",
                "sentiments",
                "goals",
                "modifiers",
                "knowledge",
                "navigation",
                "claims",
                "world_events",
            )
            if before_parts.get(name) != after_parts.get(name)
        ]

    @staticmethod
    def _irreversible_changes(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        changes: List[str] = []
        before_parts = before.get("material_parts", {})
        after_parts = after.get("material_parts", {})
        before_scene = before_parts.get("scene", {})
        after_scene = after_parts.get("scene", {})
        before_objects = before_scene.get("world_objects", {})
        after_objects = after_scene.get("world_objects", {})
        for name in sorted(set(after_objects) - set(before_objects)):
            changes.append(f"object_created:{name}")
        for name in sorted(set(before_objects) - set(after_objects)):
            changes.append(f"object_removed:{name}")
        before_actors = before_scene.get("actor_states", {})
        after_actors = after_scene.get("actor_states", {})
        for name in sorted(set(after_actors) - set(before_actors)):
            changes.append(f"actor_added:{name}")
        for name in sorted(set(before_actors) - set(after_actors)):
            changes.append(f"actor_removed:{name}")
        before_events = before_parts.get("world_events", {})
        after_events = after_parts.get("world_events", {})
        for event_id in sorted(set(after_events) - set(before_events)):
            changes.append(f"world_event_created:{event_id}")
        for name in sorted(set(before_objects).intersection(after_objects)):
            old = before_objects.get(name, {})
            new = after_objects.get(name, {})
            for field_name in ("owner", "container"):
                if old.get(field_name) != new.get(field_name):
                    changes.append(f"object_{field_name}_changed:{name}")

        before_plots = before_parts.get("plots", {})
        after_plots = after_parts.get("plots", {})
        for plot_id in sorted(set(before_plots).intersection(after_plots)):
            old = before_plots[plot_id]
            new = after_plots[plot_id]
            if int(new.get("current_stage", 0) or 0) != int(
                old.get("current_stage", 0) or 0
            ):
                changes.append(f"plot_stage_changed:{plot_id}")
            old_clock = int(old.get("clock", 0) or 0)
            new_clock = int(new.get("clock", 0) or 0)
            max_clock = int(new.get("max_clock", 0) or 0)
            if new_clock >= max_clock > old_clock:
                changes.append(f"plot_completed:{plot_id}")

        before_agreements = before_parts.get("agreements", {}).get("agreements", {})
        after_agreements = after_parts.get("agreements", {}).get("agreements", {})
        terminal_agreements = {
            "settled",
            "rejected",
            "withdrawn",
            "expired",
            "countered",
        }
        for agreement_id, record in after_agreements.items():
            if agreement_id not in before_agreements:
                changes.append(f"agreement_created:{agreement_id}")
            old_status = str(before_agreements.get(agreement_id, {}).get("status", ""))
            new_status = str(record.get("status", ""))
            if new_status in terminal_agreements and old_status != new_status:
                changes.append(f"agreement_resolved:{agreement_id}:{new_status}")
            old_performance = str(
                before_agreements.get(agreement_id, {}).get("performance_status", "")
            )
            new_performance = str(record.get("performance_status", ""))
            if new_performance in {"fulfilled", "breached", "cancelled"} and (
                old_performance != new_performance
            ):
                changes.append(
                    f"agreement_performance_resolved:{agreement_id}:{new_performance}"
                )

        before_obligations = before_parts.get("obligations", {})
        after_obligations = after_parts.get("obligations", {})
        terminal_obligations = {"fulfilled", "breached", "cancelled", "delegated"}
        for actor, state in after_obligations.items():
            records = state.get("obligations", {})
            old_records = before_obligations.get(actor, {}).get("obligations", {})
            for obligation_id, record in records.items():
                if obligation_id not in old_records:
                    changes.append(f"obligation_created:{actor}:{obligation_id}")
                old_status = str(old_records.get(obligation_id, {}).get("status", ""))
                new_status = str(record.get("status", ""))
                if new_status in terminal_obligations and old_status != new_status:
                    changes.append(
                        f"obligation_resolved:{actor}:{obligation_id}:{new_status}"
                    )
        before_goals = before_parts.get("goals", {})
        after_goals = after_parts.get("goals", {})
        for actor, state in after_goals.items():
            records = state.get("goals", {})
            old_records = before_goals.get(actor, {}).get("goals", {})
            for goal_id, record in records.items():
                if goal_id not in old_records and record.get("origin") == "agent":
                    changes.append(f"goal_adopted:{actor}:{goal_id}")
                old_refined_step = old_records.get(goal_id, {}).get("refined_step")
                new_refined_step = record.get("refined_step")
                if (
                    new_refined_step is not None
                    and old_refined_step != new_refined_step
                ):
                    changes.append(
                        f"goal_refined:{actor}:{goal_id}:step:{int(new_refined_step)}"
                    )
                old_status = str(old_records.get(goal_id, {}).get("status", ""))
                new_status = str(record.get("status", ""))
                if new_status in {"achieved", "failed", "abandoned"} and (
                    old_status != new_status
                ):
                    changes.append(f"goal_resolved:{actor}:{goal_id}:{new_status}")
        before_knowledge = before_parts.get("knowledge", {})
        after_knowledge = after_parts.get("knowledge", {})
        for actor, state in after_knowledge.items():
            records = state.get("claims", {})
            old_records = before_knowledge.get(actor, {}).get("claims", {})
            for claim_id, record in records.items():
                if claim_id not in old_records:
                    changes.append(f"claim_knowledge_learned:{actor}:{claim_id}")
                elif int(record.get("updated_step", 0) or 0) != int(
                    old_records.get(claim_id, {}).get("updated_step", 0) or 0
                ):
                    changes.append(f"claim_knowledge_revised:{actor}:{claim_id}")
        before_sentiments = before_parts.get("sentiments", {})
        after_sentiments = after_parts.get("sentiments", {})
        for actor, state in after_sentiments.items():
            records = state.get("sentiments", {})
            old_records = before_sentiments.get(actor, {}).get("sentiments", {})
            for sentiment_id, record in records.items():
                old = old_records.get(sentiment_id)
                if old is None:
                    changes.append(f"sentiment_created:{actor}:{sentiment_id}")
                elif int(record.get("updated_step", 0) or 0) != int(
                    old.get("updated_step", 0) or 0
                ) and str(record.get("source_event", "")).strip() != str(
                    old.get("source_event", "")
                ).strip():
                    # Deterministic decay also changes updated_step, but is not
                    # a new narrative consequence. A new authoritative source is.
                    changes.append(f"sentiment_updated:{actor}:{sentiment_id}")

        before_modifiers = before_parts.get("modifiers", {})
        after_modifiers = after_parts.get("modifiers", {})
        for actor, state in after_modifiers.items():
            records = state.get("modifiers", {})
            old_records = before_modifiers.get(actor, {}).get("modifiers", {})
            for modifier_id, record in records.items():
                old = old_records.get(modifier_id)
                if old is None:
                    changes.append(f"modifier_created:{actor}:{modifier_id}")
                elif record.get("provenance", {}) != old.get("provenance", {}):
                    changes.append(f"modifier_updated:{actor}:{modifier_id}")

        before_relationships = before_parts.get("relationships", {}).get(
            "relationships", {}
        )
        after_relationships = after_parts.get("relationships", {}).get(
            "relationships", {}
        )
        for relation_id, record in after_relationships.items():
            old_record = before_relationships.get(relation_id, {})
            old_bits = old_record.get("bits", {})
            for bit_id in sorted(set(record.get("bits", {})) - set(old_bits)):
                changes.append(f"relationship_bit_added:{relation_id}:{bit_id}")
            old_directed = old_record.get("directed_tracks", {})
            for direction, tracks in record.get("directed_tracks", {}).items():
                old_tracks = old_directed.get(direction, {})
                for track_id, track in tracks.items():
                    old_track = old_tracks.get(track_id)
                    if old_track is None or track.get("provenance", {}) != old_track.get(
                        "provenance", {}
                    ):
                        changes.append(
                            f"relationship_track_changed:{direction}:{track_id}"
                        )
        before_navigation = before_parts.get("navigation", {})
        after_navigation = after_parts.get("navigation", {})
        for actor, state in after_navigation.items():
            records = state.get("problems", {})
            old_records = before_navigation.get(actor, {}).get("problems", {})
            for problem_id, record in records.items():
                if problem_id not in old_records:
                    changes.append(
                        f"navigation_problem_created:{actor}:{problem_id}"
                    )
                old_status = str(old_records.get(problem_id, {}).get("status", ""))
                new_status = str(record.get("status", ""))
                if new_status == "resolved" and old_status != "resolved":
                    changes.append(
                        f"navigation_problem_resolved:{actor}:{problem_id}"
                    )
        return changes

    @staticmethod
    def _causal_handoffs(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        """Extract explicit authoritative provenance edges, not semantic guesses."""

        edges: List[str] = []
        before_parts = before.get("material_parts", {})
        after_parts = after.get("material_parts", {})

        before_events = before_parts.get("world_events", {})
        after_events = after_parts.get("world_events", {})
        for event_id in sorted(set(after_events) - set(before_events)):
            fact = after_events[event_id].get("fact", {})
            source_kind = str(fact.get("source_type", "")).strip()
            source_ref = str(fact.get("source_ref", "")).strip()
            if source_kind and source_ref:
                edges.append(
                    f"world_event:{event_id}<-{source_kind}:{source_ref}"
                )
            if source_kind == "timeline_resolution" and source_ref:
                resolution_node = f"timeline_resolution:{source_ref}"
                occurred_step = int(fact.get("occurred_step", 0) or 0)
                location = str(fact.get("location", "")).strip()
                metadata = fact.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                commitment_id = source_ref.rsplit(":", 1)[0]
                edges.append(
                    f"{resolution_node}<-timeline_commitment:{commitment_id}"
                )
                edges.append(
                    f"{resolution_node}<-clock:step:{occurred_step}"
                )
                if location:
                    for actor in metadata.get("present_participants", []) or []:
                        actor = str(actor).strip()
                        if actor:
                            edges.append(
                                f"{resolution_node}"
                                f"<-actor_presence:{actor}:{location}"
                            )
                    for actor in metadata.get("missing_participants", []) or []:
                        actor = str(actor).strip()
                        if actor:
                            edges.append(
                                f"{resolution_node}"
                                f"<-actor_absence:{actor}:{location}"
                            )

        before_agreements = before_parts.get("agreements", {}).get(
            "agreements", {}
        )
        after_agreements = after_parts.get("agreements", {}).get(
            "agreements", {}
        )
        for agreement_id, record in after_agreements.items():
            if agreement_id not in before_agreements:
                source_kind = str(record.get("source_kind", "")).strip()
                source_ref = str(record.get("source_ref", "")).strip()
                if source_kind and source_ref:
                    edges.append(
                        f"agreement:{agreement_id}<-{source_kind}:{source_ref}"
                    )
            old_status = str(
                before_agreements.get(agreement_id, {}).get("status", "")
            ).strip()
            new_status = str(record.get("status", "")).strip()
            if new_status in {
                "settled",
                "rejected",
                "withdrawn",
                "expired",
                "countered",
            } and old_status != new_status:
                resolution_node = (
                    f"agreement_resolution:{agreement_id}:{new_status}"
                )
                edges.append(f"{resolution_node}<-agreement:{agreement_id}")
                source_kind = str(
                    record.get("resolution_source_kind", "")
                ).strip()
                source_ref = str(
                    record.get("resolution_source_ref", "")
                ).strip()
                if source_kind and source_ref:
                    edges.append(
                        f"{resolution_node}<-{source_kind}:{source_ref}"
                    )
            old_performance = str(
                before_agreements.get(agreement_id, {}).get(
                    "performance_status", ""
                )
            ).strip()
            new_performance = str(
                record.get("performance_status", "")
            ).strip()
            if new_performance in {
                "fulfilled",
                "breached",
                "cancelled",
            } and old_performance != new_performance:
                performance_node = (
                    f"agreement_performance_resolution:{agreement_id}:"
                    f"{new_performance}"
                )
                edges.append(
                    f"{performance_node}"
                    f"<-agreement_resolution:{agreement_id}:settled"
                )
                for link in record.get("performance_obligations", []) or []:
                    if not isinstance(link, dict):
                        continue
                    if str(link.get("resolved_status", "")).strip() != new_performance:
                        continue
                    actor = str(
                        link.get("current_actor") or link.get("actor") or ""
                    ).strip()
                    obligation_id = str(link.get("obligation_id", "")).strip()
                    if actor and obligation_id:
                        edges.append(
                            f"{performance_node}"
                            f"<-obligation:{actor}:{obligation_id}"
                        )
            old_lots = {
                str(item.get("custody_id", "")): item
                for item in before_agreements.get(agreement_id, {}).get(
                    "escrow_lots", []
                )
                if isinstance(item, dict) and str(item.get("custody_id", ""))
            }
            for lot in record.get("escrow_lots", []) or []:
                if not isinstance(lot, dict):
                    continue
                custody_id = str(lot.get("custody_id", "")).strip()
                if not custody_id:
                    continue
                escrow_node = f"agreement_escrow:{agreement_id}:{custody_id}"
                if custody_id not in old_lots:
                    edges.append(
                        f"{escrow_node}"
                        f"<-agreement_resolution:{agreement_id}:settled"
                    )
                old_status = str(
                    old_lots.get(custody_id, {}).get("status", "")
                ).strip()
                new_status = str(lot.get("status", "")).strip()
                if (
                    new_status not in {"released", "refunded"}
                    or old_status == new_status
                ):
                    continue
                resolution_node = (
                    f"agreement_escrow_resolution:{agreement_id}:"
                    f"{custody_id}:{new_status}"
                )
                edges.append(f"{resolution_node}<-{escrow_node}")
                service_id = str(lot.get("release_on_service", "")).strip()
                service_status = ""
                for link in record.get("performance_obligations", []) or []:
                    if (
                        isinstance(link, dict)
                        and str(link.get("obligation_id", "")).strip()
                        == service_id
                    ):
                        service_status = str(
                            link.get("resolved_status", "")
                        ).strip()
                        break
                if service_status in {"fulfilled", "breached", "cancelled"}:
                    edges.append(
                        f"{resolution_node}"
                        f"<-agreement_performance_resolution:"
                        f"{agreement_id}:{service_status}"
                    )

        before_obligations = before_parts.get("obligations", {})
        after_obligations = after_parts.get("obligations", {})
        for actor, state in after_obligations.items():
            records = state.get("obligations", {})
            old_records = before_obligations.get(actor, {}).get("obligations", {})
            for obligation_id, record in records.items():
                if obligation_id in old_records:
                    continue
                source_kind = str(record.get("source_kind", "")).strip()
                source_ref = str(record.get("source_ref", "")).strip()
                if not source_kind or not source_ref:
                    continue
                source = f"{source_kind}:{source_ref}"
                if source_kind == "delegated_obligation":
                    source = f"obligation:{source_ref}"
                edges.append(f"obligation:{actor}:{obligation_id}<-{source}")
                agreement = after_agreements.get(source_ref, {})
                old_agreement = before_agreements.get(source_ref, {})
                if (
                    source_kind == "agreement"
                    and str(agreement.get("status", "")) == "settled"
                    and str(old_agreement.get("status", "")) != "settled"
                ):
                    edges.append(
                        f"obligation:{actor}:{obligation_id}"
                        f"<-agreement_resolution:{source_ref}:settled"
                    )

        for event_id, payload in after_events.items():
            current = payload.get("responses", {}).get("communications", []) or []
            previous = (
                before_events.get(event_id, {})
                .get("responses", {})
                .get("communications", [])
                or []
            )
            previous_ids = {
                str(item.get("response_id", ""))
                for item in previous
                if isinstance(item, dict)
            }
            for item in current:
                if not isinstance(item, dict):
                    continue
                response_id = str(item.get("response_id", "")).strip()
                if response_id and response_id not in previous_ids:
                    edges.append(
                        f"event_response:{response_id}<-world_event:{event_id}"
                    )

        before_goals = before_parts.get("goals", {})
        after_goals = after_parts.get("goals", {})
        for actor, state in after_goals.items():
            records = state.get("goals", {})
            old_records = before_goals.get(actor, {}).get("goals", {})
            for goal_id, record in records.items():
                old_refined_step = old_records.get(goal_id, {}).get("refined_step")
                refined_step = record.get("refined_step")
                refinement_node = ""
                if refined_step is not None:
                    refinement_node = (
                        f"goal_refinement:{actor}:{goal_id}:step:{int(refined_step)}"
                    )
                if refinement_node and old_refined_step != refined_step:
                    edges.append(
                        f"{refinement_node}<-goal:{actor}:{goal_id}"
                    )
                old_status = str(
                    old_records.get(goal_id, {}).get("status", "")
                ).strip()
                new_status = str(record.get("status", "")).strip()
                if (
                    new_status in {"achieved", "failed", "abandoned"}
                    and old_status != new_status
                ):
                    resolution_parent = refinement_node or f"goal:{actor}:{goal_id}"
                    edges.append(
                        f"goal_resolution:{actor}:{goal_id}:{new_status}"
                        f"<-{resolution_parent}"
                    )
            for goal_id, record in records.items():
                if goal_id in old_records or record.get("origin") != "agent":
                    continue
                source_kind = str(record.get("source_kind", "")).strip()
                source_ref = str(record.get("source_ref", "")).strip()
                if not source_kind or not source_ref:
                    continue
                source = f"{source_kind}:{source_ref}"
                if source_kind == "resolved_goal":
                    source_status = str(
                        records.get(source_ref, {}).get("status", "")
                    ).strip()
                    source = (
                        f"goal_resolution:{actor}:{source_ref}:{source_status}"
                        if source_status in {"achieved", "failed", "abandoned"}
                        else f"goal:{actor}:{source_ref}"
                    )
                elif source_kind == "claim":
                    source = f"claim_knowledge:{actor}:{source_ref}"
                elif source_kind == "navigation_problem":
                    source = f"navigation_problem:{actor}:{source_ref}"
                elif source_kind == "sentiment":
                    source = f"sentiment:{actor}:{source_ref}"
                elif source_kind == "drive_need":
                    source = f"drive_need:{actor}:{source_ref}"
                edges.append(f"goal:{actor}:{goal_id}<-{source}")

        before_sentiments = before_parts.get("sentiments", {})
        after_sentiments = after_parts.get("sentiments", {})
        for actor, state in after_sentiments.items():
            records = state.get("sentiments", {})
            old_records = before_sentiments.get(actor, {}).get("sentiments", {})
            for sentiment_id, record in records.items():
                old = old_records.get(sentiment_id)
                source = str(record.get("source_event", "")).strip()
                changed = old is None or source != str(
                    old.get("source_event", "")
                ).strip()
                if not changed or not source:
                    continue
                if not source.startswith((
                    "resolved_action:",
                    "world_event:",
                    "agreement_performance_resolution:",
                )):
                    continue
                edges.append(f"sentiment:{actor}:{sentiment_id}<-{source}")

        before_modifiers = before_parts.get("modifiers", {})
        after_modifiers = after_parts.get("modifiers", {})
        for actor, state in after_modifiers.items():
            records = state.get("modifiers", {})
            old_records = before_modifiers.get(actor, {}).get("modifiers", {})
            for modifier_id, record in records.items():
                provenance = record.get("provenance", {})
                old_provenance = old_records.get(modifier_id, {}).get(
                    "provenance", {}
                )
                if provenance == old_provenance:
                    continue
                source_kind = str(provenance.get("source_kind", "")).strip()
                source_ref = str(provenance.get("source_ref", "")).strip()
                if source_kind and source_ref:
                    edges.append(
                        f"modifier:{actor}:{modifier_id}"
                        f"<-{source_kind}:{source_ref}"
                    )

        before_drives = before_parts.get("drives", {})
        after_drives = after_parts.get("drives", {})
        for actor, state in after_drives.items():
            provenance_map = state.get("need_provenance", {})
            old_map = before_drives.get(actor, {}).get("need_provenance", {})
            for need_id, history in provenance_map.items():
                old_entries = {
                    json.dumps(item, ensure_ascii=False, sort_keys=True)
                    for item in old_map.get(need_id, [])
                    if isinstance(item, dict)
                }
                for provenance in history or []:
                    if not isinstance(provenance, dict):
                        continue
                    serialized = json.dumps(
                        provenance, ensure_ascii=False, sort_keys=True
                    )
                    if serialized in old_entries:
                        continue
                    source_kind = str(
                        provenance.get("source_kind", "")
                    ).strip()
                    source_ref = str(provenance.get("source_ref", "")).strip()
                    if not source_kind or not source_ref:
                        continue
                    source = f"{source_kind}:{source_ref}"
                    if source_kind == "obligation":
                        source = (
                            f"obligation:{source_ref}"
                            if ":" in source_ref
                            else f"obligation:{actor}:{source_ref}"
                        )
                    edges.append(f"drive_need:{actor}:{need_id}<-{source}")

        before_relationships = before_parts.get("relationships", {}).get(
            "relationships", {}
        )
        after_relationships = after_parts.get("relationships", {}).get(
            "relationships", {}
        )
        for relation_id, record in after_relationships.items():
            old_record = before_relationships.get(relation_id, {})
            old_bits = old_record.get("bits", {})
            for bit_id, bit in record.get("bits", {}).items():
                if bit_id in old_bits:
                    continue
                provenance = bit.get("provenance", {})
                source_kind = str(provenance.get("source_kind", "")).strip()
                source_ref = str(provenance.get("source_ref", "")).strip()
                if source_kind and source_ref:
                    edges.append(
                        f"relationship_bit:{relation_id}:{bit_id}"
                        f"<-{source_kind}:{source_ref}"
                    )
            old_directed = old_record.get("directed_tracks", {})
            for direction, tracks in record.get("directed_tracks", {}).items():
                old_tracks = old_directed.get(direction, {})
                for track_id, track in tracks.items():
                    provenance = track.get("provenance", {})
                    old_provenance = old_tracks.get(track_id, {}).get(
                        "provenance", {}
                    )
                    if provenance == old_provenance:
                        continue
                    source_kind = str(provenance.get("source_kind", "")).strip()
                    source_ref = str(provenance.get("source_ref", "")).strip()
                    if not source_kind or not source_ref:
                        continue
                    source = f"{source_kind}:{source_ref}"
                    if source_kind == "sentiment":
                        source = f"sentiment:{source_ref}"
                    edges.append(
                        f"relationship_track:{direction}:{track_id}<-{source}"
                    )

        before_knowledge = before_parts.get("knowledge", {})
        after_knowledge = after_parts.get("knowledge", {})
        for actor, state in after_knowledge.items():
            records = state.get("claims", {})
            old_records = before_knowledge.get(actor, {}).get("claims", {})
            for claim_id, record in records.items():
                old = old_records.get(claim_id)
                changed = old is None or int(record.get("updated_step", 0) or 0) != int(
                    old.get("updated_step", 0) or 0
                )
                if not changed:
                    continue
                basis = str(record.get("basis", "")).strip()
                source_ref = str(record.get("source", "")).strip()
                learned_step = int(record.get("updated_step", 0) or 0)
                knowledge_node = f"claim_knowledge:{actor}:{claim_id}"
                if basis == "observed" and source_ref.startswith("evidence:"):
                    evidence_ref = source_ref.split(":", 1)[1].strip()
                    if not evidence_ref:
                        continue
                    observation_node = (
                        f"evidence_observation:{actor}:{evidence_ref}:"
                        f"step:{learned_step}"
                    )
                    edges.append(f"{knowledge_node}<-{observation_node}")
                    edges.append(
                        f"{observation_node}"
                        f"<-resolved_action:step:{learned_step}:actor:{actor}"
                    )
                    edges.append(f"{observation_node}<-evidence:{evidence_ref}")
                elif basis == "reported" and source_ref:
                    report_node = (
                        f"claim_report:{source_ref}->{actor}:{claim_id}:"
                        f"step:{learned_step}"
                    )
                    edges.append(f"{knowledge_node}<-{report_node}")
                    edges.append(
                        f"{report_node}"
                        f"<-resolved_action:step:{learned_step}:actor:{source_ref}"
                    )

        before_navigation = before_parts.get("navigation", {})
        after_navigation = after_parts.get("navigation", {})
        for actor, state in after_navigation.items():
            records = state.get("problems", {})
            old_records = before_navigation.get(actor, {}).get("problems", {})
            for problem_id, record in records.items():
                if problem_id in old_records:
                    continue
                failure_rule = str(record.get("failure_rule", "")).strip()
                route_source = str(record.get("route_source", "")).strip()
                route_target = str(record.get("route_target", "")).strip()
                if failure_rule and route_source and route_target:
                    edges.append(
                        f"navigation_problem:{actor}:{problem_id}"
                        f"<-movement_failure:{failure_rule}:"
                        f"{route_source}->{route_target}"
                    )

        return sorted(dict.fromkeys(edges))

    @staticmethod
    def _causal_chain_depth(handoffs: List[str]) -> int:
        parents: Dict[str, set[str]] = {}
        for handoff in handoffs:
            child, separator, parent = str(handoff).partition("<-")
            child = child.strip()
            parent = parent.strip()
            if separator and child and parent:
                parents.setdefault(child, set()).add(parent)

        memo: Dict[str, int] = {}

        def depth(node: str, visiting: set[str]) -> int:
            if node in memo:
                return memo[node]
            if node in visiting or node not in parents:
                return 0
            value = 1 + max(
                depth(parent, visiting | {node})
                for parent in parents[node]
            )
            memo[node] = value
            return value

        return max((depth(node, set()) for node in parents), default=0)

    @staticmethod
    def _temporal_causal_metrics(
        traces: List[EpisodeStepTrace],
    ) -> Dict[str, int]:
        """Measure explicit causal propagation across committed Episode steps.

        Node birth is the first step where it appears as a consequence. Roots
        that predate the Episode remain valid causes but do not fabricate a
        temporal span. Cycles are bounded and never semantically repaired.
        """
        birth: Dict[str, int] = {}
        parents: Dict[str, set[str]] = {}
        for trace in traces:
            for handoff in trace.causal_handoffs:
                child, separator, parent = str(handoff).partition("<-")
                child = child.strip()
                parent = parent.strip()
                if not separator or not child or not parent:
                    continue
                birth[child] = min(birth.get(child, trace.index), trace.index)
                parents.setdefault(child, set()).add(parent)

        cross_step_handoffs = 0
        cross_steps = set()
        for child, sources in parents.items():
            child_step = birth.get(child)
            if child_step is None:
                continue
            for parent in sources:
                parent_step = birth.get(parent)
                if parent_step is not None and parent_step < child_step:
                    cross_step_handoffs += 1
                    cross_steps.update((parent_step, child_step))

        memo: Dict[str, int] = {}

        def earliest_birth(node: str, visiting: set[str]) -> int:
            own = birth.get(node)
            if own is None:
                return 10**9
            if node in memo:
                return memo[node]
            if node in visiting:
                return own
            value = own
            for parent in parents.get(node, set()):
                if parent not in birth:
                    continue
                value = min(
                    value,
                    earliest_birth(parent, visiting | {node}),
                )
            memo[node] = value
            return value

        max_span = 0
        for node, step in birth.items():
            earliest = earliest_birth(node, set())
            if earliest != 10**9:
                max_span = max(max_span, int(step) - int(earliest) + 1)

        return {
            "cross_step_handoff_count": cross_step_handoffs,
            "cross_step_count": len(cross_steps),
            "max_span_steps": max_span,
        }

    @staticmethod
    def _policy_causal_handoffs(
        context: Dict[str, Any], actions: List[Dict[str, Any]]
    ) -> List[str]:
        """Connect committed selected actions to Host-scored private motives.

        These edges use the selected candidate's actual policy trace. They do
        not infer motives from action prose and do not claim that a negative
        utility term caused an action to be selected.
        """

        transaction = context.get("state_transaction", {})
        if not transaction.get("committed"):
            return []
        clock = context.get("clock")
        current_step = int(getattr(clock, "current_step", 0) or 0)
        resolved_actors = {
            str(action.get("actor", "")).strip()
            for action in actions
            if isinstance(action, dict)
            and str(action.get("actor", "")).strip()
            and str(action.get("actor", "")).strip() != "World"
        }
        edges: List[str] = []
        completed_traces = context.get("completed_action_policy_traces", {}) or {}
        trace_items = (
            (
                actor,
                payload.get("trace") if isinstance(payload, dict) else None,
            )
            for actor, payload in completed_traces.items()
        ) if completed_traces else (
            (actor, trace)
            for actor, trace in (context.get("policy_traces", {}) or {}).items()
        )
        for actor, trace in trace_items:
            actor = str(actor).strip()
            if (
                not actor
                or actor not in resolved_actors
                or not isinstance(trace, dict)
                or trace.get("mode") != "host_sampled"
            ):
                continue
            selected_id = str(trace.get("selected_candidate_id", "")).strip()
            selected = next(
                (
                    item
                    for item in trace.get("candidates", [])
                    if isinstance(item, dict)
                    and str(item.get("candidate_id", "")).strip() == selected_id
                ),
                None,
            )
            if selected is None:
                continue
            action_node = f"resolved_action:step:{current_step}:actor:{actor}"
            for goal_id, contribution in (
                selected.get("goal_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(goal_id).strip():
                    edges.append(
                        f"{action_node}<-goal:{actor}:{str(goal_id).strip()}"
                    )
            for sentiment_id, contribution in (
                selected.get("sentiment_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(sentiment_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-sentiment:{actor}:{str(sentiment_id).strip()}"
                    )
            for obligation_id, contribution in (
                selected.get("obligation_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(obligation_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-obligation:{actor}:{str(obligation_id).strip()}"
                    )
            for problem_id, contribution in (
                selected.get("navigation_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(problem_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-navigation_problem:{actor}:"
                        f"{str(problem_id).strip()}"
                    )
            for event_id, contribution in (
                selected.get("action_failure_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(event_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-action_failure:{actor}:{str(event_id).strip()}"
                    )
            for claim_id, contribution in (
                selected.get("knowledge_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(claim_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-claim_knowledge:{actor}:{str(claim_id).strip()}"
                    )
            for agreement_id, contribution in (
                selected.get("agreement_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(agreement_id).strip():
                    edges.append(
                        f"{action_node}<-agreement:{str(agreement_id).strip()}"
                    )
            for event_id, contribution in (
                selected.get("world_event_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(event_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-world_event:{actor}:{str(event_id).strip()}"
                    )
            for response_id, contribution in (
                selected.get("event_response_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(response_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-event_response:{actor}:{str(response_id).strip()}"
                    )
            for modifier_id, contribution in (
                selected.get("modifier_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(modifier_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-modifier:{actor}:{str(modifier_id).strip()}"
                    )
            for need_id, contribution in (
                selected.get("relief_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(need_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-drive_need:{actor}:{str(need_id).strip()}"
                    )
            for commitment_id, contribution in (
                selected.get("schedule_contributions", {}) or {}
            ).items():
                if float(contribution or 0.0) > 0 and str(commitment_id).strip():
                    edges.append(
                        f"{action_node}"
                        f"<-timeline_commitment:{str(commitment_id).strip()}"
                    )
            target = str(
                (selected.get("action", {}) or {}).get("target", "")
            ).strip()
            if target:
                for track_id, contribution in (
                    selected.get("relationship_contributions", {}) or {}
                ).items():
                    if float(contribution or 0.0) > 0 and str(track_id).strip():
                        edges.append(
                            f"{action_node}"
                            f"<-relationship_track:{actor}->{target}:"
                            f"{str(track_id).strip()}"
                        )
        return sorted(dict.fromkeys(edges))

    @staticmethod
    def _goal_engaged_actors(context: Dict[str, Any]) -> List[str]:
        actors = []
        for actor, trace in (context.get("policy_traces", {}) or {}).items():
            if not isinstance(trace, dict) or trace.get("mode") != "host_sampled":
                continue
            selected = str(trace.get("selected_candidate_id", ""))
            candidate = next(
                (
                    item
                    for item in trace.get("candidates", [])
                    if isinstance(item, dict)
                    and str(item.get("candidate_id", "")) == selected
                ),
                None,
            )
            if candidate and float(candidate.get("goal_contribution", 0.0) or 0.0) > 0:
                actors.append(str(actor))
        return sorted(set(actors))

    @staticmethod
    def _sampled_policy_actors(context: Dict[str, Any]) -> List[str]:
        return sorted(
            str(actor)
            for actor, trace in (context.get("policy_traces", {}) or {}).items()
            if isinstance(trace, dict) and trace.get("mode") == "host_sampled"
        )

    @staticmethod
    def _policy_audits(context: Dict[str, Any]) -> List[PolicyDecisionAudit]:
        audits = []
        scalar_motives = (
            "trait_contribution",
            "risk_contribution",
            "relief_contribution",
            "relationship_contribution",
            "obligation_contribution",
            "navigation_contribution",
            "action_failure_contribution",
            "goal_contribution",
            "sentiment_contribution",
            "modifier_contribution",
            "knowledge_contribution",
            "agreement_contribution",
            "world_event_contribution",
            "event_response_contribution",
            "schedule_contribution",
        )
        mapped_motives = (
            "trait_contributions",
            "relief_contributions",
            "relationship_contributions",
            "obligation_contributions",
            "navigation_contributions",
            "action_failure_contributions",
            "goal_contributions",
            "sentiment_contributions",
            "modifier_contributions",
            "knowledge_contributions",
            "agreement_contributions",
            "world_event_contributions",
            "event_response_contributions",
            "schedule_contributions",
        )
        for actor, trace in sorted(
            (context.get("policy_traces", {}) or {}).items(),
            key=lambda item: str(item[0]),
        ):
            if not isinstance(trace, dict):
                continue
            candidates = [
                item
                for item in trace.get("candidates", []) or []
                if isinstance(item, dict)
            ]
            runtime_candidates = [
                item for item in candidates if item.get("source") == "runtime"
            ]
            environment_candidates = [
                item for item in candidates if item.get("source") == "environment"
            ]
            selected_id = str(trace.get("selected_candidate_id", ""))
            selected = next(
                (
                    item
                    for item in candidates
                    if str(item.get("candidate_id", "")) == selected_id
                ),
                {},
            )
            selected_action = selected.get("action", {}) or {}
            validated_refs = [
                ref
                for candidate in candidates
                for ref in candidate.get("validated_motive_refs", []) or []
                if isinstance(ref, dict)
            ]
            rejected_refs = [
                ref
                for candidate in candidates
                for ref in candidate.get("rejected_motive_refs", []) or []
                if isinstance(ref, dict)
            ]
            selected_validated_refs = [
                ref
                for ref in selected.get("validated_motive_refs", []) or []
                if isinstance(ref, dict)
            ]
            motivated = any(
                float(selected.get(key, 0.0) or 0.0) > 0
                for key in scalar_motives
            ) or any(
                any(float(value or 0.0) > 0 for value in values.values())
                for key in mapped_motives
                if isinstance((values := selected.get(key, {})), dict)
            )
            runtime_actions = [
                item.get("action", {}) or {} for item in runtime_candidates
            ]
            audits.append(PolicyDecisionAudit(
                actor=str(actor),
                mode=str(trace.get("mode", "")),
                selected_source=str(selected.get("source", "")),
                selected_action_kind=str(selected_action.get("kind", "")),
                runtime_candidate_count=len(runtime_candidates),
                environment_candidate_count=len(environment_candidates),
                runtime_action_kind_count=len({
                    str(action.get("kind", "")).casefold()
                    for action in runtime_actions
                    if str(action.get("kind", "")).strip()
                }),
                runtime_target_count=len({
                    str(action.get("target", "")).strip().casefold()
                    for action in runtime_actions
                    if str(action.get("target", "")).strip()
                }),
                attention_motive_available_count=max(
                    0,
                    int(
                        trace.get("attention_motive_available_count", 0)
                        or 0
                    ),
                ),
                urgent_attention_motive_available_count=max(
                    0,
                    int(
                        trace.get(
                            "urgent_attention_motive_available_count", 0
                        ) or 0
                    ),
                ),
                validated_motive_ref_count=len(validated_refs),
                rejected_motive_ref_count=len(rejected_refs),
                validated_event_motive_ref_count=sum(
                    ref.get("kind") in {"world_event", "event_response"}
                    for ref in validated_refs
                ),
                selected_validated_motive_ref_count=len(
                    selected_validated_refs
                ),
                selected_event_motive_ref_count=sum(
                    ref.get("kind") in {"world_event", "event_response"}
                    for ref in selected_validated_refs
                ),
                continuity_supported=(
                    float(selected.get("continuity_contribution", 0.0) or 0.0)
                    > 0
                ),
                motivated=motivated,
            ))
        return audits

    @staticmethod
    def _changed_subjects(context: Dict[str, Any]) -> List[str]:
        transaction = context.get("state_transaction", {})
        if not transaction.get("committed"):
            return []
        result = context.get("simulation_result", {})
        updates = result.get("state_updates", {}) if isinstance(result, dict) else {}
        subjects = set()
        for key in ("world_objects", "actor_states"):
            values = updates.get(key, {}) if isinstance(updates, dict) else {}
            if isinstance(values, dict):
                subjects.update(str(name) for name in values if str(name).strip())
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            for key in ("actor", "action_target"):
                value = str(action.get(key, "")).strip()
                if value:
                    subjects.add(value)
        for item in result.get("object_lifecycle", []) or []:
            if isinstance(item, dict):
                value = str(item.get("object_id") or item.get("name") or "").strip()
                if value:
                    subjects.add(value)
        return sorted(subjects)

    @staticmethod
    def _interaction_chains(traces: List[EpisodeStepTrace]) -> tuple[int, int]:
        linked_steps = 0
        longest = 0
        current = 0
        previous_subjects: set[str] = set()
        for trace in traces:
            current_subjects = {
                value
                for actor, _, target in trace.actor_actions
                for value in (actor, target)
                if value
            }
            linked = bool(previous_subjects.intersection(current_subjects))
            if linked:
                linked_steps += 1
                current += 1
            else:
                current = 1 if current_subjects else 0
            longest = max(longest, current)
            previous_subjects = set(trace.changed_subjects)
        return linked_steps, longest

    @staticmethod
    def _actor_differentiation(traces: List[EpisodeStepTrace]) -> float:
        profiles: Dict[str, Dict[str, int]] = {}
        for trace in traces:
            for actor, kind, _ in trace.actor_actions:
                if not actor or actor == "World" or not kind:
                    continue
                profile = profiles.setdefault(actor, {})
                profile[kind] = profile.get(kind, 0) + 1
        names = sorted(profiles)
        if len(names) < 2:
            return 0.0
        distances = []
        for index, left_name in enumerate(names):
            for right_name in names[index + 1 :]:
                left = profiles[left_name]
                right = profiles[right_name]
                left_total = sum(left.values()) or 1
                right_total = sum(right.values()) or 1
                kinds = set(left).union(right)
                distances.append(
                    0.5
                    * sum(
                        abs(
                            left.get(kind, 0) / left_total
                            - right.get(kind, 0) / right_total
                        )
                        for kind in kinds
                    )
                )
        return sum(distances) / len(distances) if distances else 0.0

    @staticmethod
    def _commitment_resolutions(traces: Iterable[EpisodeStepTrace]) -> int:
        return sum(
            1
            for trace in traces
            for change in trace.irreversible_changes
            if change.startswith(
                ("obligation_resolved:", "agreement_performance_resolved:")
            )
        )

    @staticmethod
    def _deadlocked(traces: List[EpisodeStepTrace], window: int = 4) -> bool:
        if len(traces) < window:
            return False
        tail = traces[-window:]
        return all(not trace.material_change_kinds for trace in tail) and all(
            not trace.action_kinds
            or set(trace.action_kinds).issubset({"wait", "observe"})
            for trace in tail
        )

    @staticmethod
    def _hash(value: Any) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _action_batch_signature(
        trace: EpisodeStepTrace,
    ) -> tuple[tuple[str, str, str], ...]:
        if trace.actor_actions:
            return tuple(
                (
                    str(actor).strip(),
                    str(kind).strip(),
                    str(target).strip(),
                )
                for actor, kind, target in trace.actor_actions
            )
        return tuple(("", str(kind).strip(), "") for kind in trace.action_kinds)

    @staticmethod
    def _longest_repetition(items: List[tuple[Any, ...]]) -> int:
        longest = 0
        current = 0
        previous = None
        for item in items:
            if item == previous and item:
                current += 1
            else:
                current = 1 if item else 0
                previous = item
            longest = max(longest, current)
        return longest


def deepcopy_json(value: Any) -> Any:
    """Return a JSON-shaped copy without retaining mutable ECS references."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
