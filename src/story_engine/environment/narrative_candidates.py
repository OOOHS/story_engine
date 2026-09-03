"""Shared plumbing for every "new content enters the world" candidate kind.

Character entry, storylet definitions and topology growth all share the same
shape: a Host-issued, one-time-consumable authorization (with a validity
window) that a semantic candidate must cite before the Host will stage it.
This module factors out the parts of that shape that do not depend on what
is actually being created -- authorization identity/consumption/window
checking, capped dynamic-name bookkeeping, and a single audit trail -- so
each kind only has to write its own field-level compilation and world-effect
code (see ``character_entries.py``/``character_lifecycle.py`` for the
reference implementation this generalizes).

Deliberately not unified: object introduction has no authorization gate at
all (see ``world_object_lifecycle.py``), and storylet *hit detection* is a
post-commit Host derivation with zero GM authorship (see
``narrative/storylets.py``). Both still write to the shared audit trail here
so every candidate kind's outcome is visible in one place, but neither goes
through ``NarrativeCandidateAuthority``.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CANDIDATE_AUDIT_FLAG = "narrative_candidate_audit"
# Bounds the audit trail itself so a long episode cannot grow this scene flag
# without limit; recent entries are what matters for debugging a given step.
CANDIDATE_AUDIT_MAX_ENTRIES = 200

# NarrativeDirector runs strictly after this tick's commit (see
# components/narrative_director.py), so anything it proposes can only ever
# become a *next-step* authorization -- never a same-tick effect. This is the
# same cross-step queueing shape as ``scene_state.queue_director_signal``,
# generalized to the three authorization-gated candidate kinds.
PENDING_DIRECTOR_AUTHORIZATIONS_FLAG = "pending_narrative_director_authorizations"
DIRECTOR_AUTHORIZATION_WINDOW_STEPS = 20


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


def queue_director_authorization(
    scene_state: Any,
    *,
    kind: str,
    payload: Dict[str, Any],
    current_step: int,
    window: int = DIRECTOR_AUTHORIZATION_WINDOW_STEPS,
) -> Dict[str, Any]:
    """Turn one NarrativeDirector-proposed candidate into a next-step
    authorization, queued the same way ``queue_director_signal`` queues a
    suggestion: written now, only consumable from ``current_step + 1``
    onward, by whichever kind-specific ``Authority.resolve()`` next runs.
    """
    if not scene_state:
        return {}
    pending = list(
        scene_state.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG, []) or []
    )
    authorization_id = f"director:{kind}:{int(current_step)}:{len(pending)}"
    authorization = dict(payload)
    authorization["authorization_id"] = authorization_id
    authorization["kind"] = kind
    authorization.setdefault("not_before_step", int(current_step) + 1)
    authorization.setdefault("expires_step", int(current_step) + 1 + int(window))
    pending.append(authorization)
    scene_state.update_scene_flags({PENDING_DIRECTOR_AUTHORIZATIONS_FLAG: pending})
    return authorization


def drain_due_director_authorizations(
    scene_state: Any,
    *,
    kind: str,
    consumed_flag: str,
    current_step: int,
) -> List[Dict[str, Any]]:
    """Surface director-queued authorizations of ``kind`` that are in their
    validity window this step, pruning ones that expired or were already
    consumed. Authorizations still in-window but not yet cited stay queued
    for a later step instead of being dropped after one look.
    """
    if not scene_state:
        return []
    pending = list(
        scene_state.get_scene_flag(PENDING_DIRECTOR_AUTHORIZATIONS_FLAG, []) or []
    )
    if not pending:
        return []
    consumed = set(CandidateLedger.normalized_names(scene_state, consumed_flag))
    due: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict) or str(item.get("kind", "")) != kind:
            remaining.append(item)
            continue
        authorization_id = str(item.get("authorization_id", "")).strip()
        try:
            expires_step = int(item.get("expires_step", current_step))
        except (TypeError, ValueError):
            expires_step = int(current_step)
        if authorization_id in consumed or int(current_step) > expires_step:
            continue
        remaining.append(item)
        try:
            not_before_step = int(item.get("not_before_step", current_step))
        except (TypeError, ValueError):
            not_before_step = int(current_step)
        if int(current_step) >= not_before_step:
            due.append(item)
    if remaining != pending:
        scene_state.update_scene_flags(
            {PENDING_DIRECTOR_AUTHORIZATIONS_FLAG: remaining}
        )
    return due
