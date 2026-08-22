from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.systems.simulation import SimulationSystem


class ScriptedSimulationControl(Component):
    component_slot = "SimulationControl"
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


class ScriptedNarrativeDirector(Component):
    component_slot = "NarrativeDirector"
    scripted_result: dict = Field(default_factory=dict)
    payloads: list = Field(default_factory=list)
    raise_on_direct: bool = False

    def direct(self, payload):
        self.payloads.append(deepcopy(payload))
        if self.raise_on_direct:
            raise RuntimeError("narrative director unavailable")
        return deepcopy(self.scripted_result)


def _scene():
    return SceneState(
        world_objects={"粮仓": {"is_location": True}},
        actor_states={"守卫甲": {"location": "粮仓"}},
    )


def _settle_action():
    return {
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
        "conflict_level": "none",
    }


def _proposal():
    return {
        "plot_id": "southern_drought",
        "beat_id": "visitor_letter",
        "intent": "粮仓出现一封求援信",
        "kind": "environment",
        "one_shot": True,
        "conditions": [
            {
                "scope": "actor",
                "target": "守卫甲",
                "path": "location",
                "operator": "eq",
                "value": "粮仓",
            }
        ],
        "effect": {"visibility": "local", "stake": "赈灾"},
        "open_thread": {
            "title": "南方旱情",
            "description": "粮仓告急",
            "opened_reason": "守卫甲连续三夜守着空粮仓",
            "participants": ["守卫甲"],
        },
    }


def _entities(scene, plots, narrative_director):
    gm = Entity("GameMaster")
    gm.add_component(
        ScriptedSimulationControl(
            scripted_result=_settle_action(),
            scenario=ScenarioConfig(
                name="叙事导演分离",
                default_agent_runtime="llm",
                description="",
                environment="粮仓",
                initial_state="",
            ),
        )
    )
    gm.add_component(scene)
    gm.add_component(plots)
    gm.add_component(DramaState())
    if narrative_director is not None:
        gm.add_component(narrative_director)
    return {"GameMaster": gm}


def _intent():
    return {
        "actor": "守卫甲",
        "intent": "守夜",
        "action_kind": "wait",
        "location": "粮仓",
    }


def test_narrative_director_runs_only_after_commit_and_registers_a_beat():
    scene = _scene()
    plots = PlotState()
    director = ScriptedNarrativeDirector(
        scripted_result={"plot_beat_proposals": [_proposal()], "director_signals": []}
    )
    entities = _entities(scene, plots, director)

    SimulationSystem().update(entities, {"intents": [_intent()]})

    assert "southern_drought" in plots.plots
    assert plots.plots["southern_drought"]["candidate_beats"][0]["beat_id"] == (
        "visitor_letter"
    )
    # It only ever sees already-committed facts, never a pre-commit guess.
    payload = director.payloads[0]
    assert payload["committed_facts"]["resolved_actions"][0]["actor"] == "守卫甲"


def test_narrative_director_signal_is_queued_for_the_named_actor():
    scene = _scene()
    plots = PlotState()
    director = ScriptedNarrativeDirector(
        scripted_result={
            "plot_beat_proposals": [],
            "director_signals": [
                {"actor": "守卫甲", "suggestion": "你注意到锁有些松动"}
            ],
        }
    )
    entities = _entities(scene, plots, director)

    SimulationSystem().update(entities, {"intents": [_intent()]})

    popped = scene.pop_director_signals("守卫甲", current_step=1)
    assert len(popped) == 1
    assert popped[0]["suggestion"] == "你注意到锁有些松动"


def test_narrative_director_output_still_passes_through_authority_filter():
    """A malformed proposal (missing a real condition) is dropped by
    SemanticAuthorityFilter same as it would be for SimulationControl --
    NarrativeDirector does not bypass the host-owned write boundary just
    because it runs after commit.
    """
    scene = _scene()
    plots = PlotState()
    bad_proposal = _proposal()
    bad_proposal["conditions"] = []
    director = ScriptedNarrativeDirector(
        scripted_result={"plot_beat_proposals": [bad_proposal], "director_signals": []}
    )
    entities = _entities(scene, plots, director)
    context = {"intents": [_intent()]}

    SimulationSystem().update(entities, context)

    assert plots.plots == {}
    assert "result.plot_beat_proposals[0].conditions" in context.get(
        "semantic_authority_rejections", []
    )


def test_narrative_director_failure_never_rolls_back_an_already_committed_tick():
    scene = _scene()
    plots = PlotState()
    director = ScriptedNarrativeDirector(raise_on_direct=True)
    entities = _entities(scene, plots, director)
    context = {"intents": [_intent()]}

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert scene.get_actor_location("守卫甲") == "粮仓"
    assert plots.plots == {}
