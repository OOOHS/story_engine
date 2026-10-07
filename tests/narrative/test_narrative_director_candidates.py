from tests.semantic_support import narration_check_reply
import json
from copy import deepcopy

from src.story_engine.components.story_planner import StoryPlanner
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.environment.narrative_candidates import PENDING_STORY_PROPOSALS_FLAG
from src.story_engine.environment.runner import Runner
from src.story_engine.environment.step_checkpoint import RunnerStepCheckpoint
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.story_planning import StoryPlanningSystem


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.histories = []

    def generate_messages(self, messages, **kwargs):
        self.histories.append(deepcopy(messages))
        return next(self.responses)


def proposal_call(**overrides):
    storylet = {"storylet_id": "letter", "intent": "一封来信带来新的调查线索", **overrides}
    return {"id": "call-1", "type": "function", "function": {
        "name": "propose_storylet", "arguments": json.dumps({"reason": "回应玩家调查", "storylet": storylet}),
    }}


def host(director):
    entity = Entity("WorldHost")
    entity.add_component(SceneState(world_objects={"大厅": {}}, actor_states={"甲": {"location": "大厅"}}))
    entity.add_component(director)
    return entity


def delivered(turn_id, player="查找信件", narration="你找到一枚邮戳。"):
    return {"step_committed": True, "narrative_turn_id": turn_id, "player_name": "甲",
            "overrides": {"甲": player}, "rendered_text": narration}


def test_director_accumulates_player_narrative_and_reuses_its_conversation():
    director = StoryPlanner(interval_turns=2)
    director.record_opening("你站在大厅。")
    model = Model([{"content": "暂时保留玩家探索空间。"}, {"content": "继续观察。"}])
    director._llm = model
    entity = host(director)
    system = StoryPlanningSystem()
    system.update({"WorldHost": entity}, delivered("1"))
    assert model.histories == []
    system.update({"WorldHost": entity}, delivered("2", "询问邮差", "邮差指向城门。"))
    assert len(model.histories) == 1
    prompt = model.histories[0][0]['content']
    assert '内容、前置条件和执行后世界影响' in prompt
    assert '提出面向未来的剧情方向' in prompt
    assert '角色言论保持说话人的来源' in prompt
    first_request = json.loads(model.histories[0][-1]["content"])
    assert [item["content"] for item in first_request["new_player_narrative_messages"]] == [
        "你站在大厅。", "查找信件", "你找到一枚邮戳。", "询问邮差", "邮差指向城门。",
    ]
    system.update({"WorldHost": entity}, delivered("3"))
    system.update({"WorldHost": entity}, delivered("4"))
    assert model.histories[1][1:3] == director.conversation[1:3]
    assert model.histories[1][-2]["content"] == "暂时保留玩家探索空间。"
    assert len(director.narrative_messages) == 9


def test_director_registers_future_direction_without_a_semantic_reviewer():
    director = StoryPlanner(interval_turns=1)
    model = Model([{"content": "", "tool_calls": [proposal_call()]}, {"content": "草案已提出。"}])
    director._llm = model
    entity = host(director)
    context = delivered("1")
    StoryPlanningSystem().update({"WorldHost": entity}, context)
    scene = entity.get_component("SceneState")
    pending = scene.get_scene_flag(PENDING_STORY_PROPOSALS_FLAG)
    assert pending[0]["payload"]["storylet_id"] == "letter"
    assert pending[0]["review_status"] == "structural"
    assert pending[0]["status"] == "accepted"
    assert scene.get_scene_flag("dynamic_storylets")[0]["storylet_id"] == "letter"
    assert scene.get_scene_flag("pending_story_planner_authorizations", []) == []
    assert model.histories[1][-1]["role"] == "tool"
    # An old checkpoint's automatic authorizations also stay inert.
    scene.update_scene_flags({"pending_story_planner_authorizations": [{
        "kind": "storylet_definition", "authorization_id": "old-director",
        "storylet_id": "legacy", "intent": "旧提案", "not_before_step": 0,
    }]})
    input_context = {"intents": [], "inject_events": []}
    InputSystem().update({"WorldHost": entity}, input_context)
    assert input_context["storylet_definition_authorizations"] == []


def test_director_ignores_failed_and_undelivered_steps_and_deduplicates_retries():
    director = StoryPlanner(interval_turns=1)
    director._llm = Model([{"content": "继续观察。"}])
    entity = host(director)
    system = StoryPlanningSystem()
    context = delivered("1")
    context["step_committed"] = False
    system.update({"WorldHost": entity}, context)
    assert director.narrative_messages == []
    system.update({"WorldHost": entity}, {"step_committed": True})
    assert director.narrative_messages == []
    context["step_committed"] = True
    system.update({"WorldHost": entity}, context)
    system.update({"WorldHost": entity}, context)
    assert len(director.narrative_messages) == 2
    assert len(director._llm.histories) == 1


def test_director_conversation_and_proposal_records_restore_with_checkpoint():
    director = StoryPlanner(interval_turns=1)
    director._llm = Model([{"tool_calls": [proposal_call()]}, {"content": "完成。"}])
    entity = host(director)
    runner = Runner()
    runner.add_entity(entity)
    checkpoint = RunnerStepCheckpoint.capture(runner)
    StoryPlanningSystem().update(runner.entities, delivered("1"))
    assert director.conversation
    checkpoint.restore(runner)
    assert director.conversation == []
    assert director.narrative_messages == []
    assert entity.get_component("SceneState").get_scene_flag(PENDING_STORY_PROPOSALS_FLAG, []) == []


