from typing import Optional
from src.story_engine.core.component import Component

class Planning(Component):
    """
    Holds the current plan of the agent.
    """
    current_plan: Optional[str] = None
    
    def set_plan(self, plan: str):
        self.current_plan = plan

    def get_plan(self) -> str:
        return self.current_plan or "No plan."
