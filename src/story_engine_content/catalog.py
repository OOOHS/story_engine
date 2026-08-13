"""Lazy catalog for bundled examples; importing it selects no story."""

from importlib import import_module
from typing import Dict, Tuple

from src.story_engine.scenarios.config import ScenarioConfig


BUNDLED_SCENARIOS: Dict[str, Tuple[str, str]] = {
    "cthulhu-arkham": (
        "src.story_engine_content.bundled.cthulhu_arkham",
        "cthulhu_arkham_scenario",
    ),
    "false-heiress": (
        "src.story_engine_content.bundled.false_heiress",
        "false_heiress_scenario",
    ),
    "thirteenth-floor": (
        "src.story_engine_content.bundled.thirteenth_floor",
        "thirteenth_floor_scenario",
    ),
}


def available_bundled_scenarios() -> tuple[str, ...]:
    return tuple(sorted(BUNDLED_SCENARIOS))


def load_bundled_scenario(name: str) -> ScenarioConfig:
    key = str(name or "").strip()
    reference = BUNDLED_SCENARIOS.get(key)
    if reference is None:
        choices = ", ".join(available_bundled_scenarios())
        raise ValueError(f"unknown bundled scenario {key!r}; choose one of: {choices}")
    module_name, attribute = reference
    scenario = getattr(import_module(module_name), attribute, None)
    if not isinstance(scenario, ScenarioConfig):
        raise TypeError(f"bundled scenario is not ScenarioConfig: {key}")
    return scenario
