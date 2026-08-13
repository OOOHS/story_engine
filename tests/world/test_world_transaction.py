from copy import deepcopy

import pytest

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.scenarios.config import PlotEntityConfig, PlotStageConfig
from src.story_engine.social import RelationshipBook


def _state_bundle():
    scene = SceneState(
        description="旧场景",
        world_objects={
            "大厅": {
                "connected_to": ["走廊"],
                "default_zone": "door",
                "zones": {"door": {"label": "门边"}},
            },
            "走廊": {
                "connected_to": ["大厅"],
                "default_zone": "middle",
                "zones": {"middle": {"label": "走廊中央"}},
            },
        },
        actor_states={"甲": {"location": "大厅", "sub_location": "door"}},
    )
    plots = PlotState.from_configs(
        [
            PlotEntityConfig(
                plot_id="storm",
                title="风暴",
                description="风暴正在靠近。",
                max_clock=4,
                stages=[PlotStageConfig(label="远方", summary="云层聚集")],
            )
        ]
    )
    drama = DramaState(tension=0.4)
    return scene, plots, drama


def test_world_transaction_commits_scene_plot_and_drama_together():
    scene, plots, drama = _state_bundle()
    result = {
        "state_updates": {
            "scene": {"description": "新场景", "door_open": True},
            "world_objects": {"大厅": {"lighting": "dark"}},
            "actor_states": {"甲": {"location": "走廊"}},
        },
        "plot_updates": [{"plot_id": "storm", "advance": 1, "stage_shift": 0}],
        "tension_delta": 0.2,
    }

    outcome = WorldStateTransaction().commit(scene, plots, drama, result)

    assert outcome.committed is True
    assert scene.description == "新场景"
    assert scene.get_actor_state("甲")["location"] == "走廊"
    assert scene.get_actor_state("甲")["sub_location"] == "middle"
    assert scene.get_object_state("大厅")["lighting"] == "dark"
    assert plots.plots["storm"]["clock"] == 1
    assert drama.tension == pytest.approx(0.6)


def test_object_affordance_policy_tags_use_finite_host_catalog():
    scene = SceneState(
        world_objects={
            "房间": {},
            "绳索": {
                "is_location": False,
                "location": "房间",
                "portable": False,
                "hidden": False,
                "affordances": [],
            },
        }
    )
    transaction = WorldStateTransaction()
    valid = deepcopy(scene.get_object_state("绳索"))
    valid["affordances"] = [{
        "id": "rescue",
        "need_effects": {},
        "policy_tags": ["aid", "risk"],
    }]
    valid_errors = []

    transaction._validate_tangible_object(
        scene,
        "绳索",
        valid,
        valid_errors,
    )

    assert valid_errors == []

    invalid = deepcopy(valid)
    invalid["affordances"][0]["policy_tags"] = ["force_plot"]
    invalid_errors = []
    transaction._validate_tangible_object(
        scene,
        "绳索",
        invalid,
        invalid_errors,
    )

    assert invalid_errors == [
        "object affordance has unsupported policy tag: 绳索[0]"
    ]

    social_injection = deepcopy(valid)
    social_injection["affordances"][0]["policy_tags"] = ["apologize"]
    social_errors = []
    transaction._validate_tangible_object(
        scene,
        "绳索",
        social_injection,
        social_errors,
    )

    assert social_errors == [
        "object affordance has unsupported policy tag: 绳索[0]"
    ]


def test_invalid_actor_update_rolls_back_scene_plot_and_drama():
    scene, plots, drama = _state_bundle()
    before_scene = deepcopy(scene.get_snapshot())
    before_plot = deepcopy(plots.get_snapshot())
    result = {
        "state_updates": {
            "scene": {"description": "不应提交"},
            "world_objects": {},
            "actor_states": {"不存在的人": {"location": "走廊"}},
        },
        "plot_updates": [{"plot_id": "storm", "advance": 2}],
        "tension_delta": 0.4,
    }

    outcome = WorldStateTransaction().commit(scene, plots, drama, result)

    assert outcome.committed is False
    assert any("unknown actor" in error for error in outcome.errors)
    assert scene.get_snapshot() == before_scene
    assert plots.get_snapshot() == before_plot
    assert drama.tension == 0.4


