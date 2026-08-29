from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.scenarios.config import PlotEntityConfig
from src.story_engine.simulation.authority import SemanticAuthorityFilter
from src.story_engine.social import SocialRelationRegistry
from src.story_engine.systems.simulation import SimulationSystem


def test_authority_filter_strips_exact_social_plot_and_settlement_writes():
    candidate = {
        "resolved_actions": [],
        "plot_updates": [{"plot_id": "secret", "advance": 99}],
        "relationship_updates": [
            {"source": "甲", "target": "乙", "trust_delta": 5}
        ],
        "storylet_hits": ["forced_beat"],
        "social_impacts": [
            {
                "source": "甲",
                "affected": "乙",
                "kind": "grateful",
                "magnitude": 0.9,
                "reason": "甲帮助了乙",
            }
        ],
        "uncertain_outcomes": [
            {
                "success": {
                    "plot_updates": [{"plot_id": "secret", "advance": 1}],
                    "relationship_updates": [
                        {"source": "乙", "target": "甲", "favor_delta": 5}
                    ],
                },
                "failure": {},
            }
        ],
    }

    filtered = SemanticAuthorityFilter().sanitize(candidate)

    assert filtered.result["plot_updates"] == []
    assert filtered.result["relationship_updates"] == []
    assert filtered.result["storylet_hits"] == []
    assert filtered.result["social_impacts"][0]["kind"] == "grateful"
    assert filtered.result["social_impacts"][0]["magnitude"] == 0.9
    assert filtered.result["uncertain_outcomes"][0]["success"]["plot_updates"] == []
    assert (
        filtered.result["uncertain_outcomes"][0]["success"][
            "relationship_updates"
        ]
        == []
    )
    assert set(filtered.rejected_writes) == {
        "result.plot_updates",
        "result.relationship_updates",
        "result.storylet_hits",
        "uncertain_outcomes[0].success.plot_updates",
        "uncertain_outcomes[0].success.relationship_updates",
    }
    assert candidate["plot_updates"][0]["advance"] == 99


def test_authority_filter_bounds_raw_numeric_effects_without_remapping():
    """The resolver now gives magnitudes/deltas directly; the host only
    clamps them into a sane range instead of re-mapping a qualitative tier
    to a fixed number.
    """
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "tension_delta": 0.99,
            "social_impacts": [{"kind": "hurt", "magnitude": 0.6}],
            "modifier_updates": [{"kind": "shaken", "magnitude": 1.5}],
            "drive_updates": [
                {
                    "actor": "甲",
                    "need": "safety",
                    "delta": -0.9,
                }
            ],
        }
    )

    # In-range values pass through untouched.
    assert filtered.result["social_impacts"][0]["magnitude"] == 0.6
    # Out-of-range values are clamped, not remapped to a tier, and flagged.
    assert filtered.result["modifier_updates"][0]["magnitude"] == 1.0
    assert filtered.result["drive_updates"][0]["delta"] == -0.4
    assert filtered.result["drive_updates"][0]["direction"] == "decrease"
    assert filtered.result["tension_delta"] == 0.15
    assert set(filtered.rejected_writes) == {
        "result.modifier_updates[0].magnitude",
        "result.drive_updates[0].delta",
        "result.tension_delta",
    }


def test_authority_filter_bounds_drive_creation_numbers_and_strips_initial_pressure():
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "drive_creations": [
                {
                    "actor": "甲",
                    "need": "复仇心",
                    "drift_per_turn": 999,
                    "critical_threshold": 0.01,
                    "pressure": 0.9,
                    "initial_pressure": 1.0,
                    "reason": "亲人被害",
                },
                {"actor": "乙", "need": "焦虑"},
            ]
        }
    )

    creations = filtered.result["drive_creations"]
    assert creations[0]["drift_per_turn"] == 0.08
    assert creations[0]["critical_threshold"] == 0.5
    assert "pressure" not in creations[0]
    assert "initial_pressure" not in creations[0]
    # No drift/threshold given: falls back to the neutral default.
    assert creations[1]["drift_per_turn"] == 0.03
    assert creations[1]["critical_threshold"] == 0.8
    assert set(filtered.rejected_writes) == {
        "result.drive_creations[0].pressure",
        "result.drive_creations[0].initial_pressure",
        "result.drive_creations[0].drift_per_turn",
        "result.drive_creations[0].critical_threshold",
    }


class SimulationControl(Component):
    scripted_result: dict = Field(default_factory=dict)
    scenario: object = None

    def simulate(self, _payload):
        return deepcopy(self.scripted_result)


