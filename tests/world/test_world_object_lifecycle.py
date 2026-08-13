from copy import deepcopy

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.world_transaction import WorldStateTransaction
from src.story_engine.systems.rendering import RenderingSystem


def _scene():
    return SceneState(
        world_objects={
            "大厅": {
                "connected_to": ["书房"],
                "zones": {"桌边": {"label": "长桌边"}},
            },
            "书房": {"connected_to": ["大厅"]},
            "旧钥匙": {
                "is_location": False,
                "kind": "key",
                "location": "大厅",
                "owner": None,
                "sub_location": "桌边",
                "hidden": False,
                "portable": True,
            },
        },
        actor_states={
            "甲": {"location": "大厅", "sub_location": "桌边"},
            "乙": {"location": "大厅", "sub_location": "桌边"},
            "丙": {"location": "书房"},
        },
    )


def _result(action_actor="甲", operations=None):
    return {
        "resolved_actions": [
            {
                "actor": action_actor,
                "outcome": "success",
                "result": "动作已经实际完成。",
            }
        ],
        "state_updates": {"scene": {}, "world_objects": {}, "actor_states": {}},
        "plot_updates": [],
        "relationship_updates": [],
        "object_lifecycle": operations or [],
        "tension_delta": 0,
    }


def test_spawned_object_has_explicit_tangible_identity_and_is_visible():
    scene = _scene()
    result = _result(
        operations=[
            {
                "operation": "spawn",
                "object_id": "密封信",
                "actor": "甲",
                "reason": "甲写完并封好了信",
                "object_kind": "document",
                "owner": "甲",
                "portable": True,
                "hidden": False,
                "properties": {"sealed": True, "addressed_to": "乙"},
            }
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is True
    letter = scene.get_object_state("密封信")
    assert letter["is_location"] is False
    assert letter["kind"] == "document"
    assert letter["owner"] == "甲"
    assert letter["location"] is None
    assert letter["sealed"] is True
    assert "密封信" in scene.get_view_pov("甲")["visible_objects"]
    assert "密封信" in scene.get_view_pov("乙")["visible_objects"]
    assert "密封信" not in scene.get_known_locations()


def test_pick_up_hide_transfer_and_reveal_are_sequential_world_operations():
    scene = _scene()
    hide_result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "甲",
                "owner": "甲",
                "reason": "甲从桌边拿起钥匙",
            },
            {
                "operation": "set_visibility",
                "object_id": "旧钥匙",
                "actor": "甲",
                "hidden": True,
                "reason": "甲把钥匙藏进袖中",
            },
        ]
    )

    hidden = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), hide_result, proposal_actors={"甲"}
    )

    assert hidden.committed is True
    assert scene.get_object_state("旧钥匙")["owner"] == "甲"
    assert scene.get_object_state("旧钥匙")["hidden"] is True
    assert "旧钥匙" in scene.get_view_pov("甲")["visible_objects"]
    assert "旧钥匙" not in scene.get_view_pov("乙")["visible_objects"]

    transfer_result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "甲",
                "owner": "乙",
                "hidden": False,
                "reason": "甲把钥匙交给乙并摊开手掌",
            }
        ]
    )
    transferred = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), transfer_result, proposal_actors={"甲"}
    )

    assert transferred.committed is True
    assert scene.get_object_state("旧钥匙")["owner"] == "乙"
    assert scene.get_object_state("旧钥匙")["hidden"] is False
    assert "旧钥匙" in scene.get_view_pov("甲")["visible_objects"]


def test_remote_object_manipulation_rejects_entire_world_transaction():
    scene = _scene()
    before = deepcopy(scene.get_snapshot())
    result = _result(
        action_actor="丙",
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "丙",
                "owner": "丙",
                "reason": "丙试图隔着房间拿走钥匙",
            }
        ],
    )
    result["state_updates"]["scene"]["description"] = "不应提交的描述"
    result["tension_delta"] = 0.3
    drama = DramaState(tension=0.4)

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), drama, result, proposal_actors={"丙"}
    )

    assert outcome.committed is False
    assert any("not co-located with object" in error for error in outcome.errors)
    assert scene.get_snapshot() == before
    assert drama.tension == 0.4


