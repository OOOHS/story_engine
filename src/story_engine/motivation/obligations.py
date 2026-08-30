from typing import Any, Dict, List

from src.story_engine.components.obligation_state import ObligationRecord


class ObligationDynamics:
    """Validates evidence-backed creation and resolution of private duties."""

    OPERATIONS = {"create", "fulfill", "cancel", "delegate"}
    COMPLETION_SCOPES = {"actor", "world_object"}
    COMPLETION_FIELDS = {"scope", "target", "path", "operator", "value"}
    DELEGATION_POLICIES = {"forbidden", "bilateral", "creditor_consent"}

    def apply_updates(
        self,
        obligation_states: Dict[str, Any],
        drive_states: Dict[str, Any],
        scene_state: Any,
        result: Dict[str, Any],
        *,
        current_step: int,
        proposal_actors: set[str] | None = None,
    ) -> List[str]:
        updates = result.get("obligation_updates", [])
        if not isinstance(updates, list):
            return ["obligation_updates must be a list"]
        actions = [
            item for item in result.get("resolved_actions", [])
            if isinstance(item, dict)
        ]
        contract_authorizations = result.get("contract_authorizations", {})
        if not isinstance(contract_authorizations, dict):
            contract_authorizations = {}
        errors: List[str] = []
        for index, update in enumerate(updates):
            prefix = f"obligation_updates[{index}]"
            if not isinstance(update, dict):
                errors.append(f"{prefix} must be an object")
                continue
            operation = str(update.get("operation", "")).strip()
            actor = str(update.get("actor", "")).strip()
            source = str(update.get("source", actor)).strip()
            obligation_id = self._text(update.get("obligation_id"), 120)
            reason = self._text(update.get("reason"), 500)
            contract_id = self._text(update.get("contract_id"), 120)
            authorization = (
                contract_authorizations.get(contract_id, {})
                if contract_id
                else {}
            )
            state = obligation_states.get(actor)
            if operation not in self.OPERATIONS:
                errors.append(f"{prefix} has unknown operation: {operation}")
            if actor not in scene_state.actor_states:
                errors.append(f"{prefix} has unknown actor: {actor}")
            if state is None:
                errors.append(f"{prefix} actor has no ObligationState: {actor}")
            if not obligation_id:
                errors.append(f"{prefix} requires obligation_id")
            if not reason:
                errors.append(f"{prefix} requires a reason")
            if contract_id and operation not in {"create", "delegate"}:
                errors.append(
                    f"{prefix} contract authorization only supports create/delegate"
                )
            if contract_id:
                authorized_actors = (
                    set(authorization.get("actors", []))
                    if isinstance(authorization, dict)
                    else set()
                )
                if (
                    actor not in authorized_actors
                    or str(authorization.get("location", "")).strip()
                    != str(scene_state.get_actor_location(actor) or "")
                ):
                    errors.append(
                        f"{prefix} has invalid contract authorization: {contract_id}"
                    )
            if not contract_id:
                self._validate_evidence(
                    prefix,
                    actor,
                    source,
                    actions,
                    scene_state,
                    errors,
                )
            elif not isinstance(authorization, dict):
                errors.append(f"{prefix} has invalid contract authorization")
            if any(error.startswith(prefix) for error in errors):
                continue
            if operation == "create":
                self._create(
                    prefix,
                    actor,
                    state,
                    drive_states.get(actor),
                    scene_state,
                    update,
                    obligation_id,
                    current_step,
                    source,
                    contract_id,
                    errors,
                )
            elif operation == "delegate":
                self._delegate(
                    prefix,
                    actor,
                    state,
                    obligation_states,
                    drive_states,
                    scene_state,
                    update,
                    obligation_id,
                    reason,
                    current_step,
                    actions,
                    set(proposal_actors or set()),
                    contract_id,
                    authorization,
                    errors,
                )
            else:
                self._resolve(
                    prefix,
                    state,
                    operation,
                    obligation_id,
                    reason,
                    errors,
                )
        return errors

    def _create(
        self,
        prefix: str,
        actor: str,
        state: Any,
        drive_state: Any,
        scene_state: Any,
        update: Dict[str, Any],
        obligation_id: str,
        current_step: int,
        source: str,
        contract_id: str,
        errors: List[str],
    ) -> None:
        if obligation_id in state.obligations:
            errors.append(f"{prefix} obligation already exists: {obligation_id}")
        if len(state.obligations) >= state.max_obligations:
            errors.append(f"{prefix} exceeds max_obligations for {actor}")
        title = self._text(update.get("title"), 240)
        if not title:
            errors.append(f"{prefix} create requires title")
        try:
            due_step = int(update.get("due_step"))
        except (TypeError, ValueError):
            due_step = -1
            errors.append(f"{prefix}.due_step must be an integer")
        if due_step < current_step or due_step > current_step + 200:
            errors.append(
                f"{prefix}.due_step must be between current step and +200"
            )
        grace_steps = self._bounded_int(
            update.get("grace_steps", 0), 0, 100, f"{prefix}.grace_steps", errors
        )
        wake_before_steps = self._bounded_int(
            update.get("wake_before_steps", 1),
            0,
            100,
            f"{prefix}.wake_before_steps",
            errors,
        )
        creditor = self._text(update.get("creditor"), 120) or None
        if creditor and creditor not in scene_state.actor_states:
            errors.append(f"{prefix} has unknown creditor: {creditor}")
        pressure_need = self._text(update.get("pressure_need"), 80) or None
        if pressure_need and (
            drive_state is None or pressure_need not in drive_state.needs
        ):
            errors.append(f"{prefix} has unknown pressure_need: {pressure_need}")
        due_delta = self._bounded_float(
            update.get("due_pressure_delta", 0.1),
            0.0,
            0.5,
            f"{prefix}.due_pressure_delta",
            errors,
        )
        breach_delta = self._bounded_float(
            update.get("breach_pressure_delta", 0.2),
            0.0,
            0.5,
            f"{prefix}.breach_pressure_delta",
            errors,
        )
        completion_conditions = self.validate_dynamic_completion_conditions(
            prefix,
            actor,
            scene_state,
            update.get("completion_conditions", []),
            errors,
        )
        delegation_policy = str(
            update.get("delegation_policy", "creditor_consent")
        ).strip()
        if delegation_policy not in self.DELEGATION_POLICIES:
            errors.append(
                f"{prefix}.delegation_policy must be forbidden, bilateral, or creditor_consent"
            )
        if any(error.startswith(prefix) for error in errors):
            return
        state.obligations[obligation_id] = ObligationRecord(
            obligation_id=obligation_id,
            title=title,
            summary=self._text(update.get("summary"), 500),
            creditor=creditor,
            due_step=due_step,
            grace_steps=grace_steps,
            wake_before_steps=wake_before_steps,
            pressure_need=pressure_need,
            due_pressure_delta=due_delta,
            breach_pressure_delta=breach_delta,
            status="scheduled",
            created_step=current_step,
            source_kind="agreement" if contract_id else "resolved_action",
            source_ref=(
                contract_id
                if contract_id
                else f"step:{int(current_step)}:actor:{source}"
            ),
            completion_conditions=completion_conditions,
            delegation_policy=delegation_policy,
        )

    def _delegate(
        self,
        prefix: str,
        actor: str,
        state: Any,
        obligation_states: Dict[str, Any],
        drive_states: Dict[str, Any],
        scene_state: Any,
        update: Dict[str, Any],
        obligation_id: str,
        reason: str,
        current_step: int,
        actions: List[Dict[str, Any]],
        proposal_actors: set[str],
        contract_id: str,
        authorization: Dict[str, Any],
        errors: List[str],
    ) -> None:
        delegate = self._text(update.get("delegate"), 120)
        accepted_by = self._text(update.get("accepted_by"), 120)
        target_state = obligation_states.get(delegate)
        record = state.obligations.get(obligation_id)
        if not delegate or delegate == actor:
            errors.append(f"{prefix} requires a different delegate")
        if accepted_by != delegate:
            errors.append(f"{prefix}.accepted_by must equal delegate")
        if delegate not in scene_state.actor_states:
            errors.append(f"{prefix} has unknown delegate: {delegate}")
        if target_state is None:
            errors.append(f"{prefix} delegate has no ObligationState: {delegate}")
        if record is None:
            errors.append(f"{prefix} references unknown obligation: {obligation_id}")
        elif record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
            errors.append(
                f"{prefix} cannot delegate terminal obligation: {obligation_id}"
            )
        elif state.effective_status(record, current_step) == "breached":
            errors.append(f"{prefix} cannot delegate expired obligation: {obligation_id}")
        elif record.delegation_policy == "forbidden":
            errors.append(f"{prefix} obligation forbids delegation: {obligation_id}")
        elif (
            record.completion_conditions
            and all(scene_state.matches_condition(condition) for condition in record.completion_conditions)
        ):
            errors.append(f"{prefix} obligation is already satisfied: {obligation_id}")
        if target_state is not None:
            if obligation_id in target_state.obligations:
                errors.append(
                    f"{prefix} delegate already has obligation id: {obligation_id}"
                )
            if len(target_state.obligations) >= target_state.max_obligations:
                errors.append(f"{prefix} exceeds max_obligations for {delegate}")

        required_participants = [actor, delegate]
        creditor = record.creditor if record is not None else None
        if (
            record is not None
            and record.delegation_policy == "creditor_consent"
            and creditor
            and creditor not in required_participants
        ):
            approved_by = self._text(update.get("approved_by"), 120)
            if approved_by != creditor:
                errors.append(f"{prefix}.approved_by must equal creditor")
            required_participants.append(creditor)

        shared_location = scene_state.get_actor_location(actor)
        authorized_actors = (
            set(authorization.get("actors", []))
            if contract_id and isinstance(authorization, dict)
            else set()
        )
        contract_authorized = bool(
            contract_id
            and set(required_participants).issubset(authorized_actors)
            and str(authorization.get("location", "")).strip()
            == str(shared_location or "")
        )
        if contract_id and not contract_authorized:
            errors.append(f"{prefix} has invalid contract authorization: {contract_id}")
        for participant in required_participants:
            if not contract_authorized and participant not in proposal_actors:
                errors.append(
                    f"{prefix} delegation requires current-turn proposal from {participant}"
                )
            if (
                not shared_location
                or scene_state.get_actor_location(participant) != shared_location
            ):
                errors.append(
                    f"{prefix} delegation requires all consenting participants co-located"
                )
            supported = contract_authorized or any(
                self._positive_action(action)
                and str(action.get("actor", "")).strip() == participant
                and str(action.get("location", "")).strip() == shared_location
                and str(action.get("visibility", "public")).strip() != "hidden"
                for action in actions
            )
            if not supported:
                errors.append(
                    f"{prefix} delegation requires observable resolved action from {participant}"
                )

        delegate_visible_objects = set(scene_state.get_visible_objects(delegate))
        if record is not None:
            for condition in record.completion_conditions:
                scope = condition.get("scope")
                target = str(condition.get("target") or "").strip()
                if scope == "actor" and target != actor:
                    errors.append(
                        f"{prefix} cannot safely rewrite non-debtor actor condition"
                    )
                elif scope == "world_object" and target not in delegate_visible_objects:
                    errors.append(
                        f"{prefix} completion object is not visible to delegate: {target}"
                    )
                elif scope not in {"actor", "world_object"}:
                    errors.append(
                        f"{prefix} completion scope is not safe to delegate: {scope}"
                    )

        delegate_pressure_need = (
            self._text(update.get("delegate_pressure_need"), 80) or None
        )
        target_drive = drive_states.get(delegate)
        if delegate_pressure_need and (
            target_drive is None or delegate_pressure_need not in target_drive.needs
        ):
            errors.append(
                f"{prefix} has unknown delegate_pressure_need: {delegate_pressure_need}"
            )
        if any(error.startswith(prefix) for error in errors):
            return

        inherited_need = (
            record.pressure_need
            if target_drive is not None and record.pressure_need in target_drive.needs
            else None
        )
        new_pressure_need = delegate_pressure_need or inherited_need
        rewritten_conditions = []
        for condition in record.completion_conditions:
            rewritten = dict(condition)
            if (
                rewritten.get("scope") == "actor"
                and rewritten.get("target") == actor
            ):
                rewritten["target"] = delegate
            rewritten_conditions.append(rewritten)

        transferred = ObligationRecord(
            **{
                **record.model_dump(),
                "status": record.status,
                "created_step": int(current_step),
                "resolution_reason": "",
                "completion_conditions": rewritten_conditions,
                "pressure_need": new_pressure_need,
                "delegated_from": actor,
                "delegated_to": None,
                "delegation_reason": reason,
                "source_kind": "delegated_obligation",
                "source_ref": f"{actor}:{obligation_id}",
            }
        )
        target_state.obligations[obligation_id] = transferred
        record.status = "delegated"
        record.delegated_to = delegate
        record.delegation_reason = reason
        record.resolution_reason = f"delegated to {delegate}: {reason}"
        if (
            transferred.status == "due"
            and target_drive is not None
            and new_pressure_need
        ):
            target_drive.apply_need_delta(
                new_pressure_need,
                transferred.due_pressure_delta,
                provenance={
                    "source_kind": "obligation",
                    "source_ref": f"{delegate}:{obligation_id}",
                    "step": int(current_step),
                },
            )

    def validate_dynamic_completion_conditions(
        self,
        prefix: str,
        actor: str,
        scene_state: Any,
        raw_conditions: Any,
        errors: List[str],
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw_conditions, list):
            errors.append(f"{prefix}.completion_conditions must be a list")
            return []
        if len(raw_conditions) > 4:
            errors.append(f"{prefix}.completion_conditions has too many items")
        normalized: List[Dict[str, Any]] = []
        visible_objects = set(scene_state.get_visible_objects(actor))
        for index, condition in enumerate(raw_conditions[:4]):
            label = f"{prefix}.completion_conditions[{index}]"
            if not isinstance(condition, dict):
                errors.append(f"{label} must be an object")
                continue
            unknown = set(condition).difference(self.COMPLETION_FIELDS)
            if unknown:
                errors.append(
                    f"{label} has unknown fields: {', '.join(sorted(unknown))}"
                )
            scope = str(condition.get("scope", "")).strip()
            target = self._text(condition.get("target"), 120)
            path = str(condition.get("path", "")).strip()
            operator = str(condition.get("operator", "eq")).strip()
            value = condition.get("value")
            if scope not in self.COMPLETION_SCOPES:
                errors.append(f"{label} has unsupported dynamic scope: {scope}")
                continue
            if operator != "eq":
                errors.append(f"{label} dynamic operator must be eq")
            if scope == "actor":
                if target != actor or path != "location":
                    errors.append(
                        f"{label} actor condition must target debtor location"
                    )
                if str(value) not in scene_state.get_known_locations():
                    errors.append(f"{label} references unknown location: {value}")
            else:
                if target not in visible_objects:
                    errors.append(
                        f"{label} world object is not visible to debtor: {target}"
                    )
                if path not in {"location", "owner", "hidden"}:
                    errors.append(f"{label} has unsupported world object path: {path}")
                elif path == "location" and str(value) not in scene_state.get_known_locations():
                    errors.append(f"{label} references unknown location: {value}")
                elif path == "owner" and str(value) not in scene_state.actor_states:
                    errors.append(f"{label} references unknown owner: {value}")
                elif path == "hidden" and not isinstance(value, bool):
                    errors.append(f"{label}.value must be boolean for hidden")
            if not any(error.startswith(label) for error in errors):
                normalized.append(
                    {
                        "scope": scope,
                        "target": target,
                        "path": path,
                        "operator": "eq",
                        "value": value,
                    }
                )
        return normalized

    def _resolve(
        self,
        prefix: str,
        state: Any,
        operation: str,
        obligation_id: str,
        reason: str,
        errors: List[str],
    ) -> None:
        record = state.obligations.get(obligation_id)
        if record is None:
            errors.append(f"{prefix} references unknown obligation: {obligation_id}")
            return
        if record.status in {"fulfilled", "breached", "cancelled", "delegated"}:
            errors.append(
                f"{prefix} cannot change terminal obligation: {obligation_id}"
            )
            return
        record.status = "fulfilled" if operation == "fulfill" else "cancelled"
        record.resolution_reason = reason

    def _validate_evidence(
        self,
        prefix: str,
        actor: str,
        source: str,
        actions: List[Dict[str, Any]],
        scene_state: Any,
        errors: List[str],
    ) -> None:
        if not source:
            errors.append(f"{prefix} requires source")
            return
        source_actions = [
            action
            for action in actions
            if str(action.get("actor", "")).strip() == source
            and self._positive_action(action)
        ]
        if not source_actions:
            errors.append(f"{prefix} is not supported by a resolved action")
            return
        if source == actor:
            return
        actor_location = scene_state.get_actor_location(actor)
        observable = any(
            actor_location
            and str(action.get("location", "")).strip() == actor_location
            and str(action.get("visibility", "public")).strip() != "hidden"
            for action in source_actions
        )
        if not observable:
            errors.append(f"{prefix} source action is not observable by actor")

    @staticmethod
    def _positive_action(action: Dict[str, Any]) -> bool:
        return str(action.get("outcome", "")).strip().lower() in {
            "success",
            "partial",
            "complication",
        }

    @staticmethod
    def _bounded_int(value, lower, upper, label, errors) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            errors.append(f"{label} must be an integer")
            return lower
        if not lower <= parsed <= upper:
            errors.append(f"{label} must be between {lower} and {upper}")
        return parsed

    @staticmethod
    def _bounded_float(value, lower, upper, label, errors) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{label} must be numeric")
            return lower
        parsed = float(value)
        if not lower <= parsed <= upper:
            errors.append(f"{label} must be between {lower} and {upper}")
        return parsed

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
