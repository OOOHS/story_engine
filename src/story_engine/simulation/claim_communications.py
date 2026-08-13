from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class ClaimCommunicationResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, Any], ...] = ()


class ClaimCommunicationResolver:
    """Project validated Claim references into knowledge-transfer requests."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        intents: Iterable[Dict[str, Any]],
    ) -> ClaimCommunicationResolution:
        existing = result.get("knowledge_updates", [])
        if not isinstance(existing, list):
            existing = []
        # Claim transfer belongs to the original Agent proposal. Preserve event
        # and legacy semantic communication while replacing model-authored Claim
        # records with Host-derived requests.
        retained = [
            item
            for item in existing
            if not isinstance(item, dict)
            or not str(item.get("claim_id", "")).strip()
        ]
        references = {
            str(item.get("actor", "")).strip(): item
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("action_kind", "")).strip() == "communicate"
            and str(item.get("action_target", "")).strip()
            and str(item.get("action_claim_id", "")).strip()
        }
        traces = []
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            reference = references.get(actor)
            if reference is None:
                continue
            if str(action.get("outcome", "")).strip() not in self.POSITIVE_OUTCOMES:
                continue
            target = str(reference.get("action_target", "")).strip()
            claim_id = str(reference.get("action_claim_id", "")).strip()
            stance = str(reference.get("action_claim_stance", "")).strip()
            evidence_refs = [
                str(item).strip()
                for item in reference.get("action_evidence_refs", []) or []
                if str(item).strip()
            ]
            action["action_kind"] = "communicate"
            action["action_target"] = target
            retained.append(
                {
                    "source": actor,
                    "target": target,
                    "claim_id": claim_id,
                    "asserted_stance": stance,
                    "cited_evidence": evidence_refs,
                    "reason": "Agent 在沟通 proposal 中明确表达了自己知道的 Claim",
                }
            )
            traces.append(
                {
                    "source": actor,
                    "target": target,
                    "claim_id": claim_id,
                    "asserted_stance": stance,
                    "cited_evidence": evidence_refs,
                    "status": "host_claim_communication_materialized",
                }
            )
        result["knowledge_updates"] = retained
        return ClaimCommunicationResolution(result=result, traces=tuple(traces))
