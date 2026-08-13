from typing import Protocol

from src.story_engine.core.entity import Entity
from src.story_engine.agents.types import AgentDecision, AgentPerception


class CharacterAgentRuntime(Protocol):
    """Framework-neutral contract implemented by every character brain."""

    def decide(self, entity: Entity, perception: AgentPerception) -> AgentDecision:
        ...


def runtime_owns_subjective_state(entity: Entity) -> bool:
    """Whether the live runtime, rather than Host compatibility fields, owns mind state."""

    controller = entity.get_component("AgentController")
    if controller is None:
        return False
    config = dict(getattr(controller, "config", {}) or {})
    explicit = str(config.get("subjective_state_owner", "")).strip().casefold()
    return explicit == "runtime" or str(
        getattr(controller, "runtime", "")
    ).strip().casefold() == "hermes"
