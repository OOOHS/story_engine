import pytest
from pydantic import Field

from src.story_engine.agents.scheduler import AgentScheduler
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.scenarios.config import StateCondition
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.obligations import ObligationSystem
from src.story_engine.systems.rendering import RenderingSystem
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _input_payload):
        return self.scripted_result


def _scene():
    return SceneState(
        world_objects={"工坊": {}, "远处": {}},
        actor_states={
            "学徒": {"location": "工坊"},
            "师傅": {"location": "工坊"},
        },
    )


def _drive():
    return DriveState.from_initial(
        [{"name": "责任压力", "pressure": 0.2, "drift_per_turn": 0.0}]
    )


def _obligations():
    return ObligationState.from_initial(
        [
            {
                "obligation_id": "deliver_repair",
                "title": "交付修好的怀表",
                "summary": "把怀表修好并交还师傅",
                "creditor": "师傅",
                "due_step": 2,
                "grace_steps": 1,
                "wake_before_steps": 1,
                "pressure_need": "责任压力",
                "due_pressure_delta": 0.1,
                "breach_pressure_delta": 0.2,
            }
        ]
    )


def _base_result():
    return {
        "resolved_actions": [],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def test_deadline_transitions_are_deterministic_and_apply_pressure_once():
    apprentice = create_agent("学徒", "钟表学徒", "认真", [])
    apprentice.add_component(_drive())
    apprentice.add_component(_obligations())
    entities = {"学徒": apprentice}
    system = ObligationSystem()
    clock = type("Clock", (), {"current_step": 1})()
    context = {"clock": clock}

    system.update(entities, context)
    clock.current_step = 2
    system.update(entities, context)
    system.update(entities, context)

    obligation = apprentice.get_component("ObligationState").obligations["deliver_repair"]
    drive = apprentice.get_component("DriveState")
    assert obligation.status == "due"
    assert drive.needs["责任压力"].pressure == pytest.approx(0.3)

    clock.current_step = 4
    system.update(entities, context)
    system.update(entities, context)

    assert obligation.status == "breached"
    assert drive.needs["责任压力"].pressure == pytest.approx(0.5)


def test_authored_obligation_has_explicit_scenario_provenance():
    obligation = _obligations().obligations["deliver_repair"]

    assert obligation.source_kind == "scenario"
    assert obligation.source_ref == "deliver_repair"


def test_authoritative_completion_condition_fulfills_without_model_claim():
    scene = _scene()
    obligations = ObligationState.from_initial(
        [
            {
                "obligation_id": "reach_workshop",
                "title": "抵达工坊",
                "due_step": 3,
                "completion_conditions": [
                    StateCondition(
                        scope="actor",
                        target="学徒",
                        path="location",
                        operator="eq",
                        value="工坊",
                    )
                ],
            }
        ]
    )

    transitions = obligations.advance_to(1, scene_state=scene)

    assert transitions == [
        {"obligation_id": "reach_workshop", "status": "fulfilled"}
    ]
    assert obligations.obligations["reach_workshop"].status == "fulfilled"
    assert "authoritative" in obligations.obligations["reach_workshop"].resolution_reason


def test_completion_first_observed_after_grace_is_breached_not_retroactively_fulfilled():
    scene = _scene()
    obligations = ObligationState.from_initial(
        [
            {
                "obligation_id": "late_arrival",
                "title": "按时抵达工坊",
                "due_step": 2,
                "grace_steps": 0,
                "completion_conditions": [
                    StateCondition(
                        scope="actor",
                        target="学徒",
                        path="location",
                        operator="eq",
                        value="工坊",
                    )
                ],
            }
        ]
    )

    transitions = obligations.advance_to(3, scene_state=scene)

    assert transitions == [
        {"obligation_id": "late_arrival", "status": "breached"}
    ]
    record = obligations.obligations["late_arrival"]
    assert record.status == "breached"
    assert "deadline" in record.resolution_reason


def test_scheduler_wakes_offscreen_actor_before_obligation_deadline():
    apprentice = create_agent(
        "学徒",
        "钟表学徒",
        "认真",
        [],
        activation_policy="background",
        background_interval=99,
        initial_obligations=[
            {
                "obligation_id": "deliver_repair",
                "title": "交付怀表",
                "due_step": 5,
                "wake_before_steps": 2,
            }
        ],
    )

    activation = AgentScheduler().activation_for(
        apprentice,
        step=3,
        actor_location="工坊",
        player_location="王宫",
        proposals=[],
        is_player=False,
        has_manual_override=False,
    )

    assert activation.active is True
    assert activation.scope == "background"
    assert activation.reason == "obligation_due:deliver_repair"


def test_resolved_promise_creates_private_deadline_obligation_atomically():
    scene = _scene()
    drive = _drive()
    obligations = ObligationState()
    result = _base_result()
    result["resolved_actions"] = [
        {
            "actor": "学徒",
            "outcome": "success",
            "location": "工坊",
            "visibility": "public",
            "result": "学徒答应五步内修好怀表。",
        }
    ]
    result["obligation_updates"] = [
        {
            "operation": "create",
            "actor": "学徒",
            "source": "学徒",
            "obligation_id": "repair_watch",
            "title": "修好怀表",
            "summary": "在期限前完成修理并交还",
            "creditor": "师傅",
            "due_step": 7,
            "grace_steps": 1,
            "wake_before_steps": 2,
            "pressure_need": "责任压力",
            "reason": "学徒当面作出了承诺",
            "source_kind": "scenario",
            "source_ref": "forged-seed",
        }
    ]

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"学徒": drive},
        obligation_states={"学徒": obligations},
        current_step=2,
        proposal_actors={"学徒"},
    )

    assert outcome.committed is True
    record = obligations.obligations["repair_watch"]
    assert record.status == "scheduled"
    assert record.due_step == 7
    assert record.creditor == "师傅"
    assert record.pressure_need == "责任压力"
    assert record.source_kind == "resolved_action"
    assert record.source_ref == "step:2:actor:学徒"


