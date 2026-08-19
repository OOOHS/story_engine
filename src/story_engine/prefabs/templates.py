from src.story_engine.core.entity import Entity
from src.story_engine.components.identity import Identity
from src.story_engine.components.memory import Memory
from src.story_engine.components.observation import Observation
from src.story_engine.components.planning import Planning
from src.story_engine.components.agent_controller import AgentController
from src.story_engine.components.cognition import Cognition
from src.story_engine.components.drive_state import DriveState
from src.story_engine.components.obligation_state import ObligationState
from src.story_engine.components.trait_state import TraitState
from src.story_engine.components.sentiment_state import SentimentState
from src.story_engine.components.goal_state import GoalState
from src.story_engine.components.modifier_state import ModifierState
from src.story_engine.components.knowledge_state import KnowledgeState
from src.story_engine.components.navigation_state import NavigationState

def create_agent(
    name: str,
    role: str,
    personality: str,
    goals: list,
    *,
    agent_runtime: str,
    model_config: dict = None,
    is_player: bool = False,
    agent_config: dict = None,
    activation_policy: str = "auto",
    background_interval: int = 3,
    initial_beliefs: list = None,
    initial_secrets: list = None,
    initial_commitments: list = None,
    initial_needs: list = None,
    initial_traits: list = None,
    risk_tolerance: float = 0.5,
    initial_obligations: list = None,
    goal_specs: list = None,
    initial_claim_knowledge: list = None,
    memory_namespace: str = None,
) -> Entity:
    entity = Entity(name=name)

    # Add Components
    entity.add_component(Identity(name=name, role=role, personality=personality, goals=goals, is_player=is_player))
    entity.add_component(Memory(agent_name=name, namespace=memory_namespace))
    entity.add_component(Observation())
    entity.add_component(Planning())
    entity.add_component(
        Cognition(
            beliefs=list(initial_beliefs or []),
            secrets=list(initial_secrets or []),
            commitments=list(initial_commitments or []),
        )
    )
    entity.add_component(
        DriveState.from_initial(
            initial_needs or [],
            risk_tolerance=risk_tolerance,
        )
    )
    entity.add_component(TraitState.from_initial(initial_traits or []))
    entity.add_component(SentimentState())
    entity.add_component(ObligationState.from_initial(initial_obligations or []))
    entity.add_component(GoalState.from_initial(goals, goal_specs or []))
    entity.add_component(ModifierState())
    entity.add_component(KnowledgeState.from_initial(initial_claim_knowledge or []))
    entity.add_component(NavigationState())
    runtime_config = dict(agent_config or {})
    if model_config:
        runtime_config.setdefault("llm_config", dict(model_config))
    entity.add_component(
        AgentController(
            runtime=agent_runtime,
            config=runtime_config,
            activation_policy=activation_policy,
            background_interval=background_interval,
        )
    )

    return entity
