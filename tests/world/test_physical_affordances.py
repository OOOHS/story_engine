from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.physical_affordances import (
    PhysicalAffordanceEngine,
)
from src.story_engine.simulation import AffordanceActionResolver


def _scene():
    return SceneState(
        world_objects={
            "房间": {},
            "钥匙": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "portable": True,
                "hidden": False,
            },
            "背包": {
                "is_location": False,
                "location": None,
                "owner": "甲",
                "portable": True,
                "hidden": False,
            },
            "木匣": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "portable": False,
                "hidden": False,
                "is_container": True,
                "container_capacity": 2,
                "container_open": False,
                "container_opaque": True,
            },
            "乙的信": {
                "is_location": False,
                "location": None,
                "owner": "乙",
                "portable": True,
                "hidden": False,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
    )


def test_physical_opportunities_are_derived_from_state_not_story_content():
    engine = PhysicalAffordanceEngine()
    raw = engine.build_opportunities(_scene(), "甲")
    opportunities = {
        (item["object_id"], item["affordance_id"])
        for item in raw
    }

    assert ("钥匙", engine.TAKE) in opportunities
    assert ("背包", engine.DROP) in opportunities
    assert ("木匣", engine.OPEN) in opportunities
    assert ("乙的信", engine.TAKE) not in opportunities
    take = next(
        item for item in raw
        if item["object_id"] == "钥匙" and item["affordance_id"] == engine.TAKE
    )
    opening = next(
        item for item in raw
        if item["object_id"] == "木匣" and item["affordance_id"] == engine.OPEN
    )
    assert take["policy_tags"] == ["acquire"]
    assert opening["policy_tags"] == ["access"]


def test_builtin_take_is_materialized_as_authoritative_relocation():
    scene = _scene()
    intent = {
        "actor": "甲",
        "action_kind": "interact",
        "action_target": "钥匙",
        "action_affordance_id": PhysicalAffordanceEngine.TAKE,
    }
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "success"}],
        "object_lifecycle": [],
    }

    resolution = AffordanceActionResolver().resolve(
        result,
        intents=[intent],
        scene_state=scene,
    )

    assert resolution.result["object_lifecycle"] == [
        {
            "operation": "relocate",
            "object_id": "钥匙",
            "actor": "甲",
            "affordance_id": PhysicalAffordanceEngine.TAKE,
            "reason": "Agent 选择了当前可用的内建物理能力，Host 据此结算对象操作",
            "owner": "甲",
        }
    ]
    assert resolution.traces[0]["status"] == (
        "host_physical_operation_materialized"
    )


def test_drop_and_container_toggle_compile_to_existing_lifecycle_operations():
    scene = _scene()
    engine = PhysicalAffordanceEngine()

    assert engine.build_operation(
        scene, "甲", "背包", engine.DROP
    ) == {
        "operation": "relocate",
        "object_id": "背包",
        "actor": "甲",
        "affordance_id": engine.DROP,
        "reason": "Agent 选择了当前可用的内建物理能力，Host 据此结算对象操作",
        "location": "房间",
    }
    assert engine.build_operation(
        scene, "甲", "木匣", engine.OPEN
    ) == {
        "operation": "set_container_state",
        "object_id": "木匣",
        "actor": "甲",
        "affordance_id": engine.OPEN,
        "reason": "Agent 选择了当前可用的内建物理能力，Host 据此结算对象操作",
        "open": True,
    }
    scene.get_object_state("木匣")["container_open"] = True
    assert engine.is_available(scene, "甲", "木匣", engine.CLOSE) is True


def test_stale_builtin_reference_blocks_only_that_action():
    scene = _scene()
    scene.get_object_state("钥匙").update({"owner": "乙", "location": None})
    result = {
        "resolved_actions": [
            {"actor": "甲", "outcome": "success", "result": "不应保留"}
        ],
        "object_lifecycle": [],
    }

    resolution = AffordanceActionResolver().resolve(
        result,
        intents=[
            {
                "actor": "甲",
                "action_kind": "interact",
                "action_target": "钥匙",
                "action_affordance_id": PhysicalAffordanceEngine.TAKE,
            }
        ],
        scene_state=scene,
    )

    assert resolution.result["resolved_actions"][0]["outcome"] == "blocked"
    assert resolution.result["object_lifecycle"] == []
    assert resolution.traces[0]["status"] == "host_affordance_unavailable"
