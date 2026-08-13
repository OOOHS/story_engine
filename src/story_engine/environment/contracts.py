from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List

from src.story_engine.social.agreement_registry import AgreementRecord

ContractRecord = AgreementRecord
from src.story_engine.motivation.obligations import ObligationDynamics


@dataclass
class ContractResolution:
    state: Any = field(repr=False)
    result: Dict[str, Any]
    errors: List[str] = field(default_factory=list)


class ContractDynamics:
    """Resolve persistent offers without granting models direct settlement power."""

    OPERATIONS = {"propose", "counter", "accept", "reject", "withdraw"}
    POSITIVE_OUTCOMES = {"success", "partial", "complication"}
    MAX_UPDATES = 12
    MAX_TRANSFERS = 8
    MAX_DELEGATIONS = 4
    MAX_SERVICES = 4
    MAX_ESCROWS = 4

    def __init__(self) -> None:
        self.obligations = ObligationDynamics()

    def resolve(
        self,
        contract_state: Any,
        scene_state: Any,
        obligation_states: Dict[str, Any],
        result: Dict[str, Any],
        *,
        current_step: int,
        proposal_actors: set[str] | None = None,
    ) -> ContractResolution:
        working_result = deepcopy(result)
        raw_exchanges = working_result.get("exchanges", [])
        if isinstance(raw_exchanges, list):
            working_result["exchanges"] = [
                item
                for item in raw_exchanges
                if not isinstance(item, dict) or not item.get("contract_id")
            ]
        raw_obligation_updates = working_result.get("obligation_updates", [])
        if isinstance(raw_obligation_updates, list):
            working_result["obligation_updates"] = [
                item
                for item in raw_obligation_updates
                if not isinstance(item, dict) or not item.get("contract_id")
            ]
        working_result["contract_settlements"] = []
        working_result["contract_authorizations"] = {}
        working_result["contract_escrow_deposits"] = []
        updates = working_result.get("agreement_updates")
        if updates is None or updates == []:
            updates = working_result.get("contract_updates", [])
        if not isinstance(updates, list):
            return ContractResolution(
                self._copy_state(contract_state),
                working_result,
                ["agreement_updates must be a list"],
            )
        normalized_updates = []
        for update in updates:
            if not isinstance(update, dict):
                normalized_updates.append(update)
                continue
            normalized = dict(update)
            if normalized.get("agreement_id") and not normalized.get("contract_id"):
                normalized["contract_id"] = normalized["agreement_id"]
            if normalized.get("new_agreement_id") and not normalized.get("new_contract_id"):
                normalized["new_contract_id"] = normalized["new_agreement_id"]
            normalized_updates.append(normalized)
        updates = normalized_updates
        working_result["agreement_updates"] = updates
        working_result["contract_updates"] = updates
        if not updates:
            return ContractResolution(
                self._copy_state(contract_state),
                working_result,
                [],
            )
        if contract_state is None:
            return ContractResolution(
                None,
                working_result,
                ["agreement_updates require an AgreementBook transaction view"],
            )
        if len(updates) > self.MAX_UPDATES:
            return ContractResolution(
                self._copy_state(contract_state),
                working_result,
                [f"agreement_updates cannot exceed {self.MAX_UPDATES} per turn"],
            )

        state = self._copy_state(contract_state)
        original_state = self._copy_state(contract_state)
        actions = [
            action
            for action in working_result.get("resolved_actions", [])
            if isinstance(action, dict)
        ]
        proposals = set(proposal_actors or set())
        errors: List[str] = []
        seen_contract_ids = set()
        settlements = []
        authorizations = {}

        for index, update in enumerate(updates):
            prefix = f"agreement_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            operation = str(update.get("operation", "")).strip()
            contract_id = self._text(update.get("contract_id"), 120)
            actor = self._text(update.get("actor"), 120)
            reason = self._text(update.get("reason"), 500)
            if operation not in self.OPERATIONS:
                errors.append(f"{prefix} has unknown operation: {operation}")
            if not contract_id:
                errors.append(f"{prefix} requires contract_id")
            elif contract_id in seen_contract_ids:
                errors.append(
                    f"{prefix} contract can only have one update per turn: {contract_id}"
                )
            seen_contract_ids.add(contract_id)
            if actor not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown actor: {actor}")
            if actor not in proposals:
                errors.append(f"{prefix} requires current-turn proposal from {actor}")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            actor_location = scene_state.get_actor_location(actor)
            if not self._has_action(actor, actor_location, actions):
                errors.append(f"{prefix} requires observable positive action from {actor}")
            if any(error.startswith(prefix) for error in errors):
                continue

            if operation == "propose":
                self._propose(
                    state,
                    scene_state,
                    obligation_states,
                    update,
                    contract_id=contract_id,
                    proposer=actor,
                    reason=reason,
                    current_step=current_step,
                    prefix=prefix,
                    errors=errors,
                )
                continue

            record = state.contracts.get(contract_id)
            if record is None:
                errors.append(f"{prefix} references unknown contract: {contract_id}")
                continue
            if record.status != "pending":
                errors.append(
                    f"{prefix} cannot update terminal contract: {contract_id}"
                )
                continue
            if int(current_step) > record.expires_step:
                errors.append(f"{prefix} contract has expired: {contract_id}")
                continue
            if actor not in record.parties:
                errors.append(f"{prefix} actor is not a contract party: {actor}")
                continue
            self._require_parties_co_located(
                record.parties,
                scene_state,
                prefix,
                errors,
            )
            if any(error.startswith(prefix) for error in errors):
                continue

            if operation == "accept":
                if actor in record.accepted_by:
                    errors.append(f"{prefix} actor already accepted contract: {actor}")
                    continue
                record.accepted_by = sorted(set(record.accepted_by + [actor]))
                if set(record.accepted_by) == set(record.parties):
                    record.status = "settled"
                    record.resolution_reason = reason
                    self._record_resolution_source(
                        record,
                        actor=actor,
                        current_step=current_step,
                    )
                    settlement = self._materialize(
                        record,
                        scene_state,
                        current_step=current_step,
                    )
                    settlements.append(settlement)
                    authorizations[contract_id] = {
                        "actors": list(record.parties),
                        "location": scene_state.get_actor_location(record.parties[0]),
                    }
                continue

            if operation == "counter":
                new_contract_id = self._text(update.get("new_contract_id"), 120)
                if not new_contract_id:
                    errors.append(f"{prefix} counter requires new_contract_id")
                    continue
                if new_contract_id == contract_id:
                    errors.append(
                        f"{prefix} counter new_contract_id must differ from contract_id"
                    )
                    continue
                if new_contract_id in seen_contract_ids:
                    errors.append(
                        f"{prefix} counter new_contract_id already used this turn: "
                        f"{new_contract_id}"
                    )
                    continue
                seen_contract_ids.add(new_contract_id)
                raw_parties = update.get("parties")
                if raw_parties is not None:
                    counter_parties = self._text_list(
                        raw_parties, limit=3, item_limit=120
                    )
                    if set(counter_parties) != set(record.parties) or len(
                        counter_parties
                    ) != len(record.parties):
                        errors.append(
                            f"{prefix} counter cannot add or remove contract parties"
                        )
                        continue
                replacement = deepcopy(update)
                replacement["parties"] = list(record.parties)
                before_errors = len(errors)
                self._propose(
                    state,
                    scene_state,
                    obligation_states,
                    replacement,
                    contract_id=new_contract_id,
                    proposer=actor,
                    reason=reason,
                    current_step=current_step,
                    prefix=prefix,
                    errors=errors,
                )
                if len(errors) != before_errors:
                    continue
                successor = state.contracts[new_contract_id]
                successor.countered_from = contract_id
                record.status = "countered"
                record.superseded_by = new_contract_id
                record.resolution_reason = reason
                self._record_resolution_source(
                    record,
                    actor=actor,
                    current_step=current_step,
                )
                continue

            if operation == "reject":
                if actor == record.proposer:
                    errors.append(f"{prefix} proposer must withdraw rather than reject")
                    continue
                record.status = "rejected"
                record.resolution_reason = reason
                self._record_resolution_source(
                    record,
                    actor=actor,
                    current_step=current_step,
                )
                continue

            if actor != record.proposer:
                errors.append(f"{prefix} only proposer can withdraw contract")
                continue
            record.status = "withdrawn"
            record.resolution_reason = reason
            self._record_resolution_source(
                record,
                actor=actor,
                current_step=current_step,
            )

        if errors:
            working_result["contract_settlements"] = []
            working_result["contract_authorizations"] = {}
            working_result["contract_escrow_deposits"] = []
            return ContractResolution(original_state, working_result, errors)

        for settlement in settlements:
            exchange = settlement.get("exchange")
            if exchange:
                working_result.setdefault("exchanges", []).append(exchange)
            working_result.setdefault("obligation_updates", []).extend(
                settlement.get("obligation_updates", [])
            )
            working_result.setdefault("contract_escrow_deposits", []).extend(
                settlement.get("escrow_deposits", [])
            )
        working_result["contract_settlements"] = settlements
        working_result["contract_authorizations"] = authorizations
        return ContractResolution(state, working_result, [])

    @staticmethod
    def _record_resolution_source(
        record: Any,
        *,
        actor: str,
        current_step: int,
    ) -> None:
        record.resolution_source_kind = "resolved_action"
        record.resolution_source_ref = (
            f"step:{int(current_step)}:actor:{str(actor).strip()}"
        )

    def _propose(
        self,
        state: Any,
        scene_state: Any,
        obligation_states: Dict[str, Any],
        update: Dict[str, Any],
        *,
        contract_id: str,
        proposer: str,
        reason: str,
        current_step: int,
        prefix: str,
        errors: List[str],
    ) -> None:
        if contract_id in state.contracts:
            errors.append(f"{prefix} contract already exists: {contract_id}")
        self._prune_terminal_history(state)
        if len(state.contracts) >= state.max_contracts:
            errors.append(f"{prefix} exceeds max_contracts")
        raw_parties = update.get("parties")
        parties = self._text_list(raw_parties, limit=3, item_limit=120)
        if (
            not isinstance(raw_parties, list)
            or not 2 <= len(raw_parties) <= 3
            or len(parties) != len(raw_parties)
            or len(set(parties)) != len(parties)
        ):
            errors.append(f"{prefix}.parties must contain two or three distinct actors")
        if proposer not in parties:
            errors.append(f"{prefix} proposer must be a contract party")
        title = self._text(update.get("title"), 240)
        if not title:
            errors.append(f"{prefix} propose requires title")
        for party in parties:
            if party not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown party: {party}")
        self._require_parties_co_located(parties, scene_state, prefix, errors)

        try:
            expires_step = int(update.get("expires_step"))
        except (TypeError, ValueError):
            expires_step = -1
            errors.append(f"{prefix}.expires_step must be an integer")
        if not current_step + 1 <= expires_step <= current_step + 20:
            errors.append(f"{prefix}.expires_step must be between current step +1 and +20")

        transfers = update.get("transfers", [])
        delegations = update.get("delegations", [])
        services = update.get("services", [])
        escrows = update.get("escrows", [])
        if not isinstance(transfers, list):
            errors.append(f"{prefix}.transfers must be a list")
            transfers = []
        if not isinstance(delegations, list):
            errors.append(f"{prefix}.delegations must be a list")
            delegations = []
        if not isinstance(services, list):
            errors.append(f"{prefix}.services must be a list")
            services = []
        if not isinstance(escrows, list):
            errors.append(f"{prefix}.escrows must be a list")
            escrows = []
        if not transfers and not delegations and not services and not escrows:
            errors.append(
                f"{prefix} requires transfers, delegations, services, or escrows"
            )
        if len(transfers) > self.MAX_TRANSFERS:
            errors.append(f"{prefix}.transfers has too many items")
        if len(delegations) > self.MAX_DELEGATIONS:
            errors.append(f"{prefix}.delegations has too many items")
        if len(services) > self.MAX_SERVICES:
            errors.append(f"{prefix}.services has too many items")
        if len(escrows) > self.MAX_ESCROWS:
            errors.append(f"{prefix}.escrows has too many items")
        normalized_transfers = self._validate_transfers(
            transfers[: self.MAX_TRANSFERS],
            parties,
            scene_state,
            prefix,
            errors,
        )
        normalized_delegations = self._validate_delegations(
            delegations[: self.MAX_DELEGATIONS],
            parties,
            scene_state,
            obligation_states,
            current_step,
            prefix,
            errors,
        )
        normalized_services = self._validate_services(
            services[: self.MAX_SERVICES],
            parties,
            scene_state,
            obligation_states,
            prefix,
            errors,
        )
        normalized_escrows = self._validate_escrows(
            escrows[: self.MAX_ESCROWS],
            parties,
            scene_state,
            normalized_services,
            prefix,
            errors,
        )
        claimed_objects = {
            transfer["object_id"] for transfer in normalized_transfers
        }
        for escrow in normalized_escrows:
            object_id = escrow["transfer"]["object_id"]
            if object_id in claimed_objects:
                errors.append(
                    f"{prefix} object cannot be both immediate transfer and escrow: "
                    f"{object_id}"
                )
            claimed_objects.add(object_id)
        if any(error.startswith(prefix) for error in errors):
            return
        state.contracts[contract_id] = ContractRecord(
            contract_id=contract_id,
            proposer=proposer,
            parties=parties,
            title=title,
            summary=self._text(update.get("summary"), 500),
            transfers=normalized_transfers,
            delegations=normalized_delegations,
            services=normalized_services,
            escrows=normalized_escrows,
            accepted_by=[proposer],
            status="pending",
            created_step=int(current_step),
            source_kind="resolved_action",
            source_ref=f"step:{int(current_step)}:actor:{proposer}",
            expires_step=expires_step,
            resolution_reason="",
            proposal_reason=reason,
        )

    def _validate_transfers(
        self,
        transfers: List[Any],
        parties: List[str],
        scene_state: Any,
        prefix: str,
        errors: List[str],
    ) -> List[Dict[str, Any]]:
        normalized = []
        claims: Dict[str, int] = {}
        for index, transfer in enumerate(transfers):
            label = f"{prefix}.transfers[{index}]"
            if not isinstance(transfer, dict):
                errors.append(f"{label} must be an object")
                continue
            source = self._text(transfer.get("from"), 120)
            recipient = self._text(transfer.get("to"), 120)
            object_id = self._text(transfer.get("object_id"), 120)
            if source not in parties or recipient not in parties or source == recipient:
                errors.append(f"{label} from/to must be different contract parties")
            if object_id not in scene_state.world_objects or scene_state.is_location(object_id):
                errors.append(f"{label} references unknown tangible object: {object_id}")
                continue
            state = scene_state.get_object_state(object_id)
            if self._text(state.get("owner"), 120) != source:
                errors.append(f"{label} source does not own object: {object_id}")
            if not bool(state.get("portable", True)):
                errors.append(f"{label} object is non-portable: {object_id}")
            if bool(state.get("hidden", False)):
                errors.append(f"{label} object must be disclosed: {object_id}")
            available = self._quantity(state)
            raw_quantity = transfer.get("quantity", available)
            if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
                errors.append(f"{label}.quantity must be a positive integer")
                quantity = 0
            else:
                quantity = int(raw_quantity)
                if quantity < 1:
                    errors.append(f"{label}.quantity must be a positive integer")
            claims[object_id] = claims.get(object_id, 0) + quantity
            if quantity < available and not self._text(state.get("stack_key"), 120):
                errors.append(f"{label} partial quantity requires stack_key")
            normalized.append(
                {
                    "from": source,
                    "to": recipient,
                    "object_id": object_id,
                    "quantity": quantity,
                }
            )
        for object_id, requested in claims.items():
            available = self._quantity(scene_state.get_object_state(object_id))
            if requested > available:
                errors.append(
                    f"{prefix} offered quantity exceeds current units: {object_id}"
                )
        transfer_actors = {
            actor
            for transfer in normalized
            for actor in (transfer["from"], transfer["to"])
        }
        if normalized and len(transfer_actors) != 2:
            errors.append(
                f"{prefix}.transfers must form one bilateral exchange"
            )
        return normalized

    def _validate_escrows(
        self,
        escrows: List[Any],
        parties: List[str],
        scene_state: Any,
        services: List[Dict[str, Any]],
        prefix: str,
        errors: List[str],
    ) -> List[Dict[str, Any]]:
        normalized = []
        service_ids = {service["obligation_id"] for service in services}
        seen_objects = set()
        for index, escrow in enumerate(escrows):
            label = f"{prefix}.escrows[{index}]"
            if not isinstance(escrow, dict):
                errors.append(f"{label} must be an object")
                continue
            transfer = escrow.get("transfer")
            if not isinstance(transfer, dict):
                errors.append(f"{label}.transfer must be an object")
                continue
            source = self._text(transfer.get("from"), 120)
            object_id = self._text(transfer.get("object_id"), 120)
            release_to = self._text(escrow.get("release_to"), 120)
            refund_to = self._text(escrow.get("refund_to"), 120)
            release_on_service = self._text(
                escrow.get("release_on_service"), 120
            )
            if source not in parties:
                errors.append(f"{label}.transfer.from must be a contract party")
            if release_to not in parties:
                errors.append(f"{label}.release_to must be a contract party")
            if refund_to not in parties:
                errors.append(f"{label}.refund_to must be a contract party")
            if release_on_service not in service_ids:
                errors.append(
                    f"{label}.release_on_service must reference a service in this contract"
                )
            if object_id in seen_objects:
                errors.append(
                    f"{label} cannot create multiple escrow lots from one object: "
                    f"{object_id}"
                )
            seen_objects.add(object_id)
            if object_id not in scene_state.world_objects or scene_state.is_location(
                object_id
            ):
                errors.append(f"{label} references unknown tangible object: {object_id}")
                continue
            state = scene_state.get_object_state(object_id)
            if self._text(state.get("owner"), 120) != source:
                errors.append(f"{label} source does not own object: {object_id}")
            if not bool(state.get("portable", True)):
                errors.append(f"{label} object is non-portable: {object_id}")
            if bool(state.get("hidden", False)):
                errors.append(f"{label} object must be disclosed: {object_id}")
            available = self._quantity(state)
            raw_quantity = transfer.get("quantity", available)
            if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
                errors.append(f"{label}.transfer.quantity must be a positive integer")
                quantity = 0
            else:
                quantity = int(raw_quantity)
                if quantity < 1:
                    errors.append(
                        f"{label}.transfer.quantity must be a positive integer"
                    )
            if quantity > available:
                errors.append(f"{label} escrow quantity exceeds current units: {object_id}")
            if quantity < available and not self._text(state.get("stack_key"), 120):
                errors.append(f"{label} partial quantity requires stack_key")
            raw_refund_on = escrow.get("refund_on", ["breached", "cancelled"])
            refund_on = self._text_list(raw_refund_on, limit=2, item_limit=20)
            if (
                not isinstance(raw_refund_on, list)
                or not refund_on
                or len(refund_on) != len(raw_refund_on)
                or len(set(refund_on)) != len(refund_on)
                or not set(refund_on).issubset({"breached", "cancelled"})
            ):
                errors.append(
                    f"{label}.refund_on must contain unique breached/cancelled statuses"
                )
            normalized.append(
                {
                    "transfer": {
                        "from": source,
                        "object_id": object_id,
                        "quantity": quantity,
                    },
                    "release_to": release_to,
                    "refund_to": refund_to,
                    "release_on_service": release_on_service,
                    "refund_on": sorted(set(refund_on)),
                }
            )
        normalized.sort(
            key=lambda item: (
                item["transfer"]["object_id"],
                item["release_on_service"],
                item["transfer"]["from"],
            )
        )
        return normalized

    def _validate_delegations(
        self,
        delegations: List[Any],
        parties: List[str],
        scene_state: Any,
        obligation_states: Dict[str, Any],
        current_step: int,
        prefix: str,
        errors: List[str],
    ) -> List[Dict[str, Any]]:
        normalized = []
        for index, delegation in enumerate(delegations):
            label = f"{prefix}.delegations[{index}]"
            if not isinstance(delegation, dict):
                errors.append(f"{label} must be an object")
                continue
            debtor = self._text(delegation.get("actor"), 120)
            delegate = self._text(delegation.get("delegate"), 120)
            obligation_id = self._text(delegation.get("obligation_id"), 120)
            if debtor not in parties or delegate not in parties or debtor == delegate:
                errors.append(f"{label} debtor/delegate must be different contract parties")
            state = obligation_states.get(debtor)
            if obligation_states.get(delegate) is None:
                errors.append(f"{label} delegate has no ObligationState: {delegate}")
            record = state.obligations.get(obligation_id) if state else None
            if record is None:
                errors.append(f"{label} references unknown obligation: {obligation_id}")
                continue
            if record.status not in {"scheduled", "due"}:
                errors.append(f"{label} obligation is not active: {obligation_id}")
            if state.effective_status(record, current_step) == "breached":
                errors.append(f"{label} obligation is already expired: {obligation_id}")
            if record.delegation_policy == "forbidden":
                errors.append(f"{label} obligation forbids delegation: {obligation_id}")
            creditor = record.creditor
            if (
                record.delegation_policy == "creditor_consent"
                and creditor
                and creditor not in {debtor, delegate}
                and creditor not in parties
            ):
                errors.append(f"{label} creditor must be a contract party: {creditor}")
            visible = set(scene_state.get_visible_objects(delegate))
            for condition in record.completion_conditions:
                scope = condition.get("scope")
                target = self._text(condition.get("target"), 120)
                if scope == "actor" and target != debtor:
                    errors.append(f"{label} cannot rewrite non-debtor actor condition")
                elif scope == "world_object" and target not in visible:
                    errors.append(f"{label} completion object is not visible to delegate")
                elif scope not in {"actor", "world_object"}:
                    errors.append(f"{label} completion scope is unsafe to delegate")
            normalized.append(
                {
                    "actor": debtor,
                    "delegate": delegate,
                    "obligation_id": obligation_id,
                    "delegate_pressure_need": self._text(
                        delegation.get("delegate_pressure_need"), 80
                    )
                    or None,
                    "creditor": creditor,
                }
            )
        return normalized

    def _validate_services(
        self,
        services: List[Any],
        parties: List[str],
        scene_state: Any,
        obligation_states: Dict[str, Any],
        prefix: str,
        errors: List[str],
    ) -> List[Dict[str, Any]]:
        normalized = []
        seen_ids = set()
        for index, service in enumerate(services):
            label = f"{prefix}.services[{index}]"
            if not isinstance(service, dict):
                errors.append(f"{label} must be an object")
                continue
            debtor = self._text(service.get("actor"), 120)
            creditor = self._text(service.get("creditor"), 120) or None
            obligation_id = self._text(service.get("obligation_id"), 120)
            title = self._text(service.get("title"), 240)
            if debtor not in parties:
                errors.append(f"{label} debtor must be a contract party")
            if creditor and creditor not in parties:
                errors.append(f"{label} creditor must be a contract party")
            state = obligation_states.get(debtor)
            if state is None:
                errors.append(f"{label} debtor has no ObligationState: {debtor}")
            elif obligation_id in state.obligations:
                errors.append(f"{label} obligation id already exists: {obligation_id}")
            elif len(state.obligations) >= state.max_obligations:
                errors.append(f"{label} debtor has no obligation capacity")
            if not obligation_id:
                errors.append(f"{label} requires obligation_id")
            elif obligation_id in seen_ids:
                errors.append(f"{label} duplicates service obligation id: {obligation_id}")
            seen_ids.add(obligation_id)
            if not title:
                errors.append(f"{label} requires title")
            due_after_steps = self._bounded_int(
                service.get("due_after_steps"),
                1,
                50,
                f"{label}.due_after_steps",
                errors,
            )
            grace_steps = self._bounded_int(
                service.get("grace_steps", 0),
                0,
                20,
                f"{label}.grace_steps",
                errors,
            )
            wake_before_steps = self._bounded_int(
                service.get("wake_before_steps", 1),
                0,
                20,
                f"{label}.wake_before_steps",
                errors,
            )
            delegation_policy = str(
                service.get("delegation_policy", "creditor_consent")
            ).strip()
            if delegation_policy not in self.obligations.DELEGATION_POLICIES:
                errors.append(f"{label} has invalid delegation_policy")
            conditions = self.obligations.validate_dynamic_completion_conditions(
                label,
                debtor,
                scene_state,
                service.get("completion_conditions", []),
                errors,
            )
            if not conditions:
                errors.append(f"{label} requires authoritative completion_conditions")
            visible_by_party = {
                party: set(scene_state.get_visible_objects(party))
                for party in parties
            }
            for condition in conditions:
                if condition.get("scope") != "world_object":
                    continue
                target = self._text(condition.get("target"), 120)
                for party, visible in visible_by_party.items():
                    if target not in visible:
                        errors.append(
                            f"{label} completion object is not visible to party: {party}"
                        )
            normalized.append(
                {
                    "actor": debtor,
                    "creditor": creditor,
                    "obligation_id": obligation_id,
                    "title": title,
                    "summary": self._text(service.get("summary"), 500),
                    "due_after_steps": due_after_steps,
                    "grace_steps": grace_steps,
                    "wake_before_steps": wake_before_steps,
                    "delegation_policy": delegation_policy,
                    "completion_conditions": conditions,
                }
            )
        return normalized

    def _materialize(
        self,
        record: ContractRecord,
        scene_state: Any,
        *,
        current_step: int,
    ) -> Dict[str, Any]:
        transfer_parties = []
        for transfer in record.transfers:
            for actor in (transfer["from"], transfer["to"]):
                if actor not in transfer_parties:
                    transfer_parties.append(actor)
        exchange = None
        if record.transfers:
            exchange = {
                "exchange_id": f"contract:{record.contract_id}",
                "contract_id": record.contract_id,
                "parties": transfer_parties,
                "accepted_by": transfer_parties,
                "transfers": deepcopy(record.transfers),
                "reason": f"跨回合契约 {record.contract_id} 获得全部参与者接受",
            }
        obligation_updates = []
        for item in record.delegations:
            update = {
                "operation": "delegate",
                "contract_id": record.contract_id,
                "actor": item["actor"],
                "source": item["actor"],
                "obligation_id": item["obligation_id"],
                "delegate": item["delegate"],
                "accepted_by": item["delegate"],
                "reason": f"跨回合契约 {record.contract_id} 完成责任转交",
            }
            if item.get("delegate_pressure_need"):
                update["delegate_pressure_need"] = item["delegate_pressure_need"]
            creditor = item.get("creditor")
            if creditor and creditor not in {item["actor"], item["delegate"]}:
                update["approved_by"] = creditor
            obligation_updates.append(update)
        performance_links = []
        for service in record.services:
            obligation_updates.append(
                {
                    "operation": "create",
                    "contract_id": record.contract_id,
                    "actor": service["actor"],
                    "source": service["actor"],
                    "obligation_id": service["obligation_id"],
                    "title": service["title"],
                    "summary": service.get("summary", ""),
                    "creditor": service.get("creditor"),
                    "due_step": int(current_step)
                    + int(service["due_after_steps"]),
                    "grace_steps": int(service.get("grace_steps", 0)),
                    "wake_before_steps": int(service.get("wake_before_steps", 1)),
                    "delegation_policy": service.get(
                        "delegation_policy", "creditor_consent"
                    ),
                    "completion_conditions": deepcopy(
                        service.get("completion_conditions", [])
                    ),
                    "reason": (
                        f"跨回合契约 {record.contract_id} 生效并创建服务责任"
                    ),
                }
            )
            performance_links.append(
                {
                    "actor": service["actor"],
                    "obligation_id": service["obligation_id"],
                }
            )
        record.performance_obligations = performance_links
        record.performance_status = "pending" if performance_links else "none"
        record.performance_reason = ""
        escrow_deposits = []
        for escrow in record.escrows:
            transfer = escrow["transfer"]
            seed = "|".join(
                [
                    record.contract_id,
                    transfer["object_id"],
                    transfer["from"],
                    escrow["release_on_service"],
                ]
            )
            custody_suffix = sha256(seed.encode("utf-8")).hexdigest()[:12]
            escrow_deposits.append(
                {
                    "custody_id": f"{record.contract_id}:escrow:{custody_suffix}",
                    "contract_id": record.contract_id,
                    **deepcopy(escrow),
                }
            )
        return {
            "contract_id": record.contract_id,
            "parties": list(record.parties),
            "settled_at_location": scene_state.get_actor_location(record.parties[0]),
            "exchange": exchange,
            "obligation_updates": obligation_updates,
            "performance_obligations": performance_links,
            "escrow_deposits": escrow_deposits,
        }

    def _require_parties_co_located(
        self,
        parties: List[str],
        scene_state: Any,
        prefix: str,
        errors: List[str],
    ) -> None:
        if not parties:
            return
        location = scene_state.get_actor_location(parties[0])
        if not location or any(
            scene_state.get_actor_location(party) != location for party in parties
        ):
            errors.append(f"{prefix} requires all contract parties co-located")

    @staticmethod
    def _prune_terminal_history(state: Any) -> None:
        if len(state.contracts) < state.max_contracts:
            return
        protected_lineage = set()
        for record in state.contracts.values():
            if record.status != "pending":
                continue
            predecessor = record.countered_from
            visited = set()
            while predecessor and predecessor not in visited:
                visited.add(predecessor)
                protected_lineage.add(predecessor)
                previous = state.contracts.get(predecessor)
                predecessor = previous.countered_from if previous else ""
        terminal = [
            record
            for record in state.contracts.values()
            if record.status in state.TERMINAL_STATUSES
            and record.contract_id not in protected_lineage
            and record.performance_status != "pending"
            and not any(
                lot.get("status") == "held" for lot in record.escrow_lots
            )
        ]
        terminal.sort(
            key=lambda record: (
                record.created_step,
                record.expires_step,
                record.contract_id,
            )
        )
        while terminal and len(state.contracts) >= state.max_contracts:
            record = terminal.pop(0)
            state.contracts.pop(record.contract_id, None)

    def _has_action(
        self,
        actor: str,
        location: Any,
        actions: List[Dict[str, Any]],
    ) -> bool:
        return any(
            self._text(action.get("actor"), 120) == actor
            and str(action.get("outcome", "")).strip().lower()
            in self.POSITIVE_OUTCOMES
            and str(action.get("location", "")).strip() == str(location or "")
            and str(action.get("visibility", "public")).strip() != "hidden"
            for action in actions
        )

    @staticmethod
    def _bounded_int(
        value: Any,
        lower: int,
        upper: int,
        label: str,
        errors: List[str],
    ) -> int:
        if isinstance(value, bool):
            errors.append(f"{label} must be an integer")
            return lower
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            errors.append(f"{label} must be an integer")
            return lower
        if not lower <= parsed <= upper:
            errors.append(f"{label} must be between {lower} and {upper}")
        return parsed

    @staticmethod
    def _quantity(state: Dict[str, Any]) -> int:
        raw = state.get("quantity", 1) if isinstance(state, dict) else 1
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            return 0
        return raw

    @staticmethod
    def _copy_state(state: Any) -> Any:
        if state is None:
            return None
        return state.__class__(**deepcopy(state.model_dump()))

    @classmethod
    def _text_list(cls, value: Any, *, limit: int, item_limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return [
            text
            for item in value[:limit]
            if (text := cls._text(item, item_limit))
        ]

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
