from tests.semantic_support import action_reply, narration_check_reply
"""Unknown world facts are resolved by the production settlement boundary."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.story_engine.agents import HermesCharacterAgent
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.scenarios.config import StoryletConfig, StateCondition
from src.story_engine.session import create_session_from_seed, load_session


def world(session):
    return session.entities['WorldHost'].get_component('SceneState')


class Model:
    def __init__(self, drafts, verdicts=None):
        self.drafts = iter(drafts)
        self.verdicts = iter(verdicts) if verdicts is not None else None
        self.prompts = []
        self.checks = []

    def generate(self, prompt):
        if reply := action_reply(prompt):
            return reply
        if reply := narration_check_reply(prompt):
            return reply
        if prompt.startswith('## 提交前语义校对'):
            payload = json.loads(prompt.split('## 候选提交数据\n', 1)[1])
            self.checks.append(payload)
            verdict = next(self.verdicts) if self.verdicts is not None else {'valid': True, 'issues': []}
            return {'content': json.dumps(verdict, ensure_ascii=False)}
        self.prompts.append(prompt)
        intents = json.loads(prompt.split('## 本轮意图\n', 1)[1].split('\n## 宿主允许', 1)[0])
        draft = next(self.drafts)
        draft = draft(intents) if callable(draft) else draft
        return {'content': json.dumps(draft, ensure_ascii=False)}


class Narrator:
    def __init__(self):
        self.payloads = []

    def generate(self, prompt):
        if reply := action_reply(prompt):
            return reply
        if reply := narration_check_reply(prompt):
            return reply
        payload = json.loads(prompt.split('本轮结构化输入：\n', 1)[1].split('\n\n请输出', 1)[0])
        self.payloads.append(payload)
        assert not payload['simulation_result']['world_additions']
        # Preserve the source of utterances at the player-facing boundary.
        return {'content': ' '.join(
            (f"{a['actor']}说：" if a.get('action_kind') == 'communicate' else '') + a['result']
            for a in payload['simulation_result']['resolved_actions'])}


def install(session, model):
    control = SimulationControl(scenario=session.scenario)
    control._llm = model
    session.entities['WorldHost'].add_component(control)
    renderer = NarrativeRenderer(scenario=session.scenario)
    narrator = Narrator()
    renderer._llm = narrator
    session.entities['WorldHost'].add_component(renderer)
    return narrator


def session_for(model):
    session = create_session_from_seed(
        '地点：河岸\n角色：甲 | 来客 | 谨慎 | 等待 | 玩家 | 地点=河岸',
        profile='offline', random_seed='world-completion')
    install(session, model)
    return session


def settle(intents, additions=None):
    return {
        'resolved_actions': [
            {'actor': i['actor'], 'intent': i['intent'], 'location': i.get('location') or '河岸',
             'source_storylet_id': i.get('source_storylet_id', ''),
             'outcome': 'success', 'visibility': 'local', 'result': i['intent']}
            for i in intents],
        'state_updates': {'scene': {}, 'actor_states': {}, 'world_objects': {}},
        'world_additions': additions or {},
    }


def additions(two_characters=False):
    return {
        # Cross references in both directions and a dependency on a second
        # new node are intentionally independent of array order.
        'locations': [
            {'location_id': '钟表铺', 'connects_to': ['小巷'],
             'properties': {'description': '临河的旧钟表铺'},
             'actor': '甲', 'reason': '本轮寻访需要确定可进入的店铺'},
            {'location_id': '小巷', 'connects_to': ['河岸'],
             'actor': '甲', 'reason': '抵达店铺需要经过这条路'},
        ],
        'characters': [
            {'name': n, 'role': '修表匠', 'location': '钟表铺', 'goals': [],
             'actor': '甲', 'reason': '本轮看到店里的人，后续由本人自主回应',
             'activation_policy': 'foreground'}
            for n in (['老周', '学徒'] if two_characters else ['老周'])],
        'facts': ['老周与甲之间尚未发生借款。'],
    }


def visit(intents):
    draft = settle(intents, additions())
    for action in draft['resolved_actions']:
        if action['actor'] == '甲':
            action.update(location='钟表铺', result='甲循路抵达钟表铺，看见老周和一块旧怀表。')
    draft['state_updates']['actor_states']['甲'] = {'location': '钟表铺'}
    draft['object_lifecycle'] = [
        {'operation': 'spawn', 'object_id': '旧怀表', 'actor': '甲',
         'reason': '甲抵达店铺时实际看见老周持有的怀表', 'owner': '老周',
         'properties': {'material': '黄铜'}, 'portable': True}]
    return draft


def test_language_stays_attributed_and_unknown_across_later_turns():
    model = Model([settle, settle])
    session = session_for(model)
    try:
        spoken = '说老周在河对岸开着钟表铺，而且欠我钱。'
        first = session.run_step(overrides={'甲': spoken})
        assert first['step_committed']
        assert first['simulation_result']['resolved_actions'][0]['action_kind'] == 'communicate'
        assert first['simulation_result']['resolved_actions'][0]['actor'] == '甲'
        assert spoken in first['rendered_text']
        assert session.run_step(overrides={'甲': '等待'})['step_committed']
        assert set(world(session).world_objects) == {'河岸'}
        assert set(world(session).actor_states) == {'甲'}
        assert not world(session).get_scene_flag('established_facts', [])
        assert '结果对事实的依赖' in model.prompts[0]
    finally:
        session.close()


def test_impossible_claim_is_delivered_as_speech_with_no_physical_consequence():
    model = Model([settle])
    session = session_for(model)
    try:
        statement = '说我能瞬移和凭空变出金山。'
        context = session.run_step(overrides={'甲': statement})
        assert context['step_committed']
        action = context['simulation_result']['resolved_actions'][0]
        assert action['action_kind'] == 'communicate' and action['outcome'] == 'success'
        assert action['result'] == statement
        assert world(session).actor_states['甲']['location'] == '河岸'
        assert set(world(session).world_objects) == {'河岸'}
    finally:
        session.close()


def test_minimal_delivery_leaves_receiver_unresolved_and_confirmed_negative_is_retained():
    def delivered(intents):
        draft = settle(intents)
        draft['resolved_actions'][0]['result'] = '信件已投入信箱，收件人是否存在仍待确认。'
        draft['object_lifecycle'] = [{'operation': 'relocate', 'object_id': '信件',
            'actor': '甲', 'reason': '甲把信投入信箱', 'container': '信箱'}]
        return draft

    def inspected(intents):
        draft = settle(intents, {'facts': ['甲所说的陈叔是其编造的人物，截至本轮确认并无此人。']})
        draft['resolved_actions'][0].update(result='甲检查记录。', private_result='记录确认甲所说的陈叔并不存在。')
        return draft

    model = Model([delivered, inspected, settle])
    session = session_for(model)
    world(session).world_objects.update({
        '信件': {'is_location': False, 'kind': 'letter', 'owner': '甲', 'portable': True},
        '信箱': {'is_location': False, 'kind': 'mailbox', 'location': '河岸', 'portable': False,
                 'is_container': True, 'container_capacity': 10, 'container_open': True},
        '身份记录': {'is_location': False, 'kind': 'document', 'location': '河岸', 'portable': False},
    })
    try:
        first = session.run_step(overrides={'甲': '把给陈叔的信投入信箱'})
        assert first['step_committed']
        assert world(session).world_objects['信件']['container'] == '信箱'
        assert set(world(session).actor_states) == {'甲'}
        assert not world(session).get_scene_flag('established_facts', [])
        assert session.run_step(overrides={'甲': '检查陈叔的身份记录'})['step_committed']
        facts = deepcopy(world(session).get_scene_flag('established_facts'))
        assert facts[0]['step'] == 2 and '编造' in facts[0]['statement']
        assert session.run_step(overrides={'甲': '说陈叔马上就会来。'})['step_committed']
        assert world(session).get_scene_flag('established_facts') == facts
        assert '截至本轮确认并无此人' in model.prompts[-1]
        assert not model.checks[-1]['after']['actor_states'].get('陈叔')
    finally:
        session.close()


def test_speech_only_creation_is_semantically_repaired_without_registering_subject():
    bad = lambda intents: settle(intents, additions())
    model = Model([bad, settle], [
        {'valid': False, 'issues': ['原始意图只有说话，本轮外部结果无需确认老周或店铺的存在。']},
        {'valid': True, 'issues': []}])
    session = session_for(model)
    calls = []
    original_factory = session.runner.agent_runtime_factories['offline']
    session.runner.agent_runtime_factories['offline'] = lambda e, c: (calls.append(e.name) or original_factory(e, c))
    try:
        context = session.run_step(overrides={'甲': '说我叔叔老周在河对岸有钟表铺，给钱就能找到他。'})
        assert context['step_committed'] and context['settlement_attempts'] == 2
        assert calls == []
        assert '老周' not in session.entities
        assert '钟表铺' not in world(session).world_objects
        assert '上次候选结算的提交校对反馈' in model.prompts[1]
        assert model.checks[0]['after']['actor_states']['老周']['location'] == '钟表铺'
        assert not model.checks[1]['after']['scene_flags'].get('established_facts')
    finally:
        session.close()


def test_location_character_object_register_together_and_survive_save(tmp_path):
    model = Model([visit])
    session = session_for(model)
    try:
        context = session.run_step(overrides={'甲': '前往河对岸寻访传闻中的钟表铺'})
        assert context['step_committed'], context
        scene = world(session)
        assert scene.actor_states['甲']['location'] == '钟表铺'
        assert scene.actor_states['老周']['location'] == '钟表铺'
        assert scene.world_objects['旧怀表']['owner'] == '老周'
        assert scene.world_objects['钟表铺']['connected_to'] == ['小巷']
        assert set(scene.world_objects['小巷']['connected_to']) == {'河岸', '钟表铺'}
        assert scene.world_objects['钟表铺']['description'] == '临河的旧钟表铺'
        assert session.runner.agent_boundary_errors() == []
        assert session.entities['老周'].get_component('AgentController').decision_count == 0
        assert context['actor_observation_windows']['老周']['present_during_step'] is False
        assert model.checks[0]['before']['world_objects'].keys() == {'河岸'}
        assert '老周' in model.checks[0]['after']['actor_states']
        scene.public_scene_fields.append('established_facts')
        assert 'established_facts' not in scene.get_public_scene_state()['flags']
        identities = {n: e.id for n, e in session.entities.items()}
        path = session.save(tmp_path / 'grown.storysave')
        snapshot = deepcopy(scene.model_dump())
    finally:
        session.close()
    restored = load_session(path)
    try:
        assert world(restored).model_dump() == snapshot
        assert {n: e.id for n, e in restored.entities.items()} == identities
        assert restored.runner.agent_registry.is_registered('老周')
        continuation = Model([settle])
        install(restored, continuation)
        assert restored.run_step(overrides={'甲': '等待'})['step_committed']
        assert '老周与甲之间尚未发生借款' in continuation.prompts[0]
    finally:
        restored.close()


def test_new_character_uses_hermes_and_chooses_its_own_next_action():
    model = Model([visit, settle])
    session = session_for(model)
    session.scenario.default_agent_runtime = 'hermes'
    calls = []

    class Conversation:
        def __init__(self, entity):
            self.entity = entity

        def run_subject_turn(self, packet):
            calls.append((self.entity.name, packet))
            return {'protocol_version': 1, 'agent_id': self.entity.id,
                    'content': json.dumps({'action': '说今天只修表，不借钱。'}, ensure_ascii=False)}

    session.runner.agent_runtime_factories['hermes'] = lambda entity, cfg: HermesCharacterAgent(
        lambda e, _: Conversation(e), cfg)
    try:
        assert session.run_step(overrides={'甲': '前往钟表铺'})['step_committed']
        assert calls == []  # a birth is not an unsolicited subject decision
        second = session.run_step(overrides={'甲': '等待'})
        assert second['step_committed'], second
        assert calls[0][0] == '老周'
        assert any(i['actor'] == '老周' and '不借钱' in i['intent'] for i in second['intents'])
        assert session.entities['老周'].get_component('AgentController').runtime == 'hermes'
    finally:
        session.close()


def test_runtime_registration_failure_rolls_back_all_new_members_and_facts():
    def draft(intents):
        return settle(intents, additions(two_characters=True))
    model = Model([draft])
    session = session_for(model)
    original_factory = session.runner.agent_runtime_factories['offline']

    def factory(entity, config):
        if entity.name == '学徒':
            raise RuntimeError('subject startup failed')
        return original_factory(entity, config)

    session.runner.agent_runtime_factories['offline'] = factory
    before = deepcopy(world(session).model_dump())
    try:
        context = session.run_step(overrides={'甲': '寻找钟表铺里的修表匠'})
        assert context['authoritative_step_failed']
        assert world(session).model_dump() == before
        assert not session.runner.agent_registry.is_registered('老周')
        assert not session.runner.agent_registry.is_registered('学徒')
        assert '老周' not in session.entities and '学徒' not in session.entities
        assert session.step_count == 0
        assert len(model.prompts) == 1
    finally:
        session.close()


@pytest.mark.parametrize('malformation', ['unknown_location', 'duplicate_name', 'invalid_facts', 'invalid_locations'])
def test_malformed_world_additions_fail_whole_step(malformation):
    def draft(intents):
        extra = additions()
        if malformation == 'unknown_location':
            extra['characters'][0]['location'] = '不存在的位置'
        elif malformation == 'duplicate_name':
            extra['characters'].append(deepcopy(extra['characters'][0]))
        elif malformation == 'invalid_facts':
            extra['facts'] = [123]
        else:
            extra['locations'] = ['坏节点']
        return settle(intents, extra)
    model = Model([draft, draft])
    session = session_for(model)
    before = deepcopy(world(session).model_dump())
    try:
        context = session.run_step(overrides={'甲': '寻找传闻中的地方'})
        assert context['authoritative_step_failed']
        assert world(session).model_dump() == before
        assert session.runner.agent_boundary_errors() == []
    finally:
        session.close()


def test_storylet_future_event_can_introduce_a_new_place_without_player_movement():
    def event(intents):
        extra = {'locations': [{'location_id': '渡口', 'connects_to': ['河岸'],
                 'actor': 'World', 'reason': '本轮故事事件使新渡口投入使用'}]}
        draft = settle(intents, extra)
        for action in draft['resolved_actions']:
            if action['actor'] == 'World':
                action.update(location='渡口', result='渡口的灯亮起，新的渡口投入使用。')
        return draft
    model = Model([event])
    session = session_for(model)
    session.scenario.storylets = [StoryletConfig(
        storylet_id='ferry', intent='一个新的渡口投入使用', location='渡口', one_shot=True,
        conditions=[StateCondition(scope='scene', path='scene_flags.ready', value=True)])]
    world(session).update_scene_flags({'ready': True})
    try:
        context = session.run_step(overrides={'甲': '等待'})
        assert context['step_committed'], context
        assert world(session).actor_states['甲']['location'] == '河岸'
        assert world(session).is_location('渡口')
        assert context['simulation_result']['storylet_hits'] == ['ferry']
        assert world(session).get_scene_flag('consumed_storylets') == ['ferry']
        assert any(e.get_component('WorldEventFact') for e in session.entities.values())
    finally:
        session.close()


@pytest.mark.parametrize('success', [True, False])
def test_only_selected_uncertain_branch_can_register_world_members(success):
    def uncertain(intents):
        positive = visit(intents)
        negative = settle(intents)
        negative['resolved_actions'][0].update(outcome='fail', result='甲未能找到传闻中的钟表铺。')
        def branch(draft):
            return {'resolved_action': draft['resolved_actions'][0],
                    'state_updates': draft['state_updates'],
                    'object_lifecycle': draft.get('object_lifecycle', []),
                    'world_additions': draft['world_additions']}
        return {'resolved_actions': [], 'state_updates': {}, 'uncertain_outcomes': [{
            'check_id': 'find-workshop', 'actor': '甲', 'check_kind': 'world',
            'difficulty': 'normal', 'success': branch(positive), 'failure': branch(negative)}]}
    model = Model([uncertain])
    session = session_for(model)
    session.runner.check_resolver = SimpleNamespace(
        resolve=lambda check, **kwargs: SimpleNamespace(success=success, trace={'check_id': check.check_id}))
    try:
        context = session.run_step(overrides={'甲': '前往传闻中的钟表铺'})
        assert context['step_committed'], context
        assert world(session).is_location('钟表铺') is success
        assert session.runner.agent_registry.is_registered('老周') is success
        assert ('旧怀表' in world(session).world_objects) is success
        assert bool(world(session).get_scene_flag('established_facts', [])) is success
        assert session.runner.agent_boundary_errors() == []
    finally:
        session.close()


def test_a_spoken_item_claim_is_checked_by_model_before_object_publication():
    def invented(intents):
        draft = settle(intents)
        draft['object_lifecycle'] = [{'operation': 'spawn', 'object_id': '通行令', 'owner': '甲',
             'actor': '甲', 'reason': '甲说自己有一枚通行令', 'properties': {}}]
        return draft
    model = Model([invented, settle], [
        {'valid': False, 'issues': ['这项物品创建仅由言论支持，实际外部结果没有依赖通行令。']},
        {'valid': True, 'issues': []}])
    session = session_for(model)
    try:
        context = session.run_step(overrides={'甲': '说我有通行令，你们应该相信我。'})
        assert context['step_committed'] and context['settlement_attempts'] == 2
        assert '通行令' in model.checks[0]['after']['world_objects']
        assert '通行令' not in world(session).world_objects
        assert not any('通行令' in n for n in context.get('world_event_updates', []))
    finally:
        session.close()
