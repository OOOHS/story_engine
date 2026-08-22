import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.story_engine.agents import (
    AgentPerception,
    GumbelSubjectSampler,
    HermesCharacterAgent,
    IntentSignature,
    SubjectActionOption,
    SubjectInbox,
    SubjectLedgerProjector,
    SubjectMessage,
)
from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.commitment import commit_runtime_action
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.components.memory import Memory
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.memory import MemorySystem


def _options():
    return (
        SubjectActionOption(
            option_id="investigate",
            action=AgentAction("observe", "检查信封上的蜡印。", "信封"),
            signature=IntentSignature(
                motive_lens="查明真相",
                strategy="先收集证据",
                stakes=("knowledge",),
            ),
            utility=0.2,
        ),
        SubjectActionOption(
            option_id="confront",
            action=AgentAction("communicate", "要求阿德里安解释信封。", "阿德里安"),
            signature=IntentSignature(
                motive_lens="维护尊严",
                strategy="直接追问",
                stakes=("trust", "status"),
            ),
            utility=0.2,
        ),
    )


def test_subject_inbox_deduplicates_and_acknowledges_messages():
    inbox = SubjectInbox()
    message = SubjectMessage(
        message_id="evt-1",
        kind="stimulus",
        step=4,
        payload={"result": "有人打开了门。"},
        priority=80,
    )

    assert inbox.deliver(message) is True
    assert inbox.deliver(message) is False
    assert [item.message_id for item in inbox.pending()] == ["evt-1"]

    inbox.acknowledge(["evt-1"])

    assert inbox.pending() == ()
    assert inbox.deliver(message) is False
    assert inbox.snapshot()["acknowledged_count"] == 1


def test_subject_ledger_projects_only_host_verifiable_private_records_as_deltas():
    inbox = SubjectInbox()
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

    projector.project(inbox, perception)

    initial = inbox.pending(limit=100)
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

    inbox.acknowledge(item.message_id for item in initial)
    projector.project(inbox, perception)
    assert inbox.pending() == ()

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
    projector.project(inbox, changed)
    delta = inbox.pending(limit=100)
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


def test_gumbel_subject_sampling_is_replayable_with_a_configured_seed():
    left = GumbelSubjectSampler("actor-seed").choose(
        _options(), decision_id="turn-7", temperature=0.9
    )
    right = GumbelSubjectSampler("actor-seed").choose(
        _options(), decision_id="turn-7", temperature=0.9
    )

    assert left.selected.option_id == right.selected.option_id
    assert left.trace == right.trace
    assert left.trace["method"] == "hierarchical_gumbel"
    assert len(left.trace["motive_lenses"]) == 2


def test_gumbel_subject_sampling_preserves_cross_seed_action_diversity():
    selected = {
        GumbelSubjectSampler(seed).choose(
            _options(), decision_id="same-situation", temperature=1.0
        ).selected.option_id
        for seed in range(64)
    }

    assert selected == {"investigate", "confront"}


def test_subject_sampling_rejects_wording_only_or_single_lens_diversity():
    repeated = (
        SubjectActionOption(
            option_id="a",
            action=AgentAction("observe", "查看木门。", "木门"),
            signature=IntentSignature("调查", "仔细查看", ("knowledge",)),
        ),
        SubjectActionOption(
            option_id="b",
            action=AgentAction("observe", "更仔细地查看木门。", "木门"),
            signature=IntentSignature("调查", "仔细查看", ("knowledge",)),
        ),
    )

    with pytest.raises(ValueError, match="at least two motive lenses"):
        GumbelSubjectSampler(1).choose(repeated, decision_id="turn")


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


def test_hermes_subject_owns_sampling_and_host_receives_one_committed_action():
    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="骄傲而好奇",
        goals=["查明信封的去向"],
        agent_runtime="hermes",
        agent_config={"system_instruction_extras": "从不在室内奔跑。"},
    )
    conversation = _SubjectConversation(entity.id, [_deliberation()])
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation,
        config={"character_seed": "eve-seed", "character_temperature": 0.8},
    )
    perception = AgentPerception(
        actor_name="伊芙",
        step=4,
        activation_scope="foreground",
        world_view={"location": "会客厅", "visible_actors": ["阿德里安"]},
        self_state={"location": "会客厅"},
        passive_observations=[{
            "event_id": "evt-envelope",
            "observed_step": 4,
            "result": "你看见阿德里安把信封交给玛拉。",
            "priority": 80,
        }],
    )

    decision = runtime.decide(entity, perception)

    assert decision.action_spec is not None
    commitment = commit_runtime_action(decision)
    assert commitment.trace["mode"] == "runtime_committed"
    assert commitment.action == decision.action_spec
    packet = conversation.packets[0]
    assert packet["identity_bootstrap"]["name"] == "伊芙"
    assert packet["identity_bootstrap"]["persona_constraints"] == "从不在室内奔跑。"
    assert packet["agent_contract"]["assigned_character"] == "伊芙"
    assert "operate this character's next action" in packet["agent_contract"]["role"]
    assert "You are one character inside" not in json.dumps(packet)
    assert packet["messages"][0]["message_id"] == "evt-envelope"
    snapshot = runtime.subject_snapshot(entity)
    assert snapshot["inbox"]["pending"] == []
    assert snapshot["decision_ledger"][0]["method"] == "hierarchical_gumbel"


def test_hermes_subject_keeps_messages_pending_when_turn_fails():
    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    conversation = _SubjectConversation(entity.id, [RuntimeError("runtime failed")])
    runtime = HermesCharacterAgent(
        conversation_factory=lambda _entity, _config: conversation
    )
    perception = AgentPerception(
        actor_name="伊芙",
        step=4,
        passive_observations=[{
            "event_id": "evt-envelope",
            "observed_step": 4,
            "result": "你看见信封被交出。",
        }],
    )

    with pytest.raises(RuntimeError, match="runtime failed"):
        runtime.decide(entity, perception)

    snapshot = runtime.subject_snapshot(entity)
    assert snapshot["bootstrapped"] is False
    assert [item["message_id"] for item in snapshot["inbox"]["pending"]] == [
        "evt-envelope"
    ]


def test_hermes_subject_keeps_mind_private_but_can_register_a_goal_watch():
    entity = create_agent(
        name="伊芙",
        role="调查者",
        personality="谨慎",
        goals=[],
        agent_runtime="hermes",
    )
    response = {
        "action": {
            "kind": "observe",
            "detail": "检查信件上的印章。",
            "target": "信件",
        },
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
    assert conversation.packets[0]["wake"]["body"] == {
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
        "action": {
            "kind": "observe",
            "detail": "留意阿德里安的反应。",
            "target": "阿德里安",
        },
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
