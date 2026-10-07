"""Independent story agents persist, advance through settlement, then terminate."""
from copy import deepcopy
import json

import pytest

from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.story_tracking import StoryTracking
from src.story_engine.scenarios.config import StoryletConfig
from src.story_engine.session.savegame import load_session
from src.story_engine.systems.story_planning import StoryPlanningSystem
from tests.runtime.test_semantic_boundaries import director_session


def decision(status='active', progress='剧情已有进展，继续关注。', advance=None):
    return {'status': status, 'progress': progress, 'advance': advance}


class TrackerModel:
    def __init__(self, **replies):
        self.replies = {sid: iter(values) for sid, values in replies.items()}
        self.histories = []

    def generate_messages(self, history):
        self.histories.append(deepcopy(history))
        packet = json.loads(history[-1]['content'])
        result = next(self.replies[packet['storylet']['storylet_id']])
        if isinstance(result, Exception):
            raise result
        return {'content': json.dumps(result, ensure_ascii=False)}


def tracking_session(replies, *, one_shot=True):
    session, world, scene = director_session(None)
    session.entities['WorldHost'].get_component('StoryPlanner').interval_turns = 99
    session.scenario.storylets.append(StoryletConfig(storylet_id='bell', location='大厅',
        intent='大厅的钟响了一声。', conditions=[{'path': 'scene_flags.ready', 'value': True}],
        one_shot=one_shot))
    scene.update_scene_flags({'ready': True})
    tracking = StoryTracking(scenario=session.scenario)
    tracking._llm = TrackerModel(bell=replies)
    session.entities['WorldHost'].add_component(tracking)
    tracking.reconcile(scene)
    return session, world, scene, tracking


def test_tracker_advances_across_turns_and_original_condition_changes_then_exits(tmp_path):
    session, world, scene, tracking = tracking_session([
        decision(advance='门外传来敲门声。'), decision(progress='等待角色回应敲门声。'),
        decision('completed', '钟声与敲门声已交付，故事块完成。'),
    ])
    restored = None
    try:
        first = session.run_step(overrides={'甲': '等待。'})
        assert first['step_committed'], first.get('phase_errors')
        assert scene.get_scene_flag('consumed_storylets', []) == []
        assert tracking.agents['bell'].pending_advance == '门外传来敲门声。'
        assert tracking.agents['bell'].last_execution['outcome'] == 'success'
        saved_tracker = tracking.agents['bell'].model_dump()
        restored = load_session(session.save(tmp_path / 'tracking.storysave'))
        restored_tracking = restored.entities['WorldHost'].get_component('StoryTracking')
        assert restored_tracking.scenario is restored.scenario
        assert restored_tracking.agents['bell'].model_dump() == saved_tracker
        assert restored.entities['WorldHost'].get_component('StoryPlanner').narrative_messages

        scene.update_scene_flags({'ready': False})
        second = session.run_step(overrides={'甲': '等待。'})
        assert second['step_committed'], second.get('phase_errors')
        trigger = second['storylet_triggers'][0]
        assert trigger['continuation'] is True
        assert trigger['intent'] == '门外传来敲门声。'
        assert trigger['storylet_direction'] == '大厅的钟响了一声。'
        assert scene.get_scene_flag('consumed_storylets', []) == []
        assert tracking.agents['bell'].pending_advance is None
        assert scene.get_scene_flag('active_storylet_triggers') == ['bell']

        third = session.run_step(overrides={'甲': '等待。'})
        assert third['step_committed'] and not third['storylet_triggers']
        assert tracking.agents['bell'].status == 'completed'
        assert scene.get_scene_flag('consumed_storylets') == ['bell']
        assert scene.get_scene_flag('active_storylet_triggers') == []
        fourth = session.run_step(overrides={'甲': '等待。'})
        assert fourth['step_committed'] and not fourth['storylet_triggers']
        assert len(tracking._llm.histories) == 3
        # The shared ledger is read incrementally into this agent's own history.
        second_packet = json.loads(tracking._llm.histories[1][-1]['content'])
        assert len(second_packet['new_player_narrative_messages']) == 2
        assert tracking._llm.histories[1][:3] == tracking.agents['bell'].conversation[:3]
    finally:
        if restored:
            restored.close()
        session.close()


def test_independent_conversations_and_failed_tracker_keep_unread_narrative():
    scene = SceneState()
    tracking = StoryTracking()
    for sid in ('a', 'b'):
        tracking.register(StoryletConfig(storylet_id=sid, intent=f'{sid}的剧情方向'))
    tracking._llm = TrackerModel(
        a=[RuntimeError('model unavailable'), decision('abandoned', '线索已失去意义。')],
        b=[decision(progress='b独立推进。'), decision('completed', 'b已达成。')])
    story = [{'role': 'world', 'content': '开场'}]
    statuses = tracking.track(story, '1', scene)
    assert statuses['a']['status'] == 'retry_pending'
    assert tracking.agents['a'].fed_message_count == 0
    assert tracking.agents['a'].conversation == []
    assert tracking.agents['a'].pending_advance == 'a的剧情方向'
    assert tracking.agents['b'].fed_message_count == 1
    assert tracking.agents['b'].progress == 'b独立推进。'
    assert tracking.track(story, '1', scene) == {}
    story.append({'role': 'player', 'content': '继续调查'})
    tracking.track(story, '2', scene)
    a_history, b_history = tracking._llm.histories[-2:]
    assert len(json.loads(a_history[-1]['content'])['new_player_narrative_messages']) == 2
    assert len(json.loads(b_history[-1]['content'])['new_player_narrative_messages']) == 1
    assert 'b独立推进' not in json.dumps(a_history, ensure_ascii=False)
    assert tracking.agents['a'].closed and tracking.agents['b'].closed
    assert tracking.track(story, '3', scene) == {}


