import pytest
from pydantic import Field

from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.motivation import NeedDynamics
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.drives import DriveSystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return self.scripted_result


def _drive():
    return DriveState.from_initial(
        [
            {
                "name": "hunger",
                "pressure": 0.8,
                "drift_per_turn": 0.1,
                "critical_threshold": 0.75,
                "description": "需要获得食物",
            },
            {
                "name": "thirst",
                "pressure": 0.4,
                "drift_per_turn": 0.15,
            },
        ],
        risk_tolerance=0.3,
    )


def _resource_scene(quantity=2):
    return SceneState(
        world_objects={
            "营地": {},
            "面包": {
                "is_location": False,
                "kind": "food",
                "location": "营地",
                "owner": None,
                "hidden": False,
                "portable": True,
                "quantity": quantity,
                "affordances": [
                    {
                        "id": "eat",
                        "label": "吃掉一份面包",
                        "need_effects": {"hunger": -0.5},
                        "consumes": True,
                    }
                ],
            },
        },
        actor_states={"旅人": {"location": "营地"}},
    )


def _use_result():
    return {
        "resolved_actions": [
            {
                "actor": "旅人",
                "outcome": "success",
                "location": "营地",
                "result": "旅人吃掉了一份面包。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [
            {
                "operation": "use",
                "object_id": "面包",
                "affordance_id": "eat",
                "actor": "旅人",
                "reason": "旅人实际吃掉了一份面包",
            }
        ],
        "tension_delta": 0,
    }


def test_need_pressure_drift_is_deterministic_clamped_and_idempotent():
    drive = _drive()

    assert drive.advance_to(1) == {}
    assert drive.advance_to(1) == {}
    changed = drive.advance_to(3)

    assert changed["hunger"] == pytest.approx(1.0)
    assert changed["thirst"] == pytest.approx(0.7)
    snapshot = drive.get_private_snapshot()
    assert snapshot["highest_pressure_need"] == "hunger"
    assert snapshot["needs"]["hunger"]["critical"] is True
    assert snapshot["risk_tolerance"] == 0.3


def test_create_need_always_starts_at_zero_and_rejects_duplicates():
    drive = _drive()

    created = drive.create_need(
        "  恐惧  ",
        drift_per_turn=0.05,
        critical_threshold=0.7,
        description="  对黑暗的持续恐惧  ",
    )
    duplicate = drive.create_need("hunger")

    assert created is True
    assert duplicate is False
    assert drive.needs["恐惧"].pressure == 0.0
    assert drive.needs["恐惧"].description == "对黑暗的持续恐惧"
    assert drive.created_count == 1


def test_object_use_consumes_one_resource_and_relieves_need_atomically():
    scene = _resource_scene(quantity=2)
    drive = _drive()

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _use_result(),
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
    )

    assert outcome.committed is True
    assert scene.get_object_state("面包")["quantity"] == 1
    assert drive.needs["hunger"].pressure == pytest.approx(0.3)
    assert drive.need_provenance["hunger"][-1] == {
        "source_kind": "resolved_action",
        "source_ref": "step:0:actor:旅人",
        "object_id": "面包",
        "affordance_id": "eat",
        "before": 0.8,
        "after": pytest.approx(0.3),
        "delta": pytest.approx(-0.5),
    }
    assert "need_provenance" not in drive.get_private_snapshot()

    outcome.checkpoint.restore()
    assert scene.get_object_state("面包")["quantity"] == 2
    assert drive.needs["hunger"].pressure == pytest.approx(0.8)


def test_using_last_resource_removes_it_from_authoritative_world():
    scene = _resource_scene(quantity=1)
    drive = _drive()

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _use_result(),
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
    )

    assert outcome.committed is True
    assert "面包" not in scene.world_objects
    assert drive.needs["hunger"].pressure == pytest.approx(0.3)


