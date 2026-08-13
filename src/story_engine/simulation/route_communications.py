from dataclasses import dataclass
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class RouteCommunicationResolution:
    result: Dict[str, Any]
    traces: tuple[Dict[str, str], ...] = ()


class RouteCommunicationResolver:
    """Compile validated route reports without asserting current world topology."""

    POSITIVE_OUTCOMES = {"success", "partial", "complication"}

    def resolve(
        self, result: Dict[str, Any], *, intents: Iterable[Dict[str, Any]]
    ) -> RouteCommunicationResolution:
        references = {
            str(item.get("actor", "")).strip(): item
            for item in intents or []
            if isinstance(item, dict)
            and str(item.get("action_kind", "")) == "communicate"
            and len(item.get("action_route_path", []) or []) >= 2
        }
        reports = []
        traces = []
        for action in result.get("resolved_actions", []) or []:
            if not isinstance(action, dict):
                continue
            actor = str(action.get("actor", "")).strip()
            reference = references.get(actor)
            if reference is None or str(action.get("outcome", "")).strip() not in self.POSITIVE_OUTCOMES:
                continue
            path = [
                str(item).strip()
                for item in reference.get("action_route_path", []) or []
                if str(item).strip()
            ]
            report = {
                "source": actor,
                "target": str(reference.get("action_target", "")).strip(),
                "route_path": path,
                "route_source": path[0],
                "route_target": path[-1],
                "basis": "reported",
                "reason": str(reference.get("intent", "")).strip()
                or "角色明确转述自己知道的道路",
            }
            reports.append(report)
            traces.append({**report, "status": "host_route_report_materialized"})
        result["route_knowledge_updates"] = reports
        return RouteCommunicationResolution(result=result, traces=tuple(traces))