def test_unknown_location_and_graph_reference_are_rejected():
    scene, plots, drama = _state_bundle()
    transaction = WorldStateTransaction()
    bad_location = transaction.commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {"甲": {"location": "月球"}},
            },
            "plot_updates": [],
            "tension_delta": 0,
        },
    )
    bad_graph = transaction.commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {},
                "world_objects": {"大厅": {"connected_to": ["不存在的门"]}},
                "actor_states": {},
            },
            "plot_updates": [],
            "tension_delta": 0,
        },
    )

    assert bad_location.committed is False
    assert bad_graph.committed is False
    assert scene.get_actor_location("甲") == "大厅"


def test_semantic_state_update_cannot_rewrite_even_valid_spatial_topology():
    scene, plots, drama = _state_bundle()
    before = deepcopy(scene.get_snapshot())

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {},
                "world_objects": {
                    "大厅": {
                        "connected_to": ["走廊"],
                        "default_zone": "door",
                        "aliases": ["正厅"],
                    }
                },
                "actor_states": {},
            },
            "plot_updates": [],
            "tension_delta": 0,
        },
    )

    assert outcome.committed is False
    assert any("host world-building API" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_semantic_state_update_cannot_rewrite_actor_visibility_schema():
    scene, plots, drama = _state_bundle()
    before = deepcopy(scene.get_snapshot())

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {
                    "甲": {
                        "public_state_fields": ["secret_plan"],
                        "private_state_fields": ["stance"],
                    }
                },
            },
            "plot_updates": [],
            "tension_delta": 0,
        },
    )

    assert outcome.committed is False
    assert any("visibility schema is host-authored" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_semantic_state_update_cannot_rewrite_scene_visibility_schema():
    scene, plots, drama = _state_bundle()
    before = deepcopy(scene.get_snapshot())

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {
                    "public_scene_fields": ["secret_plot_clock"],
                    "private_scene_fields": ["weather"],
                },
                "world_objects": {},
                "actor_states": {},
            },
            "plot_updates": [],
            "tension_delta": 0,
        },
    )

    assert outcome.committed is False
    assert any("scene visibility schema is host-authored" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_unknown_update_section_and_unknown_plot_are_rejected():
    scene, plots, drama = _state_bundle()
    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
                "invented_section": {"x": 1},
            },
            "plot_updates": [{"plot_id": "invented_plot", "advance": 1}],
            "tension_delta": 0,
        },
    )

    assert outcome.committed is False
    assert any("unknown state_update sections" in error for error in outcome.errors)
    assert any("unknown plot" in error for error in outcome.errors)


def test_rejected_result_is_sanitized_before_rendering_or_followup_updates():
    transaction = WorldStateTransaction()
    sanitized = transaction.sanitize_rejected_result(
        {
            "resolved_actions": [{"actor": "甲", "result": "甲已经瞬移成功。"}],
            "state_updates": {"actor_states": {"甲": {"location": "月球"}}},
            "plot_updates": [{"plot_id": "x", "advance": 99}],
            "relationship_updates": [{"source": "甲", "target": "乙", "trust_delta": 5}],
            "knowledge_updates": [{"source": "甲", "target": "乙", "statement": "秘密"}],
            "storylet_hits": ["x"],
            "tension_delta": 9,
            "spawn_character": {"name": "幽灵"},
            "simulation_notes": [],
        },
        ["unknown actor location for 甲: 月球"],
    )

    assert sanitized["resolved_actions"] == []
    assert sanitized["plot_updates"] == []
    assert sanitized["relationship_updates"] == []
    assert sanitized["knowledge_updates"] == []
    assert sanitized["spawn_character"] is None
    assert sanitized["transaction_rejected"] is True
    assert "月球" in sanitized["simulation_notes"][-1]


