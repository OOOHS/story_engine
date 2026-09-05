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
    modifier_count: int
    claim_count: int
    known_claim_count: int
    world_event_count: int = 0
    material_change_kinds: tuple[str, ...] = ()
    irreversible_changes: tuple[str, ...] = ()
    causal_handoffs: tuple[str, ...] = ()
    goal_engaged_actors: tuple[str, ...] = ()
    deciding_actors: tuple[str, ...] = ()
    goal_continuation_actors: tuple[str, ...] = ()
    goal_reactivation_actors: tuple[str, ...] = ()
    stated_motives: tuple[tuple[str, str, str], ...] = ()
    rejected_motive_refs: tuple[tuple[str, str, str], ...] = ()
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
                self._motive_causal_handoffs(context, actions)
            )
            causal_handoffs = sorted(dict.fromkeys(causal_handoffs))
            goal_engaged_actors = self._goal_engaged_actors(context)
            deciding_actors = sorted(
                str(actor)
                for actor in (context.get("policy_traces", {}) or {})
                if str(actor).strip()
            )
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
                    modifier_count=after["modifier_count"],
                    claim_count=after["claim_count"],
                    known_claim_count=after["known_claim_count"],
                    world_event_count=after["world_event_count"],
                    material_change_kinds=tuple(material_change_kinds),
                    irreversible_changes=tuple(irreversible_changes),
                    causal_handoffs=tuple(causal_handoffs),
                    goal_engaged_actors=tuple(goal_engaged_actors),
                    deciding_actors=tuple(deciding_actors),
                    goal_continuation_actors=tuple(goal_continuation_actors),
                    goal_reactivation_actors=tuple(goal_reactivation_actors),
                    stated_motives=tuple(self._stated_motives(context)),
                    rejected_motive_refs=tuple(self._rejected_motive_refs(context)),
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
                    "navigation_problem:",
                    "action_failure:",
                    "claim_knowledge:",
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
        decision_steps = sum(bool(trace.deciding_actors) for trace in traces)
        decision_count = sum(len(trace.deciding_actors) for trace in traces)
        stated_motives = [item for trace in traces for item in trace.stated_motives]
        rejected_motive_refs = [
            item for trace in traces for item in trace.rejected_motive_refs
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
                "causal_handoff_steps": causal_handoff_steps,
                "causal_handoff_count": len(causal_handoffs),
                "motive_handoff_count": len(policy_motive_handoffs),
                "motivated_action_count": len({
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
                "decision_steps": decision_steps,
                "decision_count": decision_count,
                "stated_motive_count": len(stated_motives),
                # A character citing a goal, sentiment or need she
                # does not hold. Nonzero means an agent is narrating reasons it
                # cannot back, which is a credibility signal, not a crash.
                "rejected_motive_ref_count": len(rejected_motive_refs),
                "stated_motive_decision_rate": (
                    round(len(stated_motives) / decision_count, 6)
                    if decision_count
                    else None
                ),
                "goal_engagement_rate": round(
                    goal_engagement_steps / decision_steps, 6
                )
                if decision_steps
                else None,
                "actor_differentiation": round(actor_differentiation, 6),
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
        relationship_book = runner.relation_registry.to_relationship_book()
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
            "relationships": relationship_book.model_dump(mode="json"),
            "world_events": world_events,
        }
        scene_snapshot = scene.get_snapshot() if scene else {}
        material_scene = deepcopy_json(scene_snapshot)
        for flag in self._DERIVED_SCENE_FLAGS:
            material_scene.get("scene_flags", {}).pop(flag, None)
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
            drive = entity.get_component("DriveState")
            cognition = entity.get_component("Cognition")
            sentiment = entity.get_component("SentimentState")
            goal_state = entity.get_component("GoalState")
            modifier_state = entity.get_component("ModifierState")
            knowledge_state = entity.get_component("KnowledgeState")
            navigation_state = entity.get_component("NavigationState")
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
            "relationships": relationship_book.model_dump(mode="json"),
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
                "relationships",
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
    def _motive_causal_handoffs(
        context: Dict[str, Any], actions: List[Dict[str, Any]]
    ) -> List[str]:
        """Connect a committed action to the reason its character gave for it.

        The Host does not choose her action, so it cannot reconstruct why she
        acted; only she can say. These edges therefore come from her own stated
        ``motive_refs``, already checked by InputSystem against the goals,
        sentiments and needs she actually holds -- an unheld
        reference never reaches here. Nothing is inferred from action prose.
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
        completed = context.get("completed_action_motive_refs", {}) or {}
        if completed:
            stated = {
                actor: payload.get("motive_refs")
                for actor, payload in completed.items()
                if isinstance(payload, dict)
            }
        else:
            stated = dict(context.get("agent_motive_refs", {}) or {})
        edges: List[str] = []
        for actor, refs in stated.items():
            actor = str(actor).strip()
            if not actor or actor not in resolved_actors or not isinstance(refs, list):
                continue
            action_node = f"resolved_action:step:{current_step}:actor:{actor}"
            for item in refs:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind", "")).strip()
                ref = str(item.get("ref", "")).strip()
                if kind and ref:
                    edges.append(f"{action_node}<-{kind}:{actor}:{ref}")
        return sorted(dict.fromkeys(edges))

    @staticmethod
    def _goal_engaged_actors(context: Dict[str, Any]) -> List[str]:
        """Characters who said this turn's action was for one of their goals."""
        return sorted({
            str(actor)
            for actor, refs in (context.get("agent_motive_refs", {}) or {}).items()
            if isinstance(refs, list)
            and any(
                isinstance(item, dict) and str(item.get("kind", "")).strip() == "goal"
                for item in refs
            )
            and str(actor).strip()
        })

    @staticmethod
    def _stated_motives(context: Dict[str, Any]) -> List[tuple[str, str, str]]:
        return sorted({
            (str(actor), str(item.get("kind", "")), str(item.get("ref", "")))
            for actor, refs in (context.get("agent_motive_refs", {}) or {}).items()
            for item in (refs if isinstance(refs, list) else [])
            if isinstance(item, dict) and str(actor).strip()
        })

    @staticmethod
    def _rejected_motive_refs(context: Dict[str, Any]) -> List[tuple[str, str, str]]:
        """Reasons a character gave that she could not actually back."""
        return sorted({
            (
                str(item.get("actor", "")),
                str(item.get("kind", "")),
                str(item.get("ref", "")),
            )
            for item in (context.get("agent_motive_ref_rejections", []) or [])
            if isinstance(item, dict)
        })

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
