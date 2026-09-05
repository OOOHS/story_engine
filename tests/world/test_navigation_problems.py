from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.navigation_state import NavigationState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.systems.navigation import NavigationSystem


def _entities(with_alternative=True):
    gm = Entity("WorldHost")
    gm.add_component(
        SceneState(
            world_objects={
                "村口": {"connected_to": ["南路"]},
                "断桥": {"connected_to": ["城镇"]},
                "南路": {"connected_to": ["村口", "城镇"]},
                "城镇": {"connected_to": ["断桥", "南路"]},
                "包裹": {
                    "is_location": False, "owner": "旅人",
                    "location": None, "hidden": False, "portable": True,
                },
            },
            actor_states={"旅人": {"location": "村口"}},
        )
    )
    actor = Entity("旅人")
    routes = {"村口": ["断桥"], "断桥": ["城镇"]}
    if with_alternative:
        routes.update({"村口": ["断桥", "南路"], "南路": ["城镇"]})
    actor.add_component(
        KnowledgeState(
            known_locations=["村口", "断桥", "南路", "城镇"],
            known_routes=routes,
        )
    )
    actor.add_component(NavigationState())
    return {"WorldHost": gm, "旅人": actor}


def test_stale_route_creates_private_problem_with_alternative_and_deadline():
    entities = _entities()
    context = {
        "clock": type("Clock", (), {"current_step": 5})(),
        "state_transaction": {"committed": True},
        "legality": {"checks": [{
            "actor": "旅人", "action_target": "城镇",
            "rule": "stale_route", "reason": "断桥已经无法通行。",
        }]},
        "simulation_result": {"resolved_actions": [{
            "actor": "旅人", "outcome": "blocked", "action_kind": "move",
        }]},
    }

    NavigationSystem().update(entities, context)

    snapshot = entities["旅人"].get_component("NavigationState").private_snapshot()
    problem = snapshot["active"][0]
    assert problem["route_source"] == "村口"
    assert problem["route_target"] == "断桥"
    assert problem["alternative_path"] == ["村口", "南路", "城镇"]
    assert problem["failure_rule"] == "stale_route"


def test_navigation_problem_does_not_invent_an_unknown_alternative():
    entities = _entities(with_alternative=False)
    context = {
        "clock": type("Clock", (), {"current_step": 5})(),
        "state_transaction": {"committed": True},
        "legality": {"checks": [{
            "actor": "旅人", "action_target": "城镇",
            "rule": "stale_route", "reason": "断桥已经无法通行。",
        }]},
        "simulation_result": {"resolved_actions": [{
            "actor": "旅人", "outcome": "blocked",
        }]},
    }

    NavigationSystem().update(entities, context)

    problem = entities["旅人"].get_component("NavigationState").private_snapshot()[
        "active"
    ][0]
    assert problem["alternative_path"] == []
