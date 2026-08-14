from src.story_engine.components.scene_state import SceneState
from src.story_engine.rules import LegalityEngine


def test_mundane_profile_requires_matching_capability():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={
            "普通人": {"location": "庭院"},
            "法师": {"location": "庭院", "capabilities": ["magic"]},
            "飞行者": {"location": "庭院", "capabilities": ["flight"]},
        },
        world_objects={"庭院": {}},
    )

    ordinary = engine.assess_intent(
        scene, "mundane", {"actor": "普通人", "intent": "我飞起来越过围墙"}
    )
    mage = engine.assess_intent(
        scene, "mundane", {"actor": "法师", "intent": "我飞起来越过围墙"}
    )
    flyer = engine.assess_intent(
        scene, "mundane", {"actor": "飞行者", "intent": "我飞起来越过围墙"}
    )

    assert ordinary["verdict"] == "block"
    assert mage["verdict"] == "block"
    assert flyer["verdict"] == "allow"


def test_content_declared_physics_rules_override_hardcoded_mundane_table():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={
            "村民": {"location": "法师塔"},
            "见习法师": {"location": "法师塔", "capabilities": ["mage"]},
        },
        world_objects={"法师塔": {}},
    )
    physics_rules = [
        {
            "keywords": ["飞起来", "悬浮"],
            "capability": "mage",
            "reason": "在这个世界只有法师才能飞行。",
        }
    ]

    villager = engine.assess_intent(
        scene,
        "magic",
        {"actor": "村民", "intent": "我飞起来越过塔顶"},
        physics_rules=physics_rules,
    )
    apprentice = engine.assess_intent(
        scene,
        "magic",
        {"actor": "见习法师", "intent": "我飞起来越过塔顶"},
        physics_rules=physics_rules,
    )

    assert villager["verdict"] == "block"
    assert villager["reason"] == "在这个世界只有法师才能飞行。"
    assert apprentice["verdict"] == "allow"


def test_build_context_reads_physics_rules_from_scenario_without_register_profile():
    engine = LegalityEngine()
    scene = SceneState(
        actor_states={"村民": {"location": "法师塔"}},
        world_objects={"法师塔": {}},
    )

    class _Scenario:
        physics_profile = "magic"
        physics_rules = [
            {"keywords": ["飞起来"], "capability": "mage", "reason": "普通人不能飞。"}
        ]

    context = engine.build_context(
        scene, _Scenario(), [{"actor": "村民", "intent": "我飞起来越过塔顶"}]
    )

    assert context["physics_profile"] == "magic"
    assert context["checks"][0]["verdict"] == "block"
    assert context["checks"][0]["reason"] == "普通人不能飞。"


def test_custom_physics_profile_can_be_registered_without_editing_simulation():
    engine = LegalityEngine(
        profile_rules={
            "truthbound": lambda intent, state: (
                "誓约禁止角色主动说谎。" if "撒谎" in intent else ""
            )
        }
    )
    scene = SceneState(
        actor_states={"见证人": {"location": "法庭"}},
        world_objects={"法庭": {}},
    )

    verdict = engine.assess_intent(
        scene,
        "truthbound",
        {"actor": "见证人", "intent": "我向法官撒谎"},
    )

    assert verdict["verdict"] == "block"
    assert verdict["rule"] == "truthbound_physics"


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
        {"actor": "旅人", "intent": "我前往城堡"},
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
