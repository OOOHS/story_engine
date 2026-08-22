from types import SimpleNamespace

from src.story_engine.agents.actions import AgentAction
from src.story_engine.components.modifier_state import ModifierState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.simulation.modifiers import (
    MODIFIER_DEFINITIONS,
    ModifierDynamics,
)
from src.story_engine.systems.modifiers import ModifierSystem
from src.story_engine.systems.rendering import RenderingSystem


def _action(actor="甲", *, location="房间", visibility="public"):
    return {
        "actor": actor,
        "action_kind": "interact",
        "action_target": "",
        "outcome": "success",
        "location": location,
        "visibility": visibility,
        "result": "行动形成了明确的临时影响。",
    }


def _update(
    *,
    target="甲",
    source="甲",
    kind="exhausted",
    operation="apply",
    magnitude=0.5,
    reason="持续用力后明显疲惫",
    **extra,
):
    return {
        "operation": operation,
        "target": target,
        "source": source,
        "kind": kind,
        "magnitude": magnitude,
        "reason": reason,
        **extra,
    }


def _entities():
    gm = Entity("GameMaster")
    gm.add_component(
        SceneState(
            world_objects={"房间": {}},
            actor_states={
                "甲": {"location": "房间"},
                "乙": {"location": "房间"},
            },
        )
    )
    first = Entity("甲")
    first.add_component(ModifierState())
    second = Entity("乙")
    second.add_component(ModifierState())
    return {"GameMaster": gm, "甲": first, "乙": second}


def test_modifier_state_uses_host_stacking_and_deterministic_expiry():
    state = ModifierState()
    definition = MODIFIER_DEFINITIONS["exhausted"]

    first = state.apply(
        kind=definition.kind,
        description=definition.description,
        magnitude=0.5,
        current_step=0,
        duration_steps=definition.duration_steps,
        stacking=definition.stacking,
        max_stacks=definition.max_stacks,
        reason="第一次透支",
    )
    second = state.apply(
        kind=definition.kind,
        description=definition.description,
        magnitude=0.5,
        current_step=1,
        duration_steps=definition.duration_steps,
        stacking=definition.stacking,
        max_stacks=definition.max_stacks,
        reason="再次透支",
    )

    assert first is second
    assert second.stacks == 2
    assert second.intensity == 0.75
    assert second.expires_step == 5
    assert state.advance_to(4) == []
    assert state.advance_to(5)[0]["status"] == "expired"
    assert state.modifiers == {}


def test_agent_snapshot_exposes_condition_without_host_bookkeeping():
    state = ModifierState()
    definition = MODIFIER_DEFINITIONS["focused"]
    state.apply(
        kind=definition.kind,
        description=definition.description,
        magnitude=0.6,
        current_step=2,
        duration_steps=definition.duration_steps,
        stacking=definition.stacking,
        max_stacks=definition.max_stacks,
        reason="主动排除干扰",
        source="甲",
    )

    record = state.get_private_snapshot()["active"][0]

    assert record["kind"] == "focused"
    assert record["intensity"] == 0.6
    assert "provenance" not in record


def test_modifier_updates_require_committed_action_evidence_and_known_kind():
    entities = _entities()
    states = {
        name: entity.get_component("ModifierState")
        for name, entity in entities.items()
        if entity.get_component("ModifierState") is not None
    }
    dynamics = ModifierDynamics()

    applied, errors = dynamics.apply(
        modifier_states=states,
        scene_state=entities["GameMaster"].get_component("SceneState"),
        result={
            "resolved_actions": [_action("甲")],
            "modifier_updates": [
                _update(target="乙", source="丙", kind="unknown")
            ],
        },
        current_step=1,
    )

    assert applied == []
    assert any("source must be an existing actor or World" in error for error in errors)
    assert any("unknown modifier kind" in error for error in errors)
    assert any("lacks a committed source action" in error for error in errors)


