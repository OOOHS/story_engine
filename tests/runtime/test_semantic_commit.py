from tests.semantic_support import action_reply, narration_check_reply
"""Production model settlement is checked against the actual staged world."""
import json
from copy import deepcopy

import pytest

from src.story_engine.components.simulation_control import SettlementRejected
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.systems.world_events import WorldEventSystem
from tests.narrative.test_storylet_emergence import _world, _response, _step


class Model:
    def __init__(self, drafts, verdicts, scene):
        self.drafts = iter(drafts)
        self.verdicts = iter(verdicts)
        self.scene = scene
        self.requests = []
        self.checks = []

    def generate(self, prompt):
        if prompt.startswith('## 提交前语义校对'):
            payload = json.loads(prompt.split('## 候选提交数据\n', 1)[1])
            self.checks.append(payload)
            # The original authoritative Scene remains unchanged while the
            # model sees the staged after-state, including host-applied effects.
            assert not self.scene.get_scene_flag('consumed_storylets', [])
            return {'content': json.dumps(next(self.verdicts), ensure_ascii=False)}
        self.requests.append(prompt)
        return {'content': json.dumps(next(self.drafts), ensure_ascii=False)}


def attach(entities, model):
    entities['WorldHost'].get_component('SimulationControl')._llm = model


def test_missing_persistent_effect_is_corrected_before_storylet_commit():
    entities, scene, _, blocks = _world()
    blocks[0].intent = '铁门的锁自行松开，门被风吹开'
    scene.world_objects['铁门'] = {'is_location': False, 'kind': 'door', 'portable': False,
                                   'location': '广场', 'locked': True, 'open': False}
    missing = _response(blocks)
    missing['resolved_actions'][0]['result'] = '铁门已经解锁并打开。'
    corrected = deepcopy(missing)
    corrected['state_updates']['world_objects']['铁门'] = {'locked': False, 'open': True}
    model = Model([missing, corrected], [
        {'valid': False, 'issues': ['结果称铁门解锁并打开，after 中仍 locked=true/open=false，请落实物理后果。']},
        {'valid': True, 'issues': []}], scene)
    attach(entities, model)
    context = _step(entities, model, missing)
    assert context['state_transaction']['committed']
    assert context['settlement_attempts'] == 2
    assert scene.world_objects['铁门']['locked'] is False
    assert scene.world_objects['铁门']['open'] is True
    assert model.checks[0]['after']['world_objects']['铁门']['locked'] is True
    assert model.checks[1]['after']['world_objects']['铁门']['locked'] is False
    assert '上次候选结算的提交校对反馈' in model.requests[1]
    assert scene.get_scene_flag('consumed_storylets') == ['storm']
    assert len(context['intents']) == 1  # original World trigger is not duplicated
    assert model.checks[0]['intents'] == model.checks[1]['intents']
    WorldEventSystem().update(entities, context)
    events = entities['甲'].get_component('Cognition').pending_world_events
    assert events.count('world-action:storylet:storm:1') == 1


@pytest.mark.parametrize('changes', [
    {'activity': '主动追赶甲', 'posture': '举刀威胁'},
    {'visible_condition': '决定原谅甲，随后主动离开'},
    {'arbitrary_authored_field': '乙决定帮助甲并接管追捕'},
])
def test_unsolicited_actor_behavior_is_rejected_semantically_across_all_fields(changes):
    entities, scene, _, blocks = _world()
    draft = _response(blocks)
    draft['state_updates']['actor_states']['乙'] = changes
    model = Model([draft, draft], [
        {'valid': False, 'issues': ['乙没有行动意图，候选世界替乙决定了自主回应。']},
        {'valid': False, 'issues': ['修正仍含乙未经本人选择的行动。']}], scene)
    attach(entities, model)
    before = deepcopy(scene.model_dump())
    with pytest.raises(SettlementRejected, match='未经本人选择'):
        _step(entities, model, draft)
    assert scene.model_dump() == before
    assert len(model.checks) == 2
    assert model.checks[0]['after']['actor_states']['乙'].items() >= changes.items()
    assert entities['乙'].get_component('Cognition').pending_world_events == []


def test_passive_consequence_on_unproposed_actor_is_preserved():
    entities, scene, _, blocks = _world()
    scene.actor_states['乙']['location'] = '广场'
    blocks[0].intent = '冰雹砸伤广场上乙的手臂'
    draft = _response(blocks)
    draft['state_updates']['actor_states']['乙'] = {
        'posture': '被冰雹击中后摔倒', 'injuries': ['手臂擦伤'], 'activity': '因冲击倒在地上'}
    model = Model([draft], [{'valid': True, 'issues': []}], scene)
    attach(entities, model)
    context = _step(entities, model, draft)
    assert context['state_transaction']['committed']
    assert context['settlement_attempts'] == 1
    assert scene.actor_states['乙']['injuries'] == ['手臂擦伤']
    assert model.checks[0]['intents'][0]['actor'] == 'World'
    WorldEventSystem().update(entities, context)
    assert entities['乙'].get_component('Cognition').pending_world_events


