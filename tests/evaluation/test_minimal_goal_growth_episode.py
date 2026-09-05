from src.story_engine.evaluation import EpisodeClosurePolicy, EpisodeRunner
from src.story_engine_content.evaluation.minimal_goal_growth import (
    ACTOR,
    CORRIDOR,
    KEY,
    build_minimal_goal_growth_scenario,
    create_minimal_goal_growth_session,
)
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)




def test_resolved_seed_goal_grows_a_followup_before_episode_closes():
    session = create_minimal_goal_growth_session("growth")
    assert isinstance(
        session.entities["WorldHost"].get_component("SimulationControl"),
        HostRuleSimulationControl,
    )
    report = EpisodeRunner().run(
        session,
        steps=10,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    state = session.entities[ACTOR].get_component("GoalState")
    agent_goals = [record for record in state.goals.values() if record.origin == "agent"]
    changes = [change for step in report.steps for change in step.irreversible_changes]

    assert report.authoritative is True
    assert report.closure_reached is True
    assert len(report.steps) < 10
    assert report.metrics["agent_goal_adoption_count"] == 1
    assert report.metrics["active_agent_goal_count"] == 0
    assert report.metrics["cross_step_causal_handoff_count"] >= 1
    assert report.metrics["max_causal_span_steps"] >= 2
    assert report.metrics["causal_arc_present"] is True
    assert report.metrics["resolved_causal_arc"] is True
    assert len(agent_goals) == 1
    assert agent_goals[0].status == "achieved"
    assert any(change.startswith(f"goal_adopted:{ACTOR}:") for change in changes)
    assert any(change.endswith(":achieved") for change in changes)
    scene = session.entities["WorldHost"].get_component("SceneState")
    assert scene.get_object_state(KEY)["owner"] == ACTOR
    assert scene.get_actor_location(ACTOR) == CORRIDOR
