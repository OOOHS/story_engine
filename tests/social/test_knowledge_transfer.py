from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.cognition import CognitionSystem


class SimulationControl(Component):
    pass


def _world(target_location="书房"):
    gm = Entity("WorldHost")
    gm.add_component(SimulationControl())
    gm.add_component(
        SceneState(
            world_objects={"书房": {}, "花园": {}},
            actor_states={
                "知情者": {"location": "书房"},
                "听众": {"location": target_location},
                "旁观者": {"location": "书房"},
            },
        )
    )
    source = create_agent(
        "知情者", "证人", "谨慎", ["保护秘密"],
        initial_secrets=["钥匙藏在旧钟后面"],
    agent_runtime="llm")
    target = create_agent("听众", "调查员", "多疑", ["寻找钥匙"], agent_runtime="llm")
    observer = create_agent("旁观者", "仆人", "沉默", ["完成工作"], agent_runtime="llm")
    return gm, source, target, observer


def _context(statement="钥匙藏在旧钟后面"):
    return {
        "clock": None,
        "simulation_result": {
            "resolved_actions": [
                {
                    "actor": "知情者",
                    "intent": "低声告诉听众钥匙的位置",
                    "action_kind": "communicate",
                    "action_target": "听众",
                    "outcome": "success",
                    "result": "知情者把钥匙的位置告诉了听众。",
                    "location": "书房",
                    "visibility": "local",
                }
            ],
            "knowledge_updates": [
                {
                    "source": "知情者",
                    "target": "听众",
                    "statement": statement,
                    "confidence": 0.85,
                    "mode": "told",
                    "reason": "知情者本轮成功告诉了听众",
                }
            ],
        },
    }


def test_known_secret_can_be_transferred_to_one_colocated_character():
    gm, source, target, observer = _world()
    context = _context()

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    target_belief = target.get_component("Cognition").beliefs[0]
    assert target_belief["statement"] == "钥匙藏在旧钟后面"
    assert target_belief["confidence"] == 0.85
    assert target_belief["source"] == "told_by:知情者"
    assert observer.get_component("Cognition").beliefs == []
    assert context["knowledge_transfers"][0]["target"] == "听众"


def test_sender_cannot_transfer_statement_absent_from_private_knowledge():
    gm, source, target, observer = _world()
    context = _context(statement="城主其实是龙")

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    assert target.get_component("Cognition").beliefs == []
    assert context["knowledge_transfers"] == []


def test_knowledge_update_cannot_telepathically_cross_locations():
    gm, source, target, observer = _world(target_location="花园")
    context = _context()

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    assert target.get_component("Cognition").beliefs == []
    assert context["knowledge_transfers"] == []


def test_listener_can_receive_origin_message_while_moving_away_this_batch():
    gm, source, target, observer = _world(target_location="花园")
    context = _context()
    context["actor_observation_windows"] = {
        "知情者": {"locations": ["书房"]},
        "听众": {"locations": ["书房", "花园"]},
        "旁观者": {"locations": ["书房"]},
    }

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    assert target.get_component("Cognition").beliefs[0]["statement"] == (
        "钥匙藏在旧钟后面"
    )
    assert context["knowledge_transfers"][0]["target"] == "听众"


def test_failed_communication_cannot_transfer_private_knowledge():
    gm, source, target, observer = _world()
    context = _context()
    context["simulation_result"]["resolved_actions"][0]["outcome"] = "fail"

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    assert target.get_component("Cognition").beliefs == []
    assert context["knowledge_transfers"] == []


def test_knowledge_update_requires_resolved_source_action_evidence():
    gm, source, target, observer = _world()
    context = _context()
    context["simulation_result"]["resolved_actions"] = [
        {
            "actor": "旁观者",
            "intent": "整理书桌",
            "outcome": "success",
            "result": "旁观者整理了书桌。",
            "location": "书房",
            "visibility": "public",
        }
    ]

    CognitionSystem().update(
        {"WorldHost": gm, "知情者": source, "听众": target, "旁观者": observer},
        context,
    )

    assert target.get_component("Cognition").beliefs == []