def test_fulfillment_requires_action_evidence_and_checkpoint_restores_status():
    scene = _scene()
    drive = _drive()
    obligations = _obligations()
    result = _base_result()
    result["resolved_actions"] = [
        {
            "actor": "学徒",
            "outcome": "success",
            "location": "工坊",
            "visibility": "public",
            "result": "学徒把修好的怀表交给师傅。",
        }
    ]
    result["obligation_updates"] = [
        {
            "operation": "fulfill",
            "actor": "学徒",
            "source": "学徒",
            "obligation_id": "deliver_repair",
            "reason": "修好的怀表已经实际交付",
        }
    ]

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"学徒": drive},
        obligation_states={"学徒": obligations},
        current_step=2,
        proposal_actors={"学徒"},
    )

    assert outcome.committed is True
    assert obligations.obligations["deliver_repair"].status == "fulfilled"
    outcome.checkpoint.restore()
    assert obligations.obligations["deliver_repair"].status == "scheduled"


def test_model_cannot_declare_breach_or_fulfillment_without_evidence():
    scene = _scene()
    obligations = _obligations()
    drive = _drive()
    breach = _base_result()
    breach["obligation_updates"] = [
        {
            "operation": "breach",
            "actor": "学徒",
            "source": "学徒",
            "obligation_id": "deliver_repair",
            "reason": "模型自行宣布违约",
        }
    ]
    unsupported = _base_result()
    unsupported["obligation_updates"] = [
        {
            "operation": "fulfill",
            "actor": "学徒",
            "source": "学徒",
            "obligation_id": "deliver_repair",
            "reason": "没有行动但声称完成",
        }
    ]

    breach_outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        breach,
        drive_states={"学徒": drive},
        obligation_states={"学徒": obligations},
        current_step=2,
    )
    unsupported_outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        unsupported,
        drive_states={"学徒": drive},
        obligation_states={"学徒": obligations},
        current_step=2,
    )

    assert breach_outcome.committed is False
    assert unsupported_outcome.committed is False
    assert obligations.obligations["deliver_repair"].status == "scheduled"


def test_remote_hidden_assignment_cannot_create_telepathic_obligation():
    scene = _scene()
    scene.actor_states["师傅"]["location"] = "远处"
    obligations = ObligationState()
    drive = _drive()
    result = _base_result()
    result["resolved_actions"] = [
        {
            "actor": "师傅",
            "outcome": "success",
            "location": "远处",
            "visibility": "hidden",
            "result": "师傅在远处决定让学徒明天交货。",
        }
    ]
    result["obligation_updates"] = [
        {
            "operation": "create",
            "actor": "学徒",
            "source": "师傅",
            "obligation_id": "unknown_assignment",
            "title": "完成未知任务",
            "due_step": 5,
            "reason": "学徒并未听见的指派",
        }
    ]

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        result,
        drive_states={"学徒": drive},
        obligation_states={"学徒": obligations},
        current_step=1,
        proposal_actors={"师傅"},
    )

    assert outcome.committed is False
    assert obligations.obligations == {}
    assert any("not observable" in error for error in outcome.errors)


def test_obligations_are_private_in_perception_and_removed_from_render_payload():
    scene = _scene()
    apprentice = create_agent(
        "学徒",
        "钟表学徒",
        "认真",
        [],
        initial_obligations=[
            {
                "obligation_id": "deliver_repair",
                "title": "交付怀表",
                "due_step": 3,
            }
        ],
    )
    perception = InputSystem().build_agent_perception(
        apprentice,
        scene,
        [],
        {"clock": type("Clock", (), {"current_step": 2})()},
    )
    result = _base_result()
    result["obligation_updates"] = [
        {
            "operation": "create",
            "actor": "学徒",
            "obligation_id": "secret",
        }
    ]
    visible = RenderingSystem()._build_visible_simulation(
        result,
        scene.get_view_pov("学徒"),
        visible_locations=["工坊"],
    )

    assert perception.private_obligations["active"][0]["obligation_id"] == "deliver_repair"
    assert perception.private_obligations["active"][0]["steps_remaining"] == 1
    assert "obligations" not in perception.world_view
    assert visible["obligation_updates"] == []


def test_simulation_system_commits_dynamic_obligation_to_actor_component():
    scene = _scene()
    result = _base_result()
    result["resolved_actions"] = [
        {
            "actor": "学徒",
            "outcome": "success",
            "location": "工坊",
            "visibility": "public",
            "result": "学徒答应在五步内完成修理。",
        }
    ]
    result["obligation_updates"] = [
        {
            "operation": "create",
            "actor": "学徒",
            "source": "学徒",
            "obligation_id": "finish_repair",
            "title": "完成修理",
            "due_step": 6,
            "pressure_need": "责任压力",
            "reason": "学徒当面答应了期限",
        }
    ]
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    apprentice = create_agent(
        "学徒",
        "钟表学徒",
        "认真",
        [],
        initial_needs=[{"name": "责任压力", "pressure": 0.2}],
    )
    clock = type("Clock", (), {"current_step": 2})()
    context = {
        "clock": clock,
        "intents": [{"actor": "学徒", "intent": "答应完成修理"}],
    }

    SimulationSystem().update(
        {"GameMaster": gm, "学徒": apprentice},
        context,
    )

    assert context["state_transaction"]["committed"] is True
    record = apprentice.get_component("ObligationState").obligations["finish_repair"]
    assert record.due_step == 6
    assert record.pressure_need == "责任压力"