@pytest.mark.parametrize('verdict', [None, {}, {'valid': 'true', 'issues': []}, {'valid': True}, {'valid': True, 'issues': [123]}])
def test_malformed_check_stops_publication_and_has_no_rule_fallback(verdict):
    entities, scene, _, blocks = _world()
    draft = _response(blocks)
    model = Model([draft], [verdict], scene)
    attach(entities, model)
    with pytest.raises(RuntimeError, match='unavailable or malformed'):
        _step(entities, model, draft)
    assert scene.get_scene_flag('consumed_storylets', []) == []
    assert not entities['甲'].get_component('Cognition').pending_world_events
    assert len(model.requests) == 1


def test_commit_check_sees_host_staging_and_cannot_publish_on_rejection():
    from src.story_engine.components.scene_state import SceneState
    scene = SceneState(world_objects={'房间': {}}, actor_states={'甲': {'location': '房间'}})
    result = {'state_updates': {'scene': {'lighting': 'bright'}, 'actor_states': {}, 'world_objects': {}}}
    calls = []
    def check(before, after, candidate):
        calls.append(True)
        assert 'lighting' not in before.scene_flags
        assert after.scene_flags['lighting'] == 'bright'
        raise SettlementRejected(['场景描述与实际状态不一致'], candidate)
    with pytest.raises(SettlementRejected):
        WorldStateTransaction().commit(scene, None, result, semantic_validator=check)
    assert calls == [True]
    assert 'lighting' not in scene.scene_flags


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_runner_repair_preserves_one_agent_decision_and_failure_rolls_back_step(repair_succeeds):
    from src.story_engine.components.simulation_control import SimulationControl
    from src.story_engine.session import create_session_from_seed
    session = create_session_from_seed(
        '地点：房间\n角色：甲 | 来客 | 安静 | 等待 | 玩家 | 地点=房间\n角色：乙 | 守卫 | 安静 | 等待 | 地点=房间',
        profile='offline', random_seed='semantic-repair')
    host = session.entities['WorldHost']
    scene = host.get_component('SceneState')
    scene.world_objects['铁门'] = {'is_location': False, 'location': '房间', 'portable': False,
                                   'kind': 'door', 'locked': True}
    original_time = session.runner.clock.current_time
    original_states = deepcopy(scene.actor_states)
    control = SimulationControl(scenario=session.scenario)
    host.add_component(control)

    class CountingModel:
        def __init__(self):
            self.drafts = []
            self.reviews = 0
        def generate(self, prompt):
            if reply := action_reply(prompt):
                return reply
            if reply := narration_check_reply(prompt):
                return reply
            if prompt.startswith('## 提交前语义校对'):
                self.reviews += 1
                assert scene.world_objects['铁门']['locked'] is True
                if self.reviews == 2 and repair_succeeds:
                    return {'content': json.dumps({'valid': True, 'issues': []})}
                return {'content': json.dumps({'valid': False, 'issues': ['解锁结果缺少状态更新']})}
            intents = json.loads(prompt.split('## 本轮意图\n', 1)[1].split('\n## 宿主允许', 1)[0])
            self.drafts.append(intents)
            updates = {'scene': {}, 'actor_states': {}, 'world_objects': {}}
            if self.reviews and repair_succeeds:
                updates['world_objects']['铁门'] = {'locked': False}
            return {'content': json.dumps({
                'resolved_actions': [{'actor': i['actor'], 'intent': i['intent'], 'outcome': 'success',
                                      'location': '房间', 'result': ('铁门已解锁。' if i['actor'] == '甲' else '乙等待。'),
                                      'visibility': 'local'} for i in intents],
                'state_updates': updates}, ensure_ascii=False)}
    model = CountingModel()
    control._llm = model
    try:
        result = session.run_step(overrides={'甲': '解锁铁门'})
        assert len(model.drafts) == 2 and model.drafts[0] == model.drafts[1]
        assert model.reviews == 2
        if repair_succeeds:
            assert result['step_committed']
            assert result['settlement_attempts'] == 2
            assert session.step_count == 1
            assert scene.world_objects['铁门']['locked'] is False
            for name in ('甲', '乙'):
                assert session.entities[name].get_component('AgentController').decision_count == 1
        else:
            assert result['authoritative_step_failed']
            assert not result['step_committed']
            assert session.step_count == 0
            assert session.runner.clock.current_time == original_time
            assert scene.actor_states == original_states
            assert scene.world_objects['铁门']['locked'] is True
            for name in ('甲', '乙'):
                assert session.entities[name].get_component('AgentController').decision_count == 0
                assert session.entities[name].get_component('Cognition').pending_world_events == []
    finally:
        session.close()