def test_relationship_changes_commit_with_scene_plot_and_drama():
    scene, plots, drama = _state_bundle()
    scene.actor_states["乙"] = {"location": "大厅"}
    relationships = RelationshipBook()
    relationships.set_track("甲", "乙", "trust", 1)
    result = {
        "resolved_actions": [
            {"actor": "乙", "outcome": "success", "result": "乙兑现了承诺。"}
        ],
        "state_updates": {
            "scene": {"description": "承诺兑现后的大厅"},
            "world_objects": {},
            "actor_states": {},
        },
        "plot_updates": [{"plot_id": "storm", "advance": 1}],
        "relationship_updates": [
            {
                "source": "甲",
                "target": "乙",
                "trust_delta": 2,
                "reason": "乙兑现了承诺",
            }
        ],
        "tension_delta": -0.1,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        result,
        relationship_book=relationships,
        proposal_actors={"乙"},
    )

    assert outcome.committed is True
    assert scene.description == "承诺兑现后的大厅"
    assert plots.plots["storm"]["clock"] == 1
    assert drama.tension == pytest.approx(0.3)
    assert relationships.get_metrics("甲", "乙")["trust"] == 3
    assert "trust_乙" not in scene.get_actor_state("甲")
    assert scene.get_scene_flag("last_relation_deltas")["甲->乙"]["reason"] == "乙兑现了承诺"


def test_transaction_rejects_resolved_action_for_actor_without_proposal():
    scene, plots, drama = _state_bundle()
    scene.actor_states["乙"] = {"location": "大厅"}
    result = {
        "resolved_actions": [
            {
                "actor": "乙",
                "outcome": "complication",
                "location": "大厅",
                "visibility": "public",
                "result": "结算器替没有行动的乙制造了一次发难。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        result,
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert any("actor has no current-turn proposal: 乙" in error for error in outcome.errors)


def test_invalid_relationship_change_rolls_back_every_authoritative_component():
    scene, plots, drama = _state_bundle()
    scene.actor_states["乙"] = {"location": "大厅"}
    relationships = RelationshipBook()
    relationships.set_track("甲", "乙", "trust", 1)
    before_scene = deepcopy(scene.get_snapshot())
    before_plot = deepcopy(plots.get_snapshot())
    before_relations = deepcopy(relationships.relationships)
    result = {
        "resolved_actions": [],
        "state_updates": {
            "scene": {"description": "不应提交"},
            "world_objects": {},
            "actor_states": {"甲": {"location": "走廊"}},
        },
        "plot_updates": [{"plot_id": "storm", "advance": 1}],
        "relationship_updates": [
            {
                "source": "甲",
                "target": "乙",
                "trust_delta": -4,
                "reason": "没有已结算行动支持",
            }
        ],
        "tension_delta": 0.2,
    }

    outcome = WorldStateTransaction().commit(
        scene, plots, drama, result, relationship_book=relationships
    )

    assert outcome.committed is False
    assert any("not supported by a resolved action" in error for error in outcome.errors)
    assert scene.get_snapshot() == before_scene
    assert plots.get_snapshot() == before_plot
    assert drama.tension == 0.4
    assert relationships.relationships == before_relations


def test_stale_relationship_cannot_reference_a_ghost_actor():
    scene, plots, drama = _state_bundle()
    relationships = RelationshipBook()
    relationships.set_track("幽灵", "甲", "trust", 1)
    before_scene = deepcopy(scene.get_snapshot())

    outcome = WorldStateTransaction().commit(
        scene,
        plots,
        drama,
        {
            "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
            "plot_updates": [],
            "relationship_updates": [],
            "tension_delta": 0,
        },
        relationship_book=relationships,
    )

    assert outcome.committed is False
    assert any("unknown participant actor: 幽灵" in error for error in outcome.errors)
    assert scene.get_snapshot() == before_scene
    assert "幽灵" not in scene.actor_states
