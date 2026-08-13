from src.story_engine.components.scene_state import SceneState
from src.story_engine.social import RelationshipBook, SocialDynamics


def _affinities(*records):
    book = RelationshipBook()
    for source, target, metrics in records:
        for key, value in metrics.items():
            book.set_track(source, target, key, value)
    return book


def test_conflict_outcome_does_not_implicitly_create_malice():
    scene = SceneState(
        actor_states={
            "甲": {"location": "大厅"},
            "玩家": {"location": "大厅"},
        }
    )
    relationships = _affinities(("甲", "玩家", {"malice": 0, "favor": 2}))

    SocialDynamics().apply_relation_updates(
        scene,
        relationships,
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "outcome": "complication",
                    "visibility": "public",
                    "result": "甲拒绝了玩家。",
                }
            ]
        },
    )

    assert relationships.get_metrics("甲", "玩家")["malice"] == 0
    assert relationships.get_metrics("甲", "玩家")["favor"] == 2


def test_explicit_directed_relationship_updates_are_applied_and_clamped():
    scene = SceneState(
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "大厅"},
        }
    )
    relationships = _affinities(
        ("甲", "乙", {"favor": 4, "malice": 1, "trust": 0})
    )

    SocialDynamics().apply_relation_updates(
        scene,
        relationships,
        {
            "resolved_actions": [
                {
                    "actor": "乙",
                    "outcome": "success",
                    "visibility": "public",
                    "result": "乙公开违背了承诺。",
                }
            ],
            "relationship_updates": [
                {
                    "source": "甲",
                    "target": "乙",
                    "favor_delta": 3,
                    "malice_delta": -3,
                    "trust_delta": -2,
                    "reason": "乙公开违背了承诺",
                }
            ]
        },
    )

    assert relationships.get_metrics("甲", "乙") == {
        "favor": 5,
        "malice": 0,
        "trust": -2,
    }
    assert "trust_乙" not in scene.get_actor_state("甲")
    assert scene.get_scene_flag("last_relation_deltas")["甲->乙"]["reason"] == "乙公开违背了承诺"


def test_unsupported_relationship_delta_is_ignored():
    scene = SceneState(
        actor_states={
            "甲": {"location": "大厅"},
            "乙": {"location": "大厅"},
        }
    )
    relationships = _affinities(("甲", "乙", {"trust": 1}))

    SocialDynamics().apply_relation_updates(
        scene,
        relationships,
        {
            "resolved_actions": [],
            "relationship_updates": [
                {
                    "source": "甲",
                    "target": "乙",
                    "trust_delta": -5,
                    "reason": "没有任何本轮事实支持",
                }
            ],
        },
    )

    assert relationships.get_metrics("甲", "乙")["trust"] == 1
    assert scene.get_scene_flag("last_relation_deltas") is None


def test_observed_direct_interaction_lazily_creates_pair_relationship():
    scene = SceneState(
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}}
    )
    relationships = RelationshipBook()

    SocialDynamics().record_interactions(
        scene,
        relationships,
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "action_kind": "communicate",
                    "action_target": "乙",
                    "outcome": "success",
                    "visibility": "public",
                }
            ]
        },
        current_step=4,
    )

    record = relationships.relationships["pair:乙<->甲"]
    assert record.first_met_step == 4
    assert record.last_interaction_step == 4
    assert record.bits["acquainted"].created_step == 4
    assert record.bits["acquainted"].provenance == {
        "source_kind": "resolved_action",
        "source_ref": "step:4:actor:甲",
    }


def test_reaction_context_only_uses_visible_or_transition_carrier_actors():
    dynamics = SocialDynamics()
    pov = {
        "location": "前厅",
        "visible_actors": ["玩家", "甲"],
        "visible_actor_states": {
            "甲": {"location": "前厅"},
        },
    }
    context = dynamics.build_reaction_context(
        player_name="玩家",
        pov=pov,
        player_intent={"intent": "我拒绝离开"},
        social={
            "visible_relations": [
                {"actor": "甲", "toward_viewer_states": ["wary"]}
            ]
        },
        timeline={
            "transition_pressure": {
                "carrier_actors": ["乙"],
                "carrier_states": {"乙": {"location": "前厅", "bias": "甲"}},
                "requires_human_backlash": True,
            }
        },
    )

    assert context["visible_watchers"] == ["甲", "乙"]
    assert context["hostile_watchers"] == ["甲", "乙"]
    assert context["action_pressure"] == "high"
    assert "丙" not in context["visible_watchers"]
