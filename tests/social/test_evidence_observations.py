from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.knowledge import ClaimRegistry
from src.story_engine.simulation import EvidenceObservationResolver
from src.story_engine.systems.claim_knowledge import ClaimKnowledgeSystem


def _world():
    scene = SceneState(
        world_objects={
            "房间": {},
            "账册": {
                "is_location": False,
                "location": "房间",
                "owner": None,
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={"甲": {"location": "房间"}},
    )
    actor = Entity("甲")
    actor.add_component(KnowledgeState())
    entities = {"甲": actor}
    registry = ClaimRegistry()
    registry.seed(
        [
            {
                "claim_id": "secret",
                "statement": "账册把乙与秘密交易联系起来。",
                "initial_truth": "unknown",
                "visibility": "secret",
                "subjects": [],
                "supporting_evidence": ["账册"],
            }
        ],
        scene_state=scene,
        world_entities=entities,
    )
    return scene, entities, registry


def test_active_evidence_observation_derives_private_discovery_without_truth():
    scene, _, registry = _world()
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "outcome": "success",
                "result": "甲检查了账册。",
                "private_result": "模型猜测应被替换",
            }
        ],
        "claim_discoveries": [
            {"actor": "甲", "claim_id": "forged", "evidence_ref": "账册"}
        ],
    }

    resolution = EvidenceObservationResolver().resolve(
        result,
        intents=[
            {
                "actor": "甲",
                "action_kind": "observe",
                "action_target": "账册",
            }
        ],
        scene_state=scene,
        claim_registry=registry,
    )

    action = resolution.result["resolved_actions"][0]
    assert action["action_kind"] == "observe"
    assert "支持命题" in action["private_result"]
    assert "truth" not in action["private_result"]
    assert resolution.result["claim_discoveries"] == [
        {
            "actor": "甲",
            "claim_id": "secret",
            "evidence_ref": "账册",
            "reason": "主动观察确认账册与该命题存在结构化证据关联",
        }
    ]


def test_failed_or_invisible_observation_cannot_discover_claim():
    scene, _, registry = _world()
    scene.get_object_state("账册")["hidden"] = True
    result = {
        "resolved_actions": [{"actor": "甲", "outcome": "success"}],
        "claim_discoveries": [],
    }

    resolution = EvidenceObservationResolver().resolve(
        result,
        intents=[
            {
                "actor": "甲",
                "action_kind": "observe",
                "action_target": "账册",
            }
        ],
        scene_state=scene,
        claim_registry=registry,
    )

    assert resolution.result["claim_discoveries"] == []
    assert "private_result" not in result["resolved_actions"][0]


def test_host_discovery_satisfies_existing_claim_knowledge_boundary():
    scene, entities, registry = _world()
    result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "action_kind": "observe",
                "action_target": "账册",
                "outcome": "success",
                "private_result": "账册支持命题。",
            }
        ],
        "claim_discoveries": [
            {
                "actor": "甲",
                "claim_id": "secret",
                "evidence_ref": "账册",
                "reason": "Host 证据关联",
            }
        ],
    }

    applied, errors = ClaimKnowledgeSystem()._apply(
        states={"甲": entities["甲"].get_component("KnowledgeState")},
        scene_state=scene,
        claim_registry=registry,
        relation_registry=None,
        actions=result["resolved_actions"],
        discoveries=result["claim_discoveries"],
        transfers=[],
        step=1,
    )

    assert errors == []
    assert applied[0]["stance"] == "supports"