def test_director_model_failure_retains_narrative_and_does_not_issue_a_proposal():
    director = StoryPlanner(interval_turns=1)
    director._llm = Model([{"content": "[LLM error] unavailable"}])
    entity = host(director)
    context = delivered("1")
    StoryPlanningSystem().update({"WorldHost": entity}, context)
    assert context["story_planner_status"] == {"status": "failed", "error_type": "model_unavailable"}
    assert len(director.narrative_messages) == 2
    assert director.conversation == []
    assert director.fed_message_count == 0


def test_director_time_interval_is_checked_only_when_new_narrative_arrives():
    director = StoryPlanner(interval_turns=99, interval_seconds=60)
    director.record_turn("1", "调查", "看见信件")
    director.last_inquiry_time = 100
    assert not director.inquiry_due(now=159)
    assert director.inquiry_due(now=160)
    director.last_inquiry_turn = 1
    assert not director.inquiry_due(now=200)


def test_director_catalog_and_pending_drafts_are_distinct_from_player_story():
    director = StoryPlanner()
    director.record_turn("1", "调查", "看见信件")
    model = Model([{"content": "已有相同草案，继续等待。"}])
    director._llm = model
    director.propose({"existing_storylets": [{"storylet_id": "existing"}], "pending_proposals": [{"proposal_id": "p"}]})
    packet = json.loads(model.histories[0][-1]["content"])
    assert packet["existing_storylets"] == [{"storylet_id": "existing"}]
    assert packet["pending_proposals"] == [{"proposal_id": "p"}]
    assert "committed_facts" not in packet
    assert "storylet_opportunities" not in packet


def test_renderer_retry_records_director_story_once_and_memory_retry_preserves_it(monkeypatch):
    from src.story_engine.components.narrative_renderer import NarrativeRenderer
    from src.story_engine.session import create_session_from_seed
    from src.story_engine.systems.memory import MemorySystem

    session = create_session_from_seed({
        "locations": ["大厅"], "characters": [{"name": "甲", "location": "大厅", "is_player": True}],
        "initial_state": "甲在大厅。",
    }, profile="offline")
    entity = session.entities["WorldHost"]
    renderer = NarrativeRenderer()

    class Narrator:
        def __init__(self):
            self.results = iter(["[LLM error] down", "大厅里很安静。"])

        def generate(self, prompt):
            if reply := narration_check_reply(prompt):
                return reply
            return {"content": next(self.results)}

    renderer._llm = Narrator()
    entity.add_component(renderer)
    director = StoryPlanner(interval_turns=1)
    director._llm = Model([{"content": "暂时继续。"}])
    entity.add_component(director)
    attempts = []
    real_memory_update = MemorySystem.update

    def fail_once(self, entities, context):
        if not attempts:
            attempts.append(1)
            raise RuntimeError("memory delivery unavailable")
        return real_memory_update(self, entities, context)

    monkeypatch.setattr(MemorySystem, "update", fail_once)
    try:
        failed = session.run_step(overrides={"甲": "等待。"})
        assert failed["step_committed"] and failed["delivery_pending"]
        assert director.narrative_messages == []
        retried = session.retry_delivery()
        assert retried["delivery_pending"]
        assert len(director.narrated_turn_ids) == 1
        assert director.narrative_messages[0]["content"] == "等待。"
        assert director.narrative_messages[1]["content"] == retried["rendered_text"]
        completed = session.retry_delivery()
        assert not completed["delivery_pending"]
        assert len(director.narrated_turn_ids) == 1
        assert len(director._llm.histories) == 1
    finally:
        session.close()


def test_director_tool_reports_full_review_queue_without_claiming_acceptance():
    from src.story_engine.environment.narrative_candidates import STORY_PROPOSAL_CAP
    director = StoryPlanner()
    director.record_turn("1", "调查", "新线索")
    model = Model([{"tool_calls": [proposal_call()]}, {"content": "等待审核。"}])
    director._llm = model
    result = director.propose({"pending_proposals": [{"status": "pending_review"} for _ in range(STORY_PROPOSAL_CAP)]})
    assert result["narrative_candidates"] == []
    assert json.loads(model.histories[1][-1]["content"]) == {"error": "pending_proposal_cap"}


def test_tool_round_budget_retains_acknowledged_draft_and_conversation():
    director = StoryPlanner()
    unknown = lambda identifier: {"id": identifier, "type": "function",
                                   "function": {"name": "unknown_tool", "arguments": "{}"}}
    director._llm = Model([{"tool_calls": [proposal_call()]},
                           {"tool_calls": [unknown("2")]}, {"tool_calls": [unknown("3")]}])
    director.record_turn("1", "调查", "看见信件")
    result = director.propose({})
    assert result["narrative_candidates"][0]["payload"]["storylet_id"] == "letter"
    assert result["story_planner_note"] == "tool_round_limit"
    assert director.conversation[-1]["role"] == "tool"
    assert director.fed_message_count == 2