def test_actor_can_pick_up_object_then_move_with_it_in_one_resolved_action():
    scene = _scene()
    result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "甲",
                "owner": "甲",
                "reason": "甲拿起钥匙后走进书房",
            }
        ]
    )
    result["state_updates"]["actor_states"] = {"甲": {"location": "书房"}}

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is True
    assert scene.get_actor_location("甲") == "书房"
    assert scene.get_object_state("旧钥匙")["owner"] == "甲"
    assert scene.get_effective_object_location("旧钥匙") == "书房"


def test_item_placement_fields_cannot_bypass_lifecycle_through_state_updates():
    scene = _scene()
    result = _result()
    result["state_updates"]["world_objects"] = {
        "旧钥匙": {"owner": "丙", "location": None, "hidden": True}
    }

    outcome = WorldStateTransaction().commit(scene, PlotState(), DramaState(), result)

    assert outcome.committed is False
    assert any("require object_lifecycle" in error for error in outcome.errors)
    assert scene.get_object_state("旧钥匙")["owner"] is None


def test_content_cannot_shadow_reserved_engine_affordance_ids():
    scene = _scene()
    scene.get_object_state("旧钥匙")["affordances"] = [
        {"id": "engine:take", "need_effects": {}, "consumes": False}
    ]

    outcome = WorldStateTransaction().commit(
        scene,
        PlotState(),
        DramaState(),
        _result(),
        proposal_actors={"甲"},
    )

    assert outcome.committed is False
    assert any("reserved engine id" in error for error in outcome.errors)


def test_lifecycle_cannot_create_or_destroy_spatial_graph_nodes():
    scene = _scene()
    spawn_place = _result(
        operations=[
            {
                "operation": "spawn",
                "object_id": "秘密地窖",
                "actor": "甲",
                "reason": "甲声称发现了一个新地点",
                "object_kind": "location",
                "location": "大厅",
            }
        ]
    )
    destroy_place = _result(
        operations=[
            {
                "operation": "destroy",
                "object_id": "大厅",
                "actor": "甲",
                "reason": "甲试图删除整个地点节点",
            }
        ]
    )

    spawn_outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), spawn_place, proposal_actors={"甲"}
    )
    destroy_outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), destroy_place, proposal_actors={"甲"}
    )

    assert spawn_outcome.committed is False
    assert destroy_outcome.committed is False
    assert "秘密地窖" not in scene.world_objects
    assert "大厅" in scene.world_objects


