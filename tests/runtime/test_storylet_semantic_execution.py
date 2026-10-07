"""Future content is interpreted in world settlement; suggestions stay optional."""
import json

import pytest

from src.story_engine.agents.subject import build_subject_messages
from src.story_engine.components.cognition import Cognition
from src.story_engine.narrative.storylet_execution import StoryletExecution
from src.story_engine.scenarios.config import StoryletConfig
from src.story_engine.session.savegame import load_session
from src.story_engine.systems.input import InputSystem
from tests.runtime.test_semantic_boundaries import director_session, WorldModel


class StoryWorld(WorldModel):
    def __init__(self):
        super().__init__(RuntimeError('separate review must not run'))
        self.outcome = 'inactive'
        self.suggest = False
        self.fail = False
        self.semantic_packets = []

    def generate(self, prompt):
        if self.fail and not prompt.startswith('## 行动语义解释'):
            raise RuntimeError('world service unavailable')
        if prompt.startswith('## 提交前语义校对'):
            self.semantic_packets.append(json.loads(prompt.split('## 候选提交数据\n')[1]))
            return {'content': json.dumps({'valid': True, 'issues': []})}
        if prompt.startswith('## 行动语义解释'):
            return super().generate(prompt)
        intents = json.loads(prompt.split('## 本轮意图\n')[1].split('\n## 宿主允许')[0])
        self.batches.append(intents)
        actions = []
        suggestions = []
        for intent in intents:
            story = intent.get('source_storylet_id')
            action = {'actor': intent['actor'], 'intent': intent['intent'],
                      'outcome': self.outcome if story else 'success',
                      'location': '大厅', 'visibility': 'local',
                      'result': '继承权争议已公开。' if story else '甲继续等待。',
                      'source_storylet_id': story or ''}
            actions.append(action)
            if story and self.suggest:
                suggestions.append({'source_storylet_id': story, 'recipient': '甲',
                                    'text': '可以考虑询问兄长的主张，也可以继续观察。'})
        return {'content': json.dumps({'resolved_actions': actions,
                                      'director_suggestions': suggestions}, ensure_ascii=False)}


def story_session(one_shot=True):
    session, _, scene = director_session(None)
    model = StoryWorld()
    session.entities['WorldHost'].get_component('SimulationControl')._llm = model
    # Focus execution on one authored direction, including a prospective role choice.
    session.entities['WorldHost'].get_component('StoryPlanner').interval_turns = 99
    session.scenario.storylets.append(StoryletConfig(
        storylet_id='inheritance', trigger='继承权争议已经公开，且甲有机会回应',
        intent='甲与兄长就继承权发生争执，家族可能走向分裂。', one_shot=one_shot))
    return session, model, scene


def test_natural_trigger_and_role_suggestion_use_world_call_and_survive_save(tmp_path):
    session, model, scene = story_session()
    restored = None
    try:
        first = session.run_step(overrides={'甲': '等待。'})
        assert first['step_committed'], first.get('phase_errors')
        assert model.batches[-1][-1]['trigger'].startswith('继承权争议')
        assert scene.get_scene_flag('consumed_storylets', []) == []
        assert not any(e.get_component('WorldEventFact') for e in session.entities.values())
        assert '继承权争议已公开' not in first['rendered_text']

        model.outcome = 'deferred'
        model.suggest = True
        second = session.run_step(overrides={'甲': '等待。'})
        assert second['step_committed'], second.get('phase_errors')
        cognition = session.entities['甲'].get_component('Cognition')
        queue = cognition.pending_director_suggestions
        assert len(queue) == 1 and queue[0]['source'] == 'director'
        assert cognition.beliefs == [] and cognition.pending_world_events == []
        assert scene.get_scene_flag('consumed_storylets', []) == []
        perception = session.runner.get_agent_perception('甲')
        message = next(m for m in build_subject_messages(perception) if m.kind == 'director_suggestion')
        assert message.payload['text'] == queue[0]['text']
        assert 'director_suggestions' in perception.manual_decision_context()
        assert model.semantic_packets[-1]['candidate']['director_suggestions']

        restored = load_session(session.save(tmp_path / 'suggestion.storysave'))
        assert restored.entities['甲'].get_component('Cognition').pending_director_suggestions == queue

        # A failed next step restores the receipt acknowledged during Input.
        model.fail = True
        failed = session.run_step(overrides={'甲': '继续等待。'})
        assert not failed['step_committed']
        assert cognition.pending_director_suggestions == queue
        model.fail = False
        model.suggest = False
        model.outcome = 'deferred'
        ignored = session.run_step(overrides={'甲': '继续等待。'})
        assert ignored['step_committed']
        assert cognition.pending_director_suggestions == []
        assert not scene.get_actor_state('甲').get('activity')

        model.outcome = 'success'
        finished = session.run_step(overrides={'甲': '等待。'})
        assert finished['step_committed']
        assert scene.get_scene_flag('consumed_storylets') == ['inheritance']
        assert any(e.get_component('WorldEventFact') for e in session.entities.values())
    finally:
        if restored:
            restored.close()
        session.close()


