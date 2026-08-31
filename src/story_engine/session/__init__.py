"""
Session layer: binds a Runner to a Scenario and provides a single entry point for running the sandbox.
Use create_session(scenario) to build a runnable session; then run with a Driver (e.g. ConsoleDriver).
"""
from .session import Session, create_session, create_session_from_seed
from .scenario_loader import load_scenario_reference, setup_scenario
from .seed_compiler import (
    CompiledSeed,
    SeedDraft,
    ScenarioSeedError,
    compile_seed,
    compile_seed_report,
    compile_scenario_seed,
    compile_scenario_seed_file,
    load_or_compile_scenario,
)
from .play_profile import PLAY_PROFILES, bind_play_profile, runtime_factories_for_profile
from .console_driver import ConsoleDriver
from .step_status import public_step_status

__all__ = [
    "Session",
    "create_session",
    "create_session_from_seed",
    "setup_scenario",
    "load_scenario_reference",
    "ScenarioSeedError",
    "SeedDraft",
    "CompiledSeed",
    "compile_seed",
    "compile_seed_report",
    "compile_scenario_seed",
    "compile_scenario_seed_file",
    "load_or_compile_scenario",
    "PLAY_PROFILES",
    "bind_play_profile",
    "runtime_factories_for_profile",
    "ConsoleDriver",
    "public_step_status",
]
