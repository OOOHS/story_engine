"""Model semantics govern actions, narration and live director definitions."""
import json
from copy import deepcopy

import pytest

from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.components.story_planner import StoryPlanner
from src.story_engine.core.entity import Entity
from src.story_engine.session import create_session_from_seed, load_session
from src.story_engine.systems.story_planning import StoryPlanningSystem


class ReplyModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        reply = next(self.replies)
        return {"content": json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply}


@pytest.mark.parametrize('intent,kind,target', [
    ('我告诉甲：乙昨天进入密室，里面有人会瞬移。', 'communicate', '甲'),
    ('我不去码头，先问甲如何走。', 'communicate', '甲'),
    ('我假装飞起来，向甲展示骗局。', 'interact', '甲'),
    ('脚下落稳后沿着岸线抵达码头。', 'move', '码头'),
])
def test_action_uses_model_semantics_and_preserves_original_choice(intent, kind, target):
    control = SimulationControl()
    control._llm = ReplyModel([{'kind': kind, 'target': target}])
    perception = AgentPerception(actor_name='乙', step=0,
                                 world_view={'visible_actors': ['甲', '乙'], 'location': '河岸'})
    action = control.interpret_action(intent, '乙', perception)
    assert (action.kind, action.target, action.detail) == (kind, target, intent)
    packet = json.loads(control._llm.prompts[0].split('## 行动数据\n')[1])
    assert 'intents' not in packet
    assert packet['visible_context']['world_view'] == perception.world_view


@pytest.mark.parametrize('reply', ['[LLM error] unavailable', {}, {'kind': 'fly', 'target': ''},
                                   {'kind': 'move', 'target': 123}])
def test_action_semantic_protocol_fails_closed(reply):
    control = SimulationControl()
    control._llm = ReplyModel([reply])
    with pytest.raises((RuntimeError, ValueError)):
        control.interpret_action('去码头。', '乙', AgentPerception('乙', 0))


def test_narration_review_catches_unquoted_new_action_and_retains_claim_source():
    renderer = NarrativeRenderer()
    Entity('WorldHost').add_component(renderer)
    model = ReplyModel([
        '甲介绍了传闻，乙随即掏出钥匙打开铁门。',
        {'valid': False, 'issues': ['乙掏钥匙开门是未提交的新行动；传闻中的钥匙仍待确认。']},
        '甲声称乙有一把钥匙；这段说法的真实性仍待确认。',
        {'valid': True, 'issues': []},
    ])
    renderer._llm = model
    payload = {'player_pov': {'viewer': '玩家'}, 'simulation_result': {'resolved_actions': [
        {'actor': '甲', 'action_kind': 'communicate', 'result': '甲声称乙有一把钥匙。'}]}}
    result = renderer.render(payload)
    assert '真实性仍待确认' in result
    assert '打开铁门' not in result
    assert model.prompts[1].startswith('## 叙述语义校对')
    assert model.prompts[3].startswith('## 叙述语义校对')


def test_narration_review_checks_actual_final_text_after_formatting():
    renderer = NarrativeRenderer()
    Entity('WorldHost').add_component(renderer)
    renderer._llm = ReplyModel(['四周很安静。', {'valid': False, 'issues': ['遗漏玩家投信结果与收件人尚待确认。']},
        '你把信投进了信箱，收件人仍待确认。', {'valid': True, 'issues': []}])
    payload = {'player_pov': {'viewer': '甲'}, 'simulation_result': {'resolved_actions': [
        {'actor': '甲', 'result': '甲把信投入信箱，收件人仍待确认。'}]}}
    result = renderer.render(payload)
    check = json.loads(renderer._llm.prompts[-1].split('## 叙述数据\n')[1])
    assert check['narration'] == result
    assert '收件人仍待确认' in result


@pytest.mark.parametrize('verdict', [{}, {'valid': True}, {'valid': 'true', 'issues': []}])
def test_unavailable_narration_check_prevents_delivery(verdict):
    renderer = NarrativeRenderer()
    Entity('WorldHost').add_component(renderer)
    renderer._llm = ReplyModel(['安静的房间。', verdict])
    with pytest.raises(RuntimeError, match='unavailable or malformed'):
        renderer.render({'player_pov': {}, 'simulation_result': {}})


class Director:
    def __init__(self):
        self.calls = 0

    def generate_messages(self, messages, **kwargs):
        self.calls += 1
        if self.calls > 1:
            return {'content': '保留探索空间。'}
        return {'content': '', 'tool_calls': [{'id': 'proposal', 'type': 'function', 'function': {
            'name': 'propose_storylet', 'arguments': json.dumps({'reason': '未来的外部机会', 'storylet': {
                'storylet_id': 'bell', 'intent': '大厅的钟响了一声。', 'location': '大厅', 'one_shot': True,
                'conditions': [{'scope': 'scene', 'path': 'scene_flags.weather', 'value': 'rainy'}],
            }}, ensure_ascii=False)}}]}


