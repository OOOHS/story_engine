"""Bootstrap: create GM and agents from ScenarioConfig and attach to Runner."""
from copy import deepcopy
from src.story_engine.environment.runner import Runner
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.relationship_state import RelationshipState
from src.story_engine.components.situation_state import SituationState
from src.story_engine.components.observation import Observation
from src.story_engine.components.memory import Memory
from src.story_engine.scenarios.config import ScenarioConfig
from src.config.config import config


def create_gm(scenario: ScenarioConfig) -> Entity:
    """Create the world engine entity with simulation and rendering controls."""
    gm = Entity("GameMaster")
    initial_scene_flags = deepcopy(scenario.initial_scene_flags)
    initial_scene_flags.setdefault("initial_state", scenario.initial_state)
    gm.add_component(
        SceneState(
            description=scenario.environment,
            world_objects=deepcopy(scenario.initial_world_objects),
            actor_states=deepcopy(scenario.initial_actor_states),
            scene_flags=initial_scene_flags,
        )
    )
    gm.add_component(SimulationControl(
        model_config=config.get_component_config("game_master"),
        scenario=scenario,
    ))
    gm.add_component(NarrativeRenderer(
        model_config=config.get_component_config("narrator"),
        scenario=scenario,
    ))
    gm.add_component(DramaState.from_config(scenario.drama))
    gm.add_component(PlotState.from_configs(scenario.plot_entities))
    gm.add_component(RelationshipState.from_actor_states(deepcopy(scenario.initial_actor_states)))
    gm.add_component(SituationState())
    gm.add_component(Observation())
    gm.add_component(Memory(agent_name="GameMaster"))
    return gm


def setup_scenario(runner: Runner, scenario: ScenarioConfig) -> None:
    """Initialize scenario by creating GM and characters and adding them to runner."""
    print(f"\n--- Loading Scenario: {scenario.name} ---")
    env_preview = (scenario.environment[:80] + "...") if len(scenario.environment) > 80 else scenario.environment
    state_preview = (scenario.initial_state[:60] + "...") if len(scenario.initial_state) > 60 else scenario.initial_state
    print(f"Environment: {env_preview}")
    print(f"Initial State: {state_preview}")

    gm = create_gm(scenario)
    runner.add_entity(gm)

    for char_config in scenario.characters:
        print(f"Creating Character: {char_config.name} ({char_config.role})")
        base_cfg = config.get_component_config("agent").copy()
        if char_config.llm_config:
            base_cfg.update(char_config.llm_config)
        agent = create_agent(
            name=char_config.name,
            role=char_config.role,
            personality=char_config.personality,
            goals=char_config.goals,
            model_config=base_cfg,
            is_player=char_config.is_player,
        )
        runner.add_entity(agent)
        if char_config.is_player:
            print(" -> Player Character")

    if scenario.player_character_name is None:
        print("Warning: No player character defined in scenario.")
