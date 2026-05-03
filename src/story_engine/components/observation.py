from typing import List

from pydantic import Field

from src.story_engine.core.component import Component

class Observation(Component):
    """
    Holds the observations for the current step.
    """
    current_observations: List[str] = Field(default_factory=list)

    def add_observation(self, observation: str):
        self.current_observations.append(observation)

    def prune(self, keep_n: int = 20):
        """Keep only the last N observations."""
        if len(self.current_observations) > keep_n:
            self.current_observations = self.current_observations[-keep_n:]

    def clear(self):
        self.current_observations = []
    
    def get_text(self) -> str:
        return "\n".join(self.current_observations)
