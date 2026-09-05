import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.story_engine.agents import (
    AgentPerception,
    HermesCharacterAgent,
    SubjectLedgerProjector,
    SubjectMessage,
    build_subject_messages,
)
from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.commitment import commit_runtime_action
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.components.memory import Memory
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.memory import MemorySystem


def test_build_subject_messages_tags_urgency_from_cognition_records():
    perception = AgentPerception(
        actor_name="伊芙",
        step=6,
        private_cognition={
            "pending_world_event_records": [
                {
                    "event_id": "obligation:breach",
                    "statement": "关键交付已经违约。",
                    "confidence": 1.0,
                    "updated_step": 6,
                    "urgency": "critical",
                },
                {
                    "event_id": "movement:同事",
                    "statement": "同事走进了房间。",
                    "confidence": 1.0,
                    "updated_step": 5,
                    "urgency": "ambient",
                },
            ],
            "pending_event_response_records": [
                {
                    "response_id": "event-response:evt:甲->伊芙:apologize",
                    "event_id": "evt",
                    "source": "甲",
                    "response_kind": "apologize",
                    "statement": "甲向伊芙道歉。",
                    "step": 6,
                    "urgency": "direct",
                },
            ],
        },
    )

    messages = build_subject_messages(perception)

    by_ref = {item.source_ref: item for item in messages}
    assert by_ref["obligation:breach"].kind == "stimulus"
    assert by_ref["obligation:breach"].urgency == "critical"
    assert by_ref["movement:同事"].urgency == "ambient"
    assert (
        by_ref["event-response:evt:甲->伊芙:apologize"].kind
        == "active_observation_result"
    )
    assert by_ref["event-response:evt:甲->伊芙:apologize"].urgency == "direct"
    # urgency is Cognition-internal scheduling metadata, not something the
    # subject-facing payload should carry twice (it is already the top-level tag).
    assert "urgency" not in by_ref["obligation:breach"].payload


def test_build_subject_messages_is_idempotent_across_a_failed_retry():
    """No inbox is needed: the same still-pending Cognition records always
    reproduce the identical message set until the caller actually acks them."""

    perception = AgentPerception(
        actor_name="伊芙",
        step=6,
        private_cognition={
            "pending_world_event_records": [
                {
                    "event_id": "obligation:breach",
                    "statement": "关键交付已经违约。",
                    "confidence": 1.0,
                    "updated_step": 6,
                    "urgency": "critical",
                },
            ],
        },
    )

    first_attempt = build_subject_messages(perception)
    second_attempt = build_subject_messages(perception)
    assert [item.to_dict() for item in first_attempt] == [
        item.to_dict() for item in second_attempt
    ]


def test_subject_ledger_projects_only_host_verifiable_private_records_as_deltas():
    projector = SubjectLedgerProjector()
    perception = AgentPerception(
        actor_name="伊芙",
        step=3,
        private_cognition={
            "beliefs": [{
                "event_id": "evt-letter",
                "statement": "我亲眼看见信件被交出。",
                "confidence": 1.0,
                "source": "direct_world_event:evt-letter",
            }],
            "secrets": ["作者提供的旧私人线索"],
            "commitments": ["旧系统中的私人承诺"],
            "current_focus": "Host 不应替 Hermes 保存的关注点",
        },
        private_drives={
            "needs": {
                "truth": {"pressure": 0.6, "description": "想弄清真相"},
            }
        },
        private_goals={
            "active": [{
                "goal_id": "goal-letter",
                "title": "查明信件去向",
                "status": "active",
            }],
            "recent_history": [],
        },
        private_sentiments={
            "active": [{
                "sentiment_id": "host-feeling",
                "kind": "grateful",
            }]
        },
        relevant_memories=["Host 检索出的整段回忆不应投递"],
        current_plan="Host 不应镜像 Hermes 的计划",
    )

    initial = projector.project(perception)
    projector.commit()

    categories = {
        item.payload.get("category")
        for item in initial
        if item.kind == "ledger_update"
    }
    assert {
        "epistemic_record",
        "legacy_private_note",
        "legacy_commitment",
        "drive_signal",
        "goal_registration",
    }.issubset(categories)
    encoded = json.dumps([item.to_dict() for item in initial], ensure_ascii=False)
    assert "Host 不应替 Hermes 保存的关注点" not in encoded
    assert "Host 不应镜像 Hermes 的计划" not in encoded
    assert "Host 检索出的整段回忆不应投递" not in encoded
    assert "host-feeling" not in encoded

    assert projector.project(perception) == []

    changed = replace(
        perception,
        step=4,
        private_drives={
            "needs": {
                "truth": {"pressure": 0.8, "description": "想弄清真相"},
            }
        },
        private_goals={"active": [], "recent_history": []},
    )
    delta = projector.project(changed)
    assert any(
        item.kind == "ledger_update"
        and item.payload.get("category") == "drive_signal"
        and item.payload.get("revision") == 2
        for item in delta
    )
    assert any(
        item.kind == "ledger_retraction"
        and item.payload.get("category") == "goal_registration"
        and item.payload.get("ref") == "goal-letter"
        for item in delta
    )


