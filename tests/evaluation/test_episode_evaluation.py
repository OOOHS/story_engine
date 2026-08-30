from copy import deepcopy
from dataclasses import asdict
import json

from pydantic import Field

from src.story_engine.agents.actions import AgentAction
from src.story_engine.agents.types import AgentDecision
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.components.world_event import (
    WorldEventFact,
    WorldEventResponses,
    WorldEventWitnesses,
)
from src.story_engine.evaluation import EpisodeRunner, EpisodeStepTrace
from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig
from src.story_engine.session import create_session


class AlternatingRuntime:
    def decide(self, entity, perception):
        other = "乙" if entity.name == "甲" else "甲"
        if entity.name == "甲" and perception.step % 2 == 0:
            action = AgentAction(
                "communicate",
                f"向{other}认真说明自己的当前打算。",
                other,
            )
        elif entity.name == "甲":
            action = AgentAction(
                "interact",
                "整理桌面上的线索，尝试让接下来的交谈更具体。",
                "会客室",
            )
        elif perception.step % 2 == 0:
            action = AgentAction(
                "observe",
                f"观察{other}听完后的反应。",
                other,
            )
        else:
            action = AgentAction(
                "communicate",
                f"向{other}说明自己仍然保留的疑问。",
                other,
            )
        return AgentDecision(action=action.detail, action_spec=action)


class WaitingRuntime:
    def decide(self, entity, perception):
        action = AgentAction("wait", "暂时等待，不改变眼前局面。")
        return AgentDecision(action=action.detail, action_spec=action)


class ChoiceRuntime:
    def decide(self, entity, perception):
        del entity, perception
        observe = AgentAction("observe", "检查门边的痕迹。", "房门")
        communicate = AgentAction("communicate", "询问守门人刚才发生了什么。", "守门人")
        return AgentDecision(
            action=observe.detail,
            action_spec=observe,
            candidates=(observe, communicate),
        )


class SimulationControl(Component):
    """Generic deterministic semantic resolver used only by the episode audit."""

    scenario: object = None
    seen_batches: list = Field(default_factory=list)

    def simulate(self, payload):
        intents = [
            item for item in payload.get("intents", []) if isinstance(item, dict)
        ]
        self.seen_batches.append(deepcopy(intents))
        actions = []
        impacts = []
        for intent in intents:
            actor = str(intent.get("actor", ""))
            kind = str(intent.get("action_kind", "interact"))
            target = str(intent.get("action_target", ""))
            actions.append(
                {
                    "actor": actor,
                    "intent": intent.get("intent", ""),
                    "action_kind": kind,
                    "action_target": target,
                    "outcome": "success",
                    "location": intent.get("location"),
                    "visibility": "public",
                    "result": (
                        f"{actor}完成了一次主动观察。"
                        if kind == "observe"
                        else f"{actor}向{target}清楚表达了自己的打算。"
                    ),
                    "private_result": (
                        f"{actor}注意到{target}正在认真权衡。"
                        if kind == "observe"
                        else ""
                    ),
                }
            )
            if kind == "communicate" and target:
                impacts.append(
                    {
                        "source": actor,
                        "affected": target,
                        "kind": "admiring",
                        "magnitude": 0.2,
                        "reason": f"{actor}进行了清楚而坦率的沟通",
                        "source_event": f"talk:{payload.get('current_step', 0)}:{actor}",
                    }
                )
        return {
            "resolved_actions": actions,
            "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
            "relationship_updates": [],
            "social_impacts": impacts,
            "knowledge_updates": [],
            "object_lifecycle": [],
            "exchanges": [],
            "agreement_updates": [],
            "drive_updates": [],
            "obligation_updates": [],
            "tension_delta": 0,
        }



class NarrativeRenderer(Component):
    def render(self, payload):
        return "；".join(
            str(item.get("result", ""))
            for item in payload.get("simulation_result", {}).get(
                "resolved_actions", []
            )
            if isinstance(item, dict) and item.get("result")
        ) or "局面暂时平静。"


