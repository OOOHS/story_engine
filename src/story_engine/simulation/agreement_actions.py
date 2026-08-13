from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable

from src.story_engine.environment.agreement_offers import AgreementOfferEngine


@dataclass(frozen=True)
class AgreementActionResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, str], ...] = ()


class AgreementActionResolver:
    """Compile validated Agent references into exact Host-owned Agreement updates."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        intents: Iterable[Dict[str, Any]],
        scenario: Any,
        current_step: int,
    ) -> AgreementActionResolution:
        templates = list(getattr(scenario, "agreement_offer_templates", []) or [])
        # The semantic layer never owns durable Agreement writes. All formal
        # changes are reconstructed from Input-validated Agent references.
        result["agreement_updates"] = []
        result["contract_updates"] = []
        references = {
            str(item.get("actor", "")).strip(): item
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("action_kind", "")).strip() == "communicate"
            and str(item.get("action_agreement_operation", "")).strip()
        }
        traces = []
        updates = []
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            reference = references.get(actor)
            if reference is None:
                continue
            operation = str(reference.get("action_agreement_operation", "")).strip()
            agreement_id = str(reference.get("action_agreement_id", "")).strip()
            if str(action.get("outcome", "")).strip() not in self.POSITIVE_OUTCOMES:
                traces.append({
                    "actor": actor,
                    "agreement_id": agreement_id,
                    "operation": operation,
                    "status": "semantic_action_not_positive",
                })
                continue
            if operation == "propose":
                template_id = str(
                    reference.get("action_agreement_template_id", "")
                ).strip()
                if template_id:
                    template = AgreementOfferEngine.find_template(scenario, template_id)
                    if (
                        template is None
                        or template.proposer != actor
                        or template.agreement_id != agreement_id
                    ):
                        traces.append({
                            "actor": actor,
                            "agreement_id": agreement_id,
                            "operation": operation,
                            "status": "host_template_unavailable",
                        })
                        continue
                    update = {
                        "operation": "propose",
                        "agreement_id": template.agreement_id,
                        "actor": template.proposer,
                        "parties": list(template.parties),
                        "title": template.title,
                        "summary": template.summary,
                        "expires_step": int(current_step) + template.expires_after_steps,
                        "transfers": deepcopy(template.transfers),
                        "services": deepcopy(template.services),
                        "escrows": deepcopy(template.escrows),
                        "delegations": deepcopy(template.delegations),
                        "reason": str(reference.get("intent", "")).strip()
                        or f"{actor}提出了协议“{template.title}”",
                    }
                else:
                    service_object = str(
                        reference.get("action_agreement_service_object", "")
                    ).strip()
                    if service_object:
                        update = self._delivery_service_update(
                            reference,
                            actor=actor,
                            agreement_id=agreement_id,
                            current_step=current_step,
                        )
                        updates.append(update)
                        action["action_kind"] = "communicate"
                        traces.append({
                            "actor": actor,
                            "agreement_id": agreement_id,
                            "operation": operation,
                            "status": "host_agreement_action_materialized",
                        })
                        continue
                    give_refs = list(reference.get("action_agreement_give_refs", []) or [])
                    request_refs = list(
                        reference.get("action_agreement_request_refs", []) or []
                    )
                    counterparty = str(reference.get("action_target", "")).strip()
                    if not counterparty or not (give_refs or request_refs):
                        continue
                    transfers = [
                        {"from": actor, "to": counterparty, "object_id": item}
                        for item in give_refs
                    ] + [
                        {"from": counterparty, "to": actor, "object_id": item}
                        for item in request_refs
                    ]
                    update = {
                        "operation": "propose",
                        "agreement_id": agreement_id,
                        "actor": actor,
                        "parties": [actor, counterparty],
                        "title": "资产报价",
                        "summary": str(reference.get("intent", "")).strip(),
                        "expires_step": int(current_step) + 8,
                        "transfers": transfers,
                        "services": [],
                        "escrows": [],
                        "delegations": [],
                        "reason": str(reference.get("intent", "")).strip()
                        or f"{actor}提出资产报价",
                    }
            else:
                update = {
                    "operation": operation,
                    "agreement_id": agreement_id,
                    "actor": actor,
                    "reason": str(reference.get("intent", "")).strip()
                    or f"{actor}对协议作出{operation}回应",
                }
            updates.append(update)
            action["action_kind"] = "communicate"
            traces.append({
                "actor": actor,
                "agreement_id": agreement_id,
                "operation": operation,
                "status": "host_agreement_action_materialized",
            })
        result["agreement_updates"] = updates
        result["contract_updates"] = updates
        return AgreementActionResolution(result=result, traces=tuple(traces))

    @staticmethod
    def _delivery_service_update(
        reference: Dict[str, Any],
        *,
        actor: str,
        agreement_id: str,
        current_step: int,
    ) -> Dict[str, Any]:
        provider = str(reference.get("action_target", "")).strip()
        object_id = str(reference.get("action_agreement_service_object", "")).strip()
        payment_ref = str(reference.get("action_agreement_payment_ref", "")).strip()
        destination = str(
            reference.get("action_agreement_service_destination", "")
        ).strip()
        deadline = str(reference.get("action_agreement_deadline", "")).strip()
        due_after_steps = {"urgent": 1, "soon": 3, "flexible": 12}[deadline]
        obligation_id = f"service:{agreement_id.split(':', 1)[-1]}"
        completion_condition = (
            {
                "scope": "world_object",
                "target": object_id,
                "path": "location",
                "operator": "eq",
                "value": destination,
            }
            if destination
            else {
                "scope": "world_object",
                "target": object_id,
                "path": "owner",
                "operator": "eq",
                "value": actor,
            }
        )
        service = {
            "actor": provider,
            "creditor": actor,
            "obligation_id": obligation_id,
            "title": (
                f"把{object_id}送到{destination}"
                if destination
                else f"把{object_id}交给{actor}"
            ),
            "summary": str(reference.get("intent", "")).strip(),
            "due_after_steps": due_after_steps,
            "grace_steps": 0,
            "wake_before_steps": 1,
            "delegation_policy": "bilateral",
            "completion_conditions": [completion_condition],
        }
        escrows = []
        if payment_ref:
            escrows.append({
                "transfer": {"from": actor, "object_id": payment_ref},
                "release_to": provider,
                "refund_to": actor,
                "release_on_service": obligation_id,
                "refund_on": ["breached", "cancelled"],
            })
        return {
            "operation": "propose",
            "agreement_id": agreement_id,
            "actor": actor,
            "parties": [actor, provider],
            "title": "交付委托",
            "summary": str(reference.get("intent", "")).strip(),
            "expires_step": int(current_step) + 8,
            "transfers": [],
            "services": [service],
            "escrows": escrows,
            "delegations": [],
            "reason": str(reference.get("intent", "")).strip()
            or f"{actor}提出交付委托",
        }
