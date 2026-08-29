from typing import Any, Dict, List, Tuple

from src.story_engine.environment.assets import AssetTransferEngine


class ExchangeDynamics(AssetTransferEngine):
    """Atomically stage consent-backed transfers between two character agents."""

    MAX_EXCHANGES = 8
    MAX_TRANSFERS = 16
    TERMINAL_ACTION_OUTCOMES = {"success", "partial", "complication"}

    def apply(
        self,
        scene_state: Any,
        result: Dict[str, Any],
        *,
        proposal_actors: set[str] | None = None,
    ) -> List[str]:
        exchanges = result.get("exchanges", [])
        if not isinstance(exchanges, list):
            return ["exchanges must be a list"]
        if not exchanges:
            return []
        if not scene_state:
            return ["exchanges require scene state"]
        if len(exchanges) > self.MAX_EXCHANGES:
            return [f"exchanges cannot exceed {self.MAX_EXCHANGES} per turn"]

        actions = [
            item
            for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        proposals = set(proposal_actors or set())
        lifecycle_objects = {
            self._text(item.get("object_id"), 120)
            for item in result.get("object_lifecycle", [])
            if isinstance(item, dict) and self._text(item.get("object_id"), 120)
        }
        errors: List[str] = []
        normalized: List[Dict[str, Any]] = []
        exchange_ids = set()
        total_transfers = 0

        for index, exchange in enumerate(exchanges):
            prefix = f"exchanges[{index}]"
            if not isinstance(exchange, dict):
                errors.append(f"{prefix} must be an object")
                continue
            exchange_id = self._text(exchange.get("exchange_id"), 120)
            reason = self._text(exchange.get("reason"), 500)
            if not exchange_id:
                errors.append(f"{prefix} requires exchange_id")
            elif exchange_id in exchange_ids:
                errors.append(f"{prefix} duplicates exchange_id: {exchange_id}")
            exchange_ids.add(exchange_id)
            if not reason:
                errors.append(f"{prefix} requires a reason")

            raw_parties = exchange.get("parties")
            parties = self._text_list(raw_parties, limit=2, item_limit=120)
            if not isinstance(raw_parties, list) or len(raw_parties) != 2:
                errors.append(f"{prefix}.parties must contain exactly two actors")
            if len(parties) != 2 or len(set(parties)) != 2:
                errors.append(f"{prefix}.parties must contain exactly two distinct actors")
            for party in parties:
                if party not in scene_state.actor_states:
                    errors.append(f"{prefix} has unknown party: {party}")
                if party not in proposals:
                    errors.append(
                        f"{prefix} requires current-turn proposal from {party}"
                    )

            raw_accepted_by = exchange.get("accepted_by")
            accepted_by = set(
                self._text_list(raw_accepted_by, limit=2, item_limit=120)
            )
            if not isinstance(raw_accepted_by, list) or len(raw_accepted_by) != 2:
                errors.append(f"{prefix}.accepted_by must contain exactly two actors")
            if set(parties) != accepted_by:
                errors.append(f"{prefix}.accepted_by must exactly match parties")

            shared_location = (
                scene_state.get_actor_location(parties[0])
                if len(parties) == 2
                else None
            )
            if (
                len(parties) == 2
                and (
                    not shared_location
                    or scene_state.get_actor_location(parties[1]) != shared_location
                )
            ):
                errors.append(f"{prefix} requires both parties co-located")
            for party in parties:
                if not self._has_action_evidence(party, shared_location, actions):
                    errors.append(
                        f"{prefix} requires observable positive action from {party}"
                    )

            transfers = exchange.get("transfers", [])
            if not isinstance(transfers, list) or not transfers:
                errors.append(f"{prefix}.transfers must be a non-empty list")
                transfers = []
            total_transfers += len(transfers)
            normalized_transfers = []
            for transfer_index, transfer in enumerate(transfers):
                label = f"{prefix}.transfers[{transfer_index}]"
                normalized_transfer = self._validate_transfer(
                    scene_state,
                    transfer,
                    parties=parties,
                    lifecycle_objects=lifecycle_objects,
                    label=label,
                    errors=errors,
                )
                if normalized_transfer:
                    normalized_transfers.append(normalized_transfer)
            normalized.append(
                {
                    "exchange_id": exchange_id,
                    "parties": parties,
                    "reason": reason,
                    "transfers": normalized_transfers,
                }
            )

        if total_transfers > self.MAX_TRANSFERS:
            errors.append(
                f"exchange transfers cannot exceed {self.MAX_TRANSFERS} per turn"
            )

        claims: Dict[str, Dict[str, Any]] = {}
        for exchange in normalized:
            for transfer in exchange["transfers"]:
                object_id = transfer["object_id"]
                claim = claims.setdefault(
                    object_id,
                    {
                        "from": transfer["from"],
                        "to": transfer["to"],
                        "quantity": 0,
                        "exchange_ids": [],
                    },
                )
                if claim["from"] != transfer["from"] or claim["to"] != transfer["to"]:
                    errors.append(
                        f"exchange object has incompatible simultaneous recipients: {object_id}"
                    )
                claim["quantity"] += transfer["quantity"]
                claim["exchange_ids"].append(exchange["exchange_id"])

        for object_id, claim in claims.items():
            state = scene_state.get_object_state(object_id)
            available = self.quantity(state)
            if claim["quantity"] > available:
                errors.append(
                    f"exchange quantity exceeds available units: {object_id} "
                    f"({claim['quantity']} > {available})"
                )
            if claim["quantity"] < available and not self.stack_key(state):
                errors.append(
                    f"partial exchange requires stack_key: {object_id}"
                )
        if errors:
            return errors

        source_object_ids = set(claims)
        for object_id in sorted(claims):
            self.apply_actor_claim(
                scene_state,
                object_id,
                claims[object_id],
                source_object_ids=source_object_ids,
                bundle_ids=claims[object_id]["exchange_ids"],
                error_prefix="exchange",
                errors=errors,
            )
        return errors

    def _validate_transfer(
        self,
        scene_state: Any,
        transfer: Any,
        *,
        parties: List[str],
        lifecycle_objects: set[str],
        label: str,
        errors: List[str],
    ) -> Dict[str, Any] | None:
        if not isinstance(transfer, dict):
            errors.append(f"{label} must be an object")
            return None
        source = self._text(transfer.get("from"), 120)
        recipient = self._text(transfer.get("to"), 120)
        object_id = self._text(transfer.get("object_id"), 120)
        if source not in parties or recipient not in parties or source == recipient:
            errors.append(f"{label} from/to must be opposite exchange parties")
        if not object_id:
            errors.append(f"{label} requires object_id")
            return None
        if object_id in lifecycle_objects:
            errors.append(
                f"{label} object cannot also appear in object_lifecycle: {object_id}"
            )
        if object_id not in scene_state.world_objects:
            errors.append(f"{label} references unknown object: {object_id}")
            return None
        if scene_state.is_location(object_id):
            errors.append(f"{label} cannot exchange a location: {object_id}")
            return None
        state = scene_state.get_object_state(object_id)
        if self._text(state.get("owner"), 120) != source:
            errors.append(f"{label} source does not own object: {source}->{object_id}")
        if not bool(state.get("portable", True)):
            errors.append(f"{label} cannot exchange non-portable object: {object_id}")
        if bool(state.get("hidden", False)):
            errors.append(f"{label} object must be disclosed before exchange: {object_id}")
        available = self.quantity(state)
        raw_quantity = transfer.get("quantity", available)
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int):
            errors.append(f"{label}.quantity must be a positive integer")
            quantity = 0
        else:
            quantity = int(raw_quantity)
            if quantity < 1:
                errors.append(f"{label}.quantity must be a positive integer")
        return {
            "from": source,
            "to": recipient,
            "object_id": object_id,
            "quantity": quantity,
        }

    def _has_action_evidence(
        self,
        actor: str,
        location: Any,
        actions: List[Dict[str, Any]],
    ) -> bool:
        return any(
            self._text(action.get("actor"), 120) == actor
            and str(action.get("outcome", "")).strip().lower()
            in self.TERMINAL_ACTION_OUTCOMES
            and str(action.get("location", "")).strip() == str(location or "")
            and str(action.get("visibility", "public")).strip() != "hidden"
            for action in actions
        )

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
