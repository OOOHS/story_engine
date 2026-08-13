from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.evaluation import EpisodeClosurePolicy, EpisodeRunner
from src.story_engine_content.evaluation.minimal_navigation_recovery import (
    ACTOR,
    DESTINATION,
    build_minimal_navigation_recovery_scenario,
    create_minimal_navigation_recovery_session,
)


def test_navigation_recovery_seed_has_no_authored_narrative_dependency():
    scenario = build_minimal_navigation_recovery_scenario()

    assert scenario.storylets == []
    assert scenario.plot_entities == []
    assert scenario.plot_rules == []
    assert scenario.agreement_offer_templates == []


def test_stale_route_grows_a_recovery_goal_and_reaches_stable_closure():
    session = create_minimal_navigation_recovery_session("stale-map")
    assert isinstance(
        session.entities["GameMaster"].get_component("SimulationControl"),
        HostRuleSimulationControl,
    )

    report = EpisodeRunner().run(
        session,
        steps=10,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    actor = session.entities[ACTOR]
    goals = actor.get_component("GoalState")
    navigation = actor.get_component("NavigationState")
    agent_goals = [
        record for record in goals.goals.values()
        if record.source_kind == "navigation_problem"
    ]
    actions = [
        (actor_name, kind, target)
        for step in report.steps
        for actor_name, kind, target in step.actor_actions
    ]

    assert report.authoritative is True
    assert report.closure_reached is True
    assert len(report.steps) < 10
    assert session.entities["GameMaster"].get_component(
        "SceneState"
    ).get_actor_location(ACTOR) == DESTINATION
    assert len(agent_goals) == 1
    assert agent_goals[0].status == "achieved"
    assert all(problem.status == "resolved" for problem in navigation.problems.values())
    assert sum(kind == "move" for _, kind, _ in actions) >= 3
    assert report.metrics["agent_goal_adoption_count"] == 1
    assert "navigation" in {
        kind for step in report.steps for kind in step.material_change_kinds
    }
    changes = [item for step in report.steps for item in step.irreversible_changes]
    handoffs = [item for step in report.steps for item in step.causal_handoffs]
    assert any(item.startswith("navigation_problem_created:") for item in changes)
    assert any(item.startswith("navigation_problem_resolved:") for item in changes)
    assert any("<-navigation_problem:" in item for item in handoffs)
    assert any("<-movement_failure:stale_route:" in item for item in handoffs)
    assert report.metrics["causal_handoff_count"] >= 1
    assert report.metrics["max_causal_chain_depth"] >= 2
    assert report.final_closure_status["details"][
        "active_navigation_problem_count"
    ] == 0
