"""Atomic host edits for ordinary, observable object properties."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Dict, Iterable, List

from src.story_engine.components.scene_state import SceneState


@dataclass(frozen=True)
class WorldEditTransactionResult:
    committed: bool
    changes: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class HostWorldEditTransaction:
    """Commit legacy host-authored descriptive patches without silent facts.

    Placement, lifecycle and topology have dedicated APIs.  This boundary is
    deliberately limited to existing objects and ordinary state properties.
    """

    PROTECTED_FIELDS = {
        "is_location",
        "connected_to",
        "zones",
        "default_zone",
        "aliases",
        "kind",
        "location",
        "owner",
        "container",
        "sub_location",
        "hidden",
        "portable",
        "quantity",
        "stack_key",
        "affordances",
        "is_container",
        "container_capacity",
        "container_size",
        "container_open",
        "container_opaque",
    }

    def apply(
        self,
        scene_state: SceneState | None,
        edits: Iterable[Any] | None,
        *,
        current_step: int,
    ) -> WorldEditTransactionResult:
        raw_edits = list(edits or [])
        if not raw_edits:
            return WorldEditTransactionResult(True)
        if scene_state is None:
            return WorldEditTransactionResult(False, errors=["SceneState is required"])

        errors: List[str] = []
        prepared: List[tuple[int, str, Dict[str, Any]]] = []
        seen_objects: set[str] = set()
        for index, raw in enumerate(raw_edits):
            label = f"world_edits[{index}]"
            if not isinstance(raw, (tuple, list)) or len(raw) != 2:
                errors.append(f"{label} must be (object_name, patch)")
                continue
            object_id = self._text(raw[0], 160)
            patch = raw[1]
            if object_id not in scene_state.world_objects:
                errors.append(f"{label} has unknown object: {object_id}")
            elif not isinstance(scene_state.get_object_state(object_id), dict):
                errors.append(f"{label} targets invalid object state: {object_id}")
            if object_id in seen_objects:
                errors.append(f"{label} duplicates object in the same batch: {object_id}")
            seen_objects.add(object_id)
            if not isinstance(patch, dict):
                errors.append(f"{label} patch must be an object")
                continue
            invalid_fields = [
                field
                for field in patch
                if not isinstance(field, str)
                or not field.strip()
                or field != field.strip()
            ]
            if invalid_fields:
                errors.append(f"{label} field names must be non-empty normalized text")
            if not self._is_json_value(patch):
                errors.append(f"{label} must contain strict JSON state values")
            forbidden = self.PROTECTED_FIELDS.intersection(patch)
            if forbidden:
                errors.append(
                    "legacy world_edits cannot write protected fields; use an explicit "
                    "host lifecycle/topology API: " + ", ".join(sorted(forbidden))
                )
            private_fields = sorted(
                str(field) for field in patch if str(field).startswith("_")
            )
            if private_fields:
                errors.append(
                    f"{label} cannot write private fields: {', '.join(private_fields)}"
                )
            prepared.append((index, object_id, deepcopy(patch)))
        if errors:
            return WorldEditTransactionResult(False, errors=errors)

        staged = SceneState(**deepcopy(scene_state.get_snapshot()))
        changes: List[Dict[str, Any]] = []
        for index, object_id, patch in prepared:
            before = deepcopy(staged.get_object_state(object_id))
            staged.update_object_state(object_id, patch)
            after = staged.get_object_state(object_id)
            paths = sorted(
                str(path) for path in patch if before.get(path) != after.get(path)
            )
            if not paths:
                continue
            if staged.is_location(object_id):
                location = object_id
                visibility = "local"
            else:
                location = str(staged.get_effective_object_location(object_id) or "")
                visibility = "hidden" if bool(after.get("hidden", False)) else "local"
            changes.append(
                {
                    "change_id": f"host-world-edit:{int(current_step)}:{index}:{object_id}",
                    "object_id": object_id,
                    "paths": paths,
                    "location": location,
                    "source_actors": [],
                    "visibility": visibility,
                    "occurred_step": int(current_step),
                    "source_type": "host_world_edit",
                    "statement": (
                        f"“{object_id}”的可观察状态发生了变化"
                        f"（{'、'.join(paths)}）。"
                    ),
                }
            )
        if not changes:
            return WorldEditTransactionResult(True)

        try:
            version = int(staged.get_scene_flag("world_version", 0) or 0)
        except (TypeError, ValueError):
            version = 0
        staged.update_scene_flags({"world_version": version + 1})
        scene_state.world_objects = deepcopy(staged.world_objects)
        scene_state.scene_flags = deepcopy(staged.scene_flags)
        return WorldEditTransactionResult(True, changes=changes)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]

    @classmethod
    def _is_json_value(cls, value: Any) -> bool:
        if value is None or isinstance(value, (str, bool, int)):
            return True
        if isinstance(value, float):
            return isfinite(value)
        if isinstance(value, list):
            return all(cls._is_json_value(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and cls._is_json_value(item)
                for key, item in value.items()
            )
        return False
