from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.core.entity import Entity


def _control():
    gm = Entity("GameMaster")
    gm.add_component(
        SceneState(
            world_objects={
                "房间": {"connected_to": ["走廊"]},
                "走廊": {"connected_to": ["房间"]},
            },
            actor_states={
                "甲": {"location": "房间"},
                "乙": {"location": "房间"},
            },
        )
    )
    control = SimulationControl(llm_config={})
    gm.add_component(control)
    return control


def _payload():
    intents = [
        {
            "actor": "甲",
            "intent": "留在原地等一会儿。",
            "action_kind": "wait",
            "action_target": "",
            "location": "房间",
            "is_player": True,
        },
        {
            "actor": "乙",
            "intent": "从房间前往走廊。",
            "action_kind": "move",
            "action_target": "走廊",
            "location": "房间",
            "is_player": False,
        },
    ]
    return {
        "intents": intents,
        "player_name": "甲",
        "player_pov": {"location": "房间"},
        "legality": {
            "checks": [
                {
                    "actor": "甲",
                    "intent": intents[0]["intent"],
                    "verdict": "allow",
                    "rule": "none",
                    "rewrite_location": None,
                },
                {
                    "actor": "乙",
                    "intent": intents[1]["intent"],
                    "verdict": "allow",
                    "rule": "movement",
                    "rewrite_location": "走廊",
                },
            ]
        },
    }


def test_host_fallback_resolves_each_agent_omitted_from_a_batch():
    control = _control()
    payload = _payload()

    result = control._normalize_result(
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "intent": "GM 改写的文本不具有权威性",
                    "outcome": "success",
                    "result": "甲等待。",
                }
            ]
        },
        payload,
    )
    result = control._enforce_legality(result, payload)

    by_actor = {item["actor"]: item for item in result["resolved_actions"]}
    assert set(by_actor) == {"甲", "乙"}
    assert by_actor["甲"]["intent"] == payload["intents"][0]["intent"]
    assert by_actor["乙"]["action_kind"] == "move"
    assert by_actor["乙"]["outcome"] == "success"
    assert result["state_updates"]["actor_states"]["乙"]["location"] == "走廊"
    assert result["simulation_notes"] == [
        "Host 为语义结算遗漏的 Agent 动作应用了原子回退：乙。"
    ]


def test_pending_host_check_counts_as_coverage_without_duplicate_resolution():
    control = _control()
    payload = _payload()

    result = control._normalize_result(
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "outcome": "success",
                    "result": "甲等待。",
                }
            ],
            "uncertain_outcomes": [
                {
                    "check_id": "乙移动检查",
                    "actor": "乙",
                    "check_kind": "world",
                    "difficulty": "normal",
                    "success": {},
                    "failure": {},
                }
            ],
        },
        payload,
    )

    assert [item["actor"] for item in result["resolved_actions"]] == ["甲"]
    assert result["simulation_notes"] == []
