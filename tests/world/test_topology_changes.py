from types import SimpleNamespace

from src.story_engine.components.cognition import Cognition
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.topology import HostTopologyTransaction
from src.story_engine.environment.runner import Runner
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.rules import LegalityEngine
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.world_events import WorldEventSystem


def _scene() -> SceneState:
    return SceneState(
        world_objects={
            "村庄": {"connected_to": ["森林"]},
            "森林": {"connected_to": ["村庄"]},
            "城堡": {"connected_to": []},
            "远方": {"connected_to": []},
        },
        actor_states={
            "旅人": {"location": "森林"},
            "旁观者": {"location": "城堡"},
            "远人": {"location": "远方"},
        },
        scene_flags={"world_version": 3},
    )


def _move_verdict(scene: SceneState):
    return LegalityEngine().assess_intent(
        scene,
        "freeform",
        {
            "actor": "旅人",
            "intent": "前往城堡",
            "action_kind": "move",
            "action_target": "城堡",
        },
    )


def _register_scene_actors(runner: Runner, scene: SceneState) -> None:
    for name in scene.actor_states:
        actor = create_agent(
            name,
            "旅行者",
            "平静",
            [],
            activation_policy="dormant",
        )
        runner.add_entity(actor)
        runner.agent_registry.register(actor, object())


def test_host_topology_transaction_atomically_opens_and_closes_a_route():
    scene = _scene()
    transaction = HostTopologyTransaction()

    assert _move_verdict(scene)["verdict"] == "block"
    opened = transaction.apply(
        scene,
        [
            {
                "change_id": "drawbridge-open",
                "operation": "connect",
                "source": "森林",
                "target": "城堡",
            }
        ],
        current_step=5,
    )

    assert opened.committed is True
    assert opened.errors == []
    assert scene.get_object_state("森林")["connected_to"] == ["村庄", "城堡"]
    assert scene.get_object_state("城堡")["connected_to"] == ["森林"]
    assert scene.get_scene_flag("world_version") == 4
    assert _move_verdict(scene)["verdict"] == "allow"

    closed = transaction.apply(
        scene,
        [
            {
                "change_id": "drawbridge-collapse",
                "operation": "disconnect",
                "source": "森林",
                "target": "城堡",
            }
        ],
        current_step=6,
    )

    assert closed.committed is True
    assert scene.get_object_state("森林")["connected_to"] == ["村庄"]
    assert scene.get_object_state("城堡")["connected_to"] == []
    assert scene.get_scene_flag("world_version") == 5
    assert _move_verdict(scene)["verdict"] == "block"


def test_invalid_topology_batch_rolls_back_every_route_and_version():
    scene = _scene()
    before = scene.model_dump()

    outcome = HostTopologyTransaction().apply(
        scene,
        [
            {
                "operation": "connect",
                "source": "森林",
                "target": "城堡",
            },
            {
                "operation": "disconnect",
                "source": "森林",
                "target": "不存在的地点",
            },
        ],
        current_step=5,
    )

    assert outcome.committed is False
    assert any("unknown target location" in error for error in outcome.errors)
    assert scene.model_dump() == before


def test_topology_ledger_is_deterministic_for_replay():
    first = _scene()
    second = _scene()
    command = {
        "operation": "connect",
        "source": "森林",
        "target": "城堡",
        "visibility": "public",
    }

    first_result = HostTopologyTransaction().apply(
        first, [command], current_step=11
    )
    second_result = HostTopologyTransaction().apply(
        second, [command], current_step=11
    )

    assert first_result == second_result
    assert first.model_dump() == second.model_dump()
    assert first_result.changes[0]["change_id"] == "11:0:connect:森林->城堡"


def test_conflicting_topology_commands_are_rejected_without_order_semantics():
    scene = _scene()

    outcome = HostTopologyTransaction().apply(
        scene,
        [
            {"operation": "connect", "source": "森林", "target": "城堡"},
            {"operation": "disconnect", "source": "森林", "target": "城堡"},
        ],
        current_step=5,
    )

    assert outcome.committed is False
    assert any("conflicts with another command" in error for error in outcome.errors)
    assert scene.get_object_state("森林")["connected_to"] == ["村庄"]


