from src.story_engine.evaluation import EpisodeSweepRunner
from src.story_engine_content.evaluation.minimal_investigation import (
    create_minimal_investigation_session,
    build_minimal_investigation_scenario,
)
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)


def test_minimal_investigation_seed_has_no_storylet_or_core_story_dependency():
    scenario = build_minimal_investigation_scenario()

    assert scenario.storylets == []
    assert scenario.plot_entities == []
    assert scenario.plot_rules == []
    assert len(scenario.characters) == 2
    assert len(scenario.claims) == 1
    assert all(character.agent_runtime == "investigation-policy" for character in scenario.characters)


def test_real_multi_seed_investigation_sweep_meets_emergence_floor():
    probe = create_minimal_investigation_session("host-only-probe")
    assert isinstance(
        probe.entities["GameMaster"].get_component("SimulationControl"),
        HostRuleSimulationControl,
    )
    sweep = EpisodeSweepRunner().run(
        create_minimal_investigation_session,
        seeds=[0, 3, 7],
        steps=4,
        quiet=True,
        metadata={"scenario": "minimal-investigation-regression"},
    )

    assert sweep.authoritative is True
    assert sweep.quality_flags == ()
    assert sweep.metrics["unique_action_trace_count"] >= 2
    assert sweep.metrics["goal_resolution_rate"] == 1.0
    assert sweep.metrics["violation_count"] == 0
    assert sweep.metrics["policy_motivated_episode_rate"] == 1.0
    assert sweep.metrics["policy_motivated_action_count"] >= 3
    summary = sweep.metrics["metric_summary"]
    assert summary["active_goal_count"]["max"] == 0
    assert summary["relationship_count"]["min"] == 1
    assert summary["known_claim_count"]["mean"] >= 1.5
    assert summary["actor_differentiation"]["mean"] >= 0.3

    winners = {
        change.split(":")[1]
        for report in sweep.episodes
        for step in report.steps
        for change in step.irreversible_changes
        if change.endswith(":achieved")
    }
    assert winners == {"调查者", "保管人"}
    handoffs = [
        edge
        for report in sweep.episodes
        for step in report.steps
        for edge in step.causal_handoffs
    ]
    assert any(
        edge.startswith(
            "claim_knowledge:调查者:ledger_implicates_keeper"
            "<-evidence_observation:调查者:密封账册:step:"
        )
        for edge in handoffs
    )
    assert any(
        edge.startswith("evidence_observation:调查者:密封账册:step:")
        and "<-resolved_action:step:" in edge
        and edge.endswith(":actor:调查者")
        for edge in handoffs
    )
