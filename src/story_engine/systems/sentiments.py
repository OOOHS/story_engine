from copy import deepcopy
from typing import Any, Dict

from src.story_engine.components.sentiment_state import SentimentState
from src.story_engine.core.entity import Entity
from src.story_engine.social.sentiments import SentimentDynamics
from src.story_engine.systems.system import System


class SentimentSystem(System):
    """Advance private sentiments and derive them from committed social impacts."""

    def __init__(self, definitions: Dict[str, Any] | None = None) -> None:
        super().__init__()
        self.dynamics = SentimentDynamics(definitions)

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        clock = context.get("clock")
        step = clock.current_step if clock else 0
        live_states = {
            name: state
            for name, entity in entities.items()
            if (state := entity.get_component("SentimentState")) is not None
        }
        decay_transitions = []
        for name, state in live_states.items():
            for transition in state.advance_to(step):
                decay_transitions.append({"actor": name, **transition})

        transaction = context.get("state_transaction", {})
        result = context.get("simulation_result", {})
        self_reports = context.get("agent_sentiment_updates", [])
        committed_social_impacts = (
            result.get("social_impacts", [])
            if transaction.get("committed")
            else []
        )
        if not committed_social_impacts and not self_reports:
            context["sentiment_updates"] = []
            context["sentiment_errors"] = []
            context["sentiment_transitions"] = decay_transitions
            return

        scene_state = self._get_scene_state(entities)
        registry = context.get("relation_registry")
        relationship_book = registry.to_relationship_book() if registry else None
        staged_states = {
            name: SentimentState(**deepcopy(state.model_dump()))
            for name, state in live_states.items()
        }
        # The listener speaks for herself first. Once she has, the GM's guess
        # about how she feels for the same actor this tick is redundant and
        # gets dropped rather than double-applied on top of her own account.
        self_applied, self_errors = self.dynamics.apply_self_reported(
            sentiment_states=staged_states,
            scene_state=scene_state,
            relationship_book=relationship_book,
            updates=self_reports,
            current_step=step,
        )
        self_resolved = {item["affected"] for item in self_applied}
        gm_impacts = [
            impact
            for impact in committed_social_impacts
            if not (
                isinstance(impact, dict)
                and str(impact.get("affected", "")).strip() in self_resolved
            )
        ]
        applied, errors = self.dynamics.apply(
            sentiment_states=staged_states,
            scene_state=scene_state,
            relationship_book=relationship_book,
            result={
                "resolved_actions": result.get("resolved_actions", []),
                "social_impacts": gm_impacts,
            },
            current_step=step,
            observation_windows=context.get("actor_observation_windows", {}),
        )
        applied = self_applied + applied
        errors = self_errors + errors
        if errors:
            context["sentiment_updates"] = []
            context["sentiment_errors"] = errors
            context["sentiment_transitions"] = decay_transitions
            return

        relation_snapshot = registry.snapshot() if registry else None
        sentiment_snapshots = {
            name: SentimentState(**deepcopy(state.model_dump()))
            for name, state in live_states.items()
        }
        try:
            for name, staged in staged_states.items():
                live_states[name].restore_from(staged)
            if registry and relationship_book is not None:
                registry.apply_relationship_book(relationship_book, entities)
        except Exception as exc:
            for name, snapshot in sentiment_snapshots.items():
                live_states[name].restore_from(snapshot)
            if registry and relation_snapshot is not None:
                registry.restore(relation_snapshot, entities)
            applied = []
            errors = [f"sentiment publication failed: {type(exc).__name__}:{exc}"]

        context["sentiment_updates"] = applied
        context["sentiment_errors"] = errors
        context["sentiment_transitions"] = decay_transitions

    @staticmethod
    def _get_scene_state(entities: Dict[str, Entity]) -> Any:
        for entity in entities.values():
            state = entity.get_component("SceneState")
            if state is not None:
                return state
        return None
