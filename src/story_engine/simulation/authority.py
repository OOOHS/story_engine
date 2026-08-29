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
        "storylet_hits",
    )
    HOST_OWNED_MAP_FIELDS = ()
    BRANCH_NAMES = ("success", "failure")
    # The resolver names a magnitude directly. The host no longer maps a
    # qualitative tier to a fixed number -- it only bounds the raw value so
    # one battle cannot swing every track to its ceiling in a single tick.
    # ``*_BOUNDS`` are (min, max) clamps, not a lookup of allowed values.
    MAGNITUDE_BOUNDS = (0.0, 1.0)
    DRIVE_DELTA_BOUNDS = (-0.4, 0.4)
    DRIFT_BOUNDS = (0.0, 0.08)
    THRESHOLD_BOUNDS = (0.5, 0.95)
    TENSION_BOUNDS = (-0.15, 0.15)
    # A hard, small ceiling so the GM cannot turn "one soft nudge" into a
    # directive stream across every actor in a single tick.
    MAX_DIRECTOR_SIGNALS_PER_TICK = 3
    MAX_DIRECTOR_SIGNAL_LENGTH = 280
    MAX_PLOT_BEAT_PROPOSALS_PER_TICK = 2
    MAX_PLOT_BEAT_CONDITIONS = 6
    BEAT_KINDS = ("environment", "character_decision")
    CONDITION_SCOPES = ("scene", "world_object", "actor", "plot")
    CONDITION_OPERATORS = (
        "eq", "ne", "gt", "gte", "lt", "lte",
        "contains", "in", "exists", "not_exists",
    )
    BEAT_VISIBILITY = ("public", "local", "hidden")

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
        self._compile_plot_beat_proposals(result, "result", rejected)

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
        if not ensure_fields and "plot_beat_proposals" in container:
            if container.get("plot_beat_proposals") not in (None, []):
                rejected.append(f"{path}.plot_beat_proposals")
            container.pop("plot_beat_proposals", None)

    def _compile_semantic_effects(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        self._compile_magnitude_list(
            container,
            field_name="social_impacts",
            path=path,
            rejected=rejected,
        )
        self._compile_magnitude_list(
            container,
            field_name="modifier_updates",
            path=path,
            rejected=rejected,
        )
        self._compile_drive_updates(container, path, rejected)
        self._compile_drive_creations(container, path, rejected)
        self._compile_director_signals(container, path, rejected)

        raw_tension = container.get("tension_delta")
        container["tension_delta"] = self._clamp(
            raw_tension, *self.TENSION_BOUNDS, default=0.0
        )
        if raw_tension is not None and container["tension_delta"] != self._as_float(
            raw_tension, 0.0
        ):
            rejected.append(f"{path}.tension_delta")

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clamp(self, value: Any, low: float, high: float, *, default: float) -> float:
        magnitude = self._as_float(value, default)
        return max(low, min(high, magnitude))

    def _compile_magnitude_list(
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
            raw_magnitude = update.get("magnitude")
            clamped = self._clamp(raw_magnitude, *self.MAGNITUDE_BOUNDS, default=0.5)
            if raw_magnitude is not None and self._as_float(raw_magnitude, 0.5) != clamped:
                rejected.append(f"{path}.{field_name}[{index}].magnitude")
            update["magnitude"] = clamped

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
            raw_delta = update.get("delta")
            clamped = self._clamp(raw_delta, *self.DRIVE_DELTA_BOUNDS, default=0.0)
            if raw_delta is not None and self._as_float(raw_delta, 0.0) != clamped:
                rejected.append(f"{path}.drive_updates[{index}].delta")
            update["delta"] = clamped
            update["direction"] = "decrease" if clamped < 0 else "increase"

    def _compile_drive_creations(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        """Shape drive_creations: a resolver may name a brand-new need and
        give it a bounded drift/threshold, but never its starting pressure
        -- created needs always start at 0.0. Budget enforcement (whether
        the actor may create at all) happens later, against real
        DriveState, in NeedDynamics.apply_creations; this pass only bounds
        the numbers the resolver names.
        """
        creations = container.get("drive_creations")
        if not isinstance(creations, list):
            return
        for index, creation in enumerate(creations):
            if not isinstance(creation, dict):
                continue
            for raw_field in ("pressure", "initial_pressure"):
                if creation.pop(raw_field, None) is not None:
                    rejected.append(f"{path}.drive_creations[{index}].{raw_field}")
            raw_drift = creation.get("drift_per_turn")
            drift = self._clamp(raw_drift, *self.DRIFT_BOUNDS, default=0.03)
            if raw_drift is not None and self._as_float(raw_drift, 0.03) != drift:
                rejected.append(f"{path}.drive_creations[{index}].drift_per_turn")
            raw_threshold = creation.get("critical_threshold")
            threshold = self._clamp(raw_threshold, *self.THRESHOLD_BOUNDS, default=0.8)
            if raw_threshold is not None and self._as_float(raw_threshold, 0.8) != threshold:
                rejected.append(f"{path}.drive_creations[{index}].critical_threshold")
            creation["drift_per_turn"] = drift
            creation["critical_threshold"] = threshold

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
                    "source_storylet_id": str(
                        item.get("source_storylet_id", "")
                    ).strip(),
                    "tags": [
                        str(tag).strip()
                        for tag in (item.get("tags") or [])[:6]
                        if str(tag).strip()
                    ],
                }
            )
        container["director_signals"] = compiled

    def _compile_plot_beat_proposals(
        self,
        container: Dict[str, Any],
        path: str,
        rejected: List[str],
    ) -> None:
        """Shape plot_beat_proposals: a resolver may open a thread and name
        a future opportunity (conditions + cash-out kind), but cannot write
        plot clocks or mutate the world through this field. World setup still
        goes through object_lifecycle / state_updates in the same tick.
        """
        raw = container.get("plot_beat_proposals")
        if raw is None:
            container["plot_beat_proposals"] = []
            return
        if not isinstance(raw, list):
            rejected.append(f"{path}.plot_beat_proposals")
            container["plot_beat_proposals"] = []
            return

        compiled: List[Dict[str, Any]] = []
        for index, item in enumerate(raw):
            prefix = f"{path}.plot_beat_proposals[{index}]"
            if not isinstance(item, dict):
                rejected.append(prefix)
                continue
            if len(compiled) >= self.MAX_PLOT_BEAT_PROPOSALS_PER_TICK:
                rejected.append(f"{prefix}:over_budget")
                continue
            proposal = self._compile_one_plot_beat(item, prefix, rejected)
            if proposal is not None:
                compiled.append(proposal)
        container["plot_beat_proposals"] = compiled

    def _compile_one_plot_beat(
        self,
        item: Dict[str, Any],
        prefix: str,
        rejected: List[str],
    ) -> Dict[str, Any] | None:
        for banned in (
            "clock",
            "max_clock",
            "advance",
            "stage_shift",
            "plot_updates",
            "state_updates",
            "object_lifecycle",
            "current_stage",
        ):
            if item.pop(banned, None) is not None:
                rejected.append(f"{prefix}.{banned}")

        plot_id = self._clean_stable_id(item.get("plot_id"))
        beat_id = self._clean_stable_id(item.get("beat_id"))
        intent = " ".join(str(item.get("intent", "")).split()).strip()[:300]
        if not plot_id or not beat_id or not intent:
            rejected.append(prefix)
            return None

        kind = str(item.get("kind", "environment")).strip()
        if kind not in self.BEAT_KINDS:
            rejected.append(f"{prefix}.kind")
            kind = "environment"

        conditions = self._compile_beat_conditions(
            item.get("conditions"),
            f"{prefix}.conditions",
            rejected,
        )
        if not conditions:
            rejected.append(f"{prefix}.conditions")
            return None

        raw_open = item.get("open_thread")
        open_thread = self._compile_open_thread(
            raw_open,
            f"{prefix}.open_thread",
            rejected,
        )
        if raw_open not in (None, {}, False) and open_thread is None:
            return None
        effect = self._compile_beat_effect(
            item.get("effect"),
            f"{prefix}.effect",
            rejected,
        )
        return {
            "plot_id": plot_id,
            "beat_id": beat_id,
            "intent": intent,
            "kind": kind,
            "one_shot": bool(item.get("one_shot", True)),
            "conditions": conditions,
            "effect": effect,
            "open_thread": open_thread,
        }

    def _compile_open_thread(
        self,
        raw: Any,
        prefix: str,
        rejected: List[str],
    ) -> Dict[str, Any] | None:
        if raw in (None, {}, False):
            return None
        if not isinstance(raw, dict):
            rejected.append(prefix)
            return None
        for banned in ("clock", "max_clock", "current_stage", "stages", "advance"):
            if raw.pop(banned, None) is not None:
                rejected.append(f"{prefix}.{banned}")
        opened_reason = " ".join(str(raw.get("opened_reason", "")).split()).strip()[:300]
        if not opened_reason:
            rejected.append(f"{prefix}.opened_reason")
            return None
        participants = [
            str(item).strip()[:80]
            for item in (raw.get("participants") or [])[:8]
            if str(item).strip()
        ]
        return {
            "title": " ".join(str(raw.get("title", "")).split()).strip()[:80],
            "description": " ".join(str(raw.get("description", "")).split()).strip()[:300],
            "opened_reason": opened_reason,
            "participants": participants,
        }

    def _compile_beat_effect(
        self,
        raw: Any,
        prefix: str,
        rejected: List[str],
    ) -> Dict[str, Any]:
        effect = raw if isinstance(raw, dict) else {}
        if raw not in (None, {}) and not isinstance(raw, dict):
            rejected.append(prefix)
            effect = {}
        for banned in (
            "plot_updates",
            "state_updates",
            "object_lifecycle",
            "advance",
            "clock",
        ):
            if effect.pop(banned, None) is not None:
                rejected.append(f"{prefix}.{banned}")
        visibility = str(effect.get("visibility", "public")).strip()
        if visibility not in self.BEAT_VISIBILITY:
            rejected.append(f"{prefix}.visibility")
            visibility = "public"
        preferred = [
            str(item).strip()[:80]
            for item in (effect.get("preferred_actors") or [])[:6]
            if str(item).strip()
        ]
        target = str(effect.get("target_actor") or "").strip()[:80]
        return {
            "visibility": visibility,
            "preferred_actors": preferred,
            "target_actor": target or None,
            "stake": " ".join(str(effect.get("stake", "")).split()).strip()[:80],
        }

    def _compile_beat_conditions(
        self,
        raw: Any,
        prefix: str,
        rejected: List[str],
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        compiled: List[Dict[str, Any]] = []
        for index, item in enumerate(raw):
            if len(compiled) >= self.MAX_PLOT_BEAT_CONDITIONS:
                rejected.append(f"{prefix}[{index}]:over_budget")
                continue
            if not isinstance(item, dict):
                rejected.append(f"{prefix}[{index}]")
                continue
            scope = str(item.get("scope", "scene")).strip()
            if scope not in self.CONDITION_SCOPES:
                rejected.append(f"{prefix}[{index}].scope")
                continue
            operator = str(item.get("operator", "eq")).strip()
            if operator not in self.CONDITION_OPERATORS:
                rejected.append(f"{prefix}[{index}].operator")
                continue
            path = str(item.get("path", "")).strip()[:80]
            if not path and operator not in {"exists", "not_exists"}:
                rejected.append(f"{prefix}[{index}].path")
                continue
            if path.count(".") > 5:
                rejected.append(f"{prefix}[{index}].path")
                continue
            target = str(item.get("target") or "").strip()[:80]
            if scope != "scene" and not target:
                rejected.append(f"{prefix}[{index}].target")
                continue
            compiled.append(
                {
                    "scope": scope,
                    "target": target or None,
                    "path": path,
                    "operator": operator,
                    "value": item.get("value"),
                }
            )
        return compiled

    @staticmethod
    def _clean_stable_id(raw: Any) -> str:
        text = " ".join(str(raw or "").split()).strip()[:80]
        for char in (":", "|", "/", "\\"):
            text = text.replace(char, "_")
        return text
