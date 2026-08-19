from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.narrative.storylets import StoryletEngine
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_results: list = Field(default_factory=list)
    scenario: object = None
    payloads: list = Field(default_factory=list)

    def simulate(self, payload):
        self.payloads.append(deepcopy(payload))
        index = min(len(self.payloads) - 1, len(self.scripted_results) - 1)
        return deepcopy(self.scripted_results[index])


def _scene():
    return SceneState(
        world_objects={"粮仓": {"is_location": True}},
        actor_states={"守卫甲": {"location": "粮仓"}},
    )


def _proposal(*, with_letter_condition=True):
    conditions = (
        [
            {
                "scope": "world_object",
                "target": "求援信",
                "path": "",
                "operator": "exists",
            }
        ]
        if with_letter_condition
        else [
            {
                "scope": "actor",
                "target": "守卫甲",
                "path": "location",
                "operator": "eq",
                "value": "粮仓",
            }
        ]
    )
    return {
        "plot_id": "southern_drought",
        "beat_id": "visitor_letter",
        "intent": "粮仓出现一封求援信",
        "kind": "environment",
        "one_shot": True,
        "conditions": conditions,
        "effect": {"visibility": "local", "stake": "赈灾"},
        "open_thread": {
            "title": "南方旱情",
            "description": "粮仓告急",
            "opened_reason": "连续三名角色抱怨粮价",
            "participants": ["守卫甲"],
        },
    }


def test_runtime_beat_waits_for_setup_object_then_surfaces_as_storylet():
    scene = _scene()
    plots = PlotState()
    plots.apply_beat_proposals([_proposal()], current_step=3, known_actors={"守卫甲"})

    engine = StoryletEngine()
    scenario = ScenarioConfig(
        name="runtime beat",
        default_agent_runtime="llm",
        description="",
        environment="粮仓",
        initial_state="",
    )

    assert engine.resolve(scene, plots, scenario) == []

    scene.world_objects["求援信"] = {"kind": "document", "location": "粮仓"}
    active = engine.resolve(scene, plots, scenario)

    assert [item["storylet_id"] for item in active] == [
        "runtime:southern_drought:visitor_letter"
    ]
    assert active[0]["kind"] == "environment"
    assert active[0]["runtime_beat"] is True


def test_world_transaction_registers_plot_beat_and_rolls_it_back_on_failure():
    scene = _scene()
    plots = PlotState()
    committed = WorldStateTransaction().commit(
        scene,
        plots,
        None,
        {
            "state_updates": {},
            "plot_beat_proposals": [_proposal(with_letter_condition=False)],
        },
        current_step=5,
    )

    assert committed.committed is True
    assert "southern_drought" in plots.plots
    assert plots.plots["southern_drought"]["candidate_beats"][0]["beat_id"] == (
        "visitor_letter"
    )

    scene_fail = _scene()
    plots_fail = PlotState()
    rejected = WorldStateTransaction().commit(
        scene_fail,
        plots_fail,
        None,
        {
            "state_updates": {},
            "plot_beat_proposals": [_proposal(with_letter_condition=False)],
            "plot_updates": [{"plot_id": "no_such_plot", "advance": 1}],
        },
        current_step=5,
    )

    assert rejected.committed is False
    assert plots_fail.plots == {}


def test_simulation_registers_beat_then_cashes_it_on_the_next_tick():
    scene = _scene()
    plots = PlotState()
    storylet_id = PlotState.runtime_storylet_id("southern_drought", "visitor_letter")
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scenario=ScenarioConfig(
                name="造点",
                default_agent_runtime="llm",
                description="",
                environment="粮仓",
                initial_state="",
            ),
            scripted_results=[
                {
                    "resolved_actions": [
                        {
                            "actor": "守卫甲",
                            "intent": "守夜",
                            "action_kind": "wait",
                            "outcome": "success",
                            "location": "粮仓",
                            "result": "守卫甲继续守夜。",
                            "visibility": "local",
                        }
                    ],
                    "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
                    "plot_beat_proposals": [_proposal(with_letter_condition=False)],
                    "conflict_level": "none",
                },
                {
                    "resolved_actions": [
                        {
                            "actor": "守卫甲",
                            "intent": "守夜",
                            "action_kind": "wait",
                            "outcome": "success",
                            "location": "粮仓",
                            "result": "守卫甲继续守夜。",
                            "visibility": "local",
                        },
                        {
                            "actor": "World",
                            "intent": "一封求援信被钉在粮仓门上",
                            "action_kind": "interact",
                            "outcome": "success",
                            "location": "粮仓",
                            "result": "粮仓门上多了一封求援信。",
                            "visibility": "public",
                            "source_storylet_id": storylet_id,
                        },
                    ],
                    "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
                    "conflict_level": "low",
                },
            ],
        )
    )
    gm.add_component(scene)
    gm.add_component(plots)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm}
    intent = {
        "actor": "守卫甲",
        "intent": "守夜",
        "action_kind": "wait",
        "location": "粮仓",
    }

    SimulationSystem().update(entities, {"intents": [intent]})

    assert "southern_drought" in plots.plots
    assert plots.plots["southern_drought"]["clock"] == 0
    control = gm.get_component("SimulationControl")
    assert "narrative_pressure" in control.payloads[0]

    SimulationSystem().update(entities, {"intents": [intent]})

    opportunities = control.payloads[1]["storylet_opportunities"]
    assert any(item["storylet_id"] == storylet_id for item in opportunities)
    assert plots.plots["southern_drought"]["candidate_beats"] == []
    assert plots.plots["southern_drought"]["clock"] == 1
