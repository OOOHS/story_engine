from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.environment.character_entries import CharacterEntryAuthority
from src.story_engine.environment.runner import Runner
from src.story_engine.agents.registry import AgentRegistry
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scene():
    return SceneState(
        world_objects={"酒馆": {}},
        actor_states={"玩家": {"location": "酒馆"}},
    )


def _authorization(**updates):
    value = {
        "authorization_id": "arrival:messenger",
        "name": "信使",
        "role": "城外信使",
        "location": "酒馆",
        "initial_state": {"carrying_letter": True},
        "personality": "疲惫",
        "goals": ["交出信件"],
        "profile_mode": "fixed",
        "not_before_step": 2,
        "expires_step": 2,
    }
    value.update(updates)
    return value


def test_entry_authority_rejects_unissued_semantic_spawn():
    resolved = CharacterEntryAuthority().resolve(
        {"authorization_id": "invented", "name": "刺客"},
        authorizations=[],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.request is None
    assert resolved.rejected == [
        "spawn_character:unknown_authorization:invented"
    ]


def test_entry_authority_rejects_duplicate_or_consumed_capability():
    scene = _scene()
    duplicated = CharacterEntryAuthority().resolve(
        {"authorization_id": "arrival:messenger"},
        authorizations=[_authorization(), _authorization(role="另一个身份")],
        scene_state=scene,
        current_step=2,
    )
    scene.update_scene_flags(
        {"consumed_character_entry_authorizations": ["arrival:messenger"]}
    )
    consumed = CharacterEntryAuthority().resolve(
        {"authorization_id": "arrival:messenger"},
        authorizations=[_authorization()],
        scene_state=scene,
        current_step=2,
    )

    assert duplicated.rejected == [
        "spawn_character:ambiguous_authorization:arrival:messenger"
    ]
    assert consumed.rejected == [
        "spawn_character:consumed_authorization:arrival:messenger"
    ]


def test_fixed_entry_uses_host_identity_location_and_initial_facts():
    authorization = _authorization()
    resolved = CharacterEntryAuthority().resolve(
        {
            "authorization_id": "arrival:messenger",
            "name": "GM伪造姓名",
            "role": "国王",
            "location": "不存在的王宫",
            "initial_state": {"owns_castle": True},
            "personality": "全知全能",
            "goals": ["统治世界"],
        },
        authorizations=[authorization],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.rejected == []
    assert resolved.request["name"] == "信使"
    assert resolved.request["role"] == "城外信使"
    assert resolved.request["location"] == "酒馆"
    assert resolved.request["initial_state"] == {"carrying_letter": True}
    assert resolved.request["personality"] == "疲惫"
    assert resolved.request["goals"] == ["交出信件"]


def test_semantic_profile_allows_prose_but_not_world_facts():
    resolved = CharacterEntryAuthority().resolve(
        {
            "authorization_id": "arrival:messenger",
            "personality": "说话很快，但会先确认门外是否有人跟踪",
            "goals": ["找到收信人"],
            "initial_state": {"has_weapon": True},
        },
        authorizations=[_authorization(profile_mode="semantic")],
        scene_state=_scene(),
        current_step=2,
    )

    assert "门外" in resolved.request["personality"]
    assert resolved.request["goals"] == ["找到收信人"]
    assert resolved.request["initial_state"] == {"carrying_letter": True}


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


def test_unauthorized_gm_spawn_is_ignored_without_losing_valid_action():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "玩家",
                        "intent": "等一会儿",
                        "action_kind": "wait",
                        "outcome": "success",
                        "location": "酒馆",
                        "visibility": "local",
                        "result": "玩家等了一会儿。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "spawn_character": {
                    "authorization_id": "invented",
                    "name": "凭空出现的人",
                    "location": "酒馆",
                },
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    context = {
        "intents": [
            {
                "actor": "玩家",
                "intent": "等一会儿",
                "action_kind": "wait",
                "location": "酒馆",
            }
        ],
        "character_spawn_authorizations": [],
    }

    SimulationSystem().update({"GameMaster": gm}, context)

    assert context["state_transaction"]["committed"] is True
    assert context["spawned_characters"] == []
    assert "凭空出现的人" not in scene.actor_states
    assert context["character_entry_rejections"] == [
        "spawn_character:unknown_authorization:invented"
    ]
    assert context["simulation_result"]["resolved_actions"][0]["actor"] == "玩家"


def test_injected_world_event_issues_one_step_entry_authorization():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(SimulationControl())
    gm.add_component(scene)
    context = {
        "intents": [],
        "inject_events": [
            {
                "event_id": "arrival:messenger",
                "intent": "一名信使抵达酒馆。",
                "character_entry": _authorization(
                    not_before_step=2,
                    expires_step=2,
                ),
            }
        ],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": "玩家",
    }

    InputSystem().update({"GameMaster": gm}, context)

    assert context["character_spawn_authorizations"] == [
        {
            **_authorization(not_before_step=2, expires_step=2),
            "source": "injected",
        }
    ]
    assert context["intents"][0]["actor"] == "World"


def test_injected_entry_materializes_body_and_registered_agent_once():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "World",
                        "intent": "一名信使抵达酒馆。",
                        "action_kind": "interact",
                        "outcome": "success",
                        "location": "酒馆",
                        "visibility": "public",
                        "result": "信使推门进入酒馆。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "spawn_character": {
                    "authorization_id": "arrival:messenger",
                    "personality": "疲惫但警觉",
                    "goals": ["找到收信人"],
                },
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm}
    registry = AgentRegistry()
    context = {
        "intents": [],
        "inject_events": [
            {
                "event_id": "arrival:messenger",
                "intent": "一名信使抵达酒馆。",
                "location": "酒馆",
                "character_entry": _authorization(
                    profile_mode="semantic",
                    not_before_step=2,
                    expires_step=2,
                ),
            }
        ],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": "玩家",
        "agent_registry": registry,
        "register_agent": lambda entity: registry.register(entity, object()),
        "unregister_agent": registry.unregister,
    }

    InputSystem().update(entities, context)
    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["spawned_characters"] == ["信使"]
    assert scene.get_actor_state("信使")["carrying_letter"] is True
    assert entities["信使"].get_component("Cognition").beliefs == []
    assert [
        record.title
        for record in entities["信使"].get_component("GoalState").goals.values()
    ] == ["找到收信人"]
    assert registry.is_registered("信使") is True
    assert scene.get_scene_flag("consumed_character_entry_authorizations") == [
        "arrival:messenger"
    ]