class WorldModel:
    def __init__(self, verdict):
        self.verdict = verdict
        self.reviews = 0
        self.review_packets = []
        self.batches = []

    def generate(self, prompt):
        if prompt.startswith('## 行动语义解释'):
            return {'content': json.dumps({'kind': 'wait', 'target': ''})}
        if prompt.startswith('## 故事块语义审核'):
            self.reviews += 1
            self.review_packets.append(json.loads(prompt.split('## 审核数据\n')[1]))
            if isinstance(self.verdict, Exception):
                raise self.verdict
            return {'content': json.dumps(self.verdict)}
        if prompt.startswith('## 提交前语义校对'):
            return {'content': json.dumps({'valid': True, 'issues': []})}
        intents = json.loads(prompt.split('## 本轮意图\n')[1].split('\n## 宿主允许')[0])
        self.batches.append(deepcopy(intents))
        return {'content': json.dumps({'resolved_actions': [
            {'actor': i['actor'], 'intent': i['intent'], 'outcome': 'success', 'location': '大厅',
             'result': i['intent'], 'visibility': 'local', 'source_storylet_id': i.get('source_storylet_id', '')}
            for i in intents], 'state_updates': {'scene': {}, 'actor_states': {}, 'world_objects': {}}}, ensure_ascii=False)}


def director_session(verdict):
    session = create_session_from_seed('地点：大厅\n角色：甲|居民|谨慎||玩家|地点=大厅', profile='offline')
    host = session.entities['WorldHost']
    model = WorldModel(verdict)
    control = SimulationControl(scenario=session.scenario)
    control._llm = model
    host.add_component(control)
    planner = StoryPlanner(scenario=session.scenario, interval_turns=1)
    planner._llm = Director()
    host.add_component(planner)
    return session, model, host.get_component('SceneState')


def test_director_registers_future_block_then_trigger_settles_and_save_restores(tmp_path):
    session, model, scene = director_session({'valid': True, 'issues': []})
    restored = None
    try:
        first = session.run_step(overrides={'甲': '等待。'})
        assert first['step_committed'] and not first['storylet_triggers']
        assert scene.get_scene_flag('pending_story_planner_proposals')[0]['status'] == 'accepted'
        assert scene.get_scene_flag('dynamic_storylet_ids') == ['bell']
        assert model.reviews == 0
        saved = session.save(tmp_path / 'director.storysave')
        restored = load_session(saved)
        restored_scene = restored.entities['WorldHost'].get_component('SceneState')
        assert restored_scene.get_scene_flag('dynamic_storylets') == scene.get_scene_flag('dynamic_storylets')
        assert restored_scene.get_scene_flag('pending_story_planner_proposals')[0]['status'] == 'accepted'
        assert restored.entities['WorldHost'].get_component('StoryPlanner').scenario is restored.scenario
        assert restored.entities['WorldHost'].get_component('SimulationControl').scenario is restored.scenario
        scene.update_scene_flags({'weather': 'rainy'})
        second = session.run_step(overrides={'甲': '等待。'})
        assert second['step_committed']
        assert any(i.get('source_storylet_id') == 'bell' for i in model.batches[-1])
        assert scene.get_scene_flag('consumed_storylets') == ['bell']
        assert any(e.get_component('WorldEventFact') and e.get_component('WorldEventFact').source_ref.startswith('storylet:bell:')
                   for e in session.entities.values())
        assert '钟响了一声' in second['rendered_text']
        third = session.run_step(overrides={'甲': '等待。'})
        assert third['step_committed'] and not third['storylet_triggers']
        assert model.reviews == 0
    finally:
        if restored:
            restored.close()
        session.close()


def test_director_registers_prospective_content_without_semantic_preapproval():
    session, model, scene = director_session(RuntimeError('review must not be called'))
    try:
        result = session.run_step(overrides={'甲': '等待。'})
        assert result['step_committed'] and not session.delivery_pending
        assert scene.get_scene_flag('dynamic_storylet_ids') == ['bell']
        assert model.reviews == 0
    finally:
        session.close()


def test_structural_registration_rejects_duplicate_id():
    from src.story_engine.scenarios.config import StoryletConfig

    session, model, scene = director_session(RuntimeError('review must not be called'))
    session.scenario.storylets.append(StoryletConfig(storylet_id='bell', intent='未来剧情',
        conditions=[{'path': 'scene_flags.never', 'value': True}]))
    try:
        result = session.run_step(overrides={'甲': '等待。'})
        assert result['step_committed']
        assert scene.get_scene_flag('dynamic_storylets', []) == []
        proposal = scene.get_scene_flag('pending_story_planner_proposals')[0]
        assert proposal['status'] == 'rejected'
        assert 'already exists' in proposal['issues'][0]
        assert model.reviews == 0
    finally:
        session.close()


def test_pending_structural_registration_continues_when_director_is_down():
    from src.story_engine.environment.narrative_candidates import record_story_proposal

    session, model, scene = director_session(RuntimeError('review must not be called'))
    record_story_proposal(scene, {'payload': {'storylet_id': 'pending', 'intent': '未来机会',
        'trigger': '有人公开质疑继承权'}}, turn_id='earlier')
    planner = session.entities['WorldHost'].get_component('StoryPlanner')

    class UnavailableDirector:
        def generate_messages(self, messages, **kwargs):
            return {'content': '[LLM error] unavailable'}

    planner._llm = UnavailableDirector()
    try:
        result = session.run_step(overrides={'甲': '等待。'})
        assert result['step_committed']
        assert scene.get_scene_flag('dynamic_storylet_ids') == ['pending']
        assert scene.get_scene_flag('dynamic_storylets')[0]['trigger'] == '有人公开质疑继承权'
        assert model.reviews == 0
    finally:
        session.close()
