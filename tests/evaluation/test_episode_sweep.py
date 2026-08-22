import importlib.util
from pathlib import Path
from types import SimpleNamespace

from src.story_engine.evaluation import (
    EpisodeClosurePolicy,
    EpisodeReport,
    EpisodeStepTrace,
    EpisodeSweepReport,
    EpisodeSweepRunner,
)


def _step(
    seed,
    *,
    index=0,
    action_kind="observe",
    action_target="房间",
    world_suffix="a",
):
    return EpisodeStepTrace(
        index=index,
        simulation_time_before=index,
        simulation_time_after=index + 1,
        world_hash_before=f"before:{seed}:{index}",
        world_hash_after=f"after:{seed}:{index}:{world_suffix}",
        character_hash_before=f"char-before:{seed}:{index}",
        character_hash_after=f"char-after:{seed}:{index}",
        proposal_actors=("甲",),
        resolved_actors=("甲",),
        action_kinds=(action_kind,),
        committed=True,
        relationship_count=0,
        sentiment_count=0,
        agreement_count=0,
        modifier_count=0,
        claim_count=0,
        known_claim_count=0,
        actor_actions=(("甲", action_kind, action_target),),
        causal_handoffs=(
            f"goal:甲:follow-up<-world_event:event-{world_suffix}",
        ),
        deciding_actors=("甲",),
        stated_motives=(("甲", "goal", f"want-{action_kind}"),),
        narrative_text=f"甲在第{index}轮选择了{action_kind}。",
    )


def _report(
    seed,
    *,
    action_kind="observe",
    action_target="房间",
    flags=(),
    violations=(),
    world_suffix="a",
    verifiable_goals=1,
    goal_resolutions=1,
    motivated_actions=1,
    goal_refinements=1,
    active_open_goals=0,
):
    return EpisodeReport(
        random_seed=seed,
        steps=(
            _step(
                seed,
                action_kind=action_kind,
                action_target=action_target,
                world_suffix=world_suffix,
            ),
        ),
        metrics={
            "step_count": 1,
            "committed_steps": 1,
            "decision_steps": 1,
            "decision_count": 1,
            "stated_motive_count": 1,
            "rejected_motive_ref_count": 0,
            "actor_differentiation": 0.5,
            "verifiable_goal_count": verifiable_goals,
            "goal_resolution_count": goal_resolutions,
            "agent_goal_refinement_count": goal_refinements,
            "active_open_agent_goal_count": active_open_goals,
            "motive_handoff_count": motivated_actions,
            "motivated_action_count": motivated_actions,
            "causal_arc_present": True,
            "resolved_causal_arc": True,
            "cross_step_causal_handoff_count": 2,
        },
        quality_flags=tuple(flags),
        violations=tuple(violations),
    )


class FakeEpisodeRunner:
    def run(self, session, *, steps, step_inputs=None):
        del steps, step_inputs
        kind = "observe" if int(session.random_seed) % 2 else "communicate"
        return _report(session.random_seed, action_kind=kind)


def test_sweep_aggregates_seed_diversity_and_goal_resolution():
    sweep = EpisodeSweepRunner(lambda: FakeEpisodeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[1, 2, 3],
        steps=6,
        metadata={"scenario": "minimal"},
    )

    assert sweep.authoritative is True
    assert sweep.metrics["completed_episode_count"] == 3
    assert sweep.metrics["authoritative_episode_count"] == 3
    assert sweep.metrics["unique_action_trace_count"] == 2
    assert sweep.metrics["unique_final_world_state_count"] == 3
    assert sweep.metrics["goal_resolution_rate"] == 1.0
    assert sweep.metrics["agent_goal_adoption_count"] == 0
    assert sweep.metrics["agent_goal_refinement_count"] == 3
    assert sweep.metrics["active_open_agent_goal_count"] == 0
    assert sweep.metrics["motivated_episode_count"] == 3
    assert sweep.metrics["motivated_episode_rate"] == 1.0
    assert sweep.metrics["motivated_action_count"] == 3
    assert sweep.metrics["causal_arc_episode_count"] == 3
    assert sweep.metrics["causal_arc_episode_rate"] == 1.0
    assert sweep.metrics["resolved_causal_arc_episode_count"] == 3
    assert sweep.metrics["cross_step_causal_handoff_count"] == 6
    assert sweep.quality_flags == ()
    assert sweep.metadata == {"scenario": "minimal"}


