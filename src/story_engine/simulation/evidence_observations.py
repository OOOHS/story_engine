from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class EvidenceObservationResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, str], ...] = ()


class EvidenceObservationResolver:
    """Derive Claim discovery from active observation of linked evidence."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def resolve(
        self,
        result: Dict[str, Any],
        *,
        intents: Iterable[Dict[str, Any]],
        scene_state: Any,
        claim_registry: Any,
    ) -> EvidenceObservationResolution:
        # Discovery is a Host projection of evidence edges, never a semantic
        # model write. An empty registry therefore authoritatively yields none.
        result["claim_discoveries"] = []
        if scene_state is None or claim_registry is None:
            return EvidenceObservationResolution(result=result)

        targets = {
            str(item.get("actor", "")).strip(): str(
                item.get("action_target", "")
            ).strip()
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("actor", "")).strip()
            and str(item.get("action_kind", "")).strip() == "observe"
            and str(item.get("action_target", "")).strip()
        }
        links: Dict[str, list[tuple[str, str, str]]] = {}
        for entity in claim_registry.entities():
            fact = entity.get_component("ClaimFact")
            evidence = entity.get_component("ClaimEvidence")
            if fact is None or evidence is None:
                continue
            for object_id in evidence.supports:
                links.setdefault(str(object_id), []).append(
                    (fact.claim_id, "supports", fact.statement)
                )
            for object_id in evidence.refutes:
                links.setdefault(str(object_id), []).append(
                    (fact.claim_id, "refutes", fact.statement)
                )

        discoveries = []
        traces = []
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            target = targets.get(actor, "")
            if (
                not target
                or str(action.get("outcome", "")).strip()
                not in self.POSITIVE_OUTCOMES
                or target not in scene_state.get_visible_objects(actor)
            ):
                continue
            linked = sorted(links.get(target, []), key=lambda item: item[0])
            if not linked:
                continue
            action["action_kind"] = "observe"
            action["action_target"] = target
            findings = []
            for claim_id, relation, statement in linked:
                verb = "支持" if relation == "supports" else "反驳"
                findings.append(f"{target}中的可核验信息{verb}命题：{statement}")
                discoveries.append(
                    {
                        "actor": actor,
                        "claim_id": claim_id,
                        "evidence_ref": target,
                        "reason": f"主动观察确认{target}与该命题存在结构化证据关联",
                    }
                )
                traces.append(
                    {
                        "actor": actor,
                        "object_id": target,
                        "claim_id": claim_id,
                        "relation": relation,
                        "status": "host_evidence_discovered",
                    }
                )
            action["private_result"] = "；".join(findings)
        result["claim_discoveries"] = discoveries
        return EvidenceObservationResolution(
            result=result,
            traces=tuple(traces),
        )
