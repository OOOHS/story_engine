from copy import deepcopy

from pydantic import Field

from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.scene_state import SceneState
from src.story_engine.core.component import Component
from src.story_engine.core.entity import Entity
from src.story_engine.simulation.authority import SemanticAuthorityFilter
from src.story_engine.social import SocialRelationRegistry
from src.story_engine.systems.simulation import SimulationSystem




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