def test_gm_cannot_choose_duration_or_stacks():
    entities = _entities()
    dynamics = ModifierDynamics()
    states = {
        "甲": entities["甲"].get_component("ModifierState"),
        "乙": entities["乙"].get_component("ModifierState"),
    }

    applied, errors = dynamics.apply(
        modifier_states=states,
        scene_state=entities["GameMaster"].get_component("SceneState"),
        result={
            "resolved_actions": [_action("甲")],
            "modifier_updates": [
                _update(duration_steps=999, stacks=8)
            ],
        },
        current_step=1,
    )

    assert applied == []
    assert errors == [
        "modifier_updates[0] contains host-owned fields: "
        "duration_steps, stacks"
    ]


def test_modifier_system_publishes_batch_atomically():
    entities = _entities()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [_action("甲")],
            "modifier_updates": [
                _update(target="甲", source="甲"),
                _update(target="乙", source="甲", kind="not_registered"),
            ],
        },
    }

    ModifierSystem().update(entities, context)

    assert context["modifier_updates"] == []
    assert context["modifier_errors"]
    assert entities["甲"].get_component("ModifierState").modifiers == {}
    assert entities["乙"].get_component("ModifierState").modifiers == {}


def test_external_modifier_uses_target_origin_observation_window():
    entities = _entities()
    scene = entities["GameMaster"].get_component("SceneState")
    scene.world_objects["走廊"] = {}
    scene.update_actor_state("乙", {"location": "走廊"})
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "actor_observation_windows": {
            "甲": {"locations": ["房间"]},
            "乙": {"locations": ["房间", "走廊"]},
        },
        "simulation_result": {
            "resolved_actions": [_action("甲", location="房间")],
            "modifier_updates": [
                _update(
                    target="乙",
                    source="甲",
                    kind="shaken",
                    reason="乙离开房间前目击了甲制造的冲击",
                )
            ],
        },
    }

    ModifierSystem().update(entities, context)

    assert context["modifier_errors"] == []
    assert "shaken" in entities["乙"].get_component("ModifierState").modifiers


def test_hidden_external_source_does_not_leak_identity_or_reason_to_target():
    entities = _entities()
    context = {
        "clock": SimpleNamespace(current_step=1),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [_action("甲", visibility="hidden")],
            "modifier_updates": [
                _update(
                    target="乙",
                    source="甲",
                    kind="shaken",
                    reason="甲暗中制造的冲击",
                    source_event="secret:attack",
                )
            ],
        },
    }

    ModifierSystem().update(entities, context)

    record = entities["乙"].get_component("ModifierState").modifiers["shaken"]
    assert record.source == ""
    assert record.source_event == ""
    assert "甲" not in record.reason
    assert record.provenance == {
        "source_kind": "resolved_action",
        "source_ref": "step:1:actor:甲",
    }
    assert "provenance" not in str(
        entities["乙"].get_component("ModifierState").get_private_snapshot()
    )


def test_visible_modifier_ignores_model_source_event_and_uses_committed_action():
    entities = _entities()
    context = {
        "clock": SimpleNamespace(current_step=3),
        "state_transaction": {"committed": True},
        "simulation_result": {
            "resolved_actions": [_action("甲")],
            "modifier_updates": [
                _update(
                    target="乙",
                    source="甲",
                    kind="inspired",
                    source_event="model:invented-cause",
                )
            ],
        },
    }

    ModifierSystem().update(entities, context)

    record = entities["乙"].get_component("ModifierState").modifiers["inspired"]
    assert record.source_event == "resolved_action:step:3:actor:甲"
    assert record.provenance["source_ref"] == "step:3:actor:甲"
def test_modifier_updates_are_private_and_removed_from_render_payload():
    visible = RenderingSystem()._build_visible_simulation(
        {
            "resolved_actions": [],
            "state_updates": {
                "scene": {},
                "world_objects": {},
                "actor_states": {},
            },
            "modifier_updates": [_update(target="甲")],
        },
        {"location": "房间", "visible_world": {}},
    )

    assert visible["modifier_updates"] == []


def test_every_character_has_modifier_state():
    entity = create_agent(
        name="甲",
        role="旅人",
        personality="平静",
        goals=[],
        agent_runtime="llm",
    )

    assert entity.get_component("ModifierState") is not None
