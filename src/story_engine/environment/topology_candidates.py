"""Runtime candidate registration for spatial-graph growth (new locations).

``HostTopologyTransaction`` (``topology.py``) is deliberately "unavailable to
Agent/GM semantic output" -- it is a pre-step, host-authored surface for
editing the *existing* graph's edges, applied before any Agent perception or
GM semantic resolution happens this step. That isolation stays exactly as
strict as it is today for the existing graph.

This module is a narrower, additive surface: a pre-authorized proposal to
grow the graph by one new location and its initial edges. It goes through
the same authorize -> resolve -> prepare -> stage -> atomic-commit pipeline
as the character/storylet_definition candidate kinds (inside
``WorldStateTransaction.commit()``), not through ``HostTopologyTransaction``'s
own pre-step snapshot/commit cycle -- so it can never bypass semantic
transaction validation, and a rejected step rolls the new location back with
everything else.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .narrative_candidates import CandidateLedger, NarrativeCandidateAuthority

CONSUMED_AUTHORIZATIONS_FLAG = "consumed_topology_authorizations"
DYNAMIC_LOCATION_NAMES_FLAG = "dynamic_location_names"
MAX_DYNAMIC_LOCATIONS_FLAG = "max_dynamic_locations"
VISIBILITIES = {"local", "public", "hidden"}


@dataclass(frozen=True)
class TopologyCandidateResolution:
    request: Optional[Dict[str, Any]] = None
    rejected: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TopologyCandidatePlan:
    location_id: str
    connects_to: List[str] = field(default_factory=list)
    visibility: str = "local"
    reason: str = ""
    authorization_id: str = ""


@dataclass(frozen=True)
class TopologyCandidatePreparation:
    plan: Optional[TopologyCandidatePlan] = None
    errors: List[str] = field(default_factory=list)


class TopologyCandidateAuthority:
    """Compile a semantic ``topology`` candidate from a host-issued
    authorization. Governed as strictly as character entry: a permanent
    structural addition to the world graph is not something GM narration can
    conjure on its own.
    """

    def __init__(self) -> None:
        self._authority = NarrativeCandidateAuthority()

    def resolve(
        self,
        request: Any,
        *,
        authorizations: Any,
        scene_state: Any,
        current_step: int,
    ) -> TopologyCandidateResolution:
        if request is None:
            return TopologyCandidateResolution()
        if not isinstance(request, dict):
            return TopologyCandidateResolution(rejected=["topology:not_an_object"])

        resolution = self._authority.resolve_authorization(
            request,
            domain="topology",
            authorizations=authorizations,
            scene_state=scene_state,
            consumed_flag=CONSUMED_AUTHORIZATIONS_FLAG,
            current_step=current_step,
        )
        if resolution.rejected:
            return TopologyCandidateResolution(rejected=resolution.rejected)
        authorization = resolution.authorization
        authorization_id = self._text(request.get("authorization_id"), 160)

        location_id = self._text(authorization.get("location_id"), 120)
        if not location_id:
            return TopologyCandidateResolution(
                rejected=[f"topology:incomplete_authorization:{authorization_id}"]
            )
        connects_to = self._text_list(authorization.get("connects_to", []), 8, 160)
        visibility = self._text(authorization.get("visibility", "local"), 20).lower()
        if visibility not in VISIBILITIES:
            visibility = "local"
        canonical = {
            "authorization_id": authorization_id,
            "location_id": location_id,
            "connects_to": connects_to,
            "visibility": visibility,
            "reason": self._text(authorization.get("reason"), 300),
        }
        return TopologyCandidateResolution(request=canonical)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]

    def _text_list(self, value: Any, limit: int, item_limit: int) -> List[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        seen: List[str] = []
        for item in value[:limit]:
            text = self._text(item, item_limit)
            if text and text not in seen:
                seen.append(text)
        return seen


class TopologyCandidateLifecycle:
    """Two-phase staging of one new location + its initial edges."""

    def prepare(
        self,
        scene_state: Any,
        request: Any,
    ) -> TopologyCandidatePreparation:
        if request is None:
            return TopologyCandidatePreparation()
        if not isinstance(request, dict):
            return TopologyCandidatePreparation(
                errors=["topology_candidate must be an object or null"]
            )
        if not scene_state:
            return TopologyCandidatePreparation(
                errors=["topology_candidate requires scene state"]
            )

        location_id = str(request.get("location_id", "")).strip()
        errors: List[str] = []
        if not location_id:
            errors.append("topology_candidate requires a location_id")
        elif (
            location_id in scene_state.world_objects
            or location_id in scene_state.actor_states
        ):
            errors.append(
                f"topology_candidate cannot create existing object: {location_id}"
            )
        connects_to = [
            str(item).strip()
            for item in request.get("connects_to", []) or []
            if str(item).strip()
        ]
        known_locations = scene_state.get_known_locations()
        for target in connects_to:
            if target not in known_locations:
                errors.append(
                    f"topology_candidate connects to unknown location: {target}"
                )
        cap_error = CandidateLedger.check_cap(
            scene_state,
            names_flag=DYNAMIC_LOCATION_NAMES_FLAG,
            cap_flag=MAX_DYNAMIC_LOCATIONS_FLAG,
            default_cap=6,
        )
        if cap_error:
            errors.append(f"topology_candidate {cap_error}")
        if errors:
            return TopologyCandidatePreparation(errors=errors)

        return TopologyCandidatePreparation(
            plan=TopologyCandidatePlan(
                location_id=location_id,
                connects_to=list(dict.fromkeys(connects_to)),
                visibility=str(request.get("visibility", "local")),
                reason=str(request.get("reason", "")),
                authorization_id=str(request.get("authorization_id", "")).strip(),
            )
        )

    def stage(
        self,
        scene_state: Any,
        plan: Optional[TopologyCandidatePlan],
    ) -> List[str]:
        if plan is None:
            return []
        if (
            plan.location_id in scene_state.world_objects
            or plan.location_id in scene_state.actor_states
        ):
            return [
                "topology_candidate cannot create existing object while "
                f"staging: {plan.location_id}"
            ]
        known_locations = scene_state.get_known_locations()
        for target in plan.connects_to:
            if target not in known_locations:
                return [
                    "topology_candidate connects to unknown location while "
                    f"staging: {target}"
                ]
        if plan.authorization_id:
            consumed = CandidateLedger.normalized_names(
                scene_state, CONSUMED_AUTHORIZATIONS_FLAG
            )
            if plan.authorization_id in consumed:
                return [
                    "topology_candidate authorization was consumed while "
                    f"staging: {plan.authorization_id}"
                ]

        scene_state.world_objects[plan.location_id] = {
            "is_location": True,
            "connected_to": list(plan.connects_to),
        }
        for target in plan.connects_to:
            state = scene_state.get_object_state(target)
            connections = [
                str(item).strip()
                for item in state.get("connected_to", []) or []
                if str(item).strip()
            ]
            if plan.location_id not in connections:
                connections.append(plan.location_id)
                scene_state.update_object_state(target, {"connected_to": connections})

        CandidateLedger.append_name(
            scene_state, DYNAMIC_LOCATION_NAMES_FLAG, plan.location_id
        )
        if plan.authorization_id:
            CandidateLedger.consume_authorization(
                scene_state, CONSUMED_AUTHORIZATIONS_FLAG, plan.authorization_id
            )
        return []