def test_runner_carries_entry_capability_across_discrete_action_completion():
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "World",
                        "intent": "一名信使抵达酒馆。",
                        "action_kind": "interact",
                        "outcome": "success",
                        "location": "酒馆",
                        "visibility": "public",
                        "result": "信使推门进入酒馆。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "spawn_character": {
                    "authorization_id": "arrival:messenger",
                    "personality": "一路赶来，仍保持警觉",
                    "goals": ["找到收信人"],
                },
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    runner = Runner(random_seed="entry-runner")
    runner.add_entity(gm)
    player = create_agent(
        "玩家",
        "在场者",
        "平静",
        [],
    agent_runtime="llm")
    player.get_component("AgentController").autonomous = False
    runner.add_entity(player)
    runner.agent_registry.register(player, object())
    authorization = _authorization(profile_mode="semantic")
    authorization.pop("not_before_step")
    authorization.pop("expires_step")

    context = runner.run_step(
        inject_events=[
            {
                "event_id": "arrival:messenger",
                "intent": "一名信使抵达酒馆。",
                "location": "酒馆",
                "character_entry": authorization,
            }
        ],
        player_name="玩家",
    )

    assert context["state_transaction"]["committed"] is True
    assert context["spawned_characters"] == ["信使"]
    assert runner.clock.current_step > 0
    assert runner.agent_registry.is_registered("信使") is True
    assert scene.get_scene_flag("consumed_character_entry_authorizations") == [
        "arrival:messenger"
    ]