def test_invalid_affordance_effect_rolls_back_resource_and_drive():
    scene = _resource_scene(quantity=2)
    scene.get_object_state("面包")["affordances"][0]["need_effects"] = {
        "hunger": -2.0
    }
    drive = _drive()

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _use_result(),
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
    )

    assert outcome.committed is False
    assert scene.get_object_state("面包")["quantity"] == 2
    assert drive.needs["hunger"].pressure == pytest.approx(0.8)
    assert any("need effect" in error for error in outcome.errors)


def test_affordance_opportunities_are_pov_safe_and_ranked_by_current_pressure():
    scene = _resource_scene(quantity=2)
    scene.world_objects["水袋"] = {
        "is_location": False,
        "kind": "drink",
        "location": "营地",
        "owner": None,
        "hidden": True,
        "portable": True,
        "affordances": [
            {
                "id": "drink",
                "need_effects": {"thirst": -0.8},
                "consumes": True,
            }
        ],
    }
    opportunities = NeedDynamics().build_opportunities(scene, "旅人", _drive())

    assert [item["object_id"] for item in opportunities] == ["面包"]
    assert opportunities[0]["affordance_id"] == "eat"
    assert opportunities[0]["relief_score"] == pytest.approx(0.4)


def test_closed_container_contents_do_not_leak_as_affordance_opportunities():
    scene = _resource_scene(quantity=2)
    scene.world_objects["食物盒"] = {
        "is_location": False,
        "kind": "box",
        "location": "营地",
        "owner": None,
        "container": None,
        "hidden": False,
        "portable": True,
        "is_container": True,
        "container_capacity": 4,
        "container_open": False,
        "container_opaque": True,
    }
    scene.get_object_state("面包").update(
        {"location": None, "owner": None, "container": "食物盒"}
    )

    opportunities = NeedDynamics().build_opportunities(scene, "旅人", _drive())

    assert opportunities == []
    assert "食物盒" in scene.get_view_pov("旅人")["visible_objects"]
    assert "面包" not in scene.get_view_pov("旅人")["visible_objects"]


def test_agent_perception_contains_only_its_private_drives_and_visible_affordances():
    scene = _resource_scene(quantity=2)
    traveler = create_agent(
        "旅人",
        "流浪者",
        "谨慎",
        ["活过今晚"],
        initial_needs=[
            {"name": "hunger", "pressure": 0.8, "drift_per_turn": 0.1}
        ],
        risk_tolerance=0.2,
    agent_runtime="llm")

    perception = InputSystem().build_agent_perception(
        traveler,
        scene,
        [],
        {},
    )

    assert perception.private_drives["needs"]["hunger"]["pressure"] == 0.8
    assert perception.private_drives["risk_tolerance"] == 0.2
    assert perception.affordance_opportunities[0]["object_id"] == "面包"
    assert "policy_tags" not in perception.affordance_opportunities[0]
    assert "affordances" not in perception.world_view["visible_world"]["面包"]
    assert "needs" not in perception.world_view


def test_drive_system_advances_each_component_once_per_clock_step():
    traveler = create_agent(
        "旅人",
        "流浪者",
        "谨慎",
        [],
        initial_needs=[
            {"name": "hunger", "pressure": 0.2, "drift_per_turn": 0.1}
        ],
    agent_runtime="llm")
    clock = type("Clock", (), {"current_step": 4})()
    context = {"clock": clock}
    system = DriveSystem()

    system.update({"旅人": traveler}, context)
    system.update({"旅人": traveler}, context)
    clock.current_step = 6
    system.update({"旅人": traveler}, context)

    drive = traveler.get_component("DriveState")
    assert drive.needs["hunger"].pressure == pytest.approx(0.4)
    assert context["drive_drift"]["旅人"]["hunger"] == pytest.approx(0.4)
    assert drive.need_provenance["hunger"][-1]["source_kind"] == "clock"
    assert drive.need_provenance["hunger"][-1]["source_ref"] == "step:6"


