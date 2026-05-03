from src.story_engine.common.movement_intent import extract_move_target_from_intent
from src.story_engine.components.scene_state import SceneState
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
    )
    assert target == "客房"


def test_extract_move_target_understands_dining_aliases():
    target = extract_move_target_from_intent(
        intent="我走到餐桌前，找到他们空出来的位置坐下",
        current_location="沈宅客厅",
        connected_locations=["餐厅", "二楼走廊"],
        known_locations=["沈宅客厅", "餐厅", "二楼走廊", "客房"],
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
