from typing import Dict, Any, List
from pydantic import Field
from src.story_engine.core.component import Component
from src.story_engine.scenarios.config import DramaConfig


class DramaState(Component):
    """
    Tracks pacing pressure and converts it into structured directing hints.
    """
    tension: float = 0.4
    target_min: float = 0.4
    target_max: float = 0.75
    crisis_threshold: float = 0.25
    recovery_bias: float = 0.05
    last_directive: str = "stay_course"
    recent_forces: List[str] = Field(default_factory=list)

    @classmethod
    def from_config(cls, config: DramaConfig) -> "DramaState":
        return cls(
            tension=config.initial_tension,
            target_min=config.target_min,
            target_max=config.target_max,
            crisis_threshold=config.crisis_threshold,
            recovery_bias=config.recovery_bias,
        )

    def apply_delta(self, delta: float) -> float:
        self.tension = min(1.0, max(0.0, self.tension + delta))
        return self.tension

    def build_directive(self) -> Dict[str, Any]:
        directive = "stay_course"
        instruction = "保持因果推进，不要凭空制造戏剧。"

        if self.tension < self.crisis_threshold:
            directive = "inject_crisis"
            instruction = "当前存在较强的危机机会；仅在已提交行动和世界因果自然支持时显化。"
        elif self.tension > self.target_max:
            directive = "allow_release"
            instruction = "当前适合容纳节奏回落，但不能抹除已有后果。"
        elif self.tension < self.target_min:
            directive = "raise_pressure"
            instruction = "隐藏风险具有较高显著性；角色是否触发仍由其行动和处境决定。"

        self.last_directive = directive
        self.recent_forces.append(directive)
        if len(self.recent_forces) > 8:
            self.recent_forces = self.recent_forces[-8:]

        return {
            "directive": directive,
            "instruction": instruction,
            "tension": round(self.tension, 3),
        }
