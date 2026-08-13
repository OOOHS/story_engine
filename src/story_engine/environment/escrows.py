from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.assets import AssetTransferEngine


class ContractEscrowDynamics:
    """Move accepted contract assets into engine custody inside a world transaction."""

    MAX_DEPOSITS = 4

    def __init__(self) -> None:
        self.assets = AssetTransferEngine()

    def apply_deposits(
        self,
        scene_state: Any,
        contract_state: Any,
        result: Dict[str, Any],
    ) -> List[str]:
        deposits = result.get("contract_escrow_deposits", [])
        if not isinstance(deposits, list):
            return ["contract_escrow_deposits must be a list"]
        if not deposits:
            return []
        if scene_state is None or contract_state is None:
            return ["agreement escrow deposits require SceneState and AgreementBook"]
        if len(deposits) > self.MAX_DEPOSITS:
            return [f"contract escrow deposits cannot exceed {self.MAX_DEPOSITS}"]

        lifecycle_objects = {
            self.assets.text(item.get("object_id"), 120)
            for item in result.get("object_lifecycle", [])
            if isinstance(item, dict)
            and self.assets.text(item.get("object_id"), 120)
        }
        errors: List[str] = []
        seen_custody_ids = set()
        seen_objects = set()
        for index, deposit in enumerate(deposits):
            label = f"contract_escrow_deposits[{index}]"
            if not isinstance(deposit, dict):
                errors.append(f"{label} must be an object")
                continue
            custody_id = self.assets.text(deposit.get("custody_id"), 160)
            contract_id = self.assets.text(deposit.get("contract_id"), 120)
            transfer = deposit.get("transfer")
            if not custody_id or custody_id in seen_custody_ids:
                errors.append(f"{label} has missing or duplicate custody_id")
            seen_custody_ids.add(custody_id)
            record = contract_state.contracts.get(contract_id)
            if record is None or record.status != "settled":
                errors.append(f"{label} references unsettled contract: {contract_id}")
                continue
            if not isinstance(transfer, dict):
                errors.append(f"{label}.transfer must be an object")
                continue
            source = self.assets.text(transfer.get("from"), 120)
            object_id = self.assets.text(transfer.get("object_id"), 120)
            raw_quantity = transfer.get("quantity")
            if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
                errors.append(f"{label}.transfer.quantity must be a positive integer")
                continue
            if object_id in seen_objects:
                errors.append(
                    f"{label} cannot deposit the same object twice: {object_id}"
                )
            seen_objects.add(object_id)
            if errors:
                continue
            lot = self.assets.take_into_custody(
                scene_state,
                object_id=object_id,
                source=source,
                quantity=int(raw_quantity),
                custody_id=custody_id,
                lifecycle_objects=lifecycle_objects,
                errors=errors,
            )
            if lot is None:
                continue
            lot.update(
                {
                    "contract_id": contract_id,
                    "release_to": self.assets.text(deposit.get("release_to"), 120),
                    "refund_to": self.assets.text(deposit.get("refund_to"), 120),
                    "release_on_service": self.assets.text(
                        deposit.get("release_on_service"), 120
                    ),
                    "refund_on": sorted(
                        {
                            self.assets.text(status, 20)
                            for status in deposit.get("refund_on", [])
                            if self.assets.text(status, 20)
                        }
                    ),
                    "status": "held",
                    "resolved_step": None,
                    "materialized_object_id": "",
                }
            )
            record.escrow_lots.append(lot)
        return errors


@dataclass
class EscrowSettlementResult:
    scene_state: Any = field(repr=False)
    contract_state: Any = field(repr=False)
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ContractEscrowSettlement:
    """Atomically release or refund custody lots from authoritative service status."""

    def __init__(self) -> None:
        self.assets = AssetTransferEngine()

    def settle_ready(
        self,
        scene_state: Any,
        contract_state: Any,
        *,
        current_step: int,
    ) -> EscrowSettlementResult:
        if scene_state is None or contract_state is None:
            return EscrowSettlementResult(scene_state, contract_state)
        staged_scene = SceneState(**deepcopy(scene_state.get_snapshot()))
        staged_contract = contract_state.__class__(
            **deepcopy(contract_state.model_dump())
        )
        transitions: List[Dict[str, Any]] = []
        errors: List[str] = []

        for contract_id in sorted(staged_contract.contracts):
            record = staged_contract.contracts[contract_id]
            if record.status != "settled" or not record.escrow_lots:
                continue
            service_statuses = {
                str(link.get("obligation_id", "")): str(
                    link.get("resolved_status", "pending")
                )
                for link in record.performance_obligations
            }
            for lot in sorted(
                record.escrow_lots,
                key=lambda item: str(item.get("custody_id", "")),
            ):
                if lot.get("status") != "held":
                    continue
                service_id = str(lot.get("release_on_service", ""))
                service_status = service_statuses.get(service_id, "pending")
                disposition = ""
                recipient = ""
                if service_status == "fulfilled":
                    disposition = "released"
                    recipient = str(lot.get("release_to", ""))
                elif service_status in set(lot.get("refund_on", [])):
                    disposition = "refunded"
                    recipient = str(lot.get("refund_to", ""))
                if not disposition:
                    continue
                materialized_id = self.assets.release_from_custody(
                    staged_scene,
                    lot,
                    recipient=recipient,
                    bundle_ids=[str(lot.get("custody_id", "")), disposition],
                    errors=errors,
                )
                if not materialized_id:
                    continue
                lot["status"] = disposition
                lot["resolved_step"] = int(current_step)
                lot["materialized_object_id"] = materialized_id
                transitions.append(
                    {
                        "contract_id": contract_id,
                        "custody_id": lot.get("custody_id"),
                        "escrow_status": disposition,
                        "recipient": recipient,
                        "object_id": materialized_id,
                        "service_status": service_status,
                    }
                )

        if errors:
            return EscrowSettlementResult(scene_state, contract_state, [], errors)
        return EscrowSettlementResult(
            staged_scene,
            staged_contract,
            transitions,
            [],
        )

    @staticmethod
    def commit(
        scene_state: Any,
        contract_state: Any,
        resolution: EscrowSettlementResult,
    ) -> None:
        scene_state.description = resolution.scene_state.description
        scene_state.world_objects = resolution.scene_state.world_objects
        scene_state.actor_states = resolution.scene_state.actor_states
        scene_state.scene_flags = resolution.scene_state.scene_flags
        contract_state.restore_from(resolution.contract_state)