def test_destroy_removes_tangible_object_and_lifecycle_bookkeeping():
    scene = _scene()
    scene.update_scene_flags({"dynamic_world_object_names": ["旧钥匙"]})
    result = _result(
        operations=[
            {
                "operation": "destroy",
                "object_id": "旧钥匙",
                "actor": "甲",
                "reason": "甲把旧钥匙熔毁",
            }
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is True
    assert "旧钥匙" not in scene.world_objects
    assert scene.get_scene_flag("dynamic_world_object_names") == []


def test_rendering_view_does_not_leak_hidden_object_operations_or_updates():
    scene = _scene()
    scene.get_object_state("旧钥匙").update({"owner": "甲", "location": None, "hidden": True})
    scene.world_objects["公开信"] = {
        "is_location": False,
        "kind": "document",
        "owner": "乙",
        "location": None,
        "hidden": False,
        "portable": True,
    }
    player_pov = scene.get_view_pov("乙")
    simulation_result = {
        "resolved_actions": [
            {
                "actor": "甲",
                "outcome": "success",
                "location": "大厅",
                "visibility": "public",
                "result": "甲动了动袖口。",
            },
            {
                "actor": "乙",
                "outcome": "success",
                "location": "大厅",
                "visibility": "public",
                "result": "乙举起公开信。",
            },
        ],
        "state_updates": {
            "scene": {},
            "world_objects": {
                "旧钥匙": {"engraving": "秘密门编号"},
                "公开信": {"opened": True},
            },
            "actor_states": {},
        },
        "object_lifecycle": [
            {
                "operation": "set_visibility",
                "object_id": "旧钥匙",
                "actor": "甲",
                "hidden": True,
            },
            {
                "operation": "relocate",
                "object_id": "公开信",
                "actor": "乙",
                "owner": "乙",
            },
        ],
    }

    visible = RenderingSystem()._build_visible_simulation(
        simulation_result, player_pov, visible_locations=["大厅"]
    )

    assert "旧钥匙" not in visible["state_updates"]["world_objects"]
    assert "公开信" in visible["state_updates"]["world_objects"]
    assert [item["object_id"] for item in visible["object_lifecycle"]] == ["公开信"]


def _add_nested_containers(scene, *, open_box=True, opaque_box=True):
    scene.world_objects.update(
        {
            "帆布包": {
                "is_location": False,
                "kind": "bag",
                "owner": "甲",
                "location": None,
                "container": None,
                "hidden": False,
                "portable": True,
                "is_container": True,
                "container_capacity": 4,
                "container_size": 2,
                "container_open": True,
                "container_opaque": True,
            },
            "木匣": {
                "is_location": False,
                "kind": "box",
                "owner": None,
                "location": None,
                "container": "帆布包",
                "hidden": False,
                "portable": True,
                "is_container": True,
                "container_capacity": 2,
                "container_size": 2,
                "container_open": open_box,
                "container_opaque": opaque_box,
            },
            "匣中钥匙": {
                "is_location": False,
                "kind": "key",
                "owner": None,
                "location": None,
                "container": "木匣",
                "hidden": False,
                "portable": True,
                "container_size": 1,
            },
        }
    )


def test_nested_contents_follow_container_owner_without_child_rewrites():
    scene = _scene()
    _add_nested_containers(scene)

    assert scene.get_effective_object_location("匣中钥匙") == "大厅"
    assert scene.get_object_container_chain("匣中钥匙") == ["木匣", "帆布包"]

    result = _result()
    result["state_updates"]["actor_states"] = {"甲": {"location": "书房"}}
    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is True
    assert scene.get_effective_object_location("木匣") == "书房"
    assert scene.get_effective_object_location("匣中钥匙") == "书房"
    assert scene.get_object_state("匣中钥匙")["container"] == "木匣"


def test_closed_opaque_container_hides_contents_and_opening_reveals_them():
    scene = _scene()
    _add_nested_containers(scene, open_box=False, opaque_box=True)

    visible_before = scene.get_view_pov("乙")["visible_objects"]
    assert "帆布包" in visible_before
    assert "木匣" in visible_before
    assert "匣中钥匙" not in visible_before

    result = _result(
        operations=[
            {
                "operation": "set_container_state",
                "object_id": "木匣",
                "actor": "甲",
                "open": True,
                "reason": "甲打开了包里的木匣",
            }
        ]
    )
    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is True
    assert scene.get_object_state("木匣")["container_open"] is True
    assert "匣中钥匙" in scene.get_view_pov("乙")["visible_objects"]


def test_transparent_closed_container_allows_sight_but_not_manipulation():
    scene = _scene()
    _add_nested_containers(scene, open_box=False, opaque_box=False)
    before = deepcopy(scene.get_snapshot())

    assert "匣中钥匙" in scene.get_view_pov("甲")["visible_objects"]
    result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "匣中钥匙",
                "actor": "甲",
                "owner": "甲",
                "reason": "甲试图隔着关闭的透明盖拿钥匙",
            }
        ]
    )
    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("inaccessible through its container chain" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_hidden_outer_container_does_not_leak_or_grant_nested_access():
    scene = _scene()
    _add_nested_containers(scene)
    scene.get_object_state("帆布包")["hidden"] = True
    before = deepcopy(scene.get_snapshot())

    assert "匣中钥匙" in scene.get_view_pov("甲")["visible_objects"]
    assert "帆布包" not in scene.get_view_pov("乙")["visible_objects"]
    assert "木匣" not in scene.get_view_pov("乙")["visible_objects"]
    assert "匣中钥匙" not in scene.get_view_pov("乙")["visible_objects"]

    result = _result(
        action_actor="乙",
        operations=[
            {
                "operation": "relocate",
                "object_id": "匣中钥匙",
                "actor": "乙",
                "owner": "乙",
                "reason": "乙试图直接拿走隐藏包内的钥匙",
            }
        ],
    )
    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"乙"}
    )

    assert outcome.committed is False
    assert any("inaccessible through its container chain" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_open_inner_container_cannot_be_targeted_through_closed_outer_container():
    scene = _scene()
    _add_nested_containers(scene)
    scene.get_object_state("帆布包").update(
        {"container_open": False, "container_opaque": False}
    )
    before = deepcopy(scene.get_snapshot())
    result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "甲",
                "container": "木匣",
                "reason": "甲试图隔着关闭的外层包把钥匙放进内层木匣",
            }
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("destination container is inaccessible" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_relocate_into_container_enforces_direct_capacity_atomically():
    scene = _scene()
    _add_nested_containers(scene)
    scene.get_object_state("木匣")["container_capacity"] = 1
    before = deepcopy(scene.get_snapshot())
    result = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "旧钥匙",
                "actor": "甲",
                "container": "木匣",
                "reason": "甲试图把第二把钥匙塞进已经装满的木匣",
            }
        ]
    )
    result["state_updates"]["scene"]["description"] = "不应提交"

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("exceeds container capacity" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_container_cycle_and_self_placement_are_rejected_atomically():
    scene = _scene()
    _add_nested_containers(scene)
    before = deepcopy(scene.get_snapshot())
    cycle = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "帆布包",
                "actor": "甲",
                "container": "木匣",
                "reason": "甲试图把外层包塞进它里面的木匣",
            }
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), cycle, proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("would create container cycle" in error for error in outcome.errors)
    assert scene.get_snapshot() == before

    self_placement = _result(
        operations=[
            {
                "operation": "relocate",
                "object_id": "木匣",
                "actor": "甲",
                "container": "木匣",
                "reason": "甲试图把木匣放进自身",
            }
        ]
    )
    self_outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), self_placement, proposal_actors={"甲"}
    )
    assert self_outcome.committed is False
    assert any("cannot place object inside itself" in error for error in self_outcome.errors)
    assert scene.get_snapshot() == before


