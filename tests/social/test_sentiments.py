import pytest

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.policy import CharacterPolicy
from src.story_engine.agents.types import AgentDecision, AgentPerception
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.sentiment_state import SentimentState
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.social import (
    AgreementRecord,
    AgreementRegistry,
    SocialRelationRegistry,
)
from src.story_engine.systems.sentiments import SentimentSystem
from src.story_engine.systems.input import InputSystem


def _world():
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )
    return scene


def _entities():
    from src.story_engine.core.entity import Entity

    gm = Entity("GameMaster")
    gm.add_component(_world())
    return {
        "GameMaster": gm,
        "甲": create_agent("甲", "访客", "平静", [], agent_runtime="llm"),
        "乙": create_agent("乙", "主人", "平静", [], agent_runtime="llm"),
    }


def _context(registry, impacts, *, step=2, visibility="public"):
    return {
        "clock": type("Clock", (), {"current_step": step})(),
        "relation_registry": registry,
        "state_transaction": {"committed": True, "errors": []},
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "action_kind": "interact",
                    "action_target": "甲",
                    "outcome": "success",
                    "location": "大厅",
                    "visibility": visibility,
                    "result": "乙帮助了甲。",
                }
            ],
            "social_impacts": impacts,
        },
    }


def test_committed_observable_help_creates_private_sentiment_and_small_track_change():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(
        registry,
        [
            {
                "source": "乙",
                "affected": "甲",
                "kind": "grateful",
                "magnitude": 0.5,
                "reason": "乙亲自帮甲摆脱了眼前困难",
                "source_event": "help_1",
            }
        ],
    )

    SentimentSystem().update(entities, context)

    sentiment = entities["甲"].get_component("SentimentState").sentiments[
        "乙:grateful"
    ]
    assert sentiment.intensity == 0.5
    assert sentiment.toward == "乙"
    assert sentiment.source_event == "resolved_action:step:2:actor:乙"
    assert "policy_weights" not in str(
        entities["甲"].get_component("SentimentState").get_private_snapshot()
    )
    relationships = registry.to_relationship_book()
    assert relationships.get_metrics("甲", "乙")["favor"] == pytest.approx(0.11)
    assert relationships.get_metrics("甲", "乙")["trust"] == pytest.approx(0.08)
    assert relationships.get_track_records("甲", "乙")["trust"].provenance == {
        "source_kind": "sentiment",
        "source_ref": "甲:乙:grateful",
        "source_event": "resolved_action:step:2:actor:乙",
        "reason": "sentiment:grateful:乙亲自帮甲摆脱了眼前困难",
    }
    assert context["sentiment_errors"] == []


def test_hidden_or_unobservable_social_impact_is_rejected_without_partial_state():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(
        registry,
        [
            {
                "source": "乙",
                "affected": "甲",
                "kind": "betrayed",
                "magnitude": 1.0,
                "reason": "甲没有实际看到的秘密行为",
            }
        ],
        visibility="hidden",
    )

    SentimentSystem().update(entities, context)

    assert entities["甲"].get_component("SentimentState").sentiments == {}
    assert registry.to_relationship_book().relationships == {}
    assert any("observable" in error for error in context["sentiment_errors"])


def test_moving_actor_keeps_sentiment_from_observed_origin_action():
    entities = _entities()
    scene = entities["GameMaster"].get_component("SceneState")
    scene.world_objects["走廊"] = {}
    scene.update_actor_state("甲", {"location": "走廊"})
    registry = SocialRelationRegistry()
    context = _context(
        registry,
        [
            {
                "source": "乙",
                "affected": "甲",
                "kind": "grateful",
                "magnitude": 0.5,
                "reason": "甲离开大厅前亲眼看到乙提供帮助",
            }
        ],
    )
    context["actor_observation_windows"] = {
        "甲": {"locations": ["大厅", "走廊"]},
        "乙": {"locations": ["大厅"]},
    }

    SentimentSystem().update(entities, context)

    assert context["sentiment_errors"] == []
    assert "乙:grateful" in entities["甲"].get_component(
        "SentimentState"
    ).sentiments


def test_social_impact_batch_is_atomic_when_one_appraisal_is_invalid():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(
        registry,
        [
            {
                "source": "乙",
                "affected": "甲",
                "kind": "grateful",
                "magnitude": 0.5,
                "reason": "乙公开帮助了甲",
            },
            {
                "source": "乙",
                "affected": "甲",
                "kind": "invented_emotion",
                "magnitude": 0.5,
                "reason": "非法类别",
            },
        ],
    )

    SentimentSystem().update(entities, context)

    assert entities["甲"].get_component("SentimentState").sentiments == {}
    assert registry.to_relationship_book().relationships == {}
    assert context["sentiment_updates"] == []


