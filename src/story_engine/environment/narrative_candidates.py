"""Bookkeeping for world additions and explicit author injections.

Normal semantic settlement can complete characters, objects and locations
when an external result requires them. Lifecycle staging and semantic commit
validation govern that path. Explicit author injections of characters,
locations and storylet definitions cite Host-issued consumable authorizations.
This module supplies that injection gate, dynamic-name ledgers, candidate
audit records and the director's pending storylet proposal records.

Storylet conditions are evaluated against committed state; triggered events
enter settlement before their effects and perceptible facts are committed.
Director proposals pass structural registration; concrete effects cross semantic settlement.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CANDIDATE_AUDIT_FLAG = "narrative_candidate_audit"
# Bounds the audit trail itself so a long episode cannot grow this scene flag
# without limit; recent entries are what matters for debugging a given step.
CANDIDATE_AUDIT_MAX_ENTRIES = 200

# Director drafts are structurally registered before their future content enters the pool.
PENDING_STORY_PROPOSALS_FLAG = "pending_story_planner_proposals"
STORY_REVIEW_STATUS = "structural"
# Older saves used pending_review for the same durable queue.
STORY_PENDING_STATUSES = ("pending_registration", "pending_review")
STORY_PROPOSAL_CAP = 100


@dataclass(frozen=True)
class AuthorizationResolution:
    """Result of resolving a candidate's ``authorization_id`` alone.

    This never inspects kind-specific payload fields (character name,
    storylet conditions, route endpoints, ...). Callers compile their own
    canonical request from ``authorization`` once it is returned here.
    """

    authorization: Optional[Dict[str, Any]] = None
    rejected: List[str] = field(default_factory=list)


class NarrativeCandidateAuthority:
    """Generic Host-issued-authorization gate shared by every candidate kind
    that requires one (currently: character entry, storylet definition,
    topology growth).
    """

    def resolve_authorization(
        self,
        request: Any,
        *,
        domain: str,
        authorizations: Any,
        scene_state: Any,
        consumed_flag: str,
        current_step: int,
    ) -> AuthorizationResolution:
        if request is None:
            return AuthorizationResolution()
        if not isinstance(request, dict):
            return AuthorizationResolution(rejected=[f"{domain}:not_an_object"])
        authorization_id = self._text(request.get("authorization_id"), 160)
        if not authorization_id:
            return AuthorizationResolution(
                rejected=[f"{domain}:missing_authorization_id"]
            )
        records: Dict[str, Dict[str, Any]] = {}
        duplicates: set[str] = set()
        for item in authorizations or []:
            if not isinstance(item, dict):
                continue
            item_id = self._text(item.get("authorization_id"), 160)
            if not item_id:
                continue
            if item_id in records:
                duplicates.add(item_id)
            records[item_id] = item
        if authorization_id in duplicates:
            return AuthorizationResolution(
                rejected=[f"{domain}:ambiguous_authorization:{authorization_id}"]
            )
        authorization = records.get(authorization_id)
        if authorization is None:
            return AuthorizationResolution(
                rejected=[f"{domain}:unknown_authorization:{authorization_id}"]
            )
        raw_consumed = (
            scene_state.get_scene_flag(consumed_flag, []) if scene_state else []
        )
        if not isinstance(raw_consumed, list):
            return AuthorizationResolution(
                rejected=[f"{domain}:invalid_consumed_authorization_ledger"]
            )
        consumed = {
            self._text(item, 160) for item in raw_consumed if self._text(item, 160)
        }
        if authorization_id in consumed:
            return AuthorizationResolution(
                rejected=[f"{domain}:consumed_authorization:{authorization_id}"]
            )
        try:
            not_before = int(authorization.get("not_before_step", current_step))
            expires_step = int(authorization.get("expires_step", current_step))
        except (TypeError, ValueError):
            return AuthorizationResolution(
                rejected=[f"{domain}:invalid_window:{authorization_id}"]
            )
        if int(current_step) < not_before or int(current_step) > expires_step:
            return AuthorizationResolution(
                rejected=[f"{domain}:authorization_out_of_window:{authorization_id}"]
            )
        return AuthorizationResolution(authorization=authorization)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split()).strip()[:limit]


class CandidateLedger:
    """Reusable dedup + cap + consumption bookkeeping for one dynamic pool of
    names/ids tracked in a scene flag.

    Generalizes the pattern already duplicated across
    ``dynamic_character_names``, ``dynamic_world_object_names`` and
    ``consumed_character_entry_authorizations``.
    """

    @staticmethod
    def normalized_names(scene_state: Any, flag: str) -> List[str]:
        raw = scene_state.get_scene_flag(flag, []) if scene_state else []
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @staticmethod
    def check_cap(
        scene_state: Any,
        *,
        names_flag: str,
        cap_flag: str,
        default_cap: int,
    ) -> Optional[str]:
        names = CandidateLedger.normalized_names(scene_state, names_flag)
        try:
            limit = max(
                0, int(scene_state.get_scene_flag(cap_flag, default_cap) or 0)
            )
        except (TypeError, ValueError):
            return f"{cap_flag} must be an integer"
        if len(names) >= limit:
            return f"exceeds {cap_flag}"
        return None

    @staticmethod
    def append_name(scene_state: Any, flag: str, name: str) -> None:
        names = CandidateLedger.normalized_names(scene_state, flag)
        if name not in names:
            names.append(name)
        scene_state.update_scene_flags({flag: names})

    @staticmethod
    def consume_authorization(
        scene_state: Any, flag: str, authorization_id: str
    ) -> None:
        if not authorization_id:
            return
        consumed = list(scene_state.get_scene_flag(flag, []) or [])
        if authorization_id not in consumed:
            consumed.append(authorization_id)
        scene_state.update_scene_flags({flag: consumed})


def record_candidate_audit(
    scene_state: Any,
    *,
    kind: str,
    source: str,
    accepted: bool,
    reason: str = "",
    candidate_id: str = "",
    step: int = 0,
) -> None:
    """Append one outcome to the shared candidate audit trail.

    Every kind writes here regardless of whether it goes through
    ``NarrativeCandidateAuthority`` (character/storylet_definition/topology)
    or stays ungated (object), so "what new content was proposed and what
    happened to it" is answerable from one place instead of per-kind ledgers
    with inconsistent coverage.
    """

    if not scene_state:
        return
    entries = list(scene_state.get_scene_flag(CANDIDATE_AUDIT_FLAG, []) or [])
    entries.append(
        {
            "kind": str(kind),
            "source": str(source),
            "accepted": bool(accepted),
            "reason": str(reason)[:300],
            "candidate_id": str(candidate_id)[:160],
            "step": int(step),
        }
    )
    if len(entries) > CANDIDATE_AUDIT_MAX_ENTRIES:
        entries = entries[-CANDIDATE_AUDIT_MAX_ENTRIES:]
    scene_state.update_scene_flags({CANDIDATE_AUDIT_FLAG: entries})


def record_story_proposal(scene_state: Any, candidate: Dict[str, Any], *, turn_id: str) -> Dict[str, Any]:
    """Record a draft for structural registration; retain a bounded recent history."""
    pending = list(scene_state.get_scene_flag(PENDING_STORY_PROPOSALS_FLAG, []) or [])
    if sum(p.get("status") in STORY_PENDING_STATUSES for p in pending) >= STORY_PROPOSAL_CAP:
        return {"status": "rejected", "reason": "pending_proposal_cap"}
    proposal = {
        "proposal_id": f"story-proposal:{turn_id}:{len(pending)}",
        **candidate,
        "status": "pending_registration",
        "review_status": STORY_REVIEW_STATUS,
    }
    pending.append(proposal)
    while len(pending) > STORY_PROPOSAL_CAP:
        removable = next((i for i, p in enumerate(pending) if p.get("status") not in STORY_PENDING_STATUSES), None)
        if removable is None:
            break
        pending.pop(removable)
    scene_state.update_scene_flags({PENDING_STORY_PROPOSALS_FLAG: pending})
    return proposal
