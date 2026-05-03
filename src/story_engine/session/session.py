"""
Session: one runnable sandbox instance (Runner + Scenario + step count).
Decouples "what to run" from "how the human interacts" (Driver).
"""
from typing import Dict, Optional, List, Any
from src.story_engine.environment.runner import Runner
from src.story_engine.scenarios.config import ScenarioConfig
from .scenario_loader import setup_scenario


class Session:
    """
    A single play session: one Runner bound to one Scenario.
    Step execution is delegated to Runner; Session tracks step count and exposes runner/scenario for drivers.
    """
    def __init__(self, runner: Runner, scenario: ScenarioConfig):
        self.runner = runner
        self.scenario = scenario
        self.step_count = 0

    @property
    def entities(self):
        return self.runner.entities

    @property
    def player_character_name(self) -> Optional[str]:
        return self.scenario.player_character_name

    def run_step(
        self,
        overrides: Optional[Dict[str, str]] = None,
        world_edits: Optional[List[tuple]] = None,
        inject_events: Optional[List[str]] = None,
        on_phase_done: Optional[Any] = None,
    ) -> None:
        """Run one simulation step and increment step count."""
        self.runner.run_step(
            overrides=overrides or {},
            world_edits=world_edits,
            inject_events=inject_events,
            player_name=self.player_character_name,
            on_phase_done=on_phase_done,
        )
        self.step_count += 1


def create_session(scenario: ScenarioConfig) -> Session:
    """Create a new Session: Runner + scenario loaded (GM and characters)."""
    runner = Runner()
    setup_scenario(runner, scenario)
    return Session(runner, scenario)