def test_long_horizon_open_goal_backlog_is_reported_without_guessing_quality():
    report = _report(
        "open",
        goal_refinements=0,
        active_open_goals=1,
    )
    report = EpisodeReport(
        **{
            **report.__dict__,
            "metrics": {**report.metrics, "step_count": 12},
        }
    )

    metrics, flags = EpisodeSweepRunner()._aggregate(
        requested=("open",),
        reports=[report],
        failures=[],
        replay_mismatches=[],
    )

    assert metrics["active_open_agent_goal_count"] == 1
    assert "open_goal_backlog" in flags


def test_sweep_flags_characters_citing_reasons_they_cannot_back():
    reports = []
    for seed in (1, 2, 3):
        report = _report(seed)
        reports.append(
            EpisodeReport(
                **{
                    **report.__dict__,
                    "metrics": {
                        **report.metrics,
                        "stated_motive_count": 0,
                        "rejected_motive_ref_count": 2,
                    },
                }
            )
        )

    metrics, flags = EpisodeSweepRunner()._aggregate(
        requested=(1, 2, 3),
        reports=reports,
        failures=[],
        replay_mismatches=[],
    )

    assert metrics["decision_count"] == 3
    assert metrics["stated_motive_count"] == 0
    assert metrics["rejected_motive_ref_count"] == 6
    assert metrics["stated_motive_decision_rate"] == 0.0
    assert "unbacked_motive_claims" in flags


class ClosureFakeRunner:
    def run(self, session, *, steps, step_inputs=None, closure_policy=None):
        del steps, step_inputs
        closed = int(session.random_seed) != 3
        report = _report(session.random_seed)
        return EpisodeReport(
            random_seed=report.random_seed,
            steps=report.steps,
            metrics={
                **report.metrics,
                "closure_reached": closed,
                "steps_to_closure": 2 if closed else None,
            },
            closure_reached=closed,
            closure_policy=closure_policy.to_dict() if closure_policy else {},
        )


def test_sweep_aggregates_host_audited_closure_rate():
    sweep = EpisodeSweepRunner(lambda: ClosureFakeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[1, 2, 3],
        steps=8,
        closure_policy=EpisodeClosurePolicy(stable_steps=1),
    )

    assert sweep.metrics["closure_policy_enabled"] is True
    assert sweep.metrics["closure_reached_count"] == 2
    assert sweep.metrics["closure_reached_rate"] == 0.666667
    assert sweep.metrics["steps_to_closure"] == {"mean": 2.0, "min": 2, "max": 2}
    assert "low_closure_rate" not in sweep.quality_flags


def test_sweep_writes_summary_and_individual_episode_artifacts(tmp_path):
    sweep = EpisodeSweepRunner(lambda: FakeEpisodeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[7, 8],
        steps=2,
    )

    target = sweep.write_directory(tmp_path / "sweep")

    payload = target.read_text(encoding="utf-8")
    episode_files = sorted((tmp_path / "sweep" / "episodes").glob("*.json"))
    transcript_files = sorted(
        (tmp_path / "sweep" / "transcripts").glob("*.md")
    )
    review = (tmp_path / "sweep" / "review.md").read_text(encoding="utf-8")
    assert '"authoritative": true' in payload
    assert '"episode_files"' in payload
    assert '"transcript_path"' in payload
    assert '"review_path": "review.md"' in payload
    assert len(episode_files) == 2
    assert len(transcript_files) == 2
    assert '"random_seed": 7' in episode_files[0].read_text(encoding="utf-8")
    assert '"narrative_text"' in episode_files[0].read_text(encoding="utf-8")
    transcript = transcript_files[0].read_text(encoding="utf-8")
    assert "# Episode 7" in transcript
    assert "## Narrative" in transcript
    assert "甲在第0轮选择了observe。" in transcript
    assert "stated_motives" not in transcript
    assert "# Episode Sweep Review" in review
    assert "[Transcript](transcripts/" in review
    assert "[JSON](episodes/" in review
    assert "故事是否真正有趣" in review
    assert "甲在第0轮选择了observe" not in review
    assert "stated_motives" not in review


