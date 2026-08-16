from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class AuthorityFilterResult:
    """A semantic result after host-owned write channels were removed."""

    result: Dict[str, Any]
    rejected_writes: List[str] = field(default_factory=list)


class SemanticAuthorityFilter:
    """Separates semantic adjudication from host-owned state transitions.

    A GM may describe concrete action consequences and qualitative appraisals.
    It does not own exact long-term relationship tracks or authored Plot clocks.
    Those are derived later by host systems from committed facts.

    The filter lives outside ``SimulationControl`` deliberately: scripted,
    Hermes-backed, or future semantic resolvers all cross the same boundary.
    """

    HOST_OWNED_LIST_FIELDS = (
        "plot_updates",
        "relationship_updates",
        "contract_settlements",
        "contract_escrow_deposits",
        "storylet_hits",
    )
    HOST_OWNED_MAP_FIELDS = ("contract_authorizations",)
    BRANCH_NAMES = ("success", "failure")
    INTENSITY_SCALE = {
        "minor": 0.25,
        "moderate": 0.5,
        "major": 0.75,
        "extreme": 1.0,
    }
    DRIVE_SCALE = {
        "minor": 0.05,
        "moderate": 0.12,
        "major": 0.25,
        "extreme": 0.4,
    }
    # Governs newly-created needs (drive_creations), not existing-need
    # updates. Same philosophy as DRIVE_SCALE: the resolver names a
    # qualitative tier, the host owns what number that tier means.
    DRIFT_SCALE = {
        "none": 0.0,
        "slow": 0.01,
        "steady": 0.03,
        "urgent": 0.08,
    }
    THRESHOLD_SCALE = {
        "fragile": 0.6,
        "normal": 0.8,
        "durable": 0.95,
    }
    TENSION_SCALE = {
        "none": 0.0,
        "low": 0.025,
        "medium": 0.06,
        "high": 0.12,
    }
    # A hard, small ceiling so the GM cannot turn "one soft nudge" into a
    # directive stream across every actor in a single tick.
    MAX_DIRECTOR_SIGNALS_PER_TICK = 3
    MAX_DIRECTOR_SIGNAL_LENGTH = 280

    def sanitize(self, candidate: Any) -> AuthorityFilterResult:
        if not isinstance(candidate, dict):
            return AuthorityFilterResult(
                result={},
                rejected_writes=["semantic_result:not_an_object"],
            )

        result = deepcopy(candidate)
        rejected: List[str] = []
        self._strip_container(result, "result", rejected, ensure_fields=True)
        self._compile_semantic_effects(result, "result", rejected)

        checks = result.get("uncertain_outcomes", [])
        if isinstance(checks, list):
            for index, check in enumerate(checks):
                if not isinstance(check, dict):
                    continue
                for branch_name in self.BRANCH_NAMES:
                    branch = check.get(branch_name)
                    if isinstance(branch, dict):
                        self._strip_container(
                            branch,
                            f"uncertain_outcomes[{index}].{branch_name}",
                            rejected,
                            ensure_fields=False,
                        )
                        self._compile_semantic_effects(
                            branch,
                            f"uncertain_outcomes[{index}].{branch_name}",
                            rejected,
                        )

        if rejected:
            notes = result.get("simulation_notes", [])
            if not isinstance(notes, list):
                notes = [str(notes)] if notes else []
            notes.append(
                "宿主忽略或重新编译了语义结算器提交的宿主数值与权威状态字段。"
            )
            result["simulation_notes"] = notes
        return AuthorityFilterResult(result=result, rejected_writes=rejected)

    def _strip_container(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
        *,
        ensure_fields: bool,
    ) -> None:
        for field_name in self.HOST_OWNED_LIST_FIELDS:
            if field_name not in container and not ensure_fields:
                continue
            value = container.get(field_name)
            if value not in (None, []):
                rejected.append(f"{path}.{field_name}")
            container[field_name] = []
        for field_name in self.HOST_OWNED_MAP_FIELDS:
            if field_name not in container and not ensure_fields:
                continue
            value = container.get(field_name)
            if value not in (None, {}):
                rejected.append(f"{path}.{field_name}")
            container[field_name] = {}

    def _compile_semantic_effects(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        self._compile_intensity_list(
            container,
            field_name="social_impacts",
            path=path,
            rejected=rejected,
        )
        self._compile_intensity_list(
            container,
            field_name="modifier_updates",
            path=path,
            rejected=rejected,
        )
        self._compile_drive_updates(container, path, rejected)
        self._compile_drive_creations(container, path, rejected)
        self._compile_director_signals(container, path, rejected)

        raw_tension = container.get("tension_delta")
        if raw_tension not in (None, 0, 0.0):
            rejected.append(f"{path}.tension_delta")
        conflict_level = str(container.get("conflict_level", "none")).strip().lower()
        container["tension_delta"] = self.TENSION_SCALE.get(conflict_level, 0.0)

    def _compile_intensity_list(
        self,
        container: Dict[str, Any],
        *,
        field_name: str,
        path: str,
        rejected: List[str],
    ) -> None:
        updates = container.get(field_name)
        if not isinstance(updates, list):
            return
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                continue
            raw_magnitude = update.pop("magnitude", None)
            if raw_magnitude is not None:
                rejected.append(f"{path}.{field_name}[{index}].magnitude")
            intensity = str(update.get("intensity", "moderate")).strip().lower()
            if intensity not in self.INTENSITY_SCALE:
                rejected.append(f"{path}.{field_name}[{index}].intensity")
                intensity = "moderate"
            update["intensity"] = intensity
            update["magnitude"] = self.INTENSITY_SCALE[intensity]

    def _compile_drive_updates(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        updates = container.get("drive_updates")
        if not isinstance(updates, list):
            return
        for index, update in enumerate(updates):
            if not isinstance(update, dict):
                continue
            raw_delta = update.pop("delta", None)
            direction = str(update.get("direction", "")).strip().lower()
            if raw_delta is not None:
                rejected.append(f"{path}.drive_updates[{index}].delta")
                if direction not in {"increase", "decrease"}:
                    try:
                        direction = "decrease" if float(raw_delta) < 0 else "increase"
                    except (TypeError, ValueError):
                        direction = "increase"
            if direction not in {"increase", "decrease"}:
                rejected.append(f"{path}.drive_updates[{index}].direction")
                direction = "increase"
            intensity = str(update.get("intensity", "moderate")).strip().lower()
            if intensity not in self.DRIVE_SCALE:
                rejected.append(f"{path}.drive_updates[{index}].intensity")
                intensity = "moderate"
            update["direction"] = direction
            update["intensity"] = intensity
            magnitude = self.DRIVE_SCALE[intensity]
            update["delta"] = -magnitude if direction == "decrease" else magnitude

    def _compile_drive_creations(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        """Shape drive_creations: a resolver may name a brand-new need and a
        qualitative drift/threshold tier, but never its numbers, and never
        its starting pressure -- created needs always start at 0.0. Budget
        enforcement (whether the actor may create at all) happens later,
        against real DriveState, in NeedDynamics.apply_creations; this pass
        only owns the qualitative-to-numeric mapping.
        """
        creations = container.get("drive_creations")
        if not isinstance(creations, list):
            return
        for index, creation in enumerate(creations):
            if not isinstance(creation, dict):
                continue
            for raw_field in (
                "pressure",
                "initial_pressure",
                "drift_per_turn",
                "critical_threshold",
            ):
                if creation.pop(raw_field, None) is not None:
                    rejected.append(f"{path}.drive_creations[{index}].{raw_field}")
            drift = str(creation.get("drift", "steady")).strip().lower()
            if drift not in self.DRIFT_SCALE:
                rejected.append(f"{path}.drive_creations[{index}].drift")
                drift = "steady"
            threshold = str(creation.get("threshold", "normal")).strip().lower()
            if threshold not in self.THRESHOLD_SCALE:
                rejected.append(f"{path}.drive_creations[{index}].threshold")
                threshold = "normal"
            creation["drift"] = drift
            creation["threshold"] = threshold
            creation["drift_per_turn"] = self.DRIFT_SCALE[drift]
            creation["critical_threshold"] = self.THRESHOLD_SCALE[threshold]

    def _compile_director_signals(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        """Shape and bound director_signals; this is GM-authored content,
        not a host-owned field, so valid entries are kept (not zeroed) --
        only malformed or excess entries are dropped.
        """
        raw = container.get("director_signals")
        if raw is None:
            container["director_signals"] = []
            return
        if not isinstance(raw, list):
            rejected.append(f"{path}.director_signals")
            container["director_signals"] = []
            return

        compiled: List[Dict[str, Any]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                rejected.append(f"{path}.director_signals[{index}]")
                continue
            actor = str(item.get("actor", "")).strip()
            suggestion = str(item.get("suggestion", "")).strip()
            if not actor or not suggestion:
                rejected.append(f"{path}.director_signals[{index}]")
                continue
            if len(compiled) >= self.MAX_DIRECTOR_SIGNALS_PER_TICK:
                rejected.append(f"{path}.director_signals[{index}]:over_budget")
                continue
            compiled.append(
                {
                    "actor": actor,
                    "suggestion": suggestion[: self.MAX_DIRECTOR_SIGNAL_LENGTH],
                    "source_plot_id": str(item.get("source_plot_id", "")).strip(),
                    "tags": [
                        str(tag).strip()
                        for tag in (item.get("tags") or [])[:6]
                        if str(tag).strip()
                    ],
                }
            )
        container["director_signals"] = compiled
