from src.story_engine.core.entity import Entity
from src.story_engine.components.identity import Identity
from src.story_engine.components.memory import Memory
from src.story_engine.components.observation import Observation
from src.story_engine.components.planning import Planning
from src.story_engine.components.persona import Persona

def create_agent(
    name: str,
    role: str,
    personality: str,
    goals: list,
    model_config: dict = None,
    is_player: bool = False,
) -> Entity:
    entity = Entity(name=name)
    
    # Add Components
    entity.add_component(Identity(name=name, role=role, personality=personality, goals=goals, is_player=is_player))
    entity.add_component(Memory(agent_name=name))
    entity.add_component(Observation())
    entity.add_component(Planning())
    entity.add_component(Persona(model_config=model_config or {}))
    
    return entity

def create_detective(name: str = "Sherlock") -> Entity:
    return create_agent(
        name=name,
        role="Detective",
        personality="Analytical, observant, slightly arrogant.",
        goals=["Solve the mystery", "Find clues"],
        model_config={"model": "gpt-4"}, # Example
    )

def create_suspect(name: str = "Moriarty") -> Entity:
    return create_agent(
        name=name,
        role="Suspect",
        personality="Secretive, intelligent, manipulative.",
        goals=["Hide the truth", "Evade capture"],
        model_config={"model": "gpt-3.5-turbo"}
    )
