import contextlib
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from src.story_engine.evaluation.closure import (
    EpisodeClosureEvaluator,
    EpisodeClosurePolicy,
)
from src.story_engine.evaluation.episode import EpisodeRunner


@dataclass(frozen=True)
class SoakSample:
    index: int
    simulation_time: int
    material_change_kinds: tuple[str, ...]
    active_agent_goal_count: int
    total_agent_goal_count: int
    pending_action_count: int
    memory_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    closure_eligible: bool = False
    violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SoakReport:
    random_seed: int | str
    requested_steps: int
    samples: tuple[SoakSample, ...]
    metrics: Dict[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

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


class SoakRunner:
    """Run a world past Episode closure and audit long-horizon stability."""

    def run(
        self,
        session: Any,
        *,
        steps: int,
        sample_every: int = 6,
        closure_policy: EpisodeClosurePolicy | None = None,
        quiet: bool = False,
    ) -> SoakReport:
        requested = max(0, int(steps))
        interval = max(1, int(sample_every))
        closure_policy = (closure_policy or EpisodeClosurePolicy()).normalized()
        closure_evaluator = EpisodeClosureEvaluator()
        episode_auditor = EpisodeRunner()
        samples: List[SoakSample] = []
        violations: List[str] = []
        closure_streak = 0
        closure_step = None
        goal_adoptions = 0
        goal_refinements = 0
        goal_resolutions = 0
        consolidation_count = 0
        max_active_agent_goals = 0
        max_total_agent_goals = 0
        max_foreground_starvation = 0
        foreground_streaks: Dict[str, int] = {}
        relation_histories: Dict[str, List[tuple[Any, ...]]] = {}
        relationship_state_changes = 0
        relationship_oscillations = 0
        action_repetition: Dict[str, tuple[tuple[str, str], int]] = {}
        longest_action_repetition = 0
        longest_preclosure_action_repetition = 0
        trailing_no_material_change = 0
        trailing_time_stall = 0
        previous_time = int(session.simulation_time)
        final_memory_counts: Dict[str, Dict[str, int]] = {}

        for index in range(requested):
            before = episode_auditor._snapshot(session)
            with self._output_context(quiet):
                context = session.run_step()
            after = episode_auditor._snapshot(session)
            step_violations = episode_auditor._audit_step(session, context)
            violations.extend(step_violations)
            material = tuple(
                episode_auditor._material_change_kinds(before, after)
            )
            trailing_no_material_change = (
                0 if material else trailing_no_material_change + 1
            )
            current_time = int(session.simulation_time)
            pending_actions = len(
                session.runner.action_queue.snapshot().get("pending", [])
            )
            if current_time == previous_time and pending_actions:
                trailing_time_stall += 1
            else:
                trailing_time_stall = 0
            previous_time = current_time

            transitions = [
                item
                for item in context.get("goal_transitions", [])
                if isinstance(item, dict)
            ]
            goal_adoptions += sum(item.get("status") == "adopted" for item in transitions)
            goal_refinements += sum(
                item.get("status") == "refined" for item in transitions
            )
            goal_resolutions += sum(
                item.get("status") in {"achieved", "failed", "abandoned"}
                for item in transitions
            )
            consolidation_count += sum(
                item.get("status") == "consolidated"
                for item in context.get("memory_consolidation_traces", {}).values()
                if isinstance(item, dict)
            )

            active_agent_goals, total_agent_goals = self._goal_counts(session)
            max_active_agent_goals = max(
                max_active_agent_goals, active_agent_goals
            )
            max_total_agent_goals = max(max_total_agent_goals, total_agent_goals)
            max_foreground_starvation = max(
                max_foreground_starvation,
                self._update_foreground_streaks(
                    session, context, foreground_streaks
                ),
            )
            changes, oscillations = self._update_relation_histories(
                session, relation_histories
            )
            relationship_state_changes += changes
            relationship_oscillations += oscillations
            current_repetition = self._update_action_repetition(
                context, action_repetition
            )
            longest_action_repetition = max(
                longest_action_repetition, current_repetition
            )
            if closure_step is None:
                longest_preclosure_action_repetition = max(
                    longest_preclosure_action_repetition,
                    current_repetition,
                )

            closure = closure_evaluator.evaluate(session, closure_policy)
            closure_streak = closure_streak + 1 if closure.eligible else 0
            if (
                closure_step is None
                and index + 1 >= closure_policy.minimum_steps
                and closure_streak >= closure_policy.stable_steps
            ):
                closure_step = index + 1

            should_sample = (
                index == 0
                or (index + 1) % interval == 0
                or index + 1 == requested
            )
            if should_sample:
                final_memory_counts = self._memory_counts(session)
                samples.append(
                    SoakSample(
                        index=index,
                        simulation_time=current_time,
                        material_change_kinds=material,
                        active_agent_goal_count=active_agent_goals,
                        total_agent_goal_count=total_agent_goals,
                        pending_action_count=pending_actions,
                        memory_counts=final_memory_counts,
                        closure_eligible=closure.eligible,
                        violations=tuple(step_violations),
                    )
                )

        low_memory_max = max(
            (
                counts.get("low_salience_episodic", 0)
                for counts in final_memory_counts.values()
            ),
            default=0,
        )
        flags = []
        if violations:
            flags.append("authority_violations")
        if max_active_agent_goals > max(
            3, 3 * self._character_count(session)
        ):
            flags.append("goal_explosion")
        if max_total_agent_goals > max(
            12, 12 * self._character_count(session)
        ):
            flags.append("goal_history_explosion")
        if requested >= 24 and low_memory_max > 30:
            flags.append("memory_unbounded")
        if requested >= 20 and closure_step is None:
            flags.append("no_episode_closure")
        if max_foreground_starvation >= 5:
            flags.append("foreground_starvation")
        if relationship_oscillations > max(6, requested // 3):
            flags.append("relationship_oscillation")
        if closure_step is None and trailing_no_material_change >= 20:
            flags.append("preclosure_stagnation")
        if longest_preclosure_action_repetition >= 10:
            flags.append("preclosure_action_loop")
        if trailing_time_stall >= 10:
            flags.append("stuck_action_queue")

        return SoakReport(
            random_seed=session.random_seed,
            requested_steps=requested,
            samples=tuple(samples),
            metrics={
                "completed_steps": requested,
                "final_simulation_time": int(session.simulation_time),
                "closure_reached": closure_step is not None,
                "steps_to_first_closure": closure_step,
                "goal_adoption_count": goal_adoptions,
                "goal_refinement_count": goal_refinements,
                "goal_resolution_count": goal_resolutions,
                "max_active_agent_goal_count": max_active_agent_goals,
                "max_total_agent_goal_count": max_total_agent_goals,
                "memory_consolidation_count": consolidation_count,
                "final_memory_counts": final_memory_counts,
                "max_foreground_starvation": max_foreground_starvation,
                "relationship_state_change_count": relationship_state_changes,
                "relationship_oscillation_count": relationship_oscillations,
                "longest_action_repetition": longest_action_repetition,
                "longest_preclosure_action_repetition": (
                    longest_preclosure_action_repetition
                ),
                "trailing_no_material_change_steps": trailing_no_material_change,
                "final_pending_action_count": len(
                    session.runner.action_queue.snapshot().get("pending", [])
                ),
            },
            quality_flags=tuple(flags),
            violations=tuple(violations),
        )

    @staticmethod
    def _goal_counts(session: Any) -> tuple[int, int]:
        active = 0
        total = 0
        for entity in session.runner.entities.values():
            state = entity.get_component("GoalState")
            if state is None:
                continue
            agent_goals = [
                record for record in state.goals.values()
                if record.origin == "agent"
            ]
            total += len(agent_goals)
            active += sum(record.status == "active" for record in agent_goals)
        return active, total

    @staticmethod
    def _memory_counts(session: Any) -> Dict[str, Dict[str, int]]:
        counts = {}
        for name, entity in session.runner.entities.items():
            if entity.get_component("AgentController") is None:
                continue
            memory = entity.get_component("Memory")
            if memory is None or not hasattr(memory, "list_memories"):
                continue
            try:
                records = memory.list_memories()
            except Exception:
                continue
            actor_counts = {
                "total": len(records),
                "episodic": 0,
                "consolidated": 0,
                "high_salience": 0,
                "low_salience_episodic": 0,
            }
            for item in records:
                metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
                kind = metadata.get("type")
                salience = float(metadata.get("salience", 0.0) or 0.0)
                actor_counts["episodic"] += kind == "episodic_log"
                actor_counts["consolidated"] += kind == "consolidated_summary"
                actor_counts["high_salience"] += salience >= 3.0
                actor_counts["low_salience_episodic"] += (
                    kind == "episodic_log" and salience < 3.0
                )
            counts[name] = actor_counts
        return counts

    @staticmethod
    def _update_foreground_streaks(
        session: Any,
        context: Dict[str, Any],
        streaks: Dict[str, int],
    ) -> int:
        activations = context.get("agent_activations", {})
        maximum = 0
        for name, entity in session.runner.entities.items():
            controller = entity.get_component("AgentController")
            identity = entity.get_component("Identity")
            if controller is None or not (
                controller.activation_policy == "foreground"
                or bool(getattr(identity, "is_player", False))
            ):
                continue
            activation = activations.get(name, {})
            reason = str(activation.get("reason", ""))
            if activation.get("active") or reason == "action_in_progress":
                streaks[name] = 0
            else:
                streaks[name] = streaks.get(name, 0) + 1
            maximum = max(maximum, streaks[name])
        return maximum

    @staticmethod
    def _update_relation_histories(
        session: Any,
        histories: Dict[str, List[tuple[Any, ...]]],
    ) -> tuple[int, int]:
        book = session.runner.relation_registry.to_relationship_book()
        changes = 0
        oscillations = 0
        for relation_id, record in book.relationships.items():
            first, second = record.participants
            signature = (
                tuple(book.describe_direction(first, second)),
                tuple(book.describe_direction(second, first)),
                tuple(sorted(record.bits)),
            )
            history = histories.setdefault(relation_id, [])
            if history and history[-1] != signature:
                changes += 1
            if len(history) >= 2 and history[-2] == signature != history[-1]:
                oscillations += 1
            history.append(signature)
            if len(history) > 4:
                history.pop(0)
        return changes, oscillations

    @staticmethod
    def _update_action_repetition(
        context: Dict[str, Any],
        repetitions: Dict[str, tuple[tuple[str, str], int]],
    ) -> int:
        maximum = 0
        for action in context.get("simulation_result", {}).get(
            "resolved_actions", []
        ):
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", ""))
            signature = (
                str(action.get("action_kind", "")),
                str(action.get("action_target", "")),
            )
            previous, count = repetitions.get(actor, (("", ""), 0))
            count = count + 1 if signature == previous else 1
            repetitions[actor] = (signature, count)
            maximum = max(maximum, count)
        return maximum

    @staticmethod
    def _character_count(session: Any) -> int:
        return sum(
            entity.get_component("AgentController") is not None
            for entity in session.runner.entities.values()
        )

    @staticmethod
    def _output_context(quiet: bool):
        return (
            contextlib.redirect_stdout(io.StringIO())
            if quiet
            else contextlib.nullcontext()
        )
