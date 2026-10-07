from src.story_engine.agents.subject import build_subject_messages
from src.story_engine.agents.types import AgentPerception
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.story_planner import StoryPlanner
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.world_events import WorldEventSystem


def world_context(committed=True, outcome="success", visibility="local"):
    host = Entity("WorldHost")
    scene = SceneState(world_objects={"山顶": {}, "营地": {}}, actor_states={
        "瞭望员": {"location": "山顶"}, "玩家": {"location": "营地"},
    })
    host.add_component(scene)
    actors = {name: Entity(name) for name in scene.actor_states}
    for actor in actors.values():
        actor.add_component(Cognition())
    entities = {"WorldHost": host, **actors}
    context = {
        "clock": type("Clock", (), {"current_step": 1})(),
        "state_transaction": {"committed": committed},
        "intents": [{"actor": "World", "intent": "山顶升起信号弹", "event_id": "flare", "location": "山顶"}],
        "simulation_result": {"resolved_actions": [{
            "actor": "World", "intent": "山顶升起信号弹", "outcome": outcome,
            "location": "山顶", "visibility": visibility, "result": "红色信号弹照亮了山顶。",
        }]},
    }
    return scene, entities, context


def test_world_occurrence_is_received_only_after_commit_and_only_by_witnesses():
    scene, entities, context = world_context()
    system = InputSystem()
    perception = system.build_agent_perception(
        entity=entities["瞭望员"], scene_state=scene, intents_buffer=context["intents"],
        context=context, activation_scope="foreground",
    )
    assert perception.world_signals == []
    WorldEventSystem().update(entities, context)
    watcher = entities["瞭望员"].get_component("Cognition")
    assert watcher.knows_event("world-action:flare")
    assert not entities["玩家"].get_component("Cognition").knows_event("world-action:flare")
    assert watcher.pending_world_events == ["world-action:flare"]
    assert entities["WorldEvent:world-action:flare"].get_component("WorldEventFact").statement == "红色信号弹照亮了山顶。"


def test_rejected_world_occurrence_cannot_become_a_character_fact():
    for committed, outcome in [(False, "success"), (True, "blocked")]:
        _, entities, context = world_context(committed=committed, outcome=outcome)
        WorldEventSystem().update(entities, context)
        assert "WorldEvent:world-action:flare" not in entities
        assert entities["瞭望员"].get_component("Cognition").pending_world_events == []


def test_hidden_world_occurrence_does_not_notify_remote_or_local_characters():
    _, entities, context = world_context(visibility="hidden")
    WorldEventSystem().update(entities, context)
    for actor in ("瞭望员", "玩家"):
        assert not entities[actor].get_component("Cognition").knows_event("world-action:flare")




def test_story_hints_have_no_character_inbox_api():
    assert not hasattr(SceneState, "queue_director_signal")
    perception = AgentPerception(actor_name="瞭望员", step=1)
    assert "director_signals" not in vars(perception)
