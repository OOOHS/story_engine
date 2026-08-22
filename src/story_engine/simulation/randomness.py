import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RandomRoll:
    stream: str
    key: str
    value: float


class DeterministicRandomStreams:
    """Order-independent, replayable random streams owned by the host."""

    # No character-choice stream: characters sample their own decisions inside
    # their own runtime, so the Host only owns objective outcomes and what a
    # given character manages to observe of them.
    STREAMS = {"world", "observation"}

    def __init__(self, seed: int | str) -> None:
        self.seed = str(seed)

    def uniform(self, stream: str, *parts: object) -> RandomRoll:
        stream_key = str(stream)
        if stream_key not in self.STREAMS:
            raise ValueError(f"unknown random stream: {stream_key}")
        key = "|".join(str(part) for part in parts)
        digest = hashlib.sha256(
            f"{self.seed}|{stream_key}|{key}".encode("utf-8")
        ).digest()
        integer = int.from_bytes(digest[:8], "big")
        value = integer / float(1 << 64)
        return RandomRoll(stream=stream_key, key=key, value=value)

    def weighted_choice(
        self,
        items: Sequence[T],
        weights: Iterable[float],
        *,
        stream: str,
        key_parts: Sequence[object],
    ) -> tuple[T, RandomRoll]:
        if not items:
            raise ValueError("weighted_choice requires at least one item")
        normalized = [max(0.0, float(weight)) for weight in weights]
        total = sum(normalized)
        if total <= 0:
            normalized = [1.0 for _ in items]
            total = float(len(items))
        roll = self.uniform(stream, *key_parts)
        cursor = roll.value * total
        cumulative = 0.0
        for item, weight in zip(items, normalized):
            cumulative += weight
            if cursor < cumulative:
                return item, roll
        return items[-1], roll
