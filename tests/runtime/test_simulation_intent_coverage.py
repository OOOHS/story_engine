import json

from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.core.entity import Entity


def _control():
    gm = Entity("WorldHost")
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


def test_production_has_no_rule_fallback_for_omitted_actions():
    control = _control()
    result = control._normalize_result({"resolved_actions": [{"actor": "甲", "result": "甲等待。"}]}, _payload())
    assert result["simulation_error"]["kind"] == "unresolved_intents"
    assert result["simulation_error"]["actors"] == ["乙"]
    assert result["state_updates"].get("actor_states", {}) == {}


def test_default_fail_closed_reports_unresolved_intents_instead_of_synthesizing():
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

    assert result["simulation_error"] == {
        "kind": "unresolved_intents",
        "actors": ["乙"],
        "message": "语义结算未覆盖全部主体意图，权威步骤应重试而非合成行动。",
    }
    assert [item["actor"] for item in result["resolved_actions"]] == ["甲"]


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


def test_semantic_physical_interaction_is_not_rewritten_by_a_keyword_policy():
    control = _control()

    class FakeLLM:
        def generate(self, prompt):
            return {
                "content": json.dumps(
                    {
                        "resolved_actions": [
                            {
                                "actor": "甲",
                                "intent": "我亲了乙一口。",
                                "action_kind": "interact",
                                "action_target": "乙",
                                "outcome": "success",
                                "location": "房间",
                                "result": "甲亲了乙一口。",
                                "private_result": "",
                                "visibility": "public",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }

    control._llm = FakeLLM()
    payload = {
        "intents": [
            {
                "actor": "甲",
                "intent": "我亲了乙一口。",
                "action_kind": "interact",
                "action_target": "乙",
                "location": "房间",
                "is_player": True,
            }
        ],
        "player_name": "甲",
        "player_pov": {"location": "房间"},
        "social": {"allow_unsignaled_touch": False},
        "legality": {
            "checks": [
                {
                    "actor": "甲",
                    "intent": "我亲了乙一口。",
                    "verdict": "allow",
                    "rule": "none",
                    "rewrite_location": None,
                }
            ]
        },
    }

    result = control.simulate(payload)

    assert result["simulation_error"] is None
    assert result["resolved_actions"] == [
        {
            "actor": "甲",
            "intent": "我亲了乙一口。",
            "action_kind": "interact",
            "action_target": "乙",
            "outcome": "success",
            "location": "房间",
            "result": "甲亲了乙一口。",
            "private_result": "",
            "visibility": "public",
            "source_storylet_id": "",
        }
    ]
