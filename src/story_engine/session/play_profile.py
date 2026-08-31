"""Application-level profiles for starting a playable session."""

from __future__ import annotations

from typing import Any

from src.story_engine.agents import (
    HermesContainerConfig,
    HermesLocalProcessConfig,
    default_hermes_runtime_factories,
    default_local_hermes_runtime_factories,
    default_offline_runtime_factories,
)
from src.story_engine.scenarios.config import ScenarioConfig


PLAY_PROFILES = ("production", "offline")


def bind_play_profile(scenario: ScenarioConfig, profile: str) -> ScenarioConfig:
    """Return a scenario bound to an explicit application profile.

    ``offline`` is a complete deterministic profile: it swaps every declared
    character to the local no-model runtime and selects Host rule simulation
    and fact-only narration.  It is never selected implicitly when production
    dependencies fail.
    """

    normalized = str(profile or "production").strip().casefold().replace("-", "_")
    if normalized not in PLAY_PROFILES:
        raise ValueError(
            f"unknown play profile {profile!r}; choose one of: {', '.join(PLAY_PROFILES)}"
        )
    if normalized == "production":
        return scenario.model_copy(deep=True)
    characters = [
        character.model_copy(
            update={
                "agent_runtime": "offline",
                "agent_config": {},
            },
            deep=True,
        )
        for character in scenario.characters
    ]
    return scenario.model_copy(
        update={
            "default_agent_runtime": "offline",
            "simulation_mode": "rules",
            "narration_mode": "rules",
            "characters": characters,
            "metadata": {
                **dict(scenario.metadata or {}),
                "play_profile": "offline",
            },
        },
        deep=True,
    )


def runtime_factories_for_profile(
    profile: str,
    *,
    hermes_transport: str = "docker",
    hermes_python: str = "python",
    hermes_entrypoint: str = "",
    hermes_vendor_root: str = "",
    hermes_working_directory: str = "",
) -> dict[str, Any]:
    """Build the explicit runtime registry for an application profile."""

    normalized = str(profile or "production").strip().casefold().replace("-", "_")
    if normalized == "offline":
        return default_offline_runtime_factories()
    if normalized != "production":
        raise ValueError(
            f"unknown play profile {profile!r}; choose one of: {', '.join(PLAY_PROFILES)}"
        )
    transport = str(hermes_transport or "docker").strip().casefold()
    if transport == "docker":
        return default_hermes_runtime_factories(HermesContainerConfig())
    if transport == "local":
        return default_local_hermes_runtime_factories(
            HermesLocalProcessConfig(
                python_executable=hermes_python,
                entrypoint_path=hermes_entrypoint,
                vendor_root=hermes_vendor_root,
                working_directory=hermes_working_directory,
            )
        )
    raise ValueError("hermes_transport must be 'docker' or 'local'")


__all__ = ["PLAY_PROFILES", "bind_play_profile", "runtime_factories_for_profile"]
