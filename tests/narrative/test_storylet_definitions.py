from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.narrative.storylet_definitions import (
    StoryletDefinitionAuthority,
    StoryletDefinitionLifecycle,
)
from src.story_engine.narrative.storylets import StoryletEngine
from src.story_engine.scenarios.config import ScenarioConfig, StoryletConfig
from src.story_engine.systems.input import InputSystem
from src.story_engine.systems.simulation import SimulationSystem


def _scenario(storylets=None):
    return ScenarioConfig(
        name="候选注册测试",
        default_agent_runtime="llm",
        description="用于测试 storylet_definition 候选注册。",
        environment="大厅",
        initial_state="甲乙都在大厅。",
        storylets=storylets or [],
    )


def _scene():
    return SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )


def _authorization(**updates):
    value = {
        "authorization_id": "director:betrayal_hint",
        "storylet_id": "betrayal_hint",
        "intent": "有人暗示背叛的迹象",
        "priority": 50,
        "one_shot": True,
        "tags": ["betrayal"],
        "not_before_step": 2,
        "expires_step": 2,
    }
    value.update(updates)
    return value


def test_authority_rejects_candidate_without_matching_authorization():
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "invented"},
        authorizations=[],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.request is None
    assert resolved.rejected == [
        "storylet_definition:unknown_authorization:invented"
    ]


def test_authority_compiles_canonical_payload_from_authorization():
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:betrayal_hint"},
        authorizations=[_authorization()],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.rejected == []
    assert resolved.request["storylet_id"] == "betrayal_hint"
    assert resolved.request["intent"] == "有人暗示背叛的迹象"
    assert resolved.request["one_shot"] is True
    assert resolved.request["authorization_id"] == "director:betrayal_hint"


def test_authority_rejects_invalid_payload_shape():
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:betrayal_hint"},
        authorizations=[_authorization(priority="not-a-number")],
        scene_state=_scene(),
        current_step=2,
    )

    assert resolved.request is None
    assert resolved.rejected[0].startswith(
        "storylet_definition:invalid_payload:director:betrayal_hint"
    )


def test_lifecycle_rejects_duplicate_of_static_storylet_id():
    scenario = _scenario(
        storylets=[StoryletConfig(storylet_id="betrayal_hint", intent="既有剧情点")]
    )
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:betrayal_hint"},
        authorizations=[_authorization()],
        scene_state=_scene(),
        current_step=2,
    )

    preparation = StoryletDefinitionLifecycle().prepare(
        scenario, _scene(), resolved.request
    )

    assert preparation.plan is None
    assert "already exists" in preparation.errors[0]


def test_lifecycle_prepare_and_stage_registers_dynamic_storylet():
    scenario = _scenario()
    scene = _scene()
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:betrayal_hint"},
        authorizations=[_authorization()],
        scene_state=scene,
        current_step=2,
    )

    lifecycle = StoryletDefinitionLifecycle()
    preparation = lifecycle.prepare(scenario, scene, resolved.request)
    assert preparation.errors == []
    assert preparation.plan is not None

    errors = lifecycle.stage(scene, preparation.plan)

    assert errors == []
    assert scene.get_scene_flag("dynamic_storylet_ids") == ["betrayal_hint"]
    assert scene.get_scene_flag("consumed_storylet_definition_authorizations") == [
        "director:betrayal_hint"
    ]
    dynamic = scene.get_scene_flag("dynamic_storylets")
    assert dynamic[0]["storylet_id"] == "betrayal_hint"


def test_lifecycle_enforces_max_dynamic_storylets_cap():
    scenario = _scenario()
    scene = _scene()
    scene.update_scene_flags({"max_dynamic_storylets": 1})
    resolved_first = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:first"},
        authorizations=[_authorization(authorization_id="director:first", storylet_id="first")],
        scene_state=scene,
        current_step=2,
    )
    lifecycle = StoryletDefinitionLifecycle()
    plan_first = lifecycle.prepare(scenario, scene, resolved_first.request).plan
    lifecycle.stage(scene, plan_first)

    resolved_second = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:second"},
        authorizations=[
            _authorization(authorization_id="director:second", storylet_id="second")
        ],
        scene_state=scene,
        current_step=2,
    )
    preparation_second = lifecycle.prepare(scenario, scene, resolved_second.request)

    assert preparation_second.plan is None
    assert any("max_dynamic_storylets" in error for error in preparation_second.errors)


def test_resolved_dynamic_storylet_is_visible_to_resolve_and_hit_detection():
    scenario = _scenario()
    scene = _scene()
    resolved = StoryletDefinitionAuthority().resolve(
        {"authorization_id": "director:betrayal_hint"},
        authorizations=[_authorization()],
        scene_state=scene,
        current_step=2,
    )
    lifecycle = StoryletDefinitionLifecycle()
    plan = lifecycle.prepare(scenario, scene, resolved.request).plan
    lifecycle.stage(scene, plan)

    engine = StoryletEngine()
    active = engine.resolve(scene, scenario, situation_packet={})

    assert [item["storylet_id"] for item in active] == ["betrayal_hint"]
    assert active[0]["one_shot"] is True

    hits = engine.detect_hits(
        active,
        {
            "resolved_actions": [
                {
                    "actor": "甲",
                    "source_storylet_id": "betrayal_hint",
                    "result": "甲暗示了背叛的迹象。",
                }
            ],
            "object_lifecycle": [],
        },
    )
    assert hits == ["betrayal_hint"]

    consumable = engine.consumable_hits(scenario, hits, active_storylets=active)
    assert consumable == ["betrayal_hint"]


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        from copy import deepcopy

        return deepcopy(self.scripted_result)


def test_runner_pipeline_authorizes_and_registers_a_new_storylet():
    """End-to-end: injected authorization -> GM cites it -> next-step resolve()
    sees the dynamic storylet, mirroring
    test_character_entry_authority.py::test_injected_entry_materializes_body_and_registered_agent_once
    for the storylet_definition candidate kind.
    """
    scenario = _scenario()
    scene = _scene()
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "World",
                        "intent": "叙事导演建议引入新的剧情点。",
                        "action_kind": "interact",
                        "outcome": "success",
                        "location": "大厅",
                        "visibility": "public",
                        "result": "空气中弥漫着不安的暗示。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "storylet_definition": {
                    "authorization_id": "director:betrayal_hint",
                },
            },
            scenario=scenario,
        )
    )
    gm.add_component(scene)
    gm.add_component(DramaState())
    entities = {"GameMaster": gm}
    context = {
        "intents": [],
        "inject_events": [
            {
                "event_id": "director:betrayal_hint",
                "intent": "叙事导演建议引入新的剧情点。",
                "storylet_definition": _authorization(),
            }
        ],
        "clock": type("Clock", (), {"current_step": 2})(),
        "player_name": None,
    }

    InputSystem().update(entities, context)
    SimulationSystem().update(entities, context)

    assert context["state_transaction"]["committed"] is True
    assert context["storylet_definition_rejections"] == []
    assert scene.get_scene_flag("dynamic_storylet_ids") == ["betrayal_hint"]
    audit = scene.get_scene_flag("narrative_candidate_audit")
    assert audit[-1]["kind"] == "storylet_definition"
    assert audit[-1]["accepted"] is True

    active = StoryletEngine().resolve(scene, scenario, situation_packet={})
    assert [item["storylet_id"] for item in active] == ["betrayal_hint"]