def test_subject_ledger_retries_a_failed_turn_without_double_incrementing():
    """If a turn fails before commit(), the next project() call must recompute
    the identical messages (same revision, same message_id) rather than
    treating the never-committed digest as already delivered."""

    projector = SubjectLedgerProjector()
    perception = AgentPerception(
        actor_name="伊芙",
        step=3,
        private_goals={
            "active": [{"goal_id": "goal-letter", "title": "查明信件去向"}],
            "recent_history": [],
        },
    )

    first_attempt = projector.project(perception)
    # Simulate a failed subject turn: commit() is never called.
    second_attempt = projector.project(perception)

    assert [item.to_dict() for item in first_attempt] == [
        item.to_dict() for item in second_attempt
    ]
    assert all(item.payload.get("revision") == 1 for item in second_attempt)

    projector.commit()
    third_attempt = projector.project(perception)
    assert third_attempt == []


def test_subject_ledger_pov_snapshot_only_resends_on_bootstrap_location_change_or_dormancy():
    projector = SubjectLedgerProjector()

    def perception_at(step, location):
        return AgentPerception(
            actor_name="伊芙",
            step=step,
            self_state={"location": location, "health": "healthy"},
            world_view={"visible_actors": ["甲"]},
        )

    bootstrap_messages = projector.project(perception_at(0, "客厅"), bootstrap=True)
    pov = [
        item for item in bootstrap_messages
        if item.payload.get("category") == "pov_snapshot"
    ]
    assert len(pov) == 1
    # A POV snapshot is a Host-verifiable ledger record: its delivery timing
    # is structural (bootstrap/location-change/dormant-refresh), not
    # priority-driven, so it carries no urgency tag at all.
    assert pov[0].urgency is None
    assert pov[0].payload["reason"] == "bootstrap"
    assert pov[0].payload["record"]["self_body"]["location"] == "客厅"
    projector.commit()

    # Same location, only a couple of steps later: no snapshot resend needed.
    quiet_messages = projector.project(perception_at(2, "客厅"))
    assert not any(
        item.payload.get("category") == "pov_snapshot" for item in quiet_messages
    )

    # Location changed: forces a fresh snapshot even though it is not bootstrap.
    moved_messages = projector.project(perception_at(3, "厨房"))
    moved_pov = [
        item for item in moved_messages
        if item.payload.get("category") == "pov_snapshot"
    ]
    assert len(moved_pov) == 1
    assert moved_pov[0].urgency is None
    assert moved_pov[0].payload["reason"] == "location_changed"
    projector.commit()

    # Long dormancy without a location change also forces a fresh snapshot.
    dormant_messages = projector.project(
        perception_at(3 + SubjectLedgerProjector.POV_DORMANT_STEPS, "厨房")
    )
    dormant_pov = [
        item for item in dormant_messages
        if item.payload.get("category") == "pov_snapshot"
    ]
    assert len(dormant_pov) == 1
    assert dormant_pov[0].payload["reason"] == "dormant_refresh"








