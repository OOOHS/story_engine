from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import (
    SimulationControl as EngineSimulationControl,
)
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.simulation.checks import HostCheckResolver
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.simulation.uncertain_outcomes import UncertainOutcomeResolver
from src.story_engine.systems.simulation import SimulationSystem


def _branch(outcome, result, description):
    return {
        "resolved_action": {
            "outcome": outcome,
            "result": result,
            "visibility": "public",
        },
        "state_updates": {
            "scene": {"description": description},
            "world_objects": {},
            "actor_states": {},
        },
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": [],
        "agreement_updates": [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def _result():
    return {
        "resolved_actions": [],
        "uncertain_outcomes": [
            {
                "check_id": "force_door",
                "actor": "甲",
                "check_kind": "world",
                "difficulty": "hard",
                "required_capability": "strength",
                "success": _branch("success", "甲撞开了门。", "门已经被撞开。"),
                "failure": _branch("fail", "门纹丝不动。", "门仍然紧闭。"),
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "knowledge_updates": [],
        "object_lifecycle": [],
        "exchanges": [],
        "agreement_updates": [],
        "drive_updates": [],
        "obligation_updates": [],
        "tension_delta": 0,
    }


def _scene():
    return SceneState(
        description="门挡在面前。",
        world_objects={"大厅": {}},
        actor_states={
            "甲": {
                "location": "大厅",
                "capabilities": ["strength"],
            }
        },
    )


def _intents():
    return [
        {
            "actor": "甲",
            "intent": "用肩膀撞门",
            "action_kind": "interact",
            "action_target": "门",
            "location": "大厅",
        }
    ]


def test_host_selects_one_consequence_branch_and_discards_the_other():
    source = _result()
    source["uncertain_outcomes"][0]["success"]["social_impacts"] = [
        {"source": "甲", "affected": "乙", "kind": "grateful", "magnitude": 0.5, "reason": "成功"}
    ]
    source["uncertain_outcomes"][0]["failure"]["social_impacts"] = [
        {"source": "甲", "affected": "乙", "kind": "angry", "magnitude": 0.5, "reason": "失败"}
    ]
    resolver = UncertainOutcomeResolver()
    checks = HostCheckResolver(DeterministicRandomStreams("branch-seed"))

    first = resolver.resolve(
        source,
        scene_state=_scene(),
        intents=_intents(),
        check_resolver=checks,
        current_step=2,
        world_version=4,
    )
    second = resolver.resolve(
        source,
        scene_state=_scene(),
        intents=_intents(),
        check_resolver=checks,
        current_step=2,
        world_version=4,
    )

    assert first.errors == []
    assert first.result == second.result
    assert first.traces == second.traces
    assert "uncertain_outcomes" not in first.result
    selected = first.traces[0]["selected_branch"]
    action = first.result["resolved_actions"][0]
    assert action["actor"] == "甲"
    assert action["action_kind"] == "interact"
    assert (action["outcome"] == "success") is (selected == "success")
    serialized = str(first.result)
    assert not (
        "甲撞开了门" in serialized and "门纹丝不动" in serialized
    )
    assert first.traces[0]["modifiers"][0]["modifier_id"] == "capability:strength"
    assert len(first.result["social_impacts"]) == 1
    expected_kind = "grateful" if selected == "success" else "angry"
    assert first.result["social_impacts"][0]["kind"] == expected_kind


def test_observation_random_stream_requires_an_observe_proposal():
    result = _result()
    result["uncertain_outcomes"][0]["check_kind"] = "observation"

    resolution = UncertainOutcomeResolver().resolve(
        result,
        scene_state=_scene(),
        intents=_intents(),
        check_resolver=HostCheckResolver(DeterministicRandomStreams(1)),
        current_step=0,
        world_version=0,
    )

    assert any("requires an observe proposal" in error for error in resolution.errors)
    assert resolution.traces == []


def test_gm_cannot_supply_probability_roll_or_numeric_modifiers():
    result = _result()
    result["uncertain_outcomes"][0]["probability"] = 1.0
    result["uncertain_outcomes"][0]["roll"] = 0.0
    result["uncertain_outcomes"][0]["modifiers"] = [{"delta": 9}]

    resolution = UncertainOutcomeResolver().resolve(
        result,
        scene_state=_scene(),
        intents=_intents(),
        check_resolver=HostCheckResolver(DeterministicRandomStreams(1)),
        current_step=0,
        world_version=0,
    )

    assert any("unknown fields" in error for error in resolution.errors)
    assert resolution.traces == []


def test_uncertain_non_move_branches_cannot_write_actor_locations():
    result = _result()
    for branch_name in ("success", "failure"):
        result["uncertain_outcomes"][0][branch_name]["state_updates"][
            "actor_states"
        ] = {
            "甲": {"location": "月球"},
            "乙": {"location": "大厅"},
        }

    resolution = UncertainOutcomeResolver().resolve(
        result,
        scene_state=_scene(),
        intents=_intents(),
        check_resolver=HostCheckResolver(DeterministicRandomStreams(1)),
        current_step=0,
        world_version=0,
    )

    assert resolution.errors == []
    assert resolution.result["state_updates"]["actor_states"] == {}
    assert resolution.result["resolved_actions"][0]["location"] == "大厅"
    assert len(resolution.rejected_writes) == 4
    assert all(path.endswith(".location") for path in resolution.rejected_writes)


def test_uncertain_move_branch_can_only_choose_origin_or_host_authorized_destination():
    scene = SceneState(
        world_objects={
            "大厅": {"connected_to": ["密室"]},
            "密室": {"connected_to": ["大厅"]},
        },
        actor_states={"甲": {"location": "大厅", "capabilities": ["agility"]}},
    )
    intents = [
        {
            "actor": "甲",
            "intent": "穿过摇晃的门前往密室",
            "action_kind": "move",
            "action_target": "密室",
            "location": "大厅",
        }
    ]
    result = _result()
    check = result["uncertain_outcomes"][0]
    check["actor"] = "甲"
    check["required_capability"] = "agility"
    check["success"] = _branch("success", "甲抵达了密室。", "门仍在摇晃。")
    check["failure"] = _branch("fail", "甲没能通过。", "门仍在摇晃。")
    check["success"]["state_updates"]["actor_states"] = {
        "甲": {"location": "密室"}
    }
    check["failure"]["state_updates"]["actor_states"] = {
        "甲": {"location": "大厅"}
    }

    resolution = UncertainOutcomeResolver().resolve(
        result,
        scene_state=scene,
        intents=intents,
        check_resolver=HostCheckResolver(
            DeterministicRandomStreams("uncertain-move")
        ),
        current_step=2,
        world_version=1,
        movement_authorizations={"甲": "密室"},
    )

    assert resolution.errors == []
    assert resolution.rejected_writes == []
    selected = resolution.traces[0]["selected_branch"]
    expected_location = "密室" if selected == "success" else "大厅"
    assert resolution.result["resolved_actions"][0]["location"] == expected_location
    assert resolution.result["state_updates"]["actor_states"]["甲"][
        "location"
    ] == expected_location


def test_uncertain_move_branch_cannot_replace_authorized_destination():
    scene = SceneState(
        world_objects={"大厅": {}, "密室": {}, "屋顶": {}},
        actor_states={"甲": {"location": "大厅"}},
    )
    intents = [
        {
            "actor": "甲",
            "intent": "前往密室",
            "action_kind": "move",
            "action_target": "密室",
            "location": "大厅",
        }
    ]
    result = _result()
    for branch_name in ("success", "failure"):
        result["uncertain_outcomes"][0][branch_name]["state_updates"][
            "actor_states"
        ] = {"甲": {"location": "屋顶"}}

    resolution = UncertainOutcomeResolver().resolve(
        result,
        scene_state=scene,
        intents=intents,
        check_resolver=HostCheckResolver(DeterministicRandomStreams(3)),
        current_step=0,
        world_version=0,
        movement_authorizations={"甲": "密室"},
    )

    assert resolution.errors == []
    assert resolution.result["state_updates"]["actor_states"] == {}
    assert resolution.result["resolved_actions"][0]["location"] == "大厅"
    assert len(resolution.rejected_writes) == 2


def test_simulation_control_preserves_uncertain_check_without_fallback_action():
    control = EngineSimulationControl()

    normalized = control._normalize_result(
        _result(),
        {"intents": _intents(), "player_pov": {"location": "大厅"}},
    )

    assert normalized["resolved_actions"] == []
    assert len(normalized["uncertain_outcomes"]) == 1


def test_hard_legality_block_cancels_actor_uncertain_check():
    control = EngineSimulationControl()
    payload = {
        "intents": _intents(),
        "player_pov": {"location": "大厅"},
        "legality": {
            "checks": [
                {
                    "actor": "甲",
                    "intent": "用肩膀撞门",
                    "verdict": "block",
                    "reason": "门并不在甲当前可接触的范围内",
                }
            ]
        },
    }
    normalized = control._normalize_result(_result(), payload)

    enforced = control._enforce_legality(normalized, payload)

    assert enforced["uncertain_outcomes"] == []
    assert enforced["resolved_actions"][0]["outcome"] == "blocked"


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


def test_simulation_system_rolls_branch_before_authoritative_transaction():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=_result()))
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {
        "clock": type("Clock", (), {"current_step": 2})(),
        "intents": _intents(),
        "check_resolver": HostCheckResolver(DeterministicRandomStreams("integration")),
    }

    SimulationSystem().update({"GameMaster": gm, "甲": Entity("甲")}, context)

    assert context["state_transaction"]["committed"] is True
    assert len(context["outcome_check_traces"]) == 1
    selected = context["outcome_check_traces"][0]["selected_branch"]
    assert scene.description == (
        "门已经被撞开。" if selected == "success" else "门仍然紧闭。"
    )
    assert "uncertain_outcomes" not in context["simulation_result"]


def test_simulation_system_audits_and_strips_uncertain_location_bypass():
    result = _result()
    for branch_name in ("success", "failure"):
        result["uncertain_outcomes"][0][branch_name]["state_updates"][
            "actor_states"
        ] = {"甲": {"location": "月球"}}
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {
        "clock": type("Clock", (), {"current_step": 2})(),
        "intents": _intents(),
        "check_resolver": HostCheckResolver(
            DeterministicRandomStreams("location-bypass")
        ),
    }

    SimulationSystem().update({"GameMaster": gm, "甲": Entity("甲")}, context)

    assert context["state_transaction"]["committed"] is True
    assert scene.get_actor_location("甲") == "大厅"
    rejected = context["semantic_authority_rejections"]
    assert len([path for path in rejected if path.endswith(".location")]) == 2


def test_invalid_selected_branch_is_rejected_atomically():
    result = _result()
    for branch in ("success", "failure"):
        result["uncertain_outcomes"][0][branch]["state_updates"][
            "actor_states"
        ] = {"不存在的人": {"mood": "forged"}}
    scene = _scene()
    before_description = scene.description
    before_actors = deepcopy(scene.actor_states)
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl(scripted_result=result))
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {
        "clock": type("Clock", (), {"current_step": 2})(),
        "intents": _intents(),
        "check_resolver": HostCheckResolver(DeterministicRandomStreams("invalid")),
    }

    SimulationSystem().update({"GameMaster": gm, "甲": Entity("甲")}, context)

    assert context["state_transaction"]["committed"] is False
    assert scene.description == before_description
    assert scene.actor_states == before_actors
    assert context["simulation_result"]["resolved_actions"] == []
