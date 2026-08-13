from typing import Any, ClassVar, Dict

from src.story_engine.components.simulation_control import SimulationControl


class HostRuleSimulationControl(SimulationControl):
    """Deterministic semantic baseline using only project-owned Host rules."""

    component_slot: ClassVar[str] = "SimulationControl"

    def simulate(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._fallback_result(
            input_payload,
            note="通用 Host 规则结算已启用。",
        )
