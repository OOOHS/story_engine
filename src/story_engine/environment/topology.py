"""Atomic, host-authorized edits to the existing spatial graph."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from src.story_engine.components.scene_state import SceneState


@dataclass(frozen=True)
class TopologyTransactionResult:
    committed: bool
    changes: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class HostTopologyTransaction:
    """Validate a batch of route changes on a copy, then commit it once.

    This class is intentionally unavailable to Agent/GM semantic output.  Its
    commands enter through ``Runner.run_step(topology_changes=...)`` or another
    future host rule source with equivalent authority.
    """

    ALLOWED_FIELDS = {
        "change_id",
        "operation",
        "source",
        "target",
        "bidirectional",
        "visibility",
        "reason",
    }
    OPERATIONS = {"connect", "disconnect"}
    VISIBILITIES = {"local", "public", "hidden"}

    def apply(
        self,
        scene_state: SceneState | None,
        commands: Iterable[Dict[str, Any]] | None,
        *,
        current_step: int,
    ) -> TopologyTransactionResult:
        raw_commands = list(commands or [])
        if not raw_commands:
            return TopologyTransactionResult(True)
        if scene_state is None:
            return TopologyTransactionResult(False, errors=["SceneState is required"])

        staged = SceneState(**deepcopy(scene_state.get_snapshot()))
        errors: List[str] = []
        prepared: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_arcs: Dict[tuple[str, str], str] = {}

        for index, raw in enumerate(raw_commands):
            label = f"topology_changes[{index}]"
            if not isinstance(raw, dict):
                errors.append(f"{label} must be an object")
                continue
            unknown = set(raw).difference(self.ALLOWED_FIELDS)
            if unknown:
                errors.append(
                    f"{label} has unknown fields: {', '.join(sorted(unknown))}"
                )
            operation = self._text(raw.get("operation"), 20).lower()
            source = self._text(raw.get("source"), 160)
            target = self._text(raw.get("target"), 160)
            visibility = self._text(raw.get("visibility", "local"), 20).lower()
            bidirectional = raw.get("bidirectional", True)
            change_id = self._text(
                raw.get("change_id")
                or f"{int(current_step)}:{index}:{operation}:{source}->{target}",
                220,
            )
            if operation not in self.OPERATIONS:
                errors.append(f"{label} has unsupported operation: {operation}")
            if not source or not target or source == target:
                errors.append(f"{label} requires two distinct locations")
            if source not in staged.get_known_locations():
                errors.append(f"{label} has unknown source location: {source}")
            if target not in staged.get_known_locations():
                errors.append(f"{label} has unknown target location: {target}")
            if not isinstance(bidirectional, bool):
                errors.append(f"{label}.bidirectional must be boolean")
            if visibility not in self.VISIBILITIES:
                errors.append(f"{label} has unsupported visibility: {visibility}")
            if not change_id or change_id in seen_ids:
                errors.append(f"{label} requires a unique change_id")
            seen_ids.add(change_id)

            arcs = [(source, target)]
            if bidirectional is True:
                arcs.append((target, source))
            for arc in arcs:
                prior = seen_arcs.get(arc)
                if prior is not None and prior != operation:
                    errors.append(
                        f"{label} conflicts with another command for {arc[0]}->{arc[1]}"
                    )
                seen_arcs[arc] = operation
            prepared.append(
                {
                    "change_id": change_id,
                    "operation": operation,
                    "source": source,
                    "target": target,
                    "bidirectional": bidirectional,
                    "visibility": visibility,
                    "reason": self._text(raw.get("reason"), 300),
                }
            )

        if errors:
            return TopologyTransactionResult(False, errors=errors)

        changes: List[Dict[str, Any]] = []
        for command in prepared:
            arcs = [(command["source"], command["target"])]
            if command["bidirectional"]:
                arcs.append((command["target"], command["source"]))
            changed_arcs = []
            for source, target in arcs:
                state = staged.get_object_state(source)
                connections = self._connections(state.get("connected_to", []))
                before = list(connections)
                if command["operation"] == "connect" and target not in connections:
                    connections.append(target)
                elif command["operation"] == "disconnect" and target in connections:
                    connections.remove(target)
                if connections != before:
                    staged.update_object_state(source, {"connected_to": connections})
                    changed_arcs.append({"source": source, "target": target})
            if changed_arcs:
                verb = "开放" if command["operation"] == "connect" else "中断"
                changes.append(
                    {
                        **command,
                        "occurred_step": int(current_step),
                        "changed_arcs": changed_arcs,
                        "statement": (
                            f"{command['source']}与{command['target']}之间的通路已经{verb}。"
                        ),
                    }
                )

        invariant_errors = self._validate_graph(staged)
        if invariant_errors:
            return TopologyTransactionResult(False, errors=invariant_errors)
        if not changes:
            return TopologyTransactionResult(True)

        try:
            previous_version = int(staged.get_scene_flag("world_version", 0) or 0)
        except (TypeError, ValueError):
            previous_version = 0
        staged.update_scene_flags({"world_version": previous_version + 1})
        scene_state.world_objects = deepcopy(staged.world_objects)
        scene_state.scene_flags = deepcopy(staged.scene_flags)
        return TopologyTransactionResult(True, changes=changes)

    @classmethod
    def _validate_graph(cls, scene_state: SceneState) -> List[str]:
        errors: List[str] = []
        locations = scene_state.get_known_locations()
        for location in sorted(locations):
            state = scene_state.get_object_state(location)
            raw_connections = state.get("connected_to", [])
            if not isinstance(raw_connections, list):
                errors.append(f"connected_to must be a list: {location}")
                continue
            connections = cls._connections(raw_connections)
            if len(connections) != len(raw_connections):
                errors.append(f"connected_to contains empty or duplicate entries: {location}")
            for target in connections:
                if target not in locations:
                    errors.append(
                        f"location graph references missing object: {location}->{target}"
                    )
        return errors

    @staticmethod
    def _connections(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return list(
            dict.fromkeys(str(item).strip() for item in value if str(item).strip())
        )

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]
