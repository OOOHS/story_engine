from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.commitment import (
    commit_runtime_action,
    repetition_signature,
    repetition_target,
)
from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.agents.types import AgentDecision
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.systems.input import InputSystem


def test_host_records_the_runtime_action_verbatim_without_re_ranking_it():
    action = AgentAction("interact", "冒险冲过火线帮助乙。", "乙")
    decision = AgentDecision(action=action.detail, action_spec=action)

    commitment = commit_runtime_action(decision)

    assert commitment.action == action
    assert commitment.trace == {
        "mode": "runtime_committed",
        "selected_candidate_id": "runtime:0",
        "selected_action": action.to_dict(),
        "candidates": [
            {
                "candidate_id": "runtime:0",
                "source": "runtime",
                "action": action.to_dict(),
            }
        ],
    }


def test_a_bare_action_string_still_commits_through_normalization():
    commitment = commit_runtime_action(AgentDecision(action="留在原地等待。"))

    assert commitment.action.detail == "留在原地等待。"
    assert commitment.trace["selected_candidate_id"] == "runtime:0"


def test_repetition_ledger_counts_the_same_plan_and_resets_on_a_new_one():
    controller = AgentController(runtime="hermes")
    wait = AgentAction("wait", "继续等待。")
    observe = AgentAction("observe", "再次检查房间。", "房间")

    for _ in range(4):
        controller.record_policy_action(repetition_signature(wait))

    assert controller.repeated_policy_action_count == 4
    assert controller.max_repeated_policy_action_count == 4

    observe_signature = repetition_signature(observe)
    controller.record_policy_action(observe_signature, "木门")
    controller.record_policy_action(observe_signature, "窗户")

    assert controller.repeated_policy_action_count == 1
    assert controller.last_policy_action_target == "窗户"
    assert controller.max_repeated_policy_action_count == 4


def test_rewording_the_same_plan_does_not_escape_the_repetition_count():
    first = AgentAction("observe", "仔细检查木门的锁。", "木门")
    reworded = AgentAction("observe", "再一次查看木门上的锁。", "木门")

    assert repetition_signature(first) == repetition_signature(reworded)


def test_input_system_commits_the_runtime_action_and_updates_the_ledger():
    class SimulationControl(Component):
        pass

    class CommittedRuntime:
        def __init__(self):
            self.perception = None
            self.action = AgentAction("interact", "冒险冲过火线帮助乙。", "乙")

        def decide(self, _entity, perception):
            self.perception = perception
            return AgentDecision(
                action=self.action.detail,
                action_spec=self.action,
            )

    entity = Entity("甲")
    entity.add_component(AgentController(runtime="hermes"))
    runtime = CommittedRuntime()
    registry = AgentRegistry()
    registry.register(entity, runtime)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"大厅": {}},
            actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
        )
    )
    context = {
        "agent_registry": registry,
        "overrides": {},
        "clock": type("Clock", (), {"current_step": 1})(),
        "player_name": None,
        "inject_events": [],
        "intents": [],
    }

    InputSystem().update({"GameMaster": gm, "甲": entity}, context)

    trace = context["policy_traces"]["甲"]
    assert trace["mode"] == "runtime_committed"
    assert context["intents"][0]["action"] == trace["selected_action"]
    assert "probability" not in context["intents"][0]
    controller = entity.get_component("AgentController")
    assert controller.repeated_policy_action_count == 1
    assert controller.last_policy_action_signature == repetition_signature(
        runtime.action
    )
    assert controller.last_policy_action_target == repetition_target(runtime.action)
