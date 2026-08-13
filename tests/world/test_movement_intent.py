from src.story_engine.common.movement_intent import extract_move_target_from_intent
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.systems.simulation import SimulationSystem


def test_extract_move_target_ignores_negated_destination():
    target = extract_move_target_from_intent(
        intent="我不去餐厅，我就在这儿看着",
        current_location="沈宅客厅",
        connected_locations=["餐厅", "二楼走廊"],
        known_locations=["沈宅客厅", "餐厅", "二楼走廊"],
    )
    assert target is None


def test_extract_move_target_keeps_later_affirmed_destination():
    target = extract_move_target_from_intent(
        intent="我不去餐厅，我回客房",
        current_location="沈宅客厅",
        connected_locations=["餐厅", "二楼走廊"],
        known_locations=["沈宅客厅", "餐厅", "二楼走廊", "客房"],
        location_aliases={"餐厅": ["餐桌", "饭桌", "餐桌前"]},
    )
    assert target == "客房"


def test_extract_move_target_understands_dining_aliases():
    target = extract_move_target_from_intent(
        intent="我走到餐桌前，找到他们空出来的位置坐下",
        current_location="沈宅客厅",
        connected_locations=["餐厅", "二楼走廊"],
        known_locations=["沈宅客厅", "餐厅", "二楼走廊", "客房"],
        location_aliases={"餐厅": ["餐桌", "饭桌", "餐桌前"]},
    )
    assert target == "餐厅"


def test_movement_legality_does_not_rewrite_negated_move():
    system = SimulationSystem()
    scene = SceneState(
        world_objects={
            "沈宅客厅": {"connected_to": ["餐厅", "二楼走廊"]},
            "餐厅": {"connected_to": ["沈宅客厅"]},
            "二楼走廊": {"connected_to": ["沈宅客厅", "客房"]},
            "客房": {"connected_to": ["二楼走廊"]},
        }
    )

    verdict = system._assess_movement_legality(
        scene_state=scene,
        actor="林见微",
        intent="我不去餐厅，我就在这儿看着",
        current_location="沈宅客厅",
    )
    assert verdict is None


def test_legal_move_location_is_committed_by_host_even_if_semantic_result_omits_it():
    control = SimulationControl()
    payload = {
        "player_name": "甲",
        "player_pov": {"location": "大厅"},
        "intents": [
            {
                "actor": "甲",
                "intent": "前往走廊",
                "action_kind": "move",
                "action_target": "走廊",
                "location": "大厅",
                "is_player": True,
            }
        ],
        "legality": {
            "checks": [
                {
                    "actor": "甲",
                    "intent": "前往走廊",
                    "action_kind": "move",
                    "action_target": "走廊",
                    "verdict": "allow",
                    "rule": "movement",
                    "rewrite_location": "走廊",
                }
            ]
        },
    }
    normalized = control._normalize_result(
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "intent": "前往走廊",
                    "outcome": "success",
                    "result": "甲离开了大厅。",
                }
            ],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
            },
        },
        payload,
    )

    enforced = control._enforce_legality(normalized, payload)

    assert enforced["state_updates"]["actor_states"] == {
        "甲": {"location": "走廊"}
    }
    assert enforced["resolved_actions"][0]["location"] == "走廊"


def test_semantic_result_cannot_move_actor_without_host_movement_verdict():
    control = SimulationControl()
    payload = {
        "player_name": "甲",
        "player_pov": {"location": "大厅"},
        "intents": [
            {
                "actor": "甲",
                "intent": "查看桌上的信",
                "action_kind": "observe",
                "action_target": "信",
                "location": "大厅",
                "is_player": True,
            }
        ],
        "legality": {
            "checks": [
                {
                    "actor": "甲",
                    "intent": "查看桌上的信",
                    "action_kind": "observe",
                    "action_target": "信",
                    "verdict": "allow",
                    "rule": "none",
                    "rewrite_location": None,
                }
            ]
        },
    }
    normalized = control._normalize_result(
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "intent": "查看桌上的信",
                    "outcome": "success",
                    "result": "甲查看了信。",
                }
            ],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {"甲": {"location": "走廊"}},
            },
        },
        payload,
    )

    enforced = control._enforce_legality(normalized, payload)

    assert enforced["state_updates"]["actor_states"] == {}
    assert any(
        "没有宿主移动裁定" in note
        for note in enforced["simulation_notes"]
    )
