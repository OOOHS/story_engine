"""Semantic world effects keep their full consequences until atomic commit."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src.story_engine.components.cognition import Cognition
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.core.entity import Entity
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.session.savegame import load_session
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.simulation.checks import HostCheckResolver
from src.story_engine.simulation.uncertain_outcomes import UncertainOutcomeResolver
from src.story_engine.systems.cognition import CognitionSystem
from src.story_engine.systems.rendering import RenderingSystem
from tests.runtime.test_semantic_boundaries import ReplyModel
from tests.runtime.test_story_tracking import tracking_session, decision


@pytest.mark.parametrize('verdict,outcome,recipients', [
    ('block', 'success', ['乙']),
    ('allow', 'failure', []),
])
def test_production_communication_keeps_semantic_delivery(verdict, outcome, recipients):
    host = Entity('WorldHost')
    host.add_component(SceneState(world_objects={'A': {}, 'B': {}}, actor_states={
        '甲': {'location': 'A'}, '乙': {'location': 'B'}}))
    control = SimulationControl()
    host.add_component(control)
    control._llm = ReplyModel([{'resolved_actions': [{
        'actor': '甲', 'outcome': outcome, 'location': 'A',
        'result': '电话的实际送达结果。', 'visibility': 'hidden', 'recipients': recipients,
        'private_result': '甲听见了线路中的声音。',
    }]}])
    result = control.simulate({'intents': [{'actor': '甲', 'intent': '打电话给乙。',
        'action_kind': 'communicate', 'action_target': '乙', 'location': 'A'}],
        'legality': {'advisory_only': True, 'checks': [{'actor': '甲',
            'action_kind': 'communicate', 'verdict': verdict}]}})
    action = result['resolved_actions'][0]
    assert action['outcome'] == outcome
    assert action['recipients'] == recipients
    assert action['visibility'] == 'hidden'
    assert action['private_result'] == '甲听见了线路中的声音。'
    assert '"topology_changes"' in control._llm.prompts[0]


def test_tracker_commits_displacement_topology_and_dynamic_container_then_continues_elsewhere(tmp_path):
    session, model, scene, tracking = tracking_session([
        decision(advance='洪水转移到河岸，原来的道路中断。'),
        decision('completed', '洪水经过大厅和河岸，追踪结束。'),
    ])
    original = model.generate
    restored = None
    try:
        scene.world_objects['河岸'] = {'is_location': True, 'connected_to': []}
        tracking.agents['bell'].pending_advance = '洪水将甲冲到河岸，带来一个可收纳物品的木箱，并冲开大厅到河岸的通路。'

        def generate(prompt):
            reply = original(prompt)
            if '## 本轮意图\n' not in prompt:
                return reply
            data = json.loads(reply['content'])
            world = next(a for a in data['resolved_actions'] if a['actor'] == 'World')
            continuing = bool(scene.get_scene_flag('active_storylet_triggers', []))
            world['location'] = '河岸' if continuing else '大厅'
            world['result'] = '洪水转移到河岸，道路中断。' if continuing else '甲被洪水冲到河岸，木箱搁浅，通路打开。'
            data['topology_changes'] = [{'operation': 'disconnect' if continuing else 'connect',
                'source': '大厅', 'target': '河岸', 'reason': '洪水改变了道路。'}]
            if not continuing:
                data['state_updates']['actor_states'] = {'甲': {'location': '河岸', 'injuries': '擦伤'}}
                data['object_lifecycle'] = [{'operation': 'spawn', 'actor': 'World',
                    'object_id': '木箱', 'location': '河岸', 'reason': '洪水带来木箱。',
                    'properties': {'is_container': True, 'container_capacity': 3,
                        'affordances': [{'id': 'shelter', 'requires_owner': False, 'consumes': False}]}}]
            return {'content': json.dumps(data, ensure_ascii=False)}

        model.generate = generate
        first = session.run_step(overrides={'甲': '等待。'})
        assert first['step_committed'], first.get('phase_errors')
        assert scene.get_actor_location('甲') == '河岸'
        assert scene.get_object_state('大厅')['connected_to'] == ['河岸']
        assert scene.get_object_state('木箱')['container_capacity'] == 3
        assert scene.get_object_state('木箱')['affordances'][0]['id'] == 'shelter'
        assert scene.get_scene_flag('world_version') == 1
        assert first['simulation_result']['topology_changes'][0]['operation'] == 'connect'
        assert any(e.get_component('WorldEventFact') for e in session.entities.values())
        restored = load_session(session.save(tmp_path / 'world-effects.storysave'))
        assert restored.entities['WorldHost'].get_component('SceneState').world_objects == scene.world_objects
        second = session.run_step(overrides={'甲': '等待。'})
        assert second['step_committed'], second.get('phase_errors')
        assert second['storylet_triggers'][0]['continuation'] is True
        assert scene.get_object_state('大厅')['connected_to'] == []
        assert scene.get_scene_flag('world_version') == 2
        assert tracking.agents['bell'].closed
    finally:
        if restored:
            restored.close()
        session.close()


def test_world_building_rolls_back_when_semantic_commit_rejects():
    scene = SceneState(world_objects={'A': {'connected_to': []}, 'B': {'connected_to': []}},
                       actor_states={'甲': {'location': 'A'}})
    before = scene.model_dump()
    candidate = {'state_updates': {'actor_states': {'甲': {'location': 'B'}}},
        'resolved_actions': [{'actor': 'World', 'outcome': 'success'}],
        'topology_changes': [{'operation': 'connect', 'source': 'A', 'target': 'B'}],
        'object_lifecycle': [{'operation': 'spawn', 'actor': 'World', 'object_id': '箱子',
            'location': 'B', 'reason': '事件带来箱子', 'properties': {'is_container': True, 'container_capacity': 2}}]}

    def reject(before, after, result):
        assert after.get_actor_location('甲') == 'B'
        assert after.get_object_state('A')['connected_to'] == ['B']
        assert after.get_object_state('箱子')['is_container'] is True
        raise RuntimeError('the staged effects conflict with established facts')

    with pytest.raises(RuntimeError, match='conflict'):
        WorldStateTransaction().commit(scene, None, candidate, semantic_validator=reject)
    assert scene.model_dump() == before


@pytest.mark.parametrize('properties', [{'is_container': True, 'container_capacity': 0},
    {'affordances': [{'id': 'broken', 'consumes': 'yes'}]}])
def test_dynamic_capabilities_still_cross_structural_validation(properties):
    scene = SceneState(world_objects={'A': {}}, actor_states={})
    before = scene.model_dump()
    result = WorldStateTransaction().commit(scene, None, {
        'resolved_actions': [{'actor': 'World', 'outcome': 'success'}],
        'object_lifecycle': [{'operation': 'spawn', 'actor': 'World', 'object_id': '箱子',
            'location': 'A', 'reason': '世界事件带来物品', 'properties': properties}],
    }, semantic_validator=lambda *args: None)
    assert not result.committed
    assert scene.model_dump() == before


def test_uncertain_branch_preserves_passive_displacement():
    scene = SceneState(world_objects={'A': {}, 'B': {}}, actor_states={
        '甲': {'location': 'A'}, '乙': {'location': 'A'}})
    branch = {'resolved_action': {'outcome': 'success', 'result': '爆炸将乙推到B。'},
              'state_updates': {'actor_states': {'乙': {'location': 'B'}}}}
    resolution = UncertainOutcomeResolver().resolve({'uncertain_outcomes': [{
        'check_id': 'blast', 'actor': '甲', 'check_kind': 'world', 'difficulty': 'trivial',
        'success': branch, 'failure': {**branch, 'resolved_action': {'outcome': 'fail', 'result': '爆炸仍将乙推到B。'}},
    }]}, scene_state=scene, intents=[{'actor': '甲', 'intent': '引爆装置', 'action_kind': 'interact'}],
        check_resolver=HostCheckResolver(DeterministicRandomStreams(1)), current_step=0, world_version=0)
    assert resolution.errors == []
    assert resolution.result['state_updates']['actor_states']['乙']['location'] == 'B'
    assert scene.get_actor_location('乙') == 'A'


def test_remote_private_message_reaches_listener_and_player_view_without_belief_write():
    host = Entity('WorldHost')
    scene = SceneState(world_objects={'A': {}, 'B': {}}, actor_states={
        '甲': {'location': 'A'}, '乙': {'location': 'B'}, '丙': {'location': 'A'}})
    host.add_component(scene)
    entities = {'WorldHost': host}
    for name in ('甲', '乙', '丙'):
        actor = Entity(name)
        actor.add_component(Cognition())
        entities[name] = actor
    action = {'actor': '甲', 'intent': '打电话告诉乙：桥塌了。', 'action_kind': 'communicate',
        'action_target': '乙', 'outcome': 'success', 'location': 'A', 'visibility': 'hidden',
        'result': '甲在电话里声称桥塌了。', 'recipients': ['乙']}
    result = {'resolved_actions': [action], 'knowledge_updates': [{
        'source': '甲', 'target': '乙', 'statement': '桥塌了', 'reason': '电话送达', 'mode': 'told'}]}
    context = {'simulation_result': result, 'clock': SimpleNamespace(current_step=1)}
    CognitionSystem().update(entities, context)
    assert entities['乙'].get_component('Cognition').beliefs == []
    assert entities['乙'].get_component('Cognition').experiences[-1]['events'][0]['actor'] == '甲'
    assert entities['丙'].get_component('Cognition').experiences == []
    render = RenderingSystem()._build_visible_simulation(result, scene.get_view_pov('乙'))
    assert render['resolved_actions'][0]['result'] == action['result']
    observer = RenderingSystem()._build_visible_simulation(result, scene.get_view_pov('丙'))
    assert observer['resolved_actions'] == []
    from src.story_engine.systems.world_events import WorldEventSystem
    from src.story_engine.agents.scheduler import AgentScheduler
    context['state_transaction'] = {'committed': True}
    WorldEventSystem().update(entities, context)
    listener = entities['乙'].get_component('Cognition')
    assert listener.pending_world_events
    assert any('甲的表达' in record['statement'] for record in listener.beliefs)
    activation = AgentScheduler().activation_for(entities['乙'], step=2, actor_location='B',
        player_location='A', proposals=[], is_player=False, has_manual_override=False, scene_state=scene)
    assert activation.active
    assert entities['丙'].get_component('Cognition').pending_world_events == []


def test_narration_keeps_faithful_paraphrase_and_players_private_result():
    renderer = NarrativeRenderer()
    Entity('WorldHost').add_component(renderer)
    renderer._llm = ReplyModel(['你走到了门外，发现钥匙还在自己的口袋里。', {'valid': True, 'issues': []}])
    result = {'resolved_actions': [{'actor': '甲', 'outcome': 'success',
        'result': '甲已走出房门。', 'private_result': '钥匙在本人口袋里。'}]}
    visible = RenderingSystem()._build_visible_simulation(result, {'viewer': '甲', 'location': 'A'})
    assert visible['resolved_actions'][0]['private_result'] == '钥匙在本人口袋里。'
    text = renderer.render({'player_pov': {'viewer': '甲'}, 'simulation_result': visible})
    assert text == '你走到了门外，发现钥匙还在自己的口袋里。'


def test_claim_receipt_preserves_listener_authored_position():
    from tests.social.test_claim_knowledge import _world, _communicate_action
    from src.story_engine.systems.claim_knowledge import ClaimKnowledgeSystem
    scene, entities, registry = _world()
    source = entities['甲'].get_component('KnowledgeState')
    target = entities['乙'].get_component('KnowledgeState')
    source.learn(claim_id='ledger_owner', stance='supports', confidence=0.9, basis='observed', source='self', step=0)
    target.learn(claim_id='ledger_owner', stance='rejects', confidence=0.8, basis='inferred', source='self', step=0)
    context = {'state_transaction': {'committed': True}, 'claim_registry': registry,
        'simulation_result': {'resolved_actions': [_communicate_action()], 'knowledge_updates': [{
            'source': '甲', 'target': '乙', 'claim_id': 'ledger_owner', 'asserted_stance': 'supports', 'reason': '甲表达了自己的看法。'}]}}
    ClaimKnowledgeSystem().update(entities, context)
    assert context['claim_knowledge_errors'] == []
    record = target.claims['ledger_owner']
    assert (record.stance, record.confidence) == ('rejects', 0.8)
    assert record.receipts[-1]['asserted_stance'] == 'supports'
    assert registry.private_snapshot(actor='乙', knowledge_state=target, scene_state=scene)['claims'][0]['receipts']
