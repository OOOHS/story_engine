from src.story_engine.components.scene_state import SceneState
from src.story_engine.rules import LegalityEngine


def test_physics_is_interpreted_semantically_and_text_is_opaque_to_host():
    engine = LegalityEngine()
    scene = SceneState(actor_states={"甲": {"location": "庭院"}}, world_objects={"庭院": {}})
    for intent in ("我飞起来越过围墙", "我告诉乙他声称会瞬移", "我先不进入庭院"):
        result = engine.assess_intent(scene, "mundane", {"actor": "甲", "intent": intent},
                                     physics_rules=[{"keywords": ["瞬移", "飞起来"], "capability": "magic"}])
        assert result["verdict"] == "allow"
        assert result["rule"] == "none"


def test_movement_is_rewritten_to_next_hop_in_world_graph():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"旅人": {"location": "村庄"}},
        world_objects={
            "村庄": {"connected_to": ["森林"]},
            "森林": {"connected_to": ["村庄", "城堡"]},
            "城堡": {"connected_to": ["森林"]},
        },
    )

    verdict = engine.assess_intent(
        scene,
        "freeform",
        {"actor": "旅人", "intent": "我前往城堡", "action_kind": "move", "action_target": "城堡"},
    )

    assert verdict["verdict"] == "rewrite"
    assert verdict["rewrite_location"] == "森林"
    assert verdict["suggested_intent"] == "先前往森林"


def test_structured_move_target_is_authoritative_over_natural_language_parsing():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"旅人": {"location": "村庄"}},
        world_objects={
            "村庄": {"connected_to": ["森林"]},
            "森林": {"connected_to": ["村庄", "城堡"]},
            "城堡": {"connected_to": ["森林"]},
        },
    )

    verdict = engine.assess_intent(
        scene,
        "freeform",
        {
            "actor": "旅人",
            "intent": "谨慎地改变所在位置",
            "action_kind": "move",
            "action_target": "城堡",
        },
    )

    assert verdict["verdict"] == "rewrite"
    assert verdict["rewrite_location"] == "森林"


def test_active_observation_can_see_transparent_container_but_interaction_cannot_reach_it():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"甲": {"location": "房间"}},
        world_objects={
            "房间": {},
            "玻璃盒": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "container": None,
                "hidden": False,
                "portable": True,
                "is_container": True,
                "container_capacity": 2,
                "container_open": False,
                "container_opaque": False,
            },
            "盒中钥匙": {
                "is_location": False,
                "location": None,
                "owner": None,
                "container": "玻璃盒",
                "hidden": False,
                "portable": True,
            },
        },
    )

    observe = engine.assess_intent(
        scene,
        "mundane",
        {
            "actor": "甲",
            "intent": "观察钥匙的形状",
            "action_kind": "observe",
            "action_target": "盒中钥匙",
        },
    )
    interact = engine.assess_intent(
        scene,
        "mundane",
        {
            "actor": "甲",
            "intent": "直接拿起钥匙",
            "action_kind": "interact",
            "action_target": "盒中钥匙",
        },
    )

    assert observe["verdict"] == "allow"
    assert interact["verdict"] == "block"
    assert interact["rule"] == "target_access"


def test_action_completion_rejects_authoritative_target_removed_since_submission():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"甲": {"location": "房间"}},
        world_objects={"房间": {}},
    )

    verdict = engine.assess_intent(
        scene,
        "mundane",
        {
            "actor": "甲",
            "intent": "拿起桌上的信",
            "action_kind": "interact",
            "action_target": "桌上的信",
            "target_reference_kind": "world_object",
            "stale_by_versions": 1,
        },
    )

    assert verdict["verdict"] == "block"
    assert verdict["rule"] == "stale_target"


def test_remote_move_cannot_use_host_map_unknown_to_actor():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"甲": {"location": "村口"}},
        world_objects={
            "村口": {"connected_to": ["林间路"]},
            "林间路": {"connected_to": ["村口", "密堡"]},
            "密堡": {"connected_to": ["林间路"]},
        },
    )

    verdict = engine.assess_intent(
        scene,
        "mundane",
        {
            "actor": "甲", "intent": "前往密堡",
            "action_kind": "move", "action_target": "密堡",
        },
        map_knowledge={
            "known_locations": ["村口", "林间路"],
            "known_routes": {"村口": ["林间路"]},
        },
    )

    assert verdict["verdict"] == "block"
    assert verdict["rule"] == "unknown_destination"


def test_remote_move_uses_only_actor_known_route_for_next_hop():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"甲": {"location": "村口"}},
        world_objects={
            "村口": {"connected_to": ["北路", "南路"]},
            "北路": {"connected_to": ["村口", "城镇"]},
            "南路": {"connected_to": ["村口", "城镇"]},
            "城镇": {"connected_to": ["北路", "南路"]},
        },
    )

    verdict = engine.assess_intent(
        scene,
        "mundane",
        {
            "actor": "甲", "intent": "前往城镇",
            "action_kind": "move", "action_target": "城镇",
        },
        map_knowledge={
            "known_locations": ["村口", "南路", "城镇"],
            "known_routes": {
                "村口": ["南路"], "南路": ["村口", "城镇"],
            },
        },
    )

    assert verdict["verdict"] == "rewrite"
    assert verdict["rewrite_location"] == "南路"
