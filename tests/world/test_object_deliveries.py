from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.simulation import ObjectDeliveryResolver
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scene():
    return SceneState(
        world_objects={
            "房间": {},
            "信件": {
                "is_location": False,
                "owner": "甲",
                "location": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
    )


def _perception():
    scene = _scene()
    return AgentPerception(
        actor_name="甲",
        step=0,
        world_view=scene.get_view_pov("甲"),
    )


def test_input_requires_owned_public_object_and_visible_recipient():
    action = AgentAction(
        "interact",
        "把信件交给乙",
        "信件",
        delivery_recipient="乙",
    )

    assert InputSystem._validated_delivery_reference(
        action, _perception()
    ) == "乙"
    assert InputSystem._validated_delivery_reference(
        AgentAction(
            "interact",
            "把不存在的物品交给乙",
            "戒指",
            delivery_recipient="乙",
        ),
        _perception(),
    ) == ""
    assert InputSystem._validated_delivery_reference(
        AgentAction(
            "interact",
            "隔空把信件交给丙",
            "信件",
            delivery_recipient="丙",
        ),
        _perception(),
    ) == ""


def test_positive_delivery_replaces_model_object_operation_with_exact_transfer():
    scene = _scene()
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "success"}],
        "object_lifecycle": [
            {
                "operation": "destroy",
                "object_id": "信件",
                "actor": "甲",
            }
        ],
    }
    intent = {
        "actor": "甲",
        "action_kind": "interact",
        "action_target": "信件",
        "action_delivery_recipient": "乙",
    }

    resolution = ObjectDeliveryResolver().resolve(
        result,
        intents=[intent],
        scene_state=scene,
    )

    assert resolution.result["object_lifecycle"] == [
        {
            "operation": "relocate",
            "object_id": "信件",
            "actor": "甲",
            "owner": "乙",
            "hidden": False,
            "reason": "Agent 的单边物品交付已由语义层正向结算，Host 提交权威转移",
        }
    ]


def test_stale_delivery_is_blocked_without_object_operation():
    scene = _scene()
    scene.get_object_state("信件")["owner"] = "乙"
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "success"}],
        "object_lifecycle": [],
    }

    resolution = ObjectDeliveryResolver().resolve(
        result,
        intents=[
            {
                "actor": "甲",
                "action_kind": "interact",
                "action_target": "信件",
                "action_delivery_recipient": "乙",
            }
        ],
        scene_state=scene,
    )

    assert result["resolved_actions"][0]["outcome"] == "blocked"
    assert resolution.result["object_lifecycle"] == []


def test_delivery_crosses_simulation_and_world_transaction():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(scene)
    gm.add_component(DramaState())
    gm.add_component(HostRuleSimulationControl(llm_config={}))
    entities = {"GameMaster": gm, "甲": Entity("甲"), "乙": Entity("乙")}
    context = {
        "intents": [
            {
                "actor": "甲",
                "intent": "把信件交给乙",
                "action_kind": "interact",
                "action_target": "信件",
                "action_delivery_recipient": "乙",
                "location": "房间",
            }
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert scene.get_object_state("信件")["owner"] == "乙"
    assert context["object_delivery_traces"][0]["status"] == (
        "host_delivery_materialized"
    )