def _session(seed):
    scenario = ScenarioConfig(
        name="最小涌现种子",
        default_agent_runtime="llm",
        description="两个有独立目标的角色第一次交谈。",
        environment="一间没有预写剧情的会客室。",
        initial_state="甲与乙刚刚见面。",
        initial_world_objects={"会客室": {}},
        initial_actor_states={
            "甲": {"location": "会客室"},
            "乙": {"location": "会客室"},
        },
        characters=[
            CharacterConfig(
                name="甲",
                role="来访者",
                personality="坦率",
                goals=["了解乙的真实意图"],
                is_player=True,
                agent_runtime="alternating",
            ),
            CharacterConfig(
                name="乙",
                role="主人",
                personality="审慎",
                goals=["判断甲是否值得合作"],
                agent_runtime="alternating",
            ),
        ],
    )
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "alternating": lambda entity, config: AlternatingRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    control = SimulationControl(scenario=scenario)
    gm.add_component(control)
    gm.add_component(NarrativeRenderer())
    return session


def _idle_session(seed):
    scenario = ScenarioConfig(
        name="停滞种子",
        default_agent_runtime="llm",
        description="一个角色持续等待。",
        environment="一间空房。",
        initial_state="屋内没有正在发展的事件。",
        initial_world_objects={"空房": {}},
        initial_actor_states={"甲": {"location": "空房"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="等待者",
                personality="被动",
                goals=["等待"],
                is_player=True,
                agent_runtime="waiting",
            )
        ],
    )
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "waiting": lambda entity, config: WaitingRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(SimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session


def _choice_session(seed):
    scenario = ScenarioConfig(
        name="候选审计种子",
        default_agent_runtime="llm",
        description="角色可以调查或询问。",
        environment="一间有房门的大厅。",
        initial_state="守门人站在房门旁。",
        initial_world_objects={"大厅": {}, "房门": {}},
        initial_actor_states={"甲": {"location": "大厅"}},
        characters=[
            CharacterConfig(
                name="甲",
                role="调查者",
                personality="谨慎",
                goals=["弄清刚才发生的事"],
                is_player=True,
                agent_runtime="choice",
            )
        ],
    )
    session = create_session(
        scenario,
        random_seed=seed,
        agent_runtime_factories={
            "choice": lambda entity, config: ChoiceRuntime()
        },
    )
    gm = session.entities["GameMaster"]
    gm.add_component(SimulationControl(scenario=scenario))
    gm.add_component(NarrativeRenderer())
    return session


def test_multi_turn_episode_audit_proves_social_state_can_grow_from_small_seed():
    report = EpisodeRunner().run(_session("emergence-1"), steps=4)

    assert report.authoritative is True
    assert report.quality_flags == ()
    assert report.metrics["committed_steps"] == 4
    assert report.metrics["proposal_actor_count"] == 2
    assert report.metrics["resolved_actor_count"] == 2
    assert report.metrics["action_kind_count"] == 3
    assert report.metrics["world_change_steps"] >= 1
    assert report.metrics["character_change_steps"] >= 1
    assert report.metrics["relationship_count"] == 1
    assert report.metrics["sentiment_count"] == 2
    assert report.metrics["active_goal_count"] == 2
    assert report.metrics["goal_resolution_count"] == 0
    assert report.metrics["modifier_count"] == 0
    assert report.metrics["claim_count"] == 0
    assert report.metrics["known_claim_count"] == 0
    assert report.metrics["narrative_step_count"] == 4
    assert report.metrics["narrative_character_count"] > 0
    assert all(step.narrative_text for step in report.steps)
    assert all(
        "注意到" not in step.narrative_text
        and "认真权衡" not in step.narrative_text
        for step in report.steps
    )
    assert report.metrics["interaction_chain_steps"] >= 3
    assert report.metrics["longest_interaction_chain"] >= 4
    assert report.metrics["actor_differentiation"] >= 0.5


def test_episode_trace_is_replayable_for_same_seed_and_runtime():
    first = EpisodeRunner().run(_session("same-seed"), steps=4)
    second = EpisodeRunner().run(_session("same-seed"), steps=4)

    assert first.metrics == second.metrics
    assert first.quality_flags == second.quality_flags
    assert [step.action_kinds for step in first.steps] == [
        step.action_kinds for step in second.steps
    ]
    assert [step.world_hash_after for step in first.steps] == [
        step.world_hash_after for step in second.steps
    ]
def test_episode_report_can_be_written_as_launcher_friendly_json(tmp_path):
    report = EpisodeRunner().run(_session("report-seed"), steps=2)

    target = report.write_json(tmp_path / "summary.json")

    payload = target.read_text(encoding="utf-8")
    assert '"random_seed": "report-seed"' in payload
    assert '"authoritative"' not in payload  # derived property, not duplicated state
    assert '"step_count": 2' in payload


def test_internal_world_version_does_not_disguise_a_deadlocked_episode():
    report = EpisodeRunner().run(_idle_session("idle-seed"), steps=5)

    assert all(step.world_hash_before != step.world_hash_after for step in report.steps)
    assert report.metrics["world_change_steps"] == 0
    assert report.metrics["max_repeated_policy_action_count"] == 5
    assert report.metrics["max_narrative_repetition"] == 5
    assert "stagnant_episode" in report.quality_flags
    assert "deadlocked_episode" in report.quality_flags
    assert "repetitive_policy_choices" in report.quality_flags
    assert "repetitive_narration" in report.quality_flags


def test_narrative_repetition_flags_dominant_template_not_only_identical_story():
    normalized = [
        "局势仍然没有变化。",
        "局势仍然没有变化。",
        "中间出现了一次真正变化。",
        "局势仍然没有变化。",
        "局势仍然没有变化。",
    ]

    assert EpisodeRunner._max_normalized_repetition(normalized) == 4


def test_irreversible_audit_reads_authoritative_lifecycle_transitions():
    before = {
        "material_parts": {
            "scene": {
                "world_objects": {"钥匙": {"owner": "甲"}},
                "actor_states": {"甲": {}},
            },
            "agreements": {
                "agreements": {
                    "deal": {
                        "status": "settled",
                        "performance_status": "pending",
                    }
                }
            },
            "obligations": {
                "甲": {
                    "obligations": {
                        "deliver": {"status": "scheduled"}
                    }
                }
            },
            "goals": {
                "甲": {
                    "goals": {
                        "escape": {"status": "active"}
                    }
                }
            },
        }
    }
    after = deepcopy(before)
    after["material_parts"]["scene"]["world_objects"]["钥匙"]["owner"] = "乙"
    after["material_parts"]["agreements"]["agreements"]["deal"][
        "performance_status"
    ] = "fulfilled"
    after["material_parts"]["obligations"]["甲"]["obligations"]["deliver"][
        "status"
    ] = "fulfilled"
    after["material_parts"]["obligations"]["甲"]["obligations"]["follow_up"] = {
        "status": "scheduled",
        "source_kind": "agreement",
        "source_ref": "deal",
    }
    after["material_parts"]["goals"]["甲"]["goals"]["escape"][
        "status"
    ] = "achieved"
    after["material_parts"]["goals"]["甲"]["goals"]["follow_up"] = {
        "status": "active",
        "origin": "agent",
        "refined_step": 4,
    }

    changes = EpisodeRunner._irreversible_changes(before, after)

    assert "object_owner_changed:钥匙" in changes
    assert "agreement_performance_resolved:deal:fulfilled" in changes
    assert "obligation_resolved:甲:deliver:fulfilled" in changes
    assert "obligation_created:甲:follow_up" in changes
    assert "goal_resolved:甲:escape:achieved" in changes
    assert "goal_adopted:甲:follow_up" in changes
    assert "goal_refined:甲:follow_up:step:4" in changes








def test_modifier_and_drive_motives_retain_their_own_authoritative_causes():
    before = {
        "material_parts": {
            "modifiers": {"甲": {"modifiers": {}}},
            "drives": {"甲": {"need_provenance": {}}},
        }
    }
    after = deepcopy(before)
    after["material_parts"]["modifiers"]["甲"]["modifiers"]["shaken"] = {
        "provenance": {
            "source_kind": "resolved_action",
            "source_ref": "step:2:actor:乙",
        }
    }
    after["material_parts"]["drives"]["甲"]["need_provenance"]["hunger"] = [
        {
            "source_kind": "obligation",
            "source_ref": "deliver",
            "before": 0.4,
            "after": 0.6,
            "delta": 0.2,
        }
    ]

    changes = EpisodeRunner._irreversible_changes(before, after)
    handoffs = EpisodeRunner._causal_handoffs(before, after)

    assert changes == ["modifier_created:甲:shaken"]
    assert handoffs == [
        "drive_need:甲:hunger<-obligation:甲:deliver",
        "modifier:甲:shaken<-resolved_action:step:2:actor:乙",
    ]


def test_evidence_observation_can_ground_private_claim_knowledge_and_goal():
    before = {
        "material_parts": {
            "knowledge": {"甲": {"claims": {}}},
            "goals": {"甲": {"goals": {}}},
        }
    }
    after = deepcopy(before)
    after["material_parts"]["knowledge"]["甲"]["claims"]["hidden-deal"] = {
        "basis": "observed",
        "source": "evidence:账册",
        "learned_step": 3,
        "updated_step": 3,
        "evidence_refs": ["账册"],
    }
    after["material_parts"]["goals"]["甲"]["goals"]["investigate"] = {
        "origin": "agent",
        "source_kind": "claim",
        "source_ref": "hidden-deal",
    }

    changes = EpisodeRunner._irreversible_changes(before, after)
    handoffs = EpisodeRunner._causal_handoffs(before, after)

    assert "claim_knowledge_learned:甲:hidden-deal" in changes
    assert handoffs == [
        "claim_knowledge:甲:hidden-deal"
        "<-evidence_observation:甲:账册:step:3",
        "evidence_observation:甲:账册:step:3<-evidence:账册",
        "evidence_observation:甲:账册:step:3"
        "<-resolved_action:step:3:actor:甲",
        "goal:甲:investigate<-claim_knowledge:甲:hidden-deal",
    ]
    assert EpisodeRunner._causal_chain_depth(handoffs) == 3








def test_action_repetition_distinguishes_actor_kind_and_target():
    traces = [
        EpisodeStepTrace(
            index=index,
            simulation_time_before=index,
            simulation_time_after=index + 1,
            world_hash_before=f"w{index}",
            world_hash_after=f"w{index + 1}",
            character_hash_before=f"c{index}",
            character_hash_after=f"c{index + 1}",
            proposal_actors=("甲",),
            resolved_actors=("甲",),
            action_kinds=("interact",),
            actor_actions=(("甲", "interact", target),),
            committed=True,
            relationship_count=0,
            sentiment_count=0,
            agreement_count=0,
            modifier_count=0,
            claim_count=0,
            known_claim_count=0,
        )
        for index, target in enumerate(("门", "窗", "账本", "钥匙"))
    ]

    signatures = [
        EpisodeRunner._action_batch_signature(trace) for trace in traces
    ]

    assert EpisodeRunner._longest_repetition(signatures) == 1
    assert EpisodeRunner._longest_repetition([signatures[0]] * 4) == 4


def test_episode_narrative_capture_is_bounded_and_removes_null_bytes():
    text = "开场\x00" + ("很长的叙事" * 3000)

    bounded = EpisodeRunner._bounded_narrative(text)

    assert "\x00" not in bounded
    assert len(bounded) == 12_000
def test_world_event_entity_changes_authoritative_replay_hash_and_audit():
    session = _session("event-hash")
    auditor = EpisodeRunner()
    before = auditor._snapshot(session)
    event = Entity("WorldEvent:door_opened")
    event.add_component(
        WorldEventFact(
            event_id="door_opened",
            kind="object_set_container_state",
            title="门被打开",
            statement="会客室的门被打开了。",
            occurred_step=2,
            location="会客室",
            subjects=["甲"],
            objects=["会客室的门"],
        )
    )
    event.add_component(WorldEventWitnesses(direct_witnesses=["甲", "乙"]))
    responses = WorldEventResponses()
    event.add_component(responses)
    session.runner.entities[event.name] = event

    after = auditor._snapshot(session)
    changes = auditor._irreversible_changes(before, after)

    assert before["world_hash"] != after["world_hash"]
    assert after["world_event_count"] == 1
    assert "world_events" in auditor._material_change_kinds(before, after)
    assert "world_event_created:door_opened" in changes

    responses.record_communication("甲", "乙", 3)
    responded = auditor._snapshot(session)
    assert after["world_hash"] != responded["world_hash"]
    assert "world_events" in auditor._material_change_kinds(after, responded)


def test_episode_audit_rejects_scene_actor_without_live_runtime_and_event_errors():
    session = _session("actor-authority")
    actor = session.runner.entities["乙"]
    session.runner.agent_registry.unregister(actor)

    violations = EpisodeRunner()._audit_step(
        session,
        {
            "simulation_result": {},
            "world_event_errors": ["invalid event witness boundary"],
        },
    )

    assert "scene_actor_without_runtime:乙" in violations
    assert "world_event_error:invalid event witness boundary" in violations