class _SubjectConversation:
    def __init__(self, agent_id, responses):
        self.agent_id = agent_id
        self.responses = list(responses)
        self.packets = []

    def run_subject_turn(self, packet):
        self.packets.append(packet)
        content = self.responses.pop(0)
        if isinstance(content, Exception):
            raise content
        return {
            "protocol_version": 1,
            "agent_id": self.agent_id,
            "content": json.dumps(content, ensure_ascii=False),
        }


def _deliberation():
    return {
        "candidates": [
            {
                "option_id": "investigate",
                "utility": 0.1,
                "motive_lens": "查明真相",
                "intent_signature": {
                    "strategy": "先检查证据",
                    "stakes": ["knowledge"],
                },
                "action": {
                    "kind": "observe",
                    "detail": "检查信封上的蜡印。",
                    "target": "信封",
                },
            },
            {
                "option_id": "confront",
                "utility": 0.1,
                "motive_lens": "维护尊严",
                "intent_signature": {
                    "strategy": "直接追问",
                    "stakes": ["trust", "status"],
                },
                "action": {
                    "kind": "communicate",
                    "detail": "要求阿德里安解释信封。",
                    "target": "阿德里安",
                },
            },
        ]
    }




def test_hermes_subject_keeps_messages_pending_when_turn_fails():
    """No inbox exists anymore: the caller (InputSystem) only acks Cognition's
    pending_world_events/pending_event_responses after decide() returns
    without raising, so a perception built from still-pending Cognition
    records is, by construction, resent verbatim on the next retry."""

    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    retry_response = {"action": "检查信封上的蜡印。"}
    conversation = _SubjectConversation(
        entity.id, [RuntimeError("runtime failed"), retry_response]
    )
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation
    )
    perception = AgentPerception(
        actor_name="伊芙",
        step=4,
        private_cognition={
            "pending_world_event_records": [{
                "event_id": "evt-envelope",
                "statement": "你看见信封被交出。",
                "confidence": 1.0,
                "updated_step": 4,
                "urgency": "critical",
            }],
        },
    )

    with pytest.raises(RuntimeError, match="runtime failed"):
        runtime.decide(entity, perception)

    snapshot = runtime.subject_snapshot(entity)
    assert snapshot["bootstrapped"] is False

    # The Cognition-owned pending record is untouched by the failed attempt,
    # so calling decide() again with the same perception must resend the
    # identical stimulus message rather than treating it as already sent.
    runtime.decide(entity, perception)
    stimulus_messages = [
        item
        for item in conversation.packets[-1]["critical_signals"]
        if item["kind"] == "stimulus"
    ]
    assert [item["message_id"] for item in stimulus_messages] == [
        "stimulus:evt-envelope"
    ]
    assert stimulus_messages[0]["urgency"] == "critical"


def test_hermes_subject_keeps_mind_private_but_can_register_a_goal_watch():
    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    response = {
        "action": "检查信件上的印章。",
        "plan": "不应写回 Host 的私人计划",
        "focus": "不应写回 Host 的私人关注点",
        "belief_updates": [{"statement": "不应写回 Host 的推断"}],
        "commitments": ["不应写回 Host 的私人承诺"],
        "goal_requests": [{
            "operation": "adopt",
            "title": "查明信件来源",
            "source_kind": "world_event",
            "source_ref": "evt-letter",
        }],
    }
    conversation = _SubjectConversation(entity.id, [response])
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation,
        config={"character_seed": "eve"},
    )
    perception = AgentPerception(
        actor_name="伊芙",
        step=5,
        self_state={
            "location": "会客厅",
            "capabilities": ["观察"],
            "fear": 0.9,
            "mood": "Host 不应替 Hermes 声明心情",
        },
        private_cognition={
            "beliefs": [{
                "event_id": "evt-letter",
                "statement": "信件被交给了阿德里安。",
            }],
            "current_focus": "旧 Host focus 不应进入 Hermes packet",
        },
        private_sentiments={
            "active": [{"sentiment_id": "host-emotion-marker"}],
        },
        relevant_memories=["host-memory-marker"],
        current_plan="host-plan-marker",
    )

    decision = runtime.decide(entity, perception)

    assert decision.metadata == {
        "subject_runtime": True,
        "goal_requests": [response["goal_requests"][0]],
    }
    packet_text = json.dumps(conversation.packets[0], ensure_ascii=False)
    assert "evt-letter" in packet_text
    assert "old Host focus" not in packet_text
    assert "旧 Host focus" not in packet_text
    assert "host-emotion-marker" not in packet_text
    assert "host-memory-marker" not in packet_text
    assert "host-plan-marker" not in packet_text
    assert "body" not in conversation.packets[0]["wake"]
    assert "visible_world" not in conversation.packets[0]["wake"]
    pov_messages = [
        item
        for item in conversation.packets[0]["messages"]
        if item["payload"].get("category") == "pov_snapshot"
    ]
    assert len(pov_messages) == 1
    assert "urgency" not in pov_messages[0]
    assert pov_messages[0]["payload"]["record"]["self_body"] == {
        "location": "会客厅",
        "capabilities": ["观察"],
    }
    assert "Host 不应替 Hermes 声明心情" not in packet_text
    assert "subject_mind" in conversation.packets[0]["ownership_contract"]


