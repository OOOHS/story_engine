from typing import Any, Dict, Iterable, List
from hashlib import sha256


class AgreementOfferEngine:
    """Expose Host-owned offer templates without leaking their executable terms."""

    def build_opportunities(
        self,
        scenario: Any,
        *,
        actor_name: str,
        scene_state: Any,
        agreement_registry: Any,
        visible_actors: Iterable[str],
        visible_objects: Iterable[str] = (),
        map_knowledge: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        if scenario is None or scene_state is None:
            return []
        visible = {str(item).strip() for item in visible_actors if str(item).strip()}
        existing = agreement_registry.to_book().agreements if agreement_registry else {}
        opportunities = []
        for template in getattr(scenario, "agreement_offer_templates", []) or []:
            if template.proposer != actor_name or template.agreement_id in existing:
                continue
            counterparties = [party for party in template.parties if party != actor_name]
            if not counterparties or not set(counterparties).issubset(visible):
                continue
            if not self._references_available(template, scene_state):
                continue
            opportunities.append(
                {
                    "template_id": template.template_id,
                    "agreement_id": template.agreement_id,
                    "title": template.title,
                    "summary": template.summary,
                    "parties": list(template.parties),
                    "counterparties": counterparties,
                }
            )
        opportunities.sort(key=lambda item: item["template_id"])
        opportunities.extend(
            self._asset_opportunities(
                actor_name=actor_name,
                scene_state=scene_state,
                visible_actors=visible,
                visible_objects=visible_objects,
                map_knowledge=map_knowledge or {},
            )
        )
        return opportunities

    @staticmethod
    def _asset_opportunities(
        *,
        actor_name: str,
        scene_state: Any,
        visible_actors: Iterable[str],
        visible_objects: Iterable[str],
        map_knowledge: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        by_owner: Dict[str, List[str]] = {}
        current_location = str(
            scene_state.get_actor_location(actor_name) or ""
        ).strip()
        known_locations = {
            str(item).strip()
            for item in map_knowledge.get("known_locations", []) or []
            if str(item).strip()
        }
        destination_options = sorted(
            known_locations - {current_location}
        )
        visible = {str(item).strip() for item in visible_objects if str(item).strip()}
        for object_id in visible:
            state = scene_state.get_object_state(object_id)
            owner = str(state.get("owner") or "").strip()
            if (
                owner
                and owner in scene_state.actor_states
                and not scene_state.is_location(object_id)
                and bool(state.get("portable", True))
                and not bool(state.get("hidden", False))
            ):
                by_owner.setdefault(owner, []).append(str(object_id))
        own = sorted(by_owner.get(actor_name, []))
        opportunities = []
        for counterparty in sorted(set(visible_actors) - {actor_name}):
            theirs = sorted(by_owner.get(counterparty, []))
            if not own and not theirs:
                continue
            opportunities.append(
                {
                    "opportunity_kind": "asset_offer",
                    "counterparty": counterparty,
                    "give_options": own,
                    "request_options": theirs,
                    "max_refs_per_side": 4,
                }
            )
            if theirs:
                opportunities.append(
                    {
                        "opportunity_kind": "delivery_service_offer",
                        "provider": counterparty,
                        "recipient": actor_name,
                        "service_object_options": theirs,
                        "destination_options": destination_options,
                        "payment_options": own,
                        "deadline_options": ["urgent", "soon", "flexible"],
                    }
                )
        return opportunities

    @staticmethod
    def asset_offer_id(
        actor: str,
        counterparty: str,
        give_refs: Iterable[str],
        request_refs: Iterable[str],
        step: int,
    ) -> str:
        canonical = "\x1f".join(
            [actor, counterparty, str(int(step))]
            + sorted(give_refs)
            + ["->"]
            + sorted(request_refs)
        )
        return f"asset-offer:{sha256(canonical.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def delivery_service_id(
        proposer: str,
        provider: str,
        object_id: str,
        destination: str,
        payment_ref: str,
        deadline: str,
        step: int,
    ) -> str:
        canonical = "\x1f".join(
            [
                proposer,
                provider,
                object_id,
                destination,
                payment_ref,
                deadline,
                str(int(step)),
            ]
        )
        return f"delivery-service:{sha256(canonical.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _references_available(template: Any, scene_state: Any) -> bool:
        for transfer in list(template.transfers) + [
            item.get("transfer", {}) for item in template.escrows
            if isinstance(item, dict)
        ]:
            if not isinstance(transfer, dict):
                return False
            object_id = str(transfer.get("object_id", "")).strip()
            owner = str(transfer.get("from", "")).strip()
            state = scene_state.get_object_state(object_id) if object_id else {}
            if not state or (owner and str(state.get("owner", "")).strip() != owner):
                return False
            if bool(state.get("hidden", False)):
                return False
        return all(party in scene_state.actor_states for party in template.parties)

    @staticmethod
    def find_template(scenario: Any, template_id: str) -> Any:
        return next(
            (
                item
                for item in getattr(scenario, "agreement_offer_templates", []) or []
                if item.template_id == template_id
            ),
            None,
        )
