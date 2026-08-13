from dataclasses import dataclass, field
from typing import Dict, Iterable, Literal

from src.story_engine.simulation.randomness import DeterministicRandomStreams


CheckStream = Literal["world", "observation"]


DIFFICULTY_PROBABILITIES = {
    "trivial": 0.98,
    "easy": 0.80,
    "normal": 0.60,
    "hard": 0.35,
    "extreme": 0.15,
    "impossible": 0.0,
}


@dataclass(frozen=True)
class CheckModifier:
    modifier_id: str
    delta: float
    reason: str


@dataclass(frozen=True)
class ProbabilityCheck:
    check_id: str
    actor: str
    difficulty: str = "normal"
    stream: CheckStream = "world"
    modifiers: tuple[CheckModifier, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    success: bool
    probability: float
    roll: float
    trace: Dict[str, object] = field(default_factory=dict)


class HostCheckResolver:
    """Fixed probability mapping and seeded rolls; agents never provide rolls."""

    def __init__(self, random_streams: DeterministicRandomStreams) -> None:
        self.random_streams = random_streams

    def resolve(
        self,
        check: ProbabilityCheck,
        *,
        step: int,
        world_version: int,
    ) -> CheckResult:
        difficulty = str(check.difficulty).strip().lower()
        if difficulty not in DIFFICULTY_PROBABILITIES:
            raise ValueError(f"unknown check difficulty: {difficulty}")
        if check.stream not in {"world", "observation"}:
            raise ValueError(f"invalid check stream: {check.stream}")
        modifiers = tuple(self._validated_modifiers(check.modifiers))
        base = DIFFICULTY_PROBABILITIES[difficulty]
        probability = min(
            1.0,
            max(0.0, base + sum(modifier.delta for modifier in modifiers)),
        )
        roll = self.random_streams.uniform(
            check.stream,
            check.check_id,
            check.actor,
            int(step),
            int(world_version),
        )
        return CheckResult(
            success=roll.value < probability,
            probability=probability,
            roll=roll.value,
            trace={
                "check_id": check.check_id,
                "actor": check.actor,
                "difficulty": difficulty,
                "stream": check.stream,
                "base_probability": base,
                "modifiers": [
                    {
                        "modifier_id": modifier.modifier_id,
                        "delta": modifier.delta,
                        "reason": modifier.reason,
                    }
                    for modifier in modifiers
                ],
                "probability": probability,
                "roll": roll.value,
                "roll_key": roll.key,
                "success": roll.value < probability,
            },
        )

    @staticmethod
    def _validated_modifiers(
        modifiers: Iterable[CheckModifier],
    ) -> Iterable[CheckModifier]:
        for modifier in modifiers:
            if not modifier.modifier_id or not modifier.reason:
                raise ValueError("check modifiers require id and reason")
            if not -0.5 <= float(modifier.delta) <= 0.5:
                raise ValueError("check modifier delta must be between -0.5 and 0.5")
            yield modifier
