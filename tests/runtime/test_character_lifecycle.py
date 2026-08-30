from typing import Any, Dict

from pydantic import Field

from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.character_lifecycle import CharacterLifecycle
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.systems.simulation import SimulationSystem


class SimulationControl(Component):
    scripted_result: Dict[str, Any] = Field(default_factory=dict)
    scenario: Any = None

    def simulate(self, _input_payload):
        return self.scripted_result


def test_dynamic_character_enters_entity_world_and_agent_registry_together():
    lifecycle = CharacterLifecycle()
    scene = SceneState(
        world_objects={"酒馆": {}, "街道": {}},
        actor_states={"玩家": {"location": "酒馆"}},
    )
    entities = {}
    registry = AgentRegistry()
    prepared = lifecycle.prepare(
        entities,
        scene,
        {
            "name": "信使",
            "role": "城外信使",
            "personality": "疲惫但警觉",
            "goals": ["把信交给玩家"],
            "location": "街道",
            "initial_state": {"stance": "running", "carrying_letter": True},
            "initial_beliefs": [
                {"statement": "有人正在追赶自己", "confidence": 0.7, "source": "脚步声"}
            ],
            "initial_obligations": [
                {
                    "obligation_id": "deliver_letter",
                    "title": "交出密信",
                    "due_step": 4,
                }
            ],
        },
        player_name="玩家",
        agent_registry=registry,
        agent_runtime="llm",
    )
    assert prepared.errors == []
    assert lifecycle.stage(scene, prepared.plan) == []
    spawned = lifecycle.finalize(
        entities,
        prepared.plan,
        register_agent=lambda entity: registry.register(entity, object()),
        unregister_agent=registry.unregister,
        agent_registry=registry,
    )

    assert spawned == ["信使"]
    assert "信使" in entities
    assert scene.get_actor_state("信使")["location"] == "街道"
    assert scene.get_actor_state("信使")["carrying_letter"] is True
    assert registry.is_registered("信使") is True
    assert scene.get_scene_flag("dynamic_character_names") == ["信使"]
    assert entities["信使"].get_component("Cognition").beliefs[0]["statement"] == "有人正在追赶自己"
    assert "deliver_letter" in entities["信使"].get_component("ObligationState").obligations


def test_invalid_spawn_location_falls_back_to_player_location():
    scene = SceneState(
        world_objects={"酒馆": {}},
        actor_states={"玩家": {"location": "酒馆"}},
    )
    prepared = CharacterLifecycle().prepare(
        {},
        scene,
        {"name": "陌生人", "location": "不存在的月球基地"},
        player_name="玩家",
        agent_runtime="llm",
    )

    assert prepared.errors == []
    assert prepared.plan.actor_state["location"] == "酒馆"
    assert "陌生人" not in scene.actor_states


def test_dynamic_character_limit_prevents_unbounded_llm_spawning():
    scene = SceneState(
        world_objects={"广场": {}},
        actor_states={"玩家": {"location": "广场"}},
        scene_flags={
            "max_dynamic_characters": 1,
            "dynamic_character_names": ["先来者"],
        },
    )

    prepared = CharacterLifecycle().prepare(
        {},
        scene,
        {"name": "后来者", "location": "广场"},
        player_name="玩家",
        agent_runtime="llm",
    )

    assert prepared.plan is None
    assert "spawn_character exceeds max_dynamic_characters" in prepared.errors
    assert "后来者" not in scene.actor_states


def test_spawn_without_any_valid_location_is_rejected():
    scene = SceneState(world_objects={"广场": {}}, actor_states={})

    prepared = CharacterLifecycle().prepare(
        {},
        scene,
        {"name": "幽灵", "location": "不存在"},
        player_name=None,
        agent_runtime="llm",
    )

    assert prepared.plan is None
    assert "spawn_character has no valid location" in prepared.errors
    assert "幽灵" not in scene.actor_states






def _spawn_authorization():
    return {
        "authorization_id": "messenger-entry",
        "name": "信使",
        "role": "城外信使",
        "location": "酒馆",
        "initial_state": {},
        "profile_mode": "semantic",
        "not_before_step": 0,
        "expires_step": 0,
    }


def _simulation_entities(result):
    gm = Entity("GameMaster")
    scene = SceneState(
        description="提交前",
        world_objects={"酒馆": {}},
        actor_states={"玩家": {"location": "酒馆"}},
    )
    drama = DramaState(tension=0.4)
    scenario = ScenarioConfig(
        name="动态角色事务测试",
        default_agent_runtime="llm",
        description="验证动态角色加入与事务回滚。",
        environment="酒馆",
        initial_state="信使尚未到达。",
    )
    gm.add_component(SimulationControl(scripted_result=result, scenario=scenario))
    gm.add_component(scene)
    gm.add_component(drama)
    return {"GameMaster": gm}, scene, drama


def _spawn_result():
    return {
        "resolved_actions": [
            {
                "actor": "World",
                "outcome": "success",
                "location": "酒馆",
                "visibility": "public",
                "result": "信使推门进入酒馆。",
            }
        ],
        "state_updates": {
            "scene": {"description": "信使已经抵达"},
            "world_objects": {},
            "actor_states": {},
        },
        "relationship_updates": [],
        "knowledge_updates": [],
        "object_lifecycle": [],
        "tension_delta": 0.2,
        "spawn_character": {
            "authorization_id": "messenger-entry",
            "name": "信使",
            "role": "城外信使",
            "personality": "疲惫而谨慎",
            "goals": ["交出密信"],
            "location": "酒馆",
        },
    }




def test_agent_registration_failure_rolls_back_entire_world_transaction():
    entities, scene, drama = _simulation_entities(_spawn_result())
    registry = AgentRegistry()

    def failing_register(entity):
        registry.register(entity, object())
        raise RuntimeError("runtime factory unavailable")

    context = {
        "intents": [],
        "agent_registry": registry,
        "register_agent": failing_register,
        "unregister_agent": registry.unregister,
        "character_spawn_authorizations": [_spawn_authorization()],
    }

    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is False
    assert context["simulation_result"]["transaction_rejected"] is True
    assert context["simulation_result"]["resolved_actions"] == []
    assert context["spawned_characters"] == []
    assert "信使" not in entities
    assert "信使" not in scene.actor_states
    assert scene.get_scene_flag("dynamic_character_names") is None
    assert scene.get_scene_flag("consumed_character_entry_authorizations") is None
    assert scene.description == "提交前"
    assert drama.tension == 0.4
    assert len(registry) == 0