def test_simulation_system_commits_resource_use_and_private_need_effect_together():
    gm = Entity("GameMaster")
    scene = _resource_scene(quantity=2)
    gm.add_component(SimulationControl(scripted_result=_use_result()))
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    traveler = create_agent(
        "旅人",
        "流浪者",
        "务实",
        ["活下去"],
        initial_needs=[{"name": "hunger", "pressure": 0.8}],
    agent_runtime="llm")
    entities = {"GameMaster": gm, "旅人": traveler}
    context = {
        "intents": [{"actor": "旅人", "intent": "吃掉一份面包"}],
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert scene.get_object_state("面包")["quantity"] == 1
    assert traveler.get_component("DriveState").needs["hunger"].pressure == pytest.approx(0.3)


def test_simulation_system_reads_emergent_meter_budget_from_scenario():
    gm = Entity("GameMaster")
    scene = _resource_scene(quantity=2)
    scripted = _use_result()
    scripted["drive_creations"] = [
        {"actor": "旅人", "need": "恐惧", "reason": "第一次直面黑暗"}
    ]
    gm.add_component(
        SimulationControl(
            scripted_result=scripted,
            scenario=ScenarioConfig(
                name="test",
                default_agent_runtime="llm",
                description="test",
                environment="test",
                initial_state="",
                emergent_meter_budget=2,
            ),
        )
    )
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    traveler = create_agent(
        "旅人",
        "流浪者",
        "务实",
        ["活下去"],
        initial_needs=[{"name": "hunger", "pressure": 0.8}],
    agent_runtime="llm")
    entities = {"GameMaster": gm, "旅人": traveler}
    context = {
        "intents": [{"actor": "旅人", "intent": "吃掉一份面包"}],
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    drive = traveler.get_component("DriveState")
    assert drive.needs["恐惧"].pressure == 0.0
    assert drive.created_count == 1


def test_evidence_backed_action_can_change_abstract_need_without_public_leakage():
    scene = _resource_scene(quantity=2)
    scene.actor_states["威胁者"] = {"location": "营地"}
    drive = _drive()
    result = {
        "resolved_actions": [
            {
                "actor": "威胁者",
                "outcome": "success",
                "location": "营地",
                "visibility": "public",
                "result": "威胁者夺走了旅人的退路。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [
            {
                "actor": "旅人",
                "source": "威胁者",
                "need": "hunger",
                "delta": 0.2,
                "reason": "威胁迫使旅人消耗更多体力，食物压力加剧",
            }
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"威胁者"},
    )
    visible = RenderingSystem()._build_visible_simulation(
        result,
        scene.get_view_pov("旅人"),
        visible_locations=["营地"],
    )

    assert outcome.committed is True
    assert drive.needs["hunger"].pressure == pytest.approx(1.0)
    assert visible["drive_updates"] == []


def test_moving_actor_keeps_drive_impact_observed_at_origin():
    scene = _resource_scene(quantity=2)
    scene.world_objects["走廊"] = {}
    scene.actor_states["威胁者"] = {"location": "营地"}
    drive = _drive()
    result = {
        "resolved_actions": [
            {
                "actor": "威胁者",
                "action_kind": "interact",
                "outcome": "success",
                "location": "营地",
                "visibility": "public",
                "result": "威胁者在旅人离开前堵住了退路。",
            },
            {
                "actor": "旅人",
                "action_kind": "move",
                "action_target": "走廊",
                "outcome": "success",
                "location": "营地",
                "visibility": "public",
                "result": "旅人随后退到了走廊。",
            },
        ],
        "state_updates": {
            "scene": {},
            "world_objects": {},
            "actor_states": {"旅人": {"location": "走廊"}},
        },
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [
            {
                "actor": "旅人",
                "source": "威胁者",
                "need": "hunger",
                "delta": 0.2,
                "reason": "旅人在离开营地前亲眼看到退路被堵",
            }
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"旅人", "威胁者"},
    )

    assert outcome.committed is True
    assert scene.get_actor_location("旅人") == "走廊"
    assert drive.needs["hunger"].pressure == pytest.approx(1.0)


def test_unsupported_drive_update_rejects_other_world_changes_too():
    scene = _resource_scene(quantity=2)
    drive = _drive()
    result = {
        "resolved_actions": [],
        "state_updates": {
            "scene": {"description": "不应提交"},
            "world_objects": {},
            "actor_states": {},
        },
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [
            {
                "actor": "旅人",
                "source": "不存在的行动者",
                "need": "hunger",
                "delta": -0.5,
                "reason": "没有本轮事实支持",
            }
        ],
        "tension_delta": 0.2,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"威胁者"},
    )

    assert outcome.committed is False
    assert scene.description == "Initial State"
    assert drive.needs["hunger"].pressure == pytest.approx(0.8)
    assert any("not supported by a resolved action" in error for error in outcome.errors)


def test_drive_creation_within_budget_starts_at_zero_pressure():
    scene = _resource_scene(quantity=2)
    drive = _drive()
    result = {
        "resolved_actions": [
            {
                "actor": "旅人",
                "outcome": "success",
                "location": "营地",
                "result": "旅人第一次直面了对黑暗的恐惧。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_creations": [
            {
                "actor": "旅人",
                "need": "恐惧",
                "drift_per_turn": 0.03,
                "critical_threshold": 0.8,
                "description": "对黑暗的持续恐惧",
                "reason": "旅人第一次直面了对黑暗的恐惧",
            }
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
        emergent_meter_budget=1,
    )

    assert outcome.committed is True
    assert drive.needs["恐惧"].pressure == 0.0
    assert drive.needs["恐惧"].drift_per_turn == pytest.approx(0.03)
    assert drive.created_count == 1
    assert drive.need_provenance["恐惧"][-1]["created"] is True


def test_drive_creation_over_budget_rejects_whole_batch():
    scene = _resource_scene(quantity=2)
    drive = _drive()
    result = {
        "resolved_actions": [
            {"actor": "旅人", "outcome": "success", "location": "营地", "result": "x"}
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_creations": [
            {"actor": "旅人", "need": "恐惧", "reason": "x"},
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
        emergent_meter_budget=0,
    )

    assert outcome.committed is False
    assert "恐惧" not in drive.needs
    assert any("emergent_meter_budget" in error for error in outcome.errors)


def test_drive_creation_cannot_shadow_an_existing_need_name():
    scene = _resource_scene(quantity=2)
    drive = _drive()
    result = {
        "resolved_actions": [
            {"actor": "旅人", "outcome": "success", "location": "营地", "result": "x"}
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_creations": [
            {"actor": "旅人", "need": "hunger", "reason": "重名尝试"},
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
        emergent_meter_budget=5,
    )

    assert outcome.committed is False
    assert any("already exists" in error for error in outcome.errors)


def test_drive_creation_without_supporting_action_is_rejected():
    scene = _resource_scene(quantity=2)
    drive = _drive()
    result = {
        "resolved_actions": [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_creations": [
            {"actor": "旅人", "need": "恐惧", "reason": "凭空产生"},
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"旅人"},
        emergent_meter_budget=5,
    )

    assert outcome.committed is False
    assert "恐惧" not in drive.needs
    assert any(
        "not supported by a resolved action" in error for error in outcome.errors
    )


def test_remote_hidden_action_cannot_telepathically_change_private_drive():
    scene = _resource_scene(quantity=2)
    scene.world_objects["远处"] = {}
    scene.actor_states["威胁者"] = {"location": "远处"}
    drive = _drive()
    result = {
        "resolved_actions": [
            {
                "actor": "威胁者",
                "outcome": "success",
                "location": "远处",
                "visibility": "hidden",
                "result": "威胁者在远处秘密谋划。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [
            {
                "actor": "旅人",
                "source": "威胁者",
                "need": "hunger",
                "delta": 0.2,
                "reason": "旅人不可能知道的远处密谋",
            }
        ],
        "tension_delta": 0,
    }

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"旅人": drive},
        proposal_actors={"威胁者"},
    )

    assert outcome.committed is False
    assert drive.needs["hunger"].pressure == pytest.approx(0.8)
    assert any("not observable" in error for error in outcome.errors)
