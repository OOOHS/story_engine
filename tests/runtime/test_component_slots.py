from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.components.simulation_control import SimulationControl
from src.story_engine.core.entity import Entity


def test_component_variant_replaces_stable_slot_and_detaches_previous_instance():
    entity = Entity("WorldHost")
    original = SimulationControl(llm_config={})
    variant = HostRuleSimulationControl(llm_config={})

    entity.add_component(original)
    entity.add_component(variant)

    assert entity.get_component("SimulationControl") is variant
    assert entity.get_component("HostRuleSimulationControl") is None
    assert original.entity is None
    assert variant.entity is entity
