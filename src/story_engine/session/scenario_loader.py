"""Bootstrap: create GM and agents from ScenarioConfig and attach to Runner."""
from copy import deepcopy
from importlib import import_module
from typing import Any
from src.story_engine.environment.runner import Runner
from src.story_engine.core.entity import Entity
from src.story_engine.prefabs.templates import create_agent
from src.story_engine.components.scene_state import SceneState
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.components.narrative_renderer import NarrativeRenderer
from src.story_engine.components.drama_state import DramaState
from src.story_engine.components.plot_state import PlotState
from src.story_engine.components.situation_state import SituationState
from src.story_engine.components.observation import Observation
from src.story_engine.components.memory import Memory
from src.story_engine.components.relationship import RelationshipBit
from src.story_engine.scenarios.config import ScenarioConfig
from src.config.config import config


def load_scenario_reference(reference: str) -> ScenarioConfig:
    """Load one explicitly named external ScenarioConfig object or factory."""

    module_name, separator, attribute = str(reference or "").strip().partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("scenario reference must use module.path:attribute")
    module = import_module(module_name)
    value: Any = getattr(module, attribute, None)
    if value is None:
        raise AttributeError(f"scenario reference has no attribute: {reference}")
    scenario = value() if callable(value) else value
    if not isinstance(scenario, ScenarioConfig):
        raise TypeError(f"scenario reference did not produce ScenarioConfig: {reference}")
    return scenario


