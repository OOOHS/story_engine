import hashlib
import contextlib
import io
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.story_engine.evaluation.closure import EpisodeClosurePolicy
from src.story_engine.evaluation.episode import EpisodeReport, EpisodeRunner


@dataclass(frozen=True)
class EpisodeSweepFailure:
    seed: int | str
    phase: str
    error_type: str
    message: str


@dataclass(frozen=True)
class EpisodeSweepReport:
    requested_seeds: tuple[int | str, ...]
    steps_per_episode: int
    episodes: tuple[EpisodeReport, ...]
    failures: tuple[EpisodeSweepFailure, ...] = ()
    replay_mismatches: tuple[int | str, ...] = ()
    metrics: Dict[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def authoritative(self) -> bool:
        return (
            not self.failures
            and not self.replay_mismatches
            and len(self.episodes) == len(self.requested_seeds)
            and all(report.authoritative for report in self.episodes)
        )

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "requested_seeds": list(self.requested_seeds),
            "steps_per_episode": self.steps_per_episode,
            "authoritative": self.authoritative,
            "metrics": self.metrics,
            "quality_flags": list(self.quality_flags),
            "failures": [asdict(item) for item in self.failures],
            "replay_mismatches": list(self.replay_mismatches),
            "metadata": self.metadata,
            "episodes": [
                {
                    "seed": report.random_seed,
                    "authoritative": report.authoritative,
                    "metrics": report.metrics,
                    "quality_flags": list(report.quality_flags),
                    "violations": list(report.violations),
                }
                for report in self.episodes
            ],
        }

    def write_directory(self, path: str | Path) -> Path:
        root = Path(path)
        episode_dir = root / "episodes"
        transcript_dir = root / "transcripts"
        episode_dir.mkdir(parents=True, exist_ok=True)
        transcript_dir.mkdir(parents=True, exist_ok=True)
        episode_files = []
        for index, report in enumerate(self.episodes):
            digest = hashlib.sha256(
                str(report.random_seed).encode("utf-8")
            ).hexdigest()[:10]
            relative = Path("episodes") / f"{index:04d}-{digest}.json"
            transcript_relative = (
                Path("transcripts") / f"{index:04d}-{digest}.md"
            )
            report.write_json(root / relative)
            (root / transcript_relative).write_text(
                self._transcript_markdown(report),
                encoding="utf-8",
            )
            episode_files.append(
                {
                    "seed": report.random_seed,
                    "path": relative.as_posix(),
                    "transcript_path": transcript_relative.as_posix(),
                }
            )
        summary = self.to_summary_dict()
        summary["episode_files"] = episode_files
        summary["review_path"] = "review.md"
        (root / "review.md").write_text(
            self._review_markdown(episode_files),
            encoding="utf-8",
        )
        target = root / "summary.json"
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def _review_markdown(self, episode_files: List[Dict[str, Any]]) -> str:
        metrics = self.metrics
        budget = self.metadata.get("hermes_invocation_budget", {})
        metric_rows = [
            ("权威结果", self.authoritative),
            (
                "完成 Episode",
                f"{metrics.get('completed_episode_count', len(self.episodes))}"
                f" / {metrics.get('requested_episode_count', len(self.requested_seeds))}",
            ),
            ("失败数", metrics.get("failure_count", len(self.failures))),
            ("Replay mismatch", metrics.get("replay_mismatch_count", 0)),
            ("自然闭合率", metrics.get("closure_reached_rate")),
            ("跨步因果弧比例", metrics.get("causal_arc_episode_rate")),
            ("已闭合因果弧比例", metrics.get("resolved_causal_arc_episode_rate")),
            ("不同动作轨迹", metrics.get("unique_action_trace_count")),
            ("角色自陈动机比例", metrics.get("stated_motive_decision_rate")),
            ("动机被驳回数", metrics.get("rejected_motive_ref_count")),
            ("有动机可查的剧集比例", metrics.get("motivated_episode_rate")),
            ("Agent 目标采用数", metrics.get("agent_goal_adoption_count")),
            ("开放目标细化数", metrics.get("agent_goal_refinement_count")),
            ("结尾开放目标数", metrics.get("active_open_agent_goal_count")),
            ("目标结算数", metrics.get("goal_resolution_count")),
        ]
        lines = [
            "# Episode Sweep Review",
            "",
            "此文件只汇总结构指标并链接玩家可见 transcript；不包含 Agent 私有思考、未选候选、隐藏关系值或 GM 私有结算包。",
            "",
            "## 结构证据",
            "",
            "| 指标 | 值 |",
            "| --- | ---: |",
        ]
        lines.extend(
            f"| {label} | {self._markdown_value(value)} |"
            for label, value in metric_rows
        )
        if isinstance(budget, dict) and budget:
            lines.extend([
                "",
                "## Hermes 调用预算",
                "",
                "| configured | consumed | remaining | exhausted |",
                "| ---: | ---: | ---: | ---: |",
                "| "
                + " | ".join(
                    self._markdown_value(budget.get(key))
                    for key in (
                        "configured",
                        "consumed",
                        "remaining",
                        "exhausted",
                    )
                )
                + " |",
            ])
        flags = ", ".join(f"`{flag}`" for flag in self.quality_flags)
        lines.extend([
            "",
            "## Sweep 标记",
            "",
            flags or "_无自动退化标记。_",
        ])
        if self.failures:
            lines.extend([
                "",
                "## 失败 Episode",
                "",
                "| Seed | Phase | Error type |",
                "| --- | --- | --- |",
            ])
            lines.extend(
                "| "
                + " | ".join([
                    self._markdown_value(item.seed),
                    self._markdown_value(item.phase),
                    self._markdown_value(item.error_type),
                ])
                + " |"
                for item in self.failures
            )
        if self.replay_mismatches:
            lines.extend([
                "",
                "## Replay Mismatch Seeds",
                "",
                ", ".join(
                    f"`{self._markdown_value(seed)}`"
                    for seed in self.replay_mismatches
                ),
            ])
        lines.extend([
            "",
            "## Episodes",
            "",
            "| Seed | 权威 | 闭合 | 因果弧 | 标记 | 玩家文本 | 结构 trace |",
            "| --- | ---: | ---: | ---: | --- | --- | --- |",
        ])
        for report, files in zip(self.episodes, episode_files):
            episode_flags = ", ".join(
                f"`{flag}`" for flag in report.quality_flags
            ) or "—"
            lines.append(
                "| "
                + " | ".join([
                    self._markdown_value(report.random_seed),
                    self._markdown_value(report.authoritative),
                    self._markdown_value(report.closure_reached),
                    self._markdown_value(
                        report.metrics.get("causal_arc_present")
                    ),
                    episode_flags,
                    f"[Transcript]({files['transcript_path']})",
                    f"[JSON]({files['path']})",
                ])
                + " |"
            )
        lines.extend([
            "",
            "## 人工审阅清单",
            "",
            "- 不同角色是否呈现稳定且可区分的动机、语气与行动方式？",
            "- 角色是否回应了前几轮真实发生的后果，而不是每轮重新开始？",
            "- 世界变化是否继续产生目标、关系、知识或新的行动机会？",
            "- 不同 seed 是否形成不同但仍合理的轨迹，而不是随机噪声？",
            "- 玩家可见文本是否避免模板重复、后台信息泄漏和无事实依据的全知叙述？",
            "- 结尾是否解决了主要行动线程，或至少形成自然的章节边界？",
            "- 故事是否真正有趣？这一项必须阅读 transcript，不能由结构指标代替。",
        ])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _markdown_value(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "yes" if value else "no"
        return " ".join(str(value).split()).replace("|", "\\|")

    @staticmethod
    def _transcript_markdown(report: EpisodeReport) -> str:
        lines = [
            f"# Episode {report.random_seed}",
            "",
            f"- Termination: `{report.termination_reason}`",
            f"- Closure reached: `{str(report.closure_reached).lower()}`",
            f"- Authoritative: `{str(report.authoritative).lower()}`",
        ]
        if report.quality_flags:
            lines.append(
                "- Quality flags: "
                + ", ".join(f"`{flag}`" for flag in report.quality_flags)
            )
        lines.extend(["", "## Narrative", ""])
        narrative_steps = [
            (trace.index, trace.narrative_text.strip())
            for trace in report.steps
            if trace.narrative_text.strip()
        ]
        if not narrative_steps:
            lines.append("_No player-visible narrative was produced._")
        else:
            for step_index, narrative in narrative_steps:
                lines.extend([f"### Step {step_index + 1}", "", narrative, ""])
        return "\n".join(lines).rstrip() + "\n"


class EpisodeSweepRunner:
    """Run the same story seed across RNG seeds and audit degeneration rates."""

    def __init__(
        self,
        episode_runner_factory: Callable[[], EpisodeRunner] = EpisodeRunner,
    ) -> None:
        self._episode_runner_factory = episode_runner_factory

    def run(
        self,
        session_factory: Callable[[int | str], Any],
        *,
        seeds: Iterable[int | str],
        steps: int,
        step_inputs: Optional[
            Callable[[int, Any], Dict[str, Any] | None]
        ] = None,
        verify_replay: bool = False,
        quiet: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        closure_policy: EpisodeClosurePolicy | None = None,
    ) -> EpisodeSweepReport:
        requested = tuple(seeds)
        reports: List[EpisodeReport] = []
        failures: List[EpisodeSweepFailure] = []
        replay_mismatches: List[int | str] = []
        for seed in requested:
            try:
                with self._output_context(quiet):
                    session = session_factory(seed)
                    run_kwargs = {
                        "steps": steps,
                        "step_inputs": step_inputs,
                    }
                    if closure_policy is not None:
                        run_kwargs["closure_policy"] = closure_policy
                    report = self._episode_runner_factory().run(session, **run_kwargs)
                if report.random_seed != seed:
                    raise ValueError(
                        f"session factory returned seed {report.random_seed!r} "
                        f"for requested seed {seed!r}"
                    )
                reports.append(report)
            except Exception as exc:
                failures.append(self._failure(seed, "episode", exc))
                continue
            if not verify_replay:
                continue
            try:
                with self._output_context(quiet):
                    replay_session = session_factory(seed)
                    replay_kwargs = {
                        "steps": steps,
                        "step_inputs": step_inputs,
                    }
                    if closure_policy is not None:
                        replay_kwargs["closure_policy"] = closure_policy
                    replay = self._episode_runner_factory().run(
                        replay_session, **replay_kwargs
                    )
                if self._signature(report) != self._signature(replay):
                    replay_mismatches.append(seed)
            except Exception as exc:
                failures.append(self._failure(seed, "replay", exc))

        metrics, flags = self._aggregate(
            requested=requested,
            reports=reports,
            failures=failures,
            replay_mismatches=replay_mismatches,
            closure_policy=closure_policy,
        )
        return EpisodeSweepReport(
            requested_seeds=requested,
            steps_per_episode=max(0, int(steps)),
            episodes=tuple(reports),
            failures=tuple(failures),
            replay_mismatches=tuple(replay_mismatches),
            metrics=metrics,
            quality_flags=tuple(flags),
            metadata=dict(metadata or {}),
        )

    def _aggregate(
        self,
        *,
        requested: tuple[int | str, ...],
        reports: List[EpisodeReport],
        failures: List[EpisodeSweepFailure],
        replay_mismatches: List[int | str],
        closure_policy: EpisodeClosurePolicy | None = None,
    ) -> tuple[Dict[str, Any], List[str]]:
        episode_count = len(reports)
        flag_counts: Dict[str, int] = {}
        violation_count = 0
        for report in reports:
            violation_count += len(report.violations)
            for flag in report.quality_flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1

        numeric_metrics: Dict[str, List[float]] = {}
        for report in reports:
            for key, value in report.metrics.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                numeric_metrics.setdefault(key, []).append(float(value))
        metric_summary = {
            key: {
                "mean": round(statistics.fmean(values), 6),
                "min": min(values),
                "max": max(values),
                "sum": round(sum(values), 6),
            }
            for key, values in sorted(numeric_metrics.items())
            if values
        }
        action_signatures = {
            self._action_trace_signature(report)
            for report in reports
        }
        final_world_hashes = {
            report.steps[-1].world_hash_after
            for report in reports
            if report.steps
        }
        deciding_reports = [
            report
            for report in reports
            if float(report.metrics.get("decision_steps", 0) or 0) > 0
        ]
        motivated_reports = [
            report
            for report in deciding_reports
            if int(report.metrics.get("motivated_action_count", 0) or 0) > 0
        ]
        verifiable_goals = sum(
            int(report.metrics.get("verifiable_goal_count", 0) or 0)
            for report in reports
        )
        goal_resolutions = sum(
            int(report.metrics.get("goal_resolution_count", 0) or 0)
            for report in reports
        )
        goal_adoptions = sum(
            int(report.metrics.get("agent_goal_adoption_count", 0) or 0)
            for report in reports
        )
        goal_refinements = sum(
            int(report.metrics.get("agent_goal_refinement_count", 0) or 0)
            for report in reports
        )
        active_open_agent_goals = sum(
            int(report.metrics.get("active_open_agent_goal_count", 0) or 0)
            for report in reports
        )
        decisions = sum(
            int(report.metrics.get("decision_count", 0) or 0)
            for report in reports
        )
        stated_motives = sum(
            int(report.metrics.get("stated_motive_count", 0) or 0)
            for report in reports
        )
        rejected_motive_refs = sum(
            int(report.metrics.get("rejected_motive_ref_count", 0) or 0)
            for report in reports
        )
        closed_reports = [report for report in reports if report.closure_reached]
        causal_arc_reports = [
            report for report in reports
            if bool(report.metrics.get("causal_arc_present", False))
        ]
        resolved_causal_arc_reports = [
            report for report in reports
            if bool(report.metrics.get("resolved_causal_arc", False))
        ]
        closure_steps = [
            int(report.metrics["steps_to_closure"])
            for report in closed_reports
            if report.metrics.get("steps_to_closure") is not None
        ]
        rates = {
            flag: round(count / episode_count, 6) if episode_count else 0.0
            for flag, count in sorted(flag_counts.items())
        }
        metrics = {
            "requested_episode_count": len(requested),
            "completed_episode_count": episode_count,
            "authoritative_episode_count": sum(
                report.authoritative for report in reports
            ),
            "failure_count": len(failures),
            "violation_count": violation_count,
            "replay_mismatch_count": len(replay_mismatches),
            "quality_flag_counts": dict(sorted(flag_counts.items())),
            "quality_flag_rates": rates,
            "metric_summary": metric_summary,
            "unique_action_trace_count": len(action_signatures),
            "unique_final_world_state_count": len(final_world_hashes),
            "decision_count": decisions,
            "stated_motive_count": stated_motives,
            "rejected_motive_ref_count": rejected_motive_refs,
            "stated_motive_decision_rate": (
                round(stated_motives / decisions, 6) if decisions else None
            ),
            "motivated_episode_count": len(motivated_reports),
            "motivated_episode_rate": (
                round(len(motivated_reports) / len(deciding_reports), 6)
                if deciding_reports
                else None
            ),
            "motive_handoff_count": sum(
                int(report.metrics.get("motive_handoff_count", 0) or 0)
                for report in reports
            ),
            "motivated_action_count": sum(
                int(report.metrics.get("motivated_action_count", 0) or 0)
                for report in reports
            ),
            "verifiable_goal_count": verifiable_goals,
            "agent_goal_adoption_count": goal_adoptions,
            "goal_resolution_count": goal_resolutions,
            "agent_goal_refinement_count": goal_refinements,
            "active_open_agent_goal_count": active_open_agent_goals,
            "goal_resolution_rate": (
                round(goal_resolutions / verifiable_goals, 6)
                if verifiable_goals
                else None
            ),
            "closure_policy_enabled": closure_policy is not None,
            "closure_reached_count": len(closed_reports),
            "closure_reached_rate": (
                round(len(closed_reports) / episode_count, 6)
                if closure_policy is not None and episode_count
                else None
            ),
            "causal_arc_episode_count": len(causal_arc_reports),
            "causal_arc_episode_rate": (
                round(len(causal_arc_reports) / episode_count, 6)
                if episode_count
                else None
            ),
            "resolved_causal_arc_episode_count": len(
                resolved_causal_arc_reports
            ),
            "resolved_causal_arc_episode_rate": (
                round(len(resolved_causal_arc_reports) / episode_count, 6)
                if episode_count
                else None
            ),
            "cross_step_causal_handoff_count": sum(
                int(report.metrics.get(
                    "cross_step_causal_handoff_count", 0
                ) or 0)
                for report in reports
            ),
            "steps_to_closure": (
                {
                    "mean": round(statistics.fmean(closure_steps), 6),
                    "min": min(closure_steps),
                    "max": max(closure_steps),
                }
                if closure_steps
                else None
            ),
        }
        flags = []
        if not requested:
            flags.append("empty_sweep")
        if failures:
            flags.append("episode_failures")
        if violation_count:
            flags.append("authority_violations")
        if replay_mismatches:
            flags.append("replay_mismatch")
        if episode_count and flag_counts.get("stagnant_episode", 0) == episode_count:
            flags.append("all_episodes_stagnant")
        if rates.get("deadlocked_episode", 0.0) >= 0.5:
            flags.append("frequent_deadlock")
        if rates.get("repetitive_actions", 0.0) >= 0.5:
            flags.append("frequent_repetitive_actions")
        if (
            len(deciding_reports) >= 3
            and len(
                {
                    self._action_trace_signature(report)
                    for report in deciding_reports
                }
            )
            <= 1
        ):
            flags.append("seed_insensitive_policy")
        if deciding_reports and not motivated_reports:
            flags.append("no_policy_motive_chain")
        elif (
            len(deciding_reports) >= 3
            and len(motivated_reports) / len(deciding_reports) < 0.5
        ):
            flags.append("low_policy_motive_coverage")
        # A character citing goals she does not hold is worse than staying
        # silent about her reasons, so it gets its own flag rather than
        # inflating the motive coverage above.
        if decisions and rejected_motive_refs >= max(1, decisions // 2):
            flags.append("unbacked_motive_claims")
        if verifiable_goals and goal_resolutions == 0:
            flags.append("no_verifiable_goal_resolution")
        long_horizon_reports = [
            report
            for report in reports
            if int(report.metrics.get("step_count", 0) or 0) >= 12
        ]
        if long_horizon_reports and sum(
            int(report.metrics.get("active_open_agent_goal_count", 0) or 0)
            for report in long_horizon_reports
        ) >= len(long_horizon_reports):
            flags.append("open_goal_backlog")
        if closure_policy is not None and episode_count:
            closure_rate = len(closed_reports) / episode_count
            if closure_rate == 0:
                flags.append("no_episode_closure")
            elif closure_rate < 0.5:
                flags.append("low_closure_rate")
        return metrics, flags

    @staticmethod
    def _action_trace_signature(report: EpisodeReport) -> tuple[Any, ...]:
        return tuple(
            (trace.actor_actions, trace.stated_motives)
            for trace in report.steps
        )

    @staticmethod
    def _signature(report: EpisodeReport) -> str:
        payload = {
            "metrics": report.metrics,
            "quality_flags": report.quality_flags,
            "violations": report.violations,
            "steps": [
                {
                    "world_hash_after": step.world_hash_after,
                    "character_hash_after": step.character_hash_after,
                    "actor_actions": step.actor_actions,
                    "stated_motives": step.stated_motives,
                    "rejected_motive_refs": step.rejected_motive_refs,
                    "material_change_kinds": step.material_change_kinds,
                    "irreversible_changes": step.irreversible_changes,
                    "causal_handoffs": step.causal_handoffs,
                }
                for step in report.steps
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure(seed: int | str, phase: str, exc: Exception) -> EpisodeSweepFailure:
        return EpisodeSweepFailure(
            seed=seed,
            phase=phase,
            error_type=type(exc).__name__,
            message=" ".join(str(exc).split())[:1000],
        )

    @staticmethod
    def _output_context(quiet: bool):
        return (
            contextlib.redirect_stdout(io.StringIO())
            if quiet
            else contextlib.nullcontext()
        )
