import uuid
from typing import Dict, Optional, Any, Type, TypeVar
from src.story_engine.core.component import Component
from src.story_engine.core.logger import logger

T = TypeVar("T", bound=Component)

class Entity:
    """
    The fundamental object in the ECS architecture.
    An Entity is essentially an ID and a container of Components.
    """
    def __init__(self, name: str, entity_id: Optional[str] = None):
        self.name = name
        self.id = entity_id or str(uuid.uuid4())
        self.components: Dict[str, Component] = {}
        logger.debug(f"Created Entity: {self.name} ({self.id})")
    
    def add_component(self, component: Component) -> None:
        """Adds a component to the entity."""
        component_name = str(
            getattr(component, "component_slot", None)
            or component.__class__.__name__
        ).strip()
        if not component_name:
            raise ValueError("component slot must be non-empty")
        previous = self.components.get(component_name)
        if previous is not None and previous is not component:
            previous.entity = None
        self.components[component_name] = component
        component.entity = self # Link back to entity
        logger.debug(f"Added component {component_name} to {self.name}")
        
    def get_component(self, component_name: str) -> Optional[Component]:
        """Retrieves a component by its class name."""
        return self.components.get(component_name)
    
    def get_component_by_type(self, component_type: Type[T]) -> Optional[T]:
        """Retrieves a component by its type."""
        for component in self.components.values():
            if isinstance(component, component_type):
                return component
        return None

    def __repr__(self):
        return f"<Entity {self.name} ({self.id})>"