def test_simulation_boundary_prevents_semantic_gm_from_writing_tracks_or_plot():
    scene = SceneState(
        world_objects={"大厅": {}},
        actor_states={"甲": {"location": "大厅"}, "乙": {"location": "大厅"}},
    )
    plots = PlotState.from_configs(
        [
            PlotEntityConfig(
                plot_id="secret",
                title="秘密",
                description="只应由宿主因果规则推进",
                max_clock=3,
            )
        ]
    )
    gm = Entity("GameMaster")
    gm.add_component(
        SimulationControl(
            scripted_result={
                "resolved_actions": [
                    {
                        "actor": "甲",
                        "intent": "向乙打招呼",
                        "action_kind": "communicate",
                        "action_target": "乙",
                        "outcome": "success",
                        "location": "大厅",
                        "visibility": "public",
                        "result": "甲向乙打了招呼。",
                    }
                ],
                "state_updates": {
                    "scene": {},
                    "world_objects": {},
                    "actor_states": {},
                },
                "plot_updates": [{"plot_id": "secret", "advance": 3}],
                "relationship_updates": [
                    {
                        "source": "乙",
                        "target": "甲",
                        "trust_delta": 5,
                        "reason": "语义模型自行决定完全信任",
                    }
                ],
                "tension_delta": 0,
            }
        )
    )
    gm.add_component(scene)
    gm.add_component(plots)
    gm.add_component(DramaState())
    registry = SocialRelationRegistry()
    context = {
        "intents": [
            {
                "actor": "甲",
                "intent": "向乙打招呼",
                "action_kind": "communicate",
                "action_target": "乙",
                "location": "大厅",
            }
        ],
        "relation_registry": registry,
    }

    SimulationSystem().update({"GameMaster": gm}, context)

    assert context["state_transaction"]["committed"] is True
    assert plots.plots["secret"]["clock"] == 0
    assert registry.to_relationship_book().relationships[
        "pair:乙<->甲"
    ].directed_tracks == {}
    assert set(context["semantic_authority_rejections"]) == {
        "result.plot_updates",
        "result.relationship_updates",
    }
    assert context["simulation_result"]["plot_updates"] == []
    assert context["simulation_result"]["relationship_updates"] == []


def _valid_beat_proposal(**overrides):
    proposal = {
        "plot_id": "southern_drought",
        "beat_id": "visitor_letter",
        "intent": "粮仓出现一封求援信",
        "kind": "environment",
        "open_thread": {
            "title": "南方旱情",
            "opened_reason": "连续三名角色抱怨粮价",
        },
        "conditions": [
            {
                "scope": "world_object",
                "target": "求援信",
                "path": "",
                "operator": "exists",
            }
        ],
        "effect": {"visibility": "local", "stake": "赈灾"},
    }
    proposal.update(overrides)
    return proposal


def test_authority_filter_compiles_plot_beat_proposals_and_strips_clock_writes():
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "plot_beat_proposals": [
                _valid_beat_proposal(
                    clock=9,
                    advance=3,
                    open_thread={
                        "title": "南方旱情",
                        "opened_reason": "连续三名角色抱怨粮价",
                        "max_clock": 99,
                    },
                )
            ]
        }
    )

    proposal = filtered.result["plot_beat_proposals"][0]
    assert proposal["plot_id"] == "southern_drought"
    assert proposal["kind"] == "environment"
    assert proposal["open_thread"]["opened_reason"] == "连续三名角色抱怨粮价"
    assert "max_clock" not in proposal["open_thread"]
    assert "clock" not in proposal
    assert "advance" not in proposal
    assert set(filtered.rejected_writes) == {
        "result.plot_beat_proposals[0].clock",
        "result.plot_beat_proposals[0].advance",
        "result.plot_beat_proposals[0].open_thread.max_clock",
    }


def test_authority_filter_drops_plot_beat_without_conditions_or_provenance():
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "plot_beat_proposals": [
                {
                    "plot_id": "x",
                    "beat_id": "y",
                    "intent": "没有条件",
                    "kind": "environment",
                    "conditions": [],
                    "open_thread": {"title": "x", "opened_reason": "因为"},
                },
                {
                    "plot_id": "x",
                    "beat_id": "z",
                    "intent": "没有来源",
                    "kind": "environment",
                    "conditions": [
                        {
                            "scope": "scene",
                            "path": "scene_flags.alarm",
                            "operator": "eq",
                            "value": True,
                        }
                    ],
                    "open_thread": {"title": "x"},
                },
            ]
        }
    )

    assert filtered.result["plot_beat_proposals"] == []
    assert "result.plot_beat_proposals[0].conditions" in filtered.rejected_writes
    assert "result.plot_beat_proposals[1].open_thread.opened_reason" in filtered.rejected_writes


def test_authority_filter_strips_plot_beat_proposals_from_uncertain_branches():
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "uncertain_outcomes": [
                {
                    "success": {
                        "plot_beat_proposals": [_valid_beat_proposal()],
                    },
                    "failure": {},
                }
            ]
        }
    )

    assert "plot_beat_proposals" not in filtered.result["uncertain_outcomes"][0]["success"]
    assert (
        "uncertain_outcomes[0].success.plot_beat_proposals"
        in filtered.rejected_writes
    )