def test_sweep_transcript_marks_missing_player_visible_narrative(tmp_path):
    report = _report(9)
    report = EpisodeReport(
        **{
            **report.__dict__,
            "steps": tuple(
                EpisodeStepTrace(
                    **{**trace.__dict__, "narrative_text": ""}
                )
                for trace in report.steps
            ),
        }
    )
    sweep = EpisodeSweepReport(
        requested_seeds=(9,),
        steps_per_episode=1,
        episodes=(report,),
    )

    sweep.write_directory(tmp_path / "sweep")

    transcript = next((tmp_path / "sweep" / "transcripts").glob("*.md"))
    assert "_No player-visible narrative was produced._" in transcript.read_text(
        encoding="utf-8"
    )


class NondeterministicFakeRunner:
    calls = {}

    def run(self, session, *, steps, step_inputs=None):
        del steps, step_inputs
        count = self.calls.get(session.random_seed, 0) + 1
        self.calls[session.random_seed] = count
        return _report(session.random_seed, world_suffix=str(count))


def test_replay_audit_marks_same_seed_divergence():
    NondeterministicFakeRunner.calls = {}
    sweep = EpisodeSweepRunner(lambda: NondeterministicFakeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[11],
        steps=3,
        verify_replay=True,
    )

    assert sweep.authoritative is False
    assert sweep.replay_mismatches == (11,)
    assert sweep.metrics["replay_mismatch_count"] == 1
    assert "replay_mismatch" in sweep.quality_flags


def test_replay_signature_includes_explicit_causal_provenance():
    left = _report("same", world_suffix="source-a")
    right_step = EpisodeStepTrace(
        **{
            **left.steps[0].__dict__,
            "causal_handoffs": (
                "goal:甲:follow-up<-world_event:source-b",
            ),
        }
    )
    right = EpisodeReport(
        random_seed=left.random_seed,
        steps=(right_step,),
        metrics=dict(left.metrics),
    )

    assert EpisodeSweepRunner._signature(left) != EpisodeSweepRunner._signature(right)


def test_replay_signature_includes_the_reason_each_character_gave():
    left = _report("same")
    left_step = EpisodeStepTrace(
        **{
            **left.steps[0].__dict__,
            "stated_motives": (("甲", "goal", "find-exit"),),
        }
    )
    right_step = EpisodeStepTrace(
        **{
            **left_step.__dict__,
            "stated_motives": (("甲", "obligation", "escort-guest"),),
        }
    )
    left_report = EpisodeReport(
        random_seed="same", steps=(left_step,), metrics=dict(left.metrics)
    )
    right_report = EpisodeReport(
        random_seed="same", steps=(right_step,), metrics=dict(left.metrics)
    )

    assert EpisodeSweepRunner._signature(left_report) != (
        EpisodeSweepRunner._signature(right_report)
    )


def test_sweep_isolates_factory_failure_and_keeps_other_seeds():
    def factory(seed):
        if seed == "bad":
            raise RuntimeError("broken scenario seed")
        return SimpleNamespace(random_seed=seed)

    class StringFakeRunner:
        def run(self, session, *, steps, step_inputs=None):
            del steps, step_inputs
            return _report(session.random_seed)

    sweep = EpisodeSweepRunner(lambda: StringFakeRunner()).run(
        factory,
        seeds=["good", "bad", "also-good"],
        steps=1,
    )

    assert len(sweep.episodes) == 2
    assert len(sweep.failures) == 1
    assert sweep.failures[0].seed == "bad"
    assert sweep.failures[0].phase == "episode"
    assert "episode_failures" in sweep.quality_flags
    assert sweep.authoritative is False


class DegenerateFakeRunner:
    def run(self, session, *, steps, step_inputs=None):
        del steps, step_inputs
        return _report(
            session.random_seed,
            action_kind="wait",
            flags=("stagnant_episode", "deadlocked_episode"),
            verifiable_goals=1,
            goal_resolutions=0,
        )