def test_reusable_natural_trigger_rearms_after_semantic_condition_turns_false():
    session, model, scene = story_session(one_shot=False)
    try:
        model.outcome = 'success'
        assert session.run_step(overrides={'甲': '等待。'})['step_committed']
        assert scene.get_scene_flag('active_storylet_triggers') == ['inheritance']
        model.outcome = 'deferred'
        result = session.run_step(overrides={'甲': '等待。'})
        assert result['step_committed']
        assert model.batches[-1][-1]['already_triggered'] is True
        model.outcome = 'inactive'
        assert session.run_step(overrides={'甲': '等待。'})['step_committed']
        assert scene.get_scene_flag('active_storylet_triggers') == []
        model.outcome = 'success'
        assert session.run_step(overrides={'甲': '等待。'})['step_committed']
        assert scene.get_scene_flag('active_storylet_triggers') == ['inheritance']
    finally:
        session.close()


@pytest.mark.parametrize('suggestion', [
    {'source_storylet_id': 'forged', 'recipient': '甲', 'text': '建议'},
    {'source_storylet_id': 's', 'recipient': 'unknown', 'text': '建议'},
    {'source_storylet_id': 's', 'recipient': '甲', 'text': ''},
])
def test_suggestion_structural_boundary_rejects_invalid_references(suggestion):
    from src.story_engine.components.scene_state import SceneState
    scene = SceneState(actor_states={'甲': {}})
    with pytest.raises(RuntimeError):
        StoryletExecution().validate([{'source_storylet_id': 's', 'intent': '剧情方向'}], {
            'resolved_actions': [{'actor': 'World', 'source_storylet_id': 's',
                                  'intent': '剧情方向', 'outcome': 'deferred'}],
            'director_suggestions': [suggestion],
        }, scene)


def test_only_delivered_suggestion_receipts_are_acknowledged():
    from src.story_engine.core.entity import Entity
    from src.story_engine.agents.types import AgentPerception
    actor = Entity('甲')
    cognition = Cognition(pending_director_suggestions=[{'suggestion_id': 'sent'}, {'suggestion_id': 'later'}])
    actor.add_component(cognition)
    perception = AgentPerception(actor_name='甲', step=1,
        private_cognition={'director_suggestions': list(cognition.pending_director_suggestions)})
    InputSystem._acknowledge_perception_attention(actor, perception, suggestion_ids=['sent'])
    assert cognition.pending_director_suggestions == [{'suggestion_id': 'later'}]


def test_hermes_retains_suggestions_excluded_by_message_budget():
    from src.story_engine.agents.hermes_runtime import HermesCharacterAgent
    from src.story_engine.agents.types import AgentPerception
    from src.story_engine.prefabs.templates import create_agent
    actor = create_agent(name='甲', role='居民', personality='谨慎', goals=[], agent_runtime='hermes')
    suggestion = {'suggestion_id': 'suggestion-1', 'source_storylet_id': 'inheritance',
                  'source': 'director', 'step': 1, 'text': '可以询问兄长。'}
    cognition = actor.get_component('Cognition')
    cognition.pending_director_suggestions = [suggestion]
    packets = []

    class Conversation:
        def run_subject_turn(self, packet):
            packets.append(packet)
            return {'protocol_version': 1, 'agent_id': actor.id,
                    'content': json.dumps({'action': '继续观察。'})}

    runtime = HermesCharacterAgent(lambda entity, cfg: Conversation(), {'message_limit': 1})
    private = {'director_suggestions': [suggestion], 'pending_world_event_records': [{
        'event_id': 'urgent', 'statement': '火焰逼近。', 'urgency': 'critical', 'updated_step': 1,
    }]}
    perception = AgentPerception(actor_name='甲', step=1, private_cognition=private)
    decision = runtime.decide(actor, perception)
    assert decision.metadata['delivered_director_suggestions'] == []
    InputSystem._acknowledge_perception_attention(actor, perception,
        suggestion_ids=decision.metadata['delivered_director_suggestions'])
    assert cognition.pending_director_suggestions == [suggestion]
    private['pending_world_event_records'] = []
    decision = runtime.decide(actor, perception)
    assert decision.metadata['delivered_director_suggestions'] == ['suggestion-1']
    assert any(m['kind'] == 'director_suggestion' for m in packets[-1]['messages'])
    InputSystem._acknowledge_perception_attention(actor, perception,
        suggestion_ids=decision.metadata['delivered_director_suggestions'])
    assert cognition.pending_director_suggestions == []
    runtime.close()