def test_repeated_sentiment_saturates_and_then_decays():
    state = SentimentState()
    kwargs = dict(
        toward="乙",
        kind="angry",
        magnitude=0.5,
        valence=-1.0,
        reason="乙再次挑衅",
        duration_steps=8,
        decay_per_step=0.1,
        policy_weights={"confront": 1.0},
    )
    state.upsert(current_step=1, **kwargs)
    state.upsert(current_step=2, **kwargs)

    assert state.sentiments["乙:angry"].intensity == 0.75
    transitions = state.advance_to(4)
    assert state.sentiments["乙:angry"].intensity == pytest.approx(0.55)
    assert transitions[0]["status"] == "decayed"


def test_agent_perception_receives_only_its_private_sentiment_view():
    agent = create_agent("甲", "访客", "平静", [], agent_runtime="llm")
    agent.get_component("SentimentState").upsert(
        toward="乙",
        kind="suspicious",
        magnitude=0.6,
        valence=-0.45,
        reason="乙的说法前后矛盾",
        current_step=1,
        duration_steps=10,
        decay_per_step=0.04,
        policy_weights={"information": 0.8},
    )
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )

    perception = InputSystem().build_agent_perception(agent, scene, [], {})

    assert perception.private_sentiments["active"][0]["kind"] == "suspicious"
    assert "policy_weights" not in str(perception.private_sentiments)
    assert "sentiments" not in perception.world_view


def test_private_sentiment_changes_host_action_distribution():
    neutral = create_agent("甲", "访客", "平静", [], agent_runtime="llm")
    angry = create_agent("甲", "访客", "平静", [], agent_runtime="llm")
    angry.get_component("SentimentState").upsert(
        toward="乙",
        kind="angry",
        magnitude=0.9,
        valence=-1.0,
        reason="乙刚刚公开挑衅",
        current_step=1,
        duration_steps=8,
        decay_per_step=0.08,
        policy_weights={"confront": 0.85, "aid": -0.65},
    )
    candidates = (
        AgentAction("communicate", "当面质问乙为何这样做。", "乙"),
        AgentAction("interact", "帮助乙整理散落的物品。", "乙"),
    )
    decision = AgentDecision(
        action=candidates[0].detail,
        action_spec=candidates[0],
        candidates=candidates,
    )
    perception = AgentPerception(actor_name="甲", step=1)
    policy = CharacterPolicy()
    neutral_trace = policy.select(
        entity=neutral,
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(4),
        world_version=1,
    ).trace
    angry_trace = policy.select(
        entity=angry,
        perception=perception,
        decision=decision,
        random_streams=DeterministicRandomStreams(4),
        world_version=1,
    ).trace
    neutral_confront = next(
        item for item in neutral_trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )
    angry_confront = next(
        item for item in angry_trace["candidates"]
        if item["candidate_id"] == "runtime:0"
    )

    assert angry_confront["probability"] > neutral_confront["probability"]
    assert angry_confront["sentiment_contribution"] > 0


def test_authoritative_agreement_breach_creates_participant_local_betrayal():
    entities = _entities()
    relation_registry = SocialRelationRegistry()
    agreement_registry = AgreementRegistry(relation_registry)
    book = agreement_registry.to_book()
    book.agreements["repair"] = AgreementRecord(
        agreement_id="repair",
        proposer="甲",
        parties=["甲", "乙"],
        status="settled",
        performance_status="breached",
        performance_obligations=[
            {
                "actor": "乙",
                "obligation_id": "repair_watch",
                "resolved_status": "breached",
            }
        ],
    )
    agreement_registry.apply_book(book, entities)
    context = {
        "clock": type("Clock", (), {"current_step": 6})(),
        "relation_registry": relation_registry,
        "agreement_registry": agreement_registry,
        "state_transaction": {"committed": True, "errors": []},
        "simulation_result": {"resolved_actions": [], "social_impacts": []},
        "agreement_transitions": [
            {"contract_id": "repair", "performance_status": "breached"}
        ],
    }

    SentimentSystem().update(entities, context)

    betrayal = entities["甲"].get_component("SentimentState").sentiments[
        "乙:betrayed"
    ]
    assert betrayal.intensity == 0.85
    assert betrayal.source_event == (
        "agreement_performance_resolution:repair:breached"
    )
    assert relation_registry.to_relationship_book().get_metrics("甲", "乙")[
        "trust"
    ] < 0
