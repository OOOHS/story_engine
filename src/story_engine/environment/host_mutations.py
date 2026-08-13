"""One fail-closed transaction for all pre-step host world mutations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.story_engine.components.scene_state import SceneState
from src.story_engine.environment.topology import HostTopologyTransaction
from src.story_engine.environment.world_edits import HostWorldEditTransaction


@dataclass(frozen=True)
class HostMutationTransactionResult:
    committed: bool
    object_changes: List[Dict[str, Any]] = field(default_factory=list)
    topology_changes: List[Dict[str, Any]] = field(default_factory=list)
    world_edit_errors: List[str] = field(default_factory=list)
    topology_errors: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class HostMutationTransaction:
    """Validate object and topology commands on one shared SceneState copy."""

    def __init__(self) -> None:
        self.world_edits = HostWorldEditTransaction()
        self.topology = HostTopologyTransaction()

    def apply(
        self,
        scene_state: SceneState | None,
        *,
        world_edits: Any = None,
        topology_changes: Any = None,
        current_step: int,
    ) -> HostMutationTransactionResult:
        has_commands = bool(world_edits) or bool(topology_changes)
        if scene_state is None:
            if not has_commands:
                return HostMutationTransactionResult(True)
            error = "SceneState is required"
            return HostMutationTransactionResult(False, errors=[error])

        staged = SceneState(**deepcopy(scene_state.get_snapshot()))
        world_result = self.world_edits.apply(
            staged,
            world_edits,
            current_step=int(current_step),
        )
        topology_result = self.topology.apply(
            staged,
            topology_changes,
            current_step=int(current_step),
        )
        errors = [
            *[f"world_edits:{error}" for error in world_result.errors],
            *[f"topology_changes:{error}" for error in topology_result.errors],
        ]
        if not world_result.committed or not topology_result.committed:
            world_errors = list(world_result.errors)
            topology_errors = list(topology_result.errors)
            if world_result.committed and world_result.changes:
                world_errors.append("rolled back with rejected host mutation batch")
            if topology_result.committed and topology_result.changes:
                topology_errors.append("rolled back with rejected host mutation batch")
            return HostMutationTransactionResult(
                False,
                world_edit_errors=world_errors,
                topology_errors=topology_errors,
                errors=errors,
            )

        changed = bool(world_result.changes or topology_result.changes)
        if changed:
            try:
                previous_version = int(
                    scene_state.get_scene_flag("world_version", 0) or 0
                )
            except (TypeError, ValueError):
                previous_version = 0
            staged.update_scene_flags({"world_version": previous_version + 1})
            scene_state.world_objects = deepcopy(staged.world_objects)
            scene_state.scene_flags = deepcopy(staged.scene_flags)
        return HostMutationTransactionResult(
            True,
            object_changes=list(world_result.changes),
            topology_changes=list(topology_result.changes),
        )
