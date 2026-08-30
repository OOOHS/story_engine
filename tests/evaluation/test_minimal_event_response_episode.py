from src.story_engine.evaluation import EpisodeClosurePolicy, EpisodeRunner
from src.story_engine_content.evaluation.minimal_event_response import (
    EVENT_ID,
    HALL,
    MESSENGER,
    RECIPIENT,
    build_minimal_event_response_scenario,
    create_minimal_event_response_session,
)




def test_objective_event_grows_verified_social_and_spatial_response_chain():
    session = create_minimal_event_response_session("event-response")
    report = EpisodeRunner().run(
        session,
        steps=12,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    event = session.entities[f"WorldEvent:{EVENT_ID}"]
    responses = event.get_component("WorldEventResponses")
    messenger_goals = session.entities[MESSENGER].get_component("GoalState")
    recipient_goals = session.entities[RECIPIENT].get_component("GoalState")
    messenger_goal = next(
        record for record in messenger_goals.goals.values() if record.origin == "agent"
    )
    recipient_goal = next(
        record for record in recipient_goals.goals.values() if record.origin == "agent"
    )

    assert report.authoritative is True
    assert report.closure_reached is True
    assert "unclosed_episode" not in report.quality_flags
    assert report.metrics["world_event_creation_count"] >= 1
    assert report.metrics["agent_goal_adoption_count"] == 2
    assert report.metrics["causal_handoff_count"] >= 5
    assert report.metrics["causal_handoff_steps"] >= 2
    handoffs = [item for step in report.steps for item in step.causal_handoffs]
    assert any(item.startswith(f"world_event:{EVENT_ID}<-") for item in handoffs)
    assert (
        f"world_event:{EVENT_ID}"
        "<-timeline_resolution:ceremony:missed"
        in handoffs
    )
    assert (
        "timeline_resolution:ceremony:missed"
        "<-timeline_commitment:ceremony"
        in handoffs
    )
    assert any(
        item.startswith("timeline_resolution:ceremony:missed<-clock:step:")
        for item in handoffs
    )
    assert (
        "timeline_resolution:ceremony:missed"
        f"<-actor_absence:{MESSENGER}:{HALL}"
        in handoffs
    )
    assert sum("<-world_event:" in item for item in handoffs) >= 3
    assert any(
        item.startswith(f"goal:{RECIPIENT}:") and "<-event_response:" in item
        for item in handoffs
    )
    assert report.metrics["max_causal_chain_depth"] >= 4
    assert report.metrics["goal_continuation_steps"] >= 1
    assert report.metrics["goal_continuation_actor_count"] == 1
    assert report.metrics["goal_continuation_attempt_count"] == 1
    assert report.metrics["max_repeated_goal_action_count"] == 1
    assert report.metrics["goal_achievement_count"] >= 3
    assert responses.communication_keys() == [
        f"{MESSENGER}->{RECIPIENT}",
        f"{RECIPIENT}->{MESSENGER}",
    ]
    assert responses.response_keys() == [
        f"{MESSENGER}->{RECIPIENT}:explain",
        f"{RECIPIENT}->{MESSENGER}:acknowledge",
    ]
    assert messenger_goal.status == "achieved"
    assert recipient_goal.status == "achieved"
    assert recipient_goal.source_kind == "event_response"
    assert session.entities[RECIPIENT].get_component("Cognition").knows_event(
        EVENT_ID
    )
    assert max(step.sentiment_count for step in report.steps) >= 1
    relationship = session.runner.relation_registry.to_relationship_book()
    assert relationship.get_metrics(RECIPIENT, MESSENGER).get("favor", 0.0) > 0
    scene = session.entities["GameMaster"].get_component("SceneState")
    assert scene.get_actor_location(RECIPIENT) == HALL
    recipient_actions = [
        (kind, target)
        for step in report.steps
        for actor, kind, target in step.actor_actions
        if actor == RECIPIENT
    ]
    assert ("communicate", MESSENGER) in recipient_actions
    assert ("move", HALL) in recipient_actions


def test_closure_waits_for_a_future_timeline_seed_before_story_aftermath_runs():
    session = create_minimal_event_response_session("future-event-response")
    scene = session.entities["GameMaster"].get_component("SceneState")
    commitments = scene.get_scene_flag("upcoming_commitments")
    commitments[0]["due_step"] = 4

    report = EpisodeRunner().run(
        session,
        steps=16,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
    )

    assert report.authoritative is True
    assert report.closure_reached is True
    assert any(
        "active_timeline_commitments" in step.closure_blockers
        for step in report.steps
    )
    assert report.steps[-1].simulation_time_after >= 4
    final_commitments = scene.get_scene_flag("upcoming_commitments")
    assert final_commitments[0]["status"] in {"resolved", "missed"}
    assert f"WorldEvent:{EVENT_ID}" in session.entities
