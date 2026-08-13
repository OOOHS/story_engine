from copy import deepcopy
from hashlib import sha256
from typing import Any, Dict, List


class AssetTransferEngine:
    """Shared deterministic mechanics for actor transfers and engine custody."""

    def apply_actor_claim(
        self,
        scene_state: Any,
        object_id: str,
        claim: Dict[str, Any],
        *,
        source_object_ids: set[str],
        bundle_ids: List[str],
        error_prefix: str,
        errors: List[str],
    ) -> None:
        state = scene_state.get_object_state(object_id)
        available = self.quantity(state)
        quantity = int(claim["quantity"])
        recipient = claim["to"]
        if quantity == available:
            state.update(
                {
                    "owner": recipient,
                    "location": None,
                    "container": None,
                    "sub_location": None,
                    "hidden": False,
                }
            )
            return

        state["quantity"] = available - quantity
        merge_target = self.find_merge_target(
            scene_state,
            source_state=state,
            recipient=recipient,
            stack_key=self.stack_key(state),
            excluded=source_object_ids,
        )
        if merge_target:
            target_state = scene_state.get_object_state(merge_target)
            target_state["quantity"] = self.quantity(target_state) + quantity
            return

        fragment_id = self.fragment_id(
            scene_state,
            object_id=object_id,
            recipient=recipient,
            bundle_ids=bundle_ids,
        )
        if not fragment_id:
            errors.append(
                f"{error_prefix} fragment id already exists for bundle: "
                f"{object_id}->{recipient}"
            )
            return
        if not self.reserve_dynamic_slot(
            scene_state, errors, f"{error_prefix} split"
        ):
            return
        fragment = deepcopy(state)
        fragment.update(
            {
                "owner": recipient,
                "location": None,
                "container": None,
                "sub_location": None,
                "hidden": False,
                "quantity": quantity,
            }
        )
        scene_state.world_objects[fragment_id] = fragment
        self.add_dynamic_name(scene_state, fragment_id)

    def take_into_custody(
        self,
        scene_state: Any,
        *,
        object_id: str,
        source: str,
        quantity: int,
        custody_id: str,
        lifecycle_objects: set[str],
        errors: List[str],
    ) -> Dict[str, Any] | None:
        label = f"escrow deposit {custody_id}"
        if object_id in lifecycle_objects:
            errors.append(
                f"{label} object cannot also appear in object_lifecycle: {object_id}"
            )
            return None
        if object_id not in scene_state.world_objects or scene_state.is_location(object_id):
            errors.append(f"{label} references unknown tangible object: {object_id}")
            return None
        state = scene_state.get_object_state(object_id)
        if self.text(state.get("owner"), 120) != source:
            errors.append(f"{label} source does not own object: {source}->{object_id}")
        if not bool(state.get("portable", True)):
            errors.append(f"{label} cannot custody non-portable object: {object_id}")
        if bool(state.get("hidden", False)):
            errors.append(f"{label} object must be disclosed before custody: {object_id}")
        if scene_state.get_contained_objects(object_id):
            errors.append(f"{label} cannot custody non-empty container: {object_id}")
        available = self.quantity(state)
        if quantity < 1:
            errors.append(f"{label} quantity must be a positive integer")
        elif quantity > available:
            errors.append(
                f"{label} quantity exceeds available units: {object_id} "
                f"({quantity} > {available})"
            )
        elif quantity < available and not self.stack_key(state):
            errors.append(f"{label} partial quantity requires stack_key: {object_id}")
        if errors:
            return None

        lot_state = deepcopy(state)
        lot_state.update(
            {
                "owner": None,
                "location": None,
                "container": None,
                "sub_location": None,
                "hidden": False,
                "quantity": quantity,
            }
        )
        dynamic_names = self.dynamic_names(scene_state)
        was_dynamic = object_id in dynamic_names
        whole_object = quantity == available
        if whole_object:
            scene_state.world_objects.pop(object_id, None)
            if was_dynamic:
                scene_state.update_scene_flags(
                    {
                        "dynamic_world_object_names": sorted(
                            name for name in dynamic_names if name != object_id
                        )
                    }
                )
        else:
            state["quantity"] = available - quantity
        return {
            "custody_id": custody_id,
            "source_object_id": object_id,
            "quantity": quantity,
            "object_state": lot_state,
            "whole_object": whole_object,
            "was_dynamic": was_dynamic,
        }

    def release_from_custody(
        self,
        scene_state: Any,
        lot: Dict[str, Any],
        *,
        recipient: str,
        bundle_ids: List[str],
        errors: List[str],
    ) -> str:
        custody_id = self.text(lot.get("custody_id"), 160)
        object_id = self.text(lot.get("source_object_id"), 120)
        state = deepcopy(lot.get("object_state", {}))
        quantity = self.quantity(state)
        if recipient not in scene_state.actor_states:
            errors.append(f"escrow {custody_id} has unknown recipient: {recipient}")
            return ""
        if not object_id or not isinstance(state, dict) or quantity < 1:
            errors.append(f"escrow {custody_id} contains invalid custody lot")
            return ""

        whole_object = bool(lot.get("whole_object"))
        if whole_object:
            if object_id in scene_state.world_objects:
                errors.append(
                    f"escrow {custody_id} cannot restore occupied object id: {object_id}"
                )
                return ""
            state.update(
                {
                    "owner": recipient,
                    "location": None,
                    "container": None,
                    "sub_location": None,
                    "hidden": False,
                }
            )
            scene_state.world_objects[object_id] = state
            if bool(lot.get("was_dynamic")):
                if not self.reserve_dynamic_slot(
                    scene_state, errors, "escrow release"
                ):
                    scene_state.world_objects.pop(object_id, None)
                    return ""
                self.add_dynamic_name(scene_state, object_id)
            return object_id

        stack_key = self.stack_key(state)
        if stack_key:
            merge_target = self.find_merge_target(
                scene_state,
                source_state=state,
                recipient=recipient,
                stack_key=stack_key,
                excluded=set(),
            )
            if merge_target:
                target_state = scene_state.get_object_state(merge_target)
                target_state["quantity"] = self.quantity(target_state) + quantity
                return merge_target

        materialized_id = self.fragment_id(
            scene_state,
            object_id=object_id,
            recipient=recipient,
            bundle_ids=bundle_ids,
        )
        if not materialized_id:
            errors.append(
                f"escrow fragment id already exists for bundle: {object_id}->{recipient}"
            )
            return ""
        if not self.reserve_dynamic_slot(scene_state, errors, "escrow release"):
            return ""
        state.update(
            {
                "owner": recipient,
                "location": None,
                "container": None,
                "sub_location": None,
                "hidden": False,
            }
        )
        scene_state.world_objects[materialized_id] = state
        self.add_dynamic_name(scene_state, materialized_id)
        return materialized_id

    def find_merge_target(
        self,
        scene_state: Any,
        *,
        source_state: Dict[str, Any],
        recipient: str,
        stack_key: str,
        excluded: set[str],
    ) -> str:
        if not stack_key:
            return ""
        signature = self.stack_signature(source_state)
        for object_id in sorted(scene_state.world_objects):
            if object_id in excluded or scene_state.is_location(object_id):
                continue
            state = scene_state.get_object_state(object_id)
            if self.text(state.get("owner"), 120) != recipient:
                continue
            if self.stack_key(state) != stack_key:
                continue
            if self.stack_signature(state) == signature:
                return object_id
        return ""

    def fragment_id(
        self,
        scene_state: Any,
        *,
        object_id: str,
        recipient: str,
        bundle_ids: List[str],
    ) -> str:
        seed = "|".join([object_id, recipient, *sorted(bundle_ids)])
        suffix = sha256(seed.encode("utf-8")).hexdigest()[:10]
        prefix = f"{object_id}·{recipient}份"[:105]
        candidate = f"{prefix}·{suffix}"
        return candidate if candidate not in scene_state.world_objects else ""

    def reserve_dynamic_slot(
        self,
        scene_state: Any,
        errors: List[str],
        error_prefix: str,
    ) -> bool:
        dynamic_names = self.dynamic_names(scene_state)
        try:
            limit = max(
                0,
                int(scene_state.get_scene_flag("max_dynamic_world_objects", 32) or 0),
            )
        except (TypeError, ValueError):
            errors.append(
                f"invalid max_dynamic_world_objects during {error_prefix}"
            )
            return False
        if len(dynamic_names) >= limit:
            errors.append(f"{error_prefix} exceeds max_dynamic_world_objects")
            return False
        return True

    @staticmethod
    def add_dynamic_name(scene_state: Any, object_id: str) -> None:
        names = AssetTransferEngine.dynamic_names(scene_state)
        names.append(object_id)
        scene_state.update_scene_flags(
            {"dynamic_world_object_names": sorted(set(names))}
        )

    @staticmethod
    def quantity(state: Dict[str, Any]) -> int:
        raw = state.get("quantity", 1) if isinstance(state, dict) else 1
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            return 0
        return raw

    @classmethod
    def stack_key(cls, state: Dict[str, Any]) -> str:
        return cls.text(state.get("stack_key"), 120) if isinstance(state, dict) else ""

    @staticmethod
    def stack_signature(state: Dict[str, Any]) -> Dict[str, Any]:
        ignored = {
            "owner",
            "location",
            "container",
            "sub_location",
            "hidden",
            "quantity",
        }
        return {
            key: deepcopy(value)
            for key, value in state.items()
            if key not in ignored
        }

    @staticmethod
    def dynamic_names(scene_state: Any) -> List[str]:
        raw = scene_state.get_scene_flag("dynamic_world_object_names", [])
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @staticmethod
    def text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
