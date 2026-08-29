from dataclasses import dataclass
from typing import Any, Dict, List

from src.story_engine.common.observation_window import shares_action_location


@dataclass(frozen=True)
class SentimentDefinition:
    kind: str
    valence: float
    duration_steps: int
    decay_per_step: float
    track_effects: Dict[str, float]


SENTIMENT_DEFINITIONS = {
    "grateful": SentimentDefinition(
        "grateful", 1.0, 10, 0.06,
        {"favor": 0.22, "trust": 0.16},
    ),
    "admiring": SentimentDefinition(
        "admiring", 0.8, 12, 0.04,
        {"favor": 0.18, "trust": 0.08},
    ),
    "hurt": SentimentDefinition(
        "hurt", -0.8, 12, 0.05,
        {"favor": -0.16, "trust": -0.18},
    ),
    "angry": SentimentDefinition(
        "angry", -1.0, 8, 0.08,
        {"malice": 0.2, "favor": -0.12},
    ),
    "afraid": SentimentDefinition(
        "afraid", -0.7, 10, 0.06,
        {"trust": -0.08},
    ),
    "suspicious": SentimentDefinition(
        "suspicious", -0.45, 14, 0.04,
        {"trust": -0.2},
    ),
    "betrayed": SentimentDefinition(
        "betrayed", -1.0, 20, 0.03,
        {"trust": -0.35, "favor": -0.18, "malice": 0.12},
    ),
    "relieved": SentimentDefinition(
        "relieved", 0.55, 6, 0.1,
        {"favor": 0.06},
    ),
}