def test_non_empty_container_cannot_be_destroyed_or_consumed():
    scene = _scene()
    _add_nested_containers(scene)
    before = deepcopy(scene.get_snapshot())
    result = _result(
        operations=[
            {
                "operation": "destroy",
                "object_id": "木匣",
                "actor": "甲",
                "reason": "甲试图砸碎仍装着钥匙的木匣",
            }
        ]
    )

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), result, proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("cannot destroy non-empty container" in error for error in outcome.errors)
    assert scene.get_snapshot() == before


def test_container_authority_fields_cannot_bypass_lifecycle_updates():
    scene = _scene()
    _add_nested_containers(scene)
    result = _result()
    result["state_updates"]["world_objects"] = {
        "木匣": {"container_open": False, "container_capacity": 99},
        "匣中钥匙": {"container": "帆布包"},
    }

    outcome = WorldStateTransaction().commit(scene, PlotState(), DramaState(), result)

    assert outcome.committed is False
    assert any("require object_lifecycle" in error for error in outcome.errors)
    assert scene.get_object_state("木匣")["container_open"] is True
    assert scene.get_object_state("匣中钥匙")["container"] == "木匣"


def test_transaction_rejects_malformed_preexisting_container_graph():
    scene = _scene()
    _add_nested_containers(scene)
    scene.get_object_state("帆布包").update(
        {"owner": None, "container": "木匣"}
    )
    before = deepcopy(scene.get_snapshot())

    outcome = WorldStateTransaction().commit(
        scene, PlotState(), DramaState(), _result(), proposal_actors={"甲"}
    )

    assert outcome.committed is False
    assert any("container cycle detected" in error for error in outcome.errors)
    assert scene.get_snapshot() == before
