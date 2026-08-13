from types import SimpleNamespace

from src.story_engine.agents.policy import CharacterPolicy
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.knowledge import ClaimRegistry
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session
from src.story_engine.systems.claim_knowledge import ClaimKnowledgeSystem
from src.story_engine.systems.claims import ClaimSystem
from src.story_engine.systems.rendering import RenderingSystem


def _claim_config(**overrides):
    data = {
        "claim_id": "ledger_owner",
        "statement": "乙秘密控制着这本账册。",
        "initial_truth": "true",
        "visibility": "secret",
        "subjects": ["乙"],
        "supporting_evidence": ["账册"],
    }
    data.update(overrides)
    return data


def _world():
    scene = SceneState(
        world_objects={
            "房间": {},
            "账册": {
                "is_location": False,
                "owner": "甲",
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
    )
    gm = Entity("GameMaster")
    gm.add_component(scene)
    gm.add_component(PlotState())
    first = Entity("甲")
    first.add_component(KnowledgeState())
    second = Entity("乙")
    second.add_component(KnowledgeState())
    entities = {"GameMaster": gm, "甲": first, "乙": second}
    registry = ClaimRegistry()
    registry.seed([_claim_config()], scene_state=scene, world_entities=entities)
    return scene, entities, registry


def _observe_action(actor="甲"):
    return {
        "actor": actor,
        "action_kind": "observe",
        "action_target": "账册",
        "outcome": "success",
        "location": "房间",
        "visibility": "local",
        "result": "甲检查了账册。",
        "private_result": "账册上的签押把它与乙联系起来。",
    }


def _communicate_action(actor="甲", target="乙"):
    return {
        "actor": actor,
        "action_kind": "communicate",
        "action_target": target,
        "outcome": "success",
        "location": "房间",
        "visibility": "local",
        "result": f"{actor}向{target}谈到账册。",
        "private_result": "",
    }


def test_claim_truth_tracks_authoritative_conditions_without_updating_beliefs():
    scene, entities, registry = _world()
    dynamic_registry = ClaimRegistry()
    dynamic_registry.seed(
        [
            _claim_config(
                claim_id="door_open",
                statement="房门是打开的。",
                initial_truth="unknown",
                subjects=["房间"],
                supporting_evidence=[],
                truth_conditions=[
                    {
                        "scope": "scene",
                        "path": "scene_flags.door_open",
                        "operator": "eq",
                        "value": True,
                    }
                ],
                false_conditions=[
                    {
                        "scope": "scene",
                        "path": "scene_flags.door_open",
                        "operator": "eq",
                        "value": False,
                    }
                ],
            )
        ],
        scene_state=scene,
        world_entities=entities,
    )
    context = {
        "clock": SimpleNamespace(current_step=2),
        "claim_registry": dynamic_registry,
    }

    scene.update_scene_flags({"door_open": True})
    ClaimSystem().update(entities, context)

    fact = dynamic_registry.get("door_open").get_component("ClaimFact")
    assert fact.truth_status == "true"
    assert context["claim_transitions"][0]["after"] == "true"

    scene.update_scene_flags({"door_open": False})
    context["clock"].current_step = 3
    ClaimSystem().update(entities, context)

    assert fact.truth_status == "false"
    assert entities["甲"].get_component("KnowledgeState").claims == {}


def test_private_snapshot_never_exposes_claim_truth_and_derives_evidence_leverage():
    scene, entities, registry = _world()
    knowledge = entities["甲"].get_component("KnowledgeState")
    knowledge.learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=1,
        evidence_refs=["账册"],
    )

    snapshot = registry.private_snapshot(
        actor="甲",
        knowledge_state=knowledge,
        scene_state=scene,
    )

    assert snapshot["claims"][0]["statement"] == "乙秘密控制着这本账册。"
    assert "truth_status" not in snapshot["claims"][0]
    assert snapshot["potential_leverage"][0]["targets"] == ["乙"]
    assert snapshot["potential_leverage"][0]["evidence_backed"] is True


def test_repeated_identical_claim_learning_is_idempotent():
    knowledge = KnowledgeState()
    first = knowledge.learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=1,
        evidence_refs=["账册"],
    )

    repeated = knowledge.learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=9,
        evidence_refs=["账册"],
    )

    assert repeated is first
    assert repeated.updated_step == 1