@pytest.mark.parametrize('invalid', [
    {}, decision('invented'), decision(progress=' '), decision(advance=' '),
    decision('completed', advance='尚未执行的变化'), {'status': 'active', 'progress': '进展', 'state_updates': {}},
])
def test_invalid_tracker_output_keeps_conversation_and_world_unchanged(invalid):
    tracking = StoryTracking()
    scene = SceneState()
    tracker = tracking.register(StoryletConfig(storylet_id='s', intent='未来方向'))
    tracking._llm = TrackerModel(s=[invalid])
    assert tracking.track([{'role': 'world', 'content': '开场'}], '1', scene)['s']['status'] == 'retry_pending'
    assert tracker.conversation == [] and tracker.fed_message_count == 0
    assert tracker.status == 'waiting' and tracker.pending_advance == '未来方向'
    assert scene.scene_flags == {}


def test_world_failure_restores_pending_advance_and_tracker_receipt():
    session, world, scene, tracking = tracking_session([decision(advance='门外传来敲门声。')])
    try:
        assert session.run_step(overrides={'甲': '等待。'})['step_committed']
        before = tracking.model_dump()
        real_generate = world.generate

        def unavailable(prompt):
            if prompt.startswith('## 行动语义解释'):
                return real_generate(prompt)
            raise RuntimeError('settlement unavailable')

        world.generate = unavailable
        result = session.run_step(overrides={'甲': '等待。'})
        assert not result['step_committed']
        assert tracking.model_dump() == before
    finally:
        session.close()


def test_memory_delivery_retry_does_not_repeat_tracker_call(monkeypatch):
    from src.story_engine.systems.memory import MemorySystem
    session, world, scene, tracking = tracking_session([decision(advance='门外传来敲门声。')])
    original = MemorySystem.update
    calls = []

    def fail_once(self, entities, context):
        if not calls:
            calls.append(1)
            raise RuntimeError('memory delivery unavailable')
        return original(self, entities, context)

    monkeypatch.setattr(MemorySystem, 'update', fail_once)
    try:
        result = session.run_step(overrides={'甲': '等待。'})
        assert result['step_committed'] and result['delivery_pending']
        before = tracking.model_dump()
        assert len(tracking._llm.histories) == 1
        assert not session.retry_delivery()['delivery_pending']
        assert len(tracking._llm.histories) == 1
        assert tracking.model_dump() == before
    finally:
        session.close()


def test_director_registration_creates_independent_tracker_and_returns_summary():
    from src.story_engine.components.story_planner import StoryPlanner
    from src.story_engine.core.entity import Entity
    from tests.narrative.test_narrative_director_candidates import Model, proposal_call, delivered
    host = Entity('WorldHost')
    host.add_component(SceneState())
    planner = StoryPlanner(interval_turns=1)
    planner._llm = Model([{'tool_calls': [proposal_call()]}, {'content': '草案提出。'}, {'content': '继续观察。'}])
    host.add_component(planner)
    tracking = StoryTracking()
    tracking._llm = TrackerModel(letter=[decision(progress='等待调查。'), decision('abandoned', '线索失去意义。')])
    host.add_component(tracking)
    system = StoryPlanningSystem()
    system.update({'WorldHost': host}, delivered('1'))
    assert list(tracking.agents) == ['letter']
    assert tracking.agents['letter'].conversation
    assert '等待调查' not in json.dumps(planner.conversation, ensure_ascii=False)
    system.update({'WorldHost': host}, delivered('2'))
    packet = json.loads(planner._llm.histories[-1][-1]['content'])
    assert packet['storylet_tracking']['letter']['progress'] == '等待调查。'
    assert tracking.agents['letter'].status == 'abandoned'


def test_older_production_save_adds_trackers_and_keeps_consumed_blocks_closed(tmp_path):
    session, world, scene = director_session(None)
    session.scenario.simulation_mode = 'llm'
    session.scenario.storylets.append(StoryletConfig(storylet_id='done', intent='已经发生', one_shot=True))
    scene.update_scene_flags({'consumed_storylets': ['done']})
    restored = None
    try:
        restored = load_session(session.save(tmp_path / 'old.storysave'))
        tracking = restored.entities['WorldHost'].get_component('StoryTracking')
        assert tracking.agents['done'].closed
        assert tracking.agents['done'].pending_advance is None
    finally:
        if restored:
            restored.close()
        session.close()


def test_role_suggestion_starts_tracker_lifetime_without_claiming_a_world_event():
    from tests.runtime.test_storylet_semantic_execution import StoryWorld
    session, world, scene, tracking = tracking_session([
        decision(advance='在角色回应后继续展开争议。'), decision('abandoned', '角色选择离开，方向失去意义。'),
    ])
    model = StoryWorld()
    model.outcome = 'deferred'
    model.suggest = True
    session.entities['WorldHost'].get_component('SimulationControl')._llm = model
    try:
        first = session.run_step(overrides={'甲': '等待。'})
        assert first['step_committed'], first.get('phase_errors')
        assert first['simulation_result']['storylet_hits'] == []
        assert scene.get_scene_flag('active_storylet_triggers') == ['bell']
        assert not any(e.get_component('WorldEventFact') for e in session.entities.values())
        scene.update_scene_flags({'ready': False})
        model.suggest = False
        second = session.run_step(overrides={'甲': '等待。'})
        assert second['step_committed'], second.get('phase_errors')
        assert second['storylet_triggers'][0]['continuation'] is True
        assert tracking.agents['bell'].status == 'abandoned'
        assert scene.get_scene_flag('consumed_storylets', []) == []
        assert scene.get_scene_flag('active_storylet_triggers') == []
    finally:
        session.close()
