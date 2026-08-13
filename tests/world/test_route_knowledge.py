from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.core.entity import Entity
from src.story_engine.rules import LegalityEngine
from src.story_engine.simulation.route_communications import RouteCommunicationResolver
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.route_knowledge import RouteKnowledgeSystem
from src.story_engine.components.scene_state import SceneState


def _perception():
    return AgentPerception(
        actor_name="向导", step=4,
        world_view={"visible_actors": ["向导", "旅人"]},
        private_knowledge={
            "map": {
                "known_locations": ["村口", "林道"],
                "known_routes": {"村口": ["林道"], "林道": ["渡口"]},
            }
        },
    )


def test_route_report_must_reference_an_edge_the_speaker_knows():
    valid = InputSystem._validated_route_reference(
        AgentAction(
            "communicate", "从村口可以走到林道。", "旅人",
            route_source="村口", route_target="林道",
        ),
        _perception(),
    )
    invented = InputSystem._validated_route_reference(
        AgentAction(
            "communicate", "从村口可以走到密堡。", "旅人",
            route_source="村口", route_target="密堡",
        ),
        _perception(),
    )

    assert valid == {
        "source": "村口", "target": "林道", "path": ("村口", "林道")
    }
    assert invented == {}


def test_complete_known_path_is_validated_atomically():
    valid = InputSystem._validated_route_reference(
        AgentAction(
            "communicate", "从村口经林道可以到渡口。", "旅人",
            route_path=("村口", "林道", "渡口"),
        ),
        _perception(),
    )
    broken = InputSystem._validated_route_reference(
        AgentAction(
            "communicate", "从村口经密堡可以到渡口。", "旅人",
            route_path=("村口", "密堡", "渡口"),
        ),
        _perception(),
    )

    assert valid["path"] == ("村口", "林道", "渡口")
    assert broken == {}


def test_positive_route_report_updates_only_the_recipient_private_map():
    source = Entity("向导")
    source_map = KnowledgeState(
        known_locations=["村口", "林道"],
        known_routes={"村口": ["林道"]},
    )
    source.add_component(source_map)
    target = Entity("旅人")
    target.add_component(KnowledgeState(known_locations=["村口"]))
    entities = {"向导": source, "旅人": target}
    intent = {
        "actor": "向导", "intent": "从村口可以走到林道。",
        "action_kind": "communicate", "action_target": "旅人",
        "action_route_source": "村口", "action_route_target": "林道",
        "action_route_path": ["村口", "林道"],
    }
    result = RouteCommunicationResolver().resolve(
        {"resolved_actions": [{"actor": "向导", "outcome": "success"}]},
        intents=[intent],
    ).result
    context = {
        "state_transaction": {"committed": True},
        "simulation_result": result,
        "clock": type("Clock", (), {"current_step": 4})(),
    }

    RouteKnowledgeSystem().update(entities, context)

    received = target.get_component("KnowledgeState")
    assert received.known_routes == {"村口": ["林道"]}
    assert received.route_provenance["村口->林道"] == {
        "basis": "reported", "source": "向导", "learned_step": 4,
    }
    assert source_map.known_routes == {"村口": ["林道"]}


def test_reported_route_can_become_stale_without_revealing_new_host_path():
    map_knowledge = {
        "known_locations": ["村口", "林道"],
        "known_routes": {"村口": ["林道"]},
    }
    scene = SceneState(
        actor_states={"旅人": {"location": "村口"}},
        world_objects={
            "村口": {"connected_to": []},
            "林道": {"connected_to": []},
            "密堡": {"connected_to": []},
        },
    )

    verdict = LegalityEngine().assess_intent(
        scene,
        "mundane",
        {
            "actor": "旅人", "intent": "前往林道",
            "action_kind": "move", "action_target": "林道",
        },
        map_knowledge=map_knowledge,
    )

    assert verdict["verdict"] == "block"
    assert verdict["rule"] == "stale_route"
    assert "密堡" not in map_knowledge["known_locations"]


def test_observing_current_exits_does_not_silently_erase_a_remembered_route():
    knowledge = KnowledgeState(
        known_locations=["村口", "东桥"],
        known_routes={"村口": ["东桥"]},
    )
    scene = SceneState(
        actor_states={"旅人": {"location": "村口"}},
        world_objects={
            "村口": {"connected_to": ["南路"]},
            "东桥": {"connected_to": []},
            "南路": {"connected_to": ["村口"]},
        },
    )

    knowledge.observe_location(scene, "村口")

    assert knowledge.known_routes["村口"] == ["东桥", "南路"]
    assert knowledge.route_provenance.get("村口->东桥", {}) == {}
    assert knowledge.route_provenance["村口->南路"] == {
        "basis": "observed",
        "source": "self",
    }
