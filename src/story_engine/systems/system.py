from abc import ABC, abstractmethod
from typing import Dict, Any, List
from src.story_engine.core.entity import Entity
from src.story_engine.core.logger import logger

class System(ABC):
    """
    Base class for all systems in the ECS architecture.
    Systems contain the logic and operate on Entities with specific Components.
    """
    
    def __init__(self):
        self.logger = logger

    @abstractmethod
    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        """
        Runs the system logic for a single step.
        
        Args:
            entities: A dictionary of all entities in the simulation, keyed by name/ID.
            context: A shared context dictionary containing global state (clock, dispatcher, overrides, etc.).
        """
        pass
