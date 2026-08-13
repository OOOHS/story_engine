from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentPerception
from src.story_engine.simulation import AffordanceActionResolver
from src.story_engine.systems.input import InputSystem


def _intent():
    return {
        "actor": "甲",
        "intent": "吃掉面包",
        "action_kind": "interact",
        "action_target": "面包",
        "action_affordance_id": "eat",
    }


def test_input_accepts_only_currently_available_matching_affordance():
    action = AgentAction("interact", "吃掉面包", "面包", "eat")
    available = AgentPerception(
        actor_name="甲",
        step=0,
        affordance_opportunities=[
            {
                "object_id": "面包",
                "affordance_id": "eat",
                "available": True,
            }
        ],
    )
    stale = AgentPerception(
        actor_name="甲",
        step=0,
        affordance_opportunities=[
            {
                "object_id": "面包",
                "affordance_id": "eat",
                "available": False,
            }
        ],
    )

    assert InputSystem._validated_affordance_reference(action, available) == "eat"
    assert InputSystem._validated_affordance_reference(action, stale) == ""
    assert InputSystem._validated_affordance_reference(
        AgentAction("interact", "吃掉面包", "别的面包", "eat"), available
    ) == ""


def test_blocked_action_cannot_keep_model_forged_use_operation():
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "action_kind": "interact",
                "action_target": "面包",
                "outcome": "blocked",
            }
        ],
        "object_lifecycle": [
            {
                "operation": "use",
                "object_id": "面包",
                "affordance_id": "drink",
                "actor": "甲",
            }
        ],
    }

    resolution = AffordanceActionResolver().resolve(result, intents=[_intent()])

    assert resolution.result["object_lifecycle"] == []
    assert resolution.traces[0]["status"] == "semantic_use_rejected"