def test_hermes_subject_can_report_her_own_sentiment_toward_someone():
    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    response = {
        "action": "留意阿德里安的反应。",
        "sentiment_updates": [
            {
                "toward": "阿德里安",
                "kind": "betrayed",
                "magnitude": 0.7,
                "reason": "他把信件交给了敌对势力",
            }
        ],
    }
    conversation = _SubjectConversation(entity.id, [response])
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation,
        config={"character_seed": "eve"},
    )
    perception = AgentPerception(actor_name="伊芙", step=5)

    decision = runtime.decide(entity, perception)

    assert decision.metadata == {
        "subject_runtime": True,
        "sentiment_updates": [response["sentiment_updates"][0]],
    }

    planning = entity.get_component("Planning")
    cognition = entity.get_component("Cognition")
    planning.set_plan("Hermes native memory owns this")
    cognition.current_focus = "Hermes native focus"
    InputSystem()._apply_agent_private_updates(
        entity,
        {
            "subject_runtime": True,
            "plan": "Host overwrite",
            "focus": "Host overwrite",
            "belief_updates": [{"statement": "Host overwrite"}],
            "commitments": ["Host overwrite"],
        },
        current_step=5,
    )
    assert planning.get_plan() == "Hermes native memory owns this"
    assert cognition.current_focus == "Hermes native focus"
    assert not cognition.knows("Host overwrite")


def test_subject_owned_runtime_skips_host_memory_retrieval_and_archival(monkeypatch):
    hermes = create_agent(
        name="Hermes角色",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    llm = create_agent(
        name="兼容角色",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="llm",
    )
    input_system = InputSystem()

    def fail_if_retrieval_queries_are_built(**_kwargs):
        raise AssertionError("Hermes must not use Host memory retrieval")

    input_system.memory_context.build_queries = fail_if_retrieval_queries_are_built
    perception = input_system.build_agent_perception(
        hermes,
        scene_state=None,
        intents_buffer=[],
        context={"clock": SimpleNamespace(current_step=1)},
    )
    assert perception.relevant_memories == []

    archived = []

    def record_memory(self, content, metadata=None, memory_id=None):
        archived.append((self.agent_name, content, metadata, memory_id))

    monkeypatch.setattr(Memory, "add_memory", record_memory)
    memory_system = MemorySystem()
    memory_system.consolidator.maybe_consolidate = lambda *_args, **_kwargs: {
        "status": "skipped"
    }
    memory_system.update(
        {hermes.name: hermes, llm.name: llm},
        {
            "clock": SimpleNamespace(current_step=2),
            "intents": [
                {"actor": hermes.name, "intent": "检查信件"},
                {"actor": llm.name, "intent": "检查窗户"},
            ],
            "simulation_result": {
                "resolved_actions": [
                    {
                        "actor": hermes.name,
                        "outcome": "success",
                        "result": "看见了印章。",
                    },
                    {
                        "actor": llm.name,
                        "outcome": "success",
                        "result": "看见街道无人。",
                    },
                ]
            },
        },
    )
    assert [item[0] for item in archived] == [llm.name]
