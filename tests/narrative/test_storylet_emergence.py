"""Triggered story blocks commit facts before characters receive their effects."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.story_engine.components.cognition import Cognition
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.core.entity import Entity
from src.story_engine.scenarios.config import ScenarioConfig, StoryletConfig, StateCondition
from src.story_engine.systems.simulation import SimulationSystem
from src.story_engine.systems.world_events import WorldEventSystem


class SettlementModel:
    def __init__(self):
        self.response = None
        self.prompts = []

    def generate(self, prompt):
        if prompt.startswith("## 提交前语义校对"):
            return {"content": json.dumps({"valid": True, "issues": []})}
        self.prompts.append(prompt)
        return {"content": json.dumps(self.response, ensure_ascii=False)}


def _world(one_shot=True, multiple=False):
    blocks = [StoryletConfig(storylet_id=sid, intent=text, one_shot=one_shot,
              conditions=[StateCondition(scope="scene", path="scene_flags.ready", value=True)])
              for sid, text in ([('storm', '天空突然落下冰雹'), ('bell', '城钟突然鸣响')]
                               if multiple else [('storm', '天空突然落下冰雹')])]
    scenario = ScenarioConfig(name="世界事件测试", description="条件触发事件", environment="广场", default_agent_runtime="llm", initial_state="广场宁静。", storylets=blocks)
    scene = SceneState(world_objects={"广场": {}, "远处": {}},
                       actor_states={"甲": {"location": "广场"}, "乙": {"location": "远处"}},
                       scene_flags={"ready": True})
    host = Entity("WorldHost")
    model = SettlementModel()
    control = SimulationControl(scenario=scenario)
    control._llm = model
    host.add_component(scene)
    host.add_component(control)
    entities = {"WorldHost": host}
    for name in scene.actor_states:
        actor = Entity(name)
        actor.add_component(Cognition())
        entities[name] = actor
    return entities, scene, model, blocks


def _response(blocks, outcome="success", **extra):
    return {"resolved_actions": [{"actor": "World", "intent": b.intent,
             "source_storylet_id": b.storylet_id, "outcome": outcome,
             "location": "广场", "visibility": "local", "result": b.intent + '。'} for b in blocks],
            "state_updates": {"scene": {}, "actor_states": {}, "world_objects": {}}, **extra}


def _step(entities, model, response, step=1):
    model.response = response
    context = {"clock": SimpleNamespace(current_step=step), "intents": []}
    SimulationSystem().update(entities, context)
    return context


def test_condition_triggers_world_event_without_player_or_character_proposal():
    entities, scene, model, blocks = _world()
    context = _step(entities, model, _response(blocks))
    assert context["state_transaction"]["committed"] is True
    assert scene.get_scene_flag("consumed_storylets") == ['storm']
    assert context["simulation_result"]["storylet_hits"] == ['storm']
    assert entities['甲'].get_component('Cognition').pending_world_events == []
    WorldEventSystem().update(entities, context)
    event = 'world-action:storylet:storm:1'
    assert entities['甲'].get_component('Cognition').knows_event(event)
    assert entities['甲'].get_component('Cognition').pending_world_events == [event]
    assert not entities['乙'].get_component('Cognition').knows_event(event)
    later = _step(entities, model, _response([]), 2)
    assert later['storylet_triggers'] == []


def test_multiple_world_triggers_keep_their_own_intent_and_source():
    entities, scene, model, blocks = _world(multiple=True)
    # The model paraphrases intents; host restores each from its exact block id.
    response = _response(blocks)
    for a in response['resolved_actions']:
        a['intent'] = '模型自己的概述'
    context = _step(entities, model, response)
    actions = context['simulation_result']['resolved_actions']
    assert {a['source_storylet_id']: a['intent'] for a in actions} == {b.storylet_id: b.intent for b in blocks}
    WorldEventSystem().update(entities, context)
    assert 'WorldEvent:world-action:storylet:storm:1' in entities
    assert 'WorldEvent:world-action:storylet:bell:1' in entities


@pytest.mark.parametrize('kind', ['omitted', 'duplicate', 'empty_result'])
def test_incomplete_semantic_settlement_fails_before_consumption(kind):
    entities, scene, model, blocks = _world()
    response = _response(blocks)
    if kind == 'omitted': response['resolved_actions'] = []
    if kind == 'duplicate': response['resolved_actions'] *= 2
    if kind == 'empty_result': response['resolved_actions'][0]['result'] = ''
    with pytest.raises(RuntimeError, match='storylet|Storylet'):
        _step(entities, model, response)
    assert scene.get_scene_flag('consumed_storylets', []) == []
    assert scene.get_scene_flag('active_storylet_triggers', []) == []


def test_rejected_transaction_keeps_trigger_for_retry_and_sends_no_fact():
    entities, scene, model, blocks = _world()
    response = _response(blocks)
    response['state_updates']['world_objects']['广场'] = {'connected_to': ['远处']}
    context = _step(entities, model, response)
    assert context['state_transaction']['committed'] is False
    WorldEventSystem().update(entities, context)
    assert scene.get_scene_flag('consumed_storylets', []) == []
    assert entities['甲'].get_component('Cognition').pending_world_events == []
    retried = _step(entities, model, _response(blocks), 2)
    assert retried['simulation_result']['storylet_hits'] == ['storm']


def test_blocked_event_does_not_consume_and_ignores_model_claimed_hit():
    entities, scene, model, blocks = _world()
    response = _response(blocks, outcome='blocked', storylet_hits=['storm'])
    context = _step(entities, model, response)
    assert context['simulation_result']['storylet_hits'] == []
    assert scene.get_scene_flag('consumed_storylets', []) == []
    assert scene.get_scene_flag('active_storylet_triggers', []) == []
    WorldEventSystem().update(entities, context)
    assert entities['甲'].get_component('Cognition').pending_world_events == []
    assert _step(entities, model, _response(blocks), 2)['storylet_triggers']


def test_reusable_block_rearms_after_conditions_become_false():
    entities, scene, model, blocks = _world(one_shot=False)
    _step(entities, model, _response(blocks))
    assert _step(entities, model, _response([]), 2)['storylet_triggers'] == []
    scene.update_scene_flags({'ready': False})
    assert _step(entities, model, _response([]), 3)['storylet_triggers'] == []
    scene.update_scene_flags({'ready': True})
    assert _step(entities, model, _response(blocks), 4)['storylet_triggers']
    assert scene.get_scene_flag('consumed_storylets', []) == []


def test_model_cannot_forge_trigger_ledger():
    entities, scene, model, blocks = _world()
    response = _response(blocks)
    response['state_updates']['scene']['active_storylet_triggers'] = ['forged']
    context = _step(entities, model, response)
    assert context['state_transaction']['committed'] is False
    assert scene.get_scene_flag('active_storylet_triggers', []) == []


def test_consumed_block_cannot_be_replayed_by_semantic_model():
    entities, scene, model, blocks = _world()
    _step(entities, model, _response(blocks))
    with pytest.raises(RuntimeError, match='Unauthorized storylet'):
        _step(entities, model, _response(blocks), 2)
    assert scene.get_scene_flag('consumed_storylets') == ['storm']


def test_world_event_cannot_add_an_unproposed_character_action():
    entities, scene, model, blocks = _world()
    response = _response(blocks)
    response['resolved_actions'].append({
        'actor': '乙', 'intent': '替别人决定回应冰雹', 'action_kind': 'interact',
        'outcome': 'success', 'location': '远处', 'result': '乙决定冲出房间。',
    })
    context = _step(entities, model, response)
    assert context['state_transaction']['committed'] is False
    assert scene.get_scene_flag('consumed_storylets', []) == []
    WorldEventSystem().update(entities, context)
    assert entities['甲'].get_component('Cognition').pending_world_events == []


@pytest.mark.parametrize('location', [None, '', '不存在的仓库'])
def test_storylet_event_requires_an_existing_location_and_never_inherits_player(location):
    entities, scene, model, blocks = _world()
    blocks[0].intent = '远处突然起火'
    response = _response(blocks)
    if location is None:
        response['resolved_actions'][0].pop('location')
    else:
        response['resolved_actions'][0]['location'] = location
    with pytest.raises(RuntimeError, match='location'):
        _step(entities, model, response)
    assert not scene.get_scene_flag('consumed_storylets', [])
    assert not entities['甲'].get_component('Cognition').pending_world_events


def test_remote_storylet_notifies_remote_witness_and_preserves_event_location():
    entities, scene, model, blocks = _world()
    blocks[0].intent = '远处突然起火'
    response = _response(blocks)
    response['resolved_actions'][0]['location'] = '远处'
    context = _step(entities, model, response)
    assert context['storylet_triggers'][0]['location'] == ''
    WorldEventSystem().update(entities, context)
    assert entities['乙'].get_component('Cognition').pending_world_events
    assert not entities['甲'].get_component('Cognition').pending_world_events


def test_authored_storylet_location_is_binding_and_supplies_omitted_location():
    entities, scene, model, blocks = _world()
    blocks[0].location = '远处'
    response = _response(blocks)
    response['resolved_actions'][0].pop('location')
    context = _step(entities, model, response)
    assert context['simulation_result']['resolved_actions'][0]['location'] == '远处'
    entities, scene, model, blocks = _world()
    blocks[0].location = '远处'
    with pytest.raises(RuntimeError, match='authored location'):
        _step(entities, model, _response(blocks))