def test_committed_local_route_change_becomes_a_pov_safe_world_event():
    scene = _scene()
    outcome = HostTopologyTransaction().apply(
        scene,
        [
            {
                "change_id": "drawbridge-collapse",
                "operation": "disconnect",
                "source": "森林",
                "target": "城堡",
                "visibility": "local",
            }
        ],
        current_step=6,
    )
    # Seed the edge and repeat the actual closing transition.
    assert outcome.changes == []
    HostTopologyTransaction().apply(
        scene,
        [{"operation": "connect", "source": "森林", "target": "城堡"}],
        current_step=6,
    )
    outcome = HostTopologyTransaction().apply(
        scene,
        [
            {
                "change_id": "drawbridge-collapse",
                "operation": "disconnect",
                "source": "森林",
                "target": "城堡",
                "visibility": "local",
            }
        ],
        current_step=7,
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    entities = {
        "GameMaster": gm,
        "旅人": create_agent("旅人", "行路者", "警觉", []),
        "旁观者": create_agent("旁观者", "守卫", "沉着", []),
        "远人": create_agent("远人", "居民", "平静", []),
    }
    goal_state = entities["旅人"].get_component("GoalState")
    adopted, error = goal_state.adopt_agent_goal(
        title="前往城堡",
        description="沿可用道路抵达城堡",
        source_kind="world_event",
        source_ref="older-route-report",
        priority=0.8,
        step=1,
        completion_conditions=[
            {
                "scope": "actor",
                "target": "旅人",
                "path": "location",
                "operator": "eq",
                "value": "城堡",
            }
        ],
    )
    assert adopted is not None and error == ""
    controller = entities["旅人"].get_component("AgentController")
    controller.repeated_goal_action_count = 5
    controller.last_goal_action_signature = "move:城堡"
    context = {
        "clock": SimpleNamespace(current_step=7),
        "topology_changes": outcome.changes,
        "simulation_result": {},
    }

    WorldEventSystem().update(entities, context)

    event_id = "topology:drawbridge-collapse"
    fact = entities[f"WorldEvent:{event_id}"].get_component("WorldEventFact")
    assert fact.kind == "route_closed"
    assert {(impact.target, impact.path) for impact in fact.impacts} == {
        ("森林", "connected_to"),
        ("城堡", "connected_to"),
    }
    for actor in ("旅人", "旁观者"):
        cognition = entities[actor].get_component("Cognition")
        assert cognition.knows_event(event_id)
        assert cognition.world_event_attention[event_id].priority == 85
    assert not entities["远人"].get_component("Cognition").knows_event(event_id)
    assert controller.repeated_goal_action_count == 0
    assert controller.last_goal_action_signature == ""
    assert context["goal_reactivations"][0]["match_basis"] == "condition_value"
    assert context["simulation_result"]["topology_changes"] == outcome.changes


def test_topology_render_projection_hides_offscreen_and_hidden_changes():
    changes = [
        {
            "change_id": "local",
            "operation": "disconnect",
            "source": "森林",
            "target": "城堡",
            "visibility": "local",
            "statement": "森林与城堡之间的通路已经中断。",
        },
        {
            "change_id": "hidden",
            "operation": "connect",
            "source": "远方",
            "target": "城堡",
            "visibility": "hidden",
            "statement": "秘密通路已经开放。",
        },
    ]
    rendering = RenderingSystem()

    local = rendering._build_visible_simulation(
        {"topology_changes": changes},
        {"location": "森林"},
    )
    offscreen = rendering._build_visible_simulation(
        {"topology_changes": changes},
        {"location": "村庄"},
    )

    assert [item["change_id"] for item in local["topology_changes"]] == ["local"]
    assert offscreen["topology_changes"] == []
    assert "reason" not in local["topology_changes"][0]


def test_runner_exposes_only_the_validated_host_topology_boundary():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(scene)
    runner = Runner(random_seed="topology-runner")
    runner.add_entity(gm)
    _register_scene_actors(runner, scene)

    context = runner.run_step(
        topology_changes=[
            {
                "change_id": "runner-opens-route",
                "operation": "connect",
                "source": "森林",
                "target": "城堡",
                "visibility": "public",
            }
        ]
    )

    assert context["topology_transaction"] == {"committed": True, "errors": []}
    assert scene.get_object_state("森林")["connected_to"] == ["村庄", "城堡"]
    assert "WorldEvent:topology:runner-opens-route" in runner.entities


def test_legacy_world_edits_cannot_bypass_topology_authority():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(scene)
    runner = Runner(random_seed="legacy-world-edit")
    runner.add_entity(gm)
    _register_scene_actors(runner, scene)

    context = runner.run_step(
        world_edits=[("森林", {"connected_to": ["村庄", "城堡"]})]
    )

    assert context["world_edit_transaction"]["committed"] is False
    assert "explicit host lifecycle/topology API" in context[
        "world_edit_transaction"
    ]["errors"][0]
    assert scene.get_object_state("森林")["connected_to"] == ["村庄"]