def _validate_behavioral_actor_seed(scenario: ScenarioConfig) -> None:
    character_names = [str(item.name).strip() for item in scenario.characters]
    duplicates = sorted({name for name in character_names if character_names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate scenario character names: {duplicates}")
    declared = set(character_names)
    embodied = {str(name).strip() for name in scenario.initial_actor_states}
    missing_bodies = sorted(declared.difference(embodied))
    orphan_bodies = sorted(embodied.difference(declared))
    errors = []
    if missing_bodies:
        errors.append(f"characters without initial actor state: {missing_bodies}")
    if orphan_bodies:
        errors.append(f"initial actor states without characters: {orphan_bodies}")
    known_locations = {
        str(name).strip()
        for name, state in scenario.initial_world_objects.items()
        if isinstance(state, dict) and state.get("is_location", True)
    }
    for name in sorted(embodied.intersection(declared)):
        state = scenario.initial_actor_states.get(name, {})
        location = (
            str(state.get("location", "")).strip()
            if isinstance(state, dict)
            else ""
        )
        if not location:
            errors.append(f"initial actor state has no location: {name}")
        elif location not in known_locations:
            errors.append(f"initial actor state has unknown location: {name}->{location}")
    if errors:
        raise ValueError("; ".join(errors))


def create_gm(scenario: ScenarioConfig, *, memory_namespace: str = "") -> Entity:
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
            public_scene_fields=list(scenario.public_scene_fields),
            private_scene_fields=list(scenario.private_scene_fields),
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
    gm.add_component(SituationState())
    gm.add_component(Observation())
    gm.add_component(
        Memory(agent_name="GameMaster", namespace=memory_namespace)
    )
    return gm


def setup_scenario(runner: Runner, scenario: ScenarioConfig) -> None:
    """Initialize scenario by creating GM and characters and adding them to runner."""
    _validate_behavioral_actor_seed(scenario)
    runner.scenario = scenario
    print(f"\n--- Loading Scenario: {scenario.name} ---")
    env_preview = (scenario.environment[:80] + "...") if len(scenario.environment) > 80 else scenario.environment
    state_preview = (scenario.initial_state[:60] + "...") if len(scenario.initial_state) > 60 else scenario.initial_state
    print(f"Environment: {env_preview}")
    print(f"Initial State: {state_preview}")

    gm = create_gm(scenario, memory_namespace=runner.memory_namespace)
    runner.add_entity(gm)

    scene_state = gm.get_component("SceneState")
    runner.claim_registry.seed(
        scenario.claims,
        scene_state=scene_state,
        world_entities=runner.entities,
    )

    relationship_book = runner.relation_registry.to_relationship_book()
    for relation in scenario.initial_relationships:
        participant_set = set(relation.participants)
        if len(participant_set) != 2 or not participant_set.issubset(
            scenario.initial_actor_states
        ):
            raise ValueError(
                f"invalid initial relationship participants: {relation.participants}"
            )
        record = relationship_book.ensure(
            *relation.participants,
            provenance={"source": "scenario"},
        )
        for direction in relation.directions:
            if {direction.source, direction.target} != participant_set:
                raise ValueError(
                    "relationship direction must reference exactly its pair: "
                    f"{direction.source}->{direction.target}"
                )
            for track_id, value in direction.tracks.items():
                relationship_book.set_track(
                    direction.source,
                    direction.target,
                    track_id,
                    value,
                    provenance={"source": "scenario"},
                )
        for bit in relation.bits:
            record.bits[bit.bit_id] = RelationshipBit(
                bit_id=bit.bit_id,
                roles=dict(bit.roles),
                visibility=bit.visibility,
                provenance={"source": "scenario"},
            )
    runner.relation_registry.apply_relationship_book(
        relationship_book, runner.entities
    )

    for char_config in scenario.characters:
        print(f"Creating Character: {char_config.name} ({char_config.role})")
        base_cfg = config.get_component_config("agent").copy()
        if char_config.llm_config:
            base_cfg.update(char_config.llm_config)
        for item in char_config.initial_claim_knowledge:
            claim = runner.claim_registry.get(item.claim_id)
            if claim is None:
                raise ValueError(
                    f"character {char_config.name} knows unknown claim: {item.claim_id}"
                )
            evidence = claim.get_component("ClaimEvidence")
            unknown_refs = set(item.evidence_refs).difference(
                set(evidence.supports).union(evidence.refutes)
            )
            if unknown_refs:
                raise ValueError(
                    f"character {char_config.name} claim knowledge references "
                    f"unlinked evidence: {sorted(unknown_refs)}"
                )
            stance_refs = (
                set(evidence.supports)
                if item.stance == "supports"
                else set(evidence.refutes)
                if item.stance == "rejects"
                else set(evidence.supports).union(evidence.refutes)
            )
            contradictory_refs = set(item.evidence_refs).difference(stance_refs)
            if contradictory_refs:
                raise ValueError(
                    f"character {char_config.name} claim stance conflicts with "
                    f"evidence links: {sorted(contradictory_refs)}"
                )
        agent = create_agent(
            name=char_config.name,
            role=char_config.role,
            personality=char_config.personality,
            goals=char_config.goals,
            model_config=base_cfg,
            is_player=char_config.is_player,
            agent_runtime=char_config.agent_runtime,
            agent_config=char_config.agent_config,
            activation_policy=char_config.activation_policy,
            background_interval=char_config.background_interval,
            initial_beliefs=char_config.initial_beliefs,
            initial_secrets=char_config.initial_secrets,
            initial_commitments=char_config.initial_commitments,
            initial_needs=char_config.initial_needs,
            initial_traits=char_config.initial_traits,
            risk_tolerance=char_config.risk_tolerance,
            initial_obligations=char_config.initial_obligations,
            goal_specs=char_config.goal_specs,
            initial_claim_knowledge=char_config.initial_claim_knowledge,
            memory_namespace=runner.memory_namespace,
        )
        if agent.name in runner.entities:
            raise ValueError(f"character entity name collision: {agent.name}")
        knowledge = agent.get_component("KnowledgeState")
        initial_known_locations = list(
            dict.fromkeys(
                [
                    str(scenario.initial_actor_states.get(char_config.name, {}).get("location") or "").strip()
                ]
                + [str(item).strip() for item in char_config.initial_known_locations]
            )
        )
        initial_known_locations = [item for item in initial_known_locations if item]
        unknown_locations = set(initial_known_locations).difference(
            scene_state.get_known_locations()
        )
        if unknown_locations:
            raise ValueError(
                f"character {char_config.name} knows unknown locations: "
                f"{sorted(unknown_locations)}"
            )
        for location in initial_known_locations:
            if location:
                knowledge.observe_location(scene_state, location)
        for claim_entity in runner.claim_registry.entities():
            fact = claim_entity.get_component("ClaimFact")
            if fact.visibility == "public" and not knowledge.knows(fact.claim_id):
                knowledge.learn(
                    claim_id=fact.claim_id,
                    stance="uncertain",
                    confidence=0.5,
                    basis="public",
                    source="public_record",
                    step=0,
                )
        runner.add_entity(agent)
        runner.register_agent(agent)
        if char_config.is_player:
            print(" -> Player Character")

    boundary_errors = runner.agent_boundary_errors()
    if boundary_errors:
        raise RuntimeError(
            "scenario Agent boundary is incomplete: " + "; ".join(boundary_errors)
        )

    if scenario.player_character_name is None:
        print("Warning: No player character defined in scenario.")