def test_active_observation_discovers_only_linked_visible_evidence():
    scene, entities, registry = _world()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "simulation_result": {
            "resolved_actions": [_observe_action()],
            "claim_discoveries": [
                {
                    "actor": "甲",
                    "claim_id": "ledger_owner",
                    "evidence_ref": "账册",
                    "reason": "签押与记录互相印证",
                }
            ],
            "knowledge_updates": [],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_errors"] == []
    record = entities["甲"].get_component("KnowledgeState").claims["ledger_owner"]
    assert record.stance == "supports"
    assert record.basis == "observed"
    assert record.evidence_refs == ["账册"]


def test_gm_cannot_put_truth_or_confidence_into_discovery():
    scene, entities, registry = _world()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "simulation_result": {
            "resolved_actions": [_observe_action()],
            "claim_discoveries": [
                {
                    "actor": "甲",
                    "claim_id": "ledger_owner",
                    "evidence_ref": "账册",
                    "reason": "发现签押",
                    "truth_status": "true",
                    "confidence": 1.0,
                }
            ],
            "knowledge_updates": [],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_updates"] == []
    assert "host-owned fields" in context["claim_knowledge_errors"][0]
    assert entities["甲"].get_component("KnowledgeState").claims == {}


def test_informed_character_can_lie_but_cannot_invent_an_unknown_claim():
    scene, entities, registry = _world()
    source = entities["甲"].get_component("KnowledgeState")
    source.learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=0,
        evidence_refs=["账册"],
    )
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "simulation_result": {
            "resolved_actions": [_communicate_action()],
            "claim_discoveries": [],
            "knowledge_updates": [
                {
                    "source": "甲",
                    "target": "乙",
                    "claim_id": "ledger_owner",
                    "asserted_stance": "rejects",
                    "cited_evidence": [],
                    "reason": "甲故意否认账册与乙有关",
                }
            ],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_errors"] == []
    received = entities["乙"].get_component("KnowledgeState").claims["ledger_owner"]
    assert received.stance == "rejects"
    assert received.source == "甲"
    assert received.confidence == 0.6

    context["simulation_result"]["knowledge_updates"][0]["claim_id"] = "invented"
    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_updates"] == []
    assert any("unknown claim" in item for item in context["claim_knowledge_errors"])


def test_claim_report_uses_shared_origin_window_when_listener_moves():
    scene, entities, registry = _world()
    scene.world_objects["走廊"] = {}
    scene.update_actor_state("乙", {"location": "走廊"})
    entities["甲"].get_component("KnowledgeState").learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=0,
    )
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "actor_observation_windows": {
            "甲": {"locations": ["房间"]},
            "乙": {"locations": ["房间", "走廊"]},
        },
        "simulation_result": {
            "resolved_actions": [_communicate_action()],
            "claim_discoveries": [],
            "knowledge_updates": [
                {
                    "source": "甲",
                    "target": "乙",
                    "claim_id": "ledger_owner",
                    "asserted_stance": "supports",
                    "cited_evidence": [],
                    "reason": "乙离开房间时听到了甲的报告",
                }
            ],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_errors"] == []
    assert entities["乙"].get_component("KnowledgeState").claims[
        "ledger_owner"
    ].source == "甲"


def test_gm_cannot_choose_recipient_confidence_or_claim_truth_on_transfer():
    scene, entities, registry = _world()
    entities["甲"].get_component("KnowledgeState").learn(
        claim_id="ledger_owner",
        stance="supports",
        confidence=0.9,
        basis="observed",
        source="evidence:账册",
        step=0,
    )
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "simulation_result": {
            "resolved_actions": [_communicate_action()],
            "claim_discoveries": [],
            "knowledge_updates": [
                {
                    "source": "甲",
                    "target": "乙",
                    "claim_id": "ledger_owner",
                    "reason": "甲谈到账册",
                    "confidence": 1.0,
                    "truth_status": "true",
                }
            ],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_updates"] == []
    assert "host-owned fields" in context["claim_knowledge_errors"][0]
    assert entities["乙"].get_component("KnowledgeState").claims == {}


def test_invalid_claim_batch_does_not_publish_valid_discovery():
    scene, entities, registry = _world()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "claim_registry": registry,
        "simulation_result": {
            "resolved_actions": [_observe_action()],
            "claim_discoveries": [
                {
                    "actor": "甲",
                    "claim_id": "ledger_owner",
                    "evidence_ref": "账册",
                    "reason": "有效发现",
                },
                {
                    "actor": "乙",
                    "claim_id": "missing",
                    "evidence_ref": "账册",
                    "reason": "无效发现",
                },
            ],
            "knowledge_updates": [],
        },
    }

    ClaimKnowledgeSystem().update(entities, context)

    assert context["claim_knowledge_updates"] == []
    assert entities["甲"].get_component("KnowledgeState").claims == {}


def test_public_claims_are_shared_but_secret_claims_remain_selective():
    scenario = ScenarioConfig(
        name="Claim 种子",
        description="验证公开与秘密命题。",
        environment="房间",
        initial_state="两人同处一室。",
        initial_world_objects={
            "房间": {},
            "公告": {"is_location": False, "location": "房间"},
            "账册": {"is_location": False, "owner": "甲"},
        },
        initial_actor_states={
            "甲": {"location": "房间"},
            "乙": {"location": "房间"},
        },
        claims=[
            {
                "claim_id": "meeting_today",
                "statement": "会议将在今天举行。",
                "visibility": "public",
                "subjects": ["房间"],
                "supporting_evidence": ["公告"],
            },
            _claim_config(),
        ],
        characters=[
            CharacterConfig(
                name="甲",
                role="调查者",
                personality="谨慎",
                goals=[],
                initial_claim_knowledge=[
                    {
                        "claim_id": "ledger_owner",
                        "stance": "supports",
                        "confidence": 0.9,
                        "basis": "observed",
                        "evidence_refs": ["账册"],
                    }
                ],
                is_player=True,
                agent_runtime="test",
            ),
            CharacterConfig(
                name="乙",
                role="商人",
                personality="沉稳",
                goals=[],
                agent_runtime="test",
            ),
        ],
    )
    session = create_session(
        scenario,
        agent_runtime_factories={"test": lambda entity, config: object()},
    )

    first = session.entities["甲"].get_component("KnowledgeState")
    second = session.entities["乙"].get_component("KnowledgeState")
    assert set(first.claims) == {"meeting_today", "ledger_owner"}
    assert set(second.claims) == {"meeting_today"}
    assert not session.runner.agent_registry.is_registered(
        session.entities["Claim:ledger_owner"]
    )


def test_leverage_becomes_a_generic_host_candidate_without_truth_leakage():
    perception = AgentPerception(
        actor_name="甲",
        step=1,
        world_view={"visible_actors": ["甲", "乙"], "visible_world": {}},
        private_knowledge={
            "claims": [
                {
                    "claim_id": "ledger_owner",
                    "statement": "乙秘密控制着这本账册。",
                }
            ],
            "potential_leverage": [
                {
                    "claim_id": "ledger_owner",
                    "targets": ["乙"],
                    "confidence": 0.9,
                    "evidence_backed": True,
                }
            ],
        },
    )

    candidates = CharacterPolicy()._environment_candidates(perception)
    leverage = next(
        item for item in candidates
        if item.candidate_id.startswith("environment:leverage:")
    )

    assert leverage.action.target == "乙"
    assert leverage.metadata["claim_id"] == "ledger_owner"
    assert CharacterPolicy._knowledge_score(perception, leverage) == 0.66
    assert "truth_status" not in leverage.metadata


def test_claim_updates_are_removed_from_public_render_payload():
    visible = RenderingSystem()._build_visible_simulation(
        {
            "resolved_actions": [],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
            },
            "knowledge_updates": [{"claim_id": "secret"}],
            "claim_discoveries": [{"claim_id": "secret"}],
        },
        {"location": "房间", "visible_world": {}},
    )

    assert visible["knowledge_updates"] == []
    assert visible["claim_discoveries"] == []
