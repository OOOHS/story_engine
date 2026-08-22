from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.scenarios.config import (
    ScenarioConfig,
    StoryBeatConfig,
    StoryletConfig,
)
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


def _scenario():
    return ScenarioConfig(
        name="自然命中测试",
        default_agent_runtime="llm",
        description="Storylet 只观察已经发生的行动。",
        environment="大厅",
        initial_state="甲乙都在大厅。",
        storylets=[
            StoryletConfig(
                storylet_id="public_objection",
                intent="有人公开提出异议",
                priority=100,
                one_shot=True,
                beat=StoryBeatConfig(
                    preferred_actors=["乙"],
                    visibility="public",
                ),
            )
        ],
    )


def _entities(result):
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=result, scenario=_scenario()))
    gm.add_component(scene)
    gm.add_component(PlotState())
    gm.add_component(DramaState())
    return {"GameMaster": gm}, scene


def _result(actor, outcome, *, claimed_hits=None):
    return {
        "resolved_actions": [
            {
                "actor": actor,
                "intent": "表达自己的意见",
                # interact, not communicate: communicate is now settled
                # deterministically by the host (always "success" once
                # legality allows it) and never carries a scripted
                # outcome, so it cannot exercise the blocked/complication/
                # partial beat-realization path this suite is testing.
                "action_kind": "interact",
                "action_target": "甲" if actor == "乙" else "乙",
                "outcome": outcome,
                "location": "大厅",
                "visibility": "public",
                "result": f"{actor}根据自己的立场表达了意见。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "storylet_hits": list(claimed_hits or []),
        "conflict_level": "low",
    }


def test_natural_committed_action_is_detected_and_consumes_one_shot_storylet():
    entities, scene = _entities(_result("乙", "complication"))
    context = {
        "intents": [
            {
                "actor": "乙",
                "intent": "表达自己的意见",
                "action_kind": "interact",
                "action_target": "甲",
                "location": "大厅",
            }
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["simulation_result"]["storylet_hits"] == ["public_objection"]
    assert scene.get_scene_flag("consumed_storylets") == ["public_objection"]


def test_gm_claimed_storylet_hit_is_ignored_when_no_matching_action_occurred():
    entities, scene = _entities(
        _result("甲", "success", claimed_hits=["public_objection"])
    )
    context = {
        "intents": [
            {
                "actor": "甲",
                "intent": "表达自己的意见",
                "action_kind": "interact",
                "action_target": "乙",
                "location": "大厅",
            }
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["simulation_result"]["storylet_hits"] == []
    assert scene.get_scene_flag("consumed_storylets") is None
    assert "result.storylet_hits" in context["semantic_authority_rejections"]


def test_natural_hit_is_not_consumed_when_world_transaction_fails():
    result = _result("乙", "complication")
    result["state_updates"]["actor_states"] = {
        "不存在的人": {"location": "大厅"}
    }
    entities, scene = _entities(result)
    context = {
        "intents": [
            {
                "actor": "乙",
                "intent": "表达自己的意见",
                "action_kind": "interact",
                "action_target": "甲",
                "location": "大厅",
            }
        ]
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is False
    assert scene.get_scene_flag("consumed_storylets") is None
    assert context["simulation_result"]["storylet_hits"] == []
