from typing import Optional
from src.story_engine.core.component import Component

class Planning(Component):
    """Legacy Host mirror for non-subject runtimes.

    Persistent subjects such as Hermes keep plans in their own conversation
    and memory tools; the Host must not mirror or restore those plans here.
    """
    current_plan: Optional[str] = None
    
    def set_plan(self, plan: str):
        self.current_plan = plan

    def get_plan(self) -> str:
        return self.current_plan or ""
