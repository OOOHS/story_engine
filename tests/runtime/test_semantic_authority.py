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
        "contract_settlements": [{"agreement_id": "a1"}],
        "contract_authorizations": {"a1": ["甲"]},
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
    assert filtered.result["contract_settlements"] == []
    assert filtered.result["contract_authorizations"] == {}
    assert filtered.result["storylet_hits"] == []
    assert filtered.result["social_impacts"][0]["kind"] == "grateful"
    assert filtered.result["social_impacts"][0]["intensity"] == "moderate"
    assert filtered.result["social_impacts"][0]["magnitude"] == 0.5
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
        "result.contract_settlements",
        "result.contract_authorizations",
        "result.storylet_hits",
        "result.social_impacts[0].magnitude",
        "uncertain_outcomes[0].success.plot_updates",
        "uncertain_outcomes[0].success.relationship_updates",
    }
    assert candidate["plot_updates"][0]["advance"] == 99


def test_authority_filter_compiles_qualitative_effects_to_fixed_host_values():
    filtered = SemanticAuthorityFilter().sanitize(
        {
            "conflict_level": "high",
            "tension_delta": 0.99,
            "social_impacts": [{"kind": "hurt", "intensity": "major"}],
            "modifier_updates": [{"kind": "shaken", "intensity": "minor"}],
            "drive_updates": [
                {
                    "actor": "甲",
                    "need": "safety",
                    "direction": "decrease",
                    "intensity": "extreme",
                }
            ],
        }
    )

    assert filtered.result["social_impacts"][0]["magnitude"] == 0.75
    assert filtered.result["modifier_updates"][0]["magnitude"] == 0.25
    assert filtered.result["drive_updates"][0]["delta"] == -0.4
    assert filtered.result["tension_delta"] == 0.12
    assert filtered.rejected_writes == ["result.tension_delta"]


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
