import pytest

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision, AgentPerception
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.sentiment_state import SentimentState
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.social import (
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

    gm = Entity("WorldHost")
    gm.add_component(_world())
    return {
        "WorldHost": gm,
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
    assert "decay_per_step" not in str(
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
    scene = entities["WorldHost"].get_component("SceneState")
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
    )
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )

    perception = InputSystem().build_agent_perception(agent, scene, [], {})

    assert perception.private_sentiments["active"][0]["kind"] == "suspicious"
    assert "decay_per_step" not in str(perception.private_sentiments)
    assert "sentiments" not in perception.world_view
def test_self_reported_sentiment_wins_over_gm_guess_for_the_same_actor():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(
        registry,
        [
            {
                "source": "乙",
                "affected": "甲",
                "kind": "grateful",
                "magnitude": 0.9,
                "reason": "GM 的默认猜测，理应被丢弃",
            }
        ],
    )
    context["agent_sentiment_updates"] = [
        {
            "actor": "甲",
            "toward": "乙",
            "kind": "suspicious",
            "magnitude": 0.4,
            "reason": "甲自己觉得乙的帮助别有目的",
        }
    ]

    SentimentSystem().update(entities, context)

    sentiments = entities["甲"].get_component("SentimentState").sentiments
    assert "乙:suspicious" in sentiments
    assert "乙:grateful" not in sentiments
    assert any(item.get("self_reported") for item in context["sentiment_updates"])
    assert context["sentiment_errors"] == []


def test_self_reported_sentiment_alone_is_committed_without_any_gm_impact():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(registry, [])
    context["agent_sentiment_updates"] = [
        {
            "actor": "甲",
            "toward": "乙",
            "kind": "hurt",
            "magnitude": 0.3,
            "reason": "甲觉得被乙的言辞刺伤",
        }
    ]

    SentimentSystem().update(entities, context)

    sentiment = entities["甲"].get_component("SentimentState").sentiments["乙:hurt"]
    assert sentiment.intensity == pytest.approx(0.3)
    assert context["sentiment_errors"] == []


def test_self_reported_sentiment_still_rejects_unknown_kind_and_actors():
    entities = _entities()
    registry = SocialRelationRegistry()
    context = _context(registry, [])
    context["agent_sentiment_updates"] = [
        {
            "actor": "甲",
            "toward": "不存在的人",
            "kind": "made_up_feeling",
            "magnitude": 0.3,
            "reason": "非法条目",
        }
    ]

    SentimentSystem().update(entities, context)

    assert entities["甲"].get_component("SentimentState").sentiments == {}
    assert context["sentiment_updates"] == []
    assert any("unknown sentiment kind" in error for error in context["sentiment_errors"])
    assert any("existing characters" in error for error in context["sentiment_errors"])
