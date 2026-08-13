from typing import Any, Dict

from src.story_engine.core.entity import Entity
from src.story_engine.environment.escrows import ContractEscrowSettlement
from src.story_engine.systems.system import System


class AgreementSystem(System):
    """Advance Agreement Entities and their compiled execution clauses.

    The live runtime accepts only the session-owned AgreementRegistry.  The
    former GM-owned ContractState adapter is intentionally not a fallback: an
    absent registry is an architecture error, not an alternate state model.
    """

    def update(self, entities: Dict[str, Entity], context: Dict[str, Any]) -> None:
        registry = context.get("agreement_registry")
        if registry is None:
            context["agreement_transitions"] = []
            context["agreement_escrow_errors"] = [
                "AgreementSystem requires the session AgreementRegistry"
            ]
            return

        clock = context.get("clock")
        step = clock.current_step if clock else 0
        obligations = {
            name: state
            for name, entity in entities.items()
            if (state := entity.get_component("ObligationState")) is not None
        }
        scene_state = next(
            (
                state
                for entity in entities.values()
                if (state := entity.get_component("SceneState")) is not None
            ),
            None,
        )

        book = registry.to_book()
        transitions = list(book.advance_to(step))
        transitions.extend(book.refresh_performance(obligations))
        settlement = ContractEscrowSettlement()
        resolution = settlement.settle_ready(
            scene_state,
            book,
            current_step=step,
        )
        errors = list(resolution.errors)
        if not errors and resolution.transitions:
            settlement.commit(scene_state, book, resolution)
            transitions.extend(resolution.transitions)
        registry.apply_book(book, entities)

        context["agreement_transitions"] = transitions
        context["agreement_escrow_errors"] = errors
