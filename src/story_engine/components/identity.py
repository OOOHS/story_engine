from typing import List, Optional
from pydantic import Field
from src.story_engine.core.component import Component

class Identity(Component):
    name: str
    personality: str
    goals: List[str] = []
    background: Optional[str] = None
    role: str
    is_player: bool = False