def test_sweep_flags_cross_seed_structural_degeneration():
    sweep = EpisodeSweepRunner(lambda: DegenerateFakeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[1, 2, 3],
        steps=8,
    )

    assert sweep.metrics["quality_flag_rates"]["deadlocked_episode"] == 1.0
    assert "all_episodes_stagnant" in sweep.quality_flags
    assert "frequent_deadlock" in sweep.quality_flags
    assert "seed_insensitive_policy" in sweep.quality_flags
    assert "no_verifiable_goal_resolution" in sweep.quality_flags


class TargetDiverseFakeRunner:
    def run(self, session, *, steps, step_inputs=None):
        del steps, step_inputs
        return _report(
            session.random_seed,
            action_kind="observe",
            action_target=f"目标-{session.random_seed}",
        )


def test_seed_sensitivity_includes_explicit_action_target():
    sweep = EpisodeSweepRunner(lambda: TargetDiverseFakeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[1, 2, 3],
        steps=1,
    )

    assert sweep.metrics["unique_action_trace_count"] == 3
    assert "seed_insensitive_policy" not in sweep.quality_flags


class UnmotivatedFakeRunner:
    def run(self, session, *, steps, step_inputs=None):
        del steps, step_inputs
        return _report(session.random_seed, motivated_actions=0)


def test_sweep_flags_characters_who_never_connect_a_reason_to_an_action():
    sweep = EpisodeSweepRunner(lambda: UnmotivatedFakeRunner()).run(
        lambda seed: SimpleNamespace(random_seed=seed),
        seeds=[1, 2, 3],
        steps=4,
    )

    assert sweep.metrics["decision_count"] == 3
    assert sweep.metrics["stated_motive_count"] == 3
    assert sweep.metrics["rejected_motive_ref_count"] == 0
    assert sweep.metrics["stated_motive_decision_rate"] == 1.0
    assert sweep.metrics["motivated_episode_count"] == 0
    assert sweep.metrics["motivated_episode_rate"] == 0.0
    assert "no_policy_motive_chain" in sweep.quality_flags


def test_launcher_helpers_parse_factory_seeds_and_metadata():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_episode_sweep.py"
    )
    spec = importlib.util.spec_from_file_location("episode_sweep_launcher", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.load_factory("builtins:len") is len
    assert module.parse_seeds("1, alpha, -2") == [1, "alpha", -2]
    assert module.parse_metadata(["scenario=demo", "runtime=rule"]) == {
        "scenario": "demo",
        "runtime": "rule",
    }
    args = module.build_parser().parse_args(
        [
            "--factory", "builtins:len",
            "--output", "artifacts/demo",
            "--stop-on-closure",
            "--closure-stable-steps", "3",
            "--closure-minimum-steps", "2",
            "--require-goal-anchor",
            "--allow-unexercised-agents",
            "--allow-material-change-closure",
            "--allow-actionable-critical-needs-closure",
            "--require-plot-closure",
            "--strict-quality",
        ]
    )
    assert args.stop_on_closure is True
    assert args.closure_stable_steps == 3
    assert args.closure_minimum_steps == 2
    assert args.strict_quality is True

    authoritative = EpisodeSweepReport(
        requested_seeds=(),
        steps_per_episode=0,
        episodes=(),
    )
    flagged = EpisodeSweepReport(
        requested_seeds=(),
        steps_per_episode=0,
        episodes=(),
        quality_flags=("open_goal_backlog",),
    )
    failed = EpisodeSweepReport(
        requested_seeds=(1,),
        steps_per_episode=0,
        episodes=(),
    )
    assert module.report_exit_code(authoritative, strict_quality=True) == 0
    assert module.report_exit_code(flagged, strict_quality=False) == 0
    assert module.report_exit_code(flagged, strict_quality=True) == 3
    assert module.report_exit_code(failed, strict_quality=True) == 2
    assert args.require_goal_anchor is True
    assert args.allow_unexercised_agents is True
    assert args.allow_material_change_closure is True
    assert args.allow_actionable_critical_needs_closure is True
    assert args.require_plot_closure is True