class SentimentDynamics:
    """Validate committed social impacts and derive private sentiments."""

    MAX_IMPACTS = 12

    def __init__(
        self,
        definitions: Dict[str, SentimentDefinition] | None = None,
    ) -> None:
        self.definitions = dict(definitions or SENTIMENT_DEFINITIONS)

    def apply(
        self,
        *,
        sentiment_states: Dict[str, Any],
        scene_state: Any,
        relationship_book: Any,
        result: Dict[str, Any],
        current_step: int,
        observation_windows: Any = None,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        impacts = result.get("social_impacts", [])
        if not isinstance(impacts, list):
            return [], ["social_impacts must be a list"]
        if len(impacts) > self.MAX_IMPACTS:
            return [], [f"social_impacts cannot exceed {self.MAX_IMPACTS} per turn"]
        actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        applied: List[Dict[str, Any]] = []
        errors: List[str] = []
        for index, impact in enumerate(impacts):
            prefix = f"social_impacts[{index}]"
            if not isinstance(impact, dict):
                errors.append(f"{prefix} must be an object")
                continue
            source = self._text(impact.get("source"), 120)
            affected = self._text(impact.get("affected"), 120)
            kind = self._text(impact.get("kind"), 40).lower()
            reason = self._text(impact.get("reason"), 500)
            # The model may describe an impact, but it cannot choose its
            # provenance.  The committed source action is the authority.
            source_event = f"resolved_action:step:{int(current_step)}:actor:{source}"
            definition = self.definitions.get(kind)
            known_actors = set(scene_state.actor_states) if scene_state else set()
            if source not in known_actors or affected not in known_actors or source == affected:
                errors.append(f"{prefix} requires two distinct existing characters")
            if definition is None:
                errors.append(f"{prefix} has unknown sentiment kind: {kind}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            try:
                magnitude = float(impact.get("magnitude", 0.5))
                if not 0.05 <= magnitude <= 1.0:
                    errors.append(f"{prefix}.magnitude must be between 0.05 and 1")
            except (TypeError, ValueError):
                magnitude = 0.5
                errors.append(f"{prefix}.magnitude must be numeric")
            evidence = next(
                (
                    action for action in actions
                    if str(action.get("actor", "")).strip() == source
                    and str(action.get("visibility", "public")) != "hidden"
                    and str(action.get("outcome", ""))
                    in {"success", "partial", "complication"}
                    and shares_action_location(
                        source,
                        affected,
                        str(action.get("location", "")).strip(),
                        scene_state,
                        observation_windows,
                    )
                ),
                None,
            )
            if evidence is None:
                errors.append(f"{prefix} lacks an observable committed source action")
            sentiment_state = sentiment_states.get(affected)
            if sentiment_state is None:
                errors.append(f"{prefix} affected actor has no SentimentState: {affected}")
            if any(error.startswith(prefix) for error in errors):
                continue
            record = sentiment_state.upsert(
                toward=source,
                kind=kind,
                magnitude=magnitude,
                valence=definition.valence,
                reason=reason,
                current_step=current_step,
                duration_steps=definition.duration_steps,
                decay_per_step=definition.decay_per_step,
                source_event=source_event,
            )
            track_changes = {}
            if relationship_book is not None:
                deltas = {
                    f"{track_id}_delta": delta * magnitude
                    for track_id, delta in definition.track_effects.items()
                }
                before = relationship_book.get_metrics(affected, source)
                after = relationship_book.apply_delta(
                    affected,
                    source,
                    current_step=current_step,
                    reason=f"sentiment:{kind}:{reason}",
                    provenance={
                        "source_kind": "sentiment",
                        "source_ref": f"{affected}:{record.sentiment_id}",
                        "source_event": source_event,
                    },
                    **deltas,
                )
                track_changes = {
                    track_id: after.get(track_id, 0.0) - before.get(track_id, 0.0)
                    for track_id in definition.track_effects
                }
            applied.append(
                {
                    "source": source,
                    "affected": affected,
                    "sentiment_id": record.sentiment_id,
                    "kind": kind,
                    "intensity": record.intensity,
                    "track_changes": track_changes,
                    "reason": reason,
                }
            )
        return applied, errors

    def apply_self_reported(
        self,
        *,
        sentiment_states: Dict[str, Any],
        scene_state: Any,
        relationship_book: Any,
        updates: List[Dict[str, Any]],
        current_step: int,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """Commit a character's own account of how she now feels.

        This is the listener's channel, not the GM's: her personality lives in
        her own agent, so nobody else is positioned to certify or invent how
        she feels about something she witnessed. Unlike GM-authored
        ``social_impacts`` it needs no third-party observable-evidence lookup
        -- she is reporting her own interior state. The Host still enforces
        the same closed sentiment vocabulary, magnitude bounds and
        deterministic Relationship Track fallout as any other sentiment path,
        so this cannot be used to write a Relationship Track delta directly.
        """
        applied: List[Dict[str, Any]] = []
        errors: List[str] = []
        if not isinstance(updates, list):
            return applied, ["sentiment_updates must be a list"]
        if len(updates) > self.MAX_IMPACTS:
            return applied, [f"sentiment_updates cannot exceed {self.MAX_IMPACTS} per turn"]
        known_actors = set(scene_state.actor_states) if scene_state else set()
        for index, update in enumerate(updates):
            prefix = f"sentiment_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            affected = self._text(update.get("actor"), 120)
            toward = self._text(update.get("toward"), 120)
            kind = self._text(update.get("kind"), 40).lower()
            reason = self._text(update.get("reason"), 500)
            definition = self.definitions.get(kind)
            if not affected or not toward or affected == toward:
                errors.append(f"{prefix} requires two distinct actors")
            elif known_actors and (affected not in known_actors or toward not in known_actors):
                errors.append(f"{prefix} requires two existing characters")
            if definition is None:
                errors.append(f"{prefix} has unknown sentiment kind: {kind}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            try:
                magnitude = float(update.get("magnitude", 0.5))
                if not 0.05 <= magnitude <= 1.0:
                    errors.append(f"{prefix}.magnitude must be between 0.05 and 1")
            except (TypeError, ValueError):
                magnitude = 0.5
                errors.append(f"{prefix}.magnitude must be numeric")
            sentiment_state = sentiment_states.get(affected)
            if sentiment_state is None:
                errors.append(f"{prefix} reporting actor has no SentimentState: {affected}")
            if any(error.startswith(prefix) for error in errors):
                continue
            source_event = f"self_reported:step:{int(current_step)}:actor:{affected}"
            record = sentiment_state.upsert(
                toward=toward,
                kind=kind,
                magnitude=magnitude,
                valence=definition.valence,
                reason=reason,
                current_step=current_step,
                duration_steps=definition.duration_steps,
                decay_per_step=definition.decay_per_step,
                source_event=source_event,
            )
            track_changes = {}
            if relationship_book is not None:
                deltas = {
                    f"{track_id}_delta": delta * magnitude
                    for track_id, delta in definition.track_effects.items()
                }
                before = relationship_book.get_metrics(affected, toward)
                after = relationship_book.apply_delta(
                    affected,
                    toward,
                    current_step=current_step,
                    reason=f"sentiment:{kind}:{reason}",
                    provenance={
                        "source_kind": "self_reported_sentiment",
                        "source_ref": f"{affected}:{record.sentiment_id}",
                        "source_event": source_event,
                    },
                    **deltas,
                )
                track_changes = {
                    track_id: after.get(track_id, 0.0) - before.get(track_id, 0.0)
                    for track_id in definition.track_effects
                }
            applied.append(
                {
                    "source": toward,
                    "affected": affected,
                    "sentiment_id": record.sentiment_id,
                    "kind": kind,
                    "intensity": record.intensity,
                    "track_changes": track_changes,
                    "reason": reason,
                    "self_reported": True,
                }
            )
        return applied, errors

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
