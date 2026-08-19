from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Tuple

from src.story_engine.agents import (
    HermesContainerConfig,
    HermesInvocationBudget,
    make_hermes_container_runtime_factory,
)
from src.story_engine.components.host_rule_narrative import (
    HostRuleNarrativeRenderer,
)
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.session import create_session


@dataclass(frozen=True)
class HermesEpisodeConfig:
    """Host-owned runtime policy for binding any Scenario to Hermes."""

    image: str = "hermes-story:latest"
    docker_binary: str = "docker"
    timeout_seconds: float = 180.0
    network_mode: str = "bridge"
    allowed_toolsets: Tuple[str, ...] = ("memory",)
    requested_toolsets: Tuple[str, ...] = ("memory",)
    environment_keys: Tuple[str, ...] = (
        "OPENAI_API_KEY",
        "IKUN_API_KEY",
        "HERMES_BASE_URL",
        "HERMES_MODEL",
        "HERMES_PROVIDER",
        "HERMES_TRACE",
    )
    entrypoint_path: str = ""
    config_path: str = ""
    invocation_budget: HermesInvocationBudget | None = None


def create_hermes_episode_session(
    scenario: ScenarioConfig,
    *,
    seed: int | str,
    config: HermesEpisodeConfig | None = None,
    command_runner: Any = None,
):
    """Bind every behavioral character to the same Host-owned Hermes policy."""

    policy = config or HermesEpisodeConfig()
    bound_scenario = deepcopy(scenario)
    bound_scenario.default_agent_runtime = "hermes"
    for character in bound_scenario.characters:
        character.agent_runtime = "hermes"
        character.agent_config = {
            **dict(character.agent_config or {}),
            "enabled_toolsets": list(policy.requested_toolsets),
        }
    runtime_factory = make_hermes_container_runtime_factory(
        HermesContainerConfig(
            image=policy.image,
            docker_binary=policy.docker_binary,
            timeout_seconds=policy.timeout_seconds,
            network_mode=policy.network_mode,
            allowed_toolsets=tuple(policy.allowed_toolsets),
            environment_keys=tuple(policy.environment_keys),
            entrypoint_path=policy.entrypoint_path,
            config_path=policy.config_path,
            invocation_budget=policy.invocation_budget,
        ),
        command_runner=command_runner,
    )
    session = create_session(
        bound_scenario,
        random_seed=seed,
        agent_runtime_factories={"hermes": runtime_factory},
    )
    gm = session.entities["GameMaster"]
    gm.add_component(HostRuleSimulationControl(scenario=bound_scenario))
    gm.add_component(HostRuleNarrativeRenderer(scenario=bound_scenario))
    return session


def normalized_toolsets(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item).strip()
            for item in values
            if str(item).strip()
        )
    )
