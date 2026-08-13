from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class MemoryQueryRoute:
    route: str
    query: str


class AgentMemoryContextBuilder:
    """Build bounded associative-memory queries from POV-safe private state."""

    MAX_ROUTES = 6
    MAX_QUERY_CHARS = 800
    MAX_RESULTS = 6
    MAX_RESULT_CHARS = 1800
    MAX_TOTAL_RESULT_CHARS = 9000
    ROUTE_PRIORITIES = {
        "goals": 1.5,
        "commitments": 1.4,
        "claims": 1.4,
        "social": 1.2,
        "situation": 1.0,
        "reflection": 0.9,
    }

    def build_queries(
        self,
        *,
        actor_name: str,
        world_view: Dict[str, Any],
        recent_observations: Iterable[Any],
        visible_proposals: Iterable[Dict[str, Any]],
        world_signals: Iterable[Dict[str, Any]],
        private_goals: Dict[str, Any],
        private_obligations: Dict[str, Any],
        private_schedule: Dict[str, Any],
        private_agreements: Dict[str, Any],
        private_knowledge: Dict[str, Any],
        private_navigation: Dict[str, Any],
        private_sentiments: Dict[str, Any],
        relationship_context: Dict[str, Any],
        private_cognition: Dict[str, Any],
        current_plan: str,
        ongoing_actions: Iterable[Dict[str, Any]],
    ) -> List[MemoryQueryRoute]:
        routes = []
        visible_world = world_view.get("visible_world", {})
        self._append(
            routes,
            "situation",
            [
                actor_name,
                world_view.get("location"),
                *(world_view.get("visible_actors", []) or [])[:12],
                *(
                    list(visible_world)[:10]
                    if isinstance(visible_world, dict)
                    else []
                ),
                *(
                    str(item.get("intent", ""))
                    for item in list(visible_proposals)[:6]
                    if isinstance(item, dict)
                ),
                *(
                    str(item.get("intent", ""))
                    for item in list(world_signals)[:6]
                    if isinstance(item, dict)
                ),
                *[str(item) for item in list(recent_observations)[-3:]],
                *(
                    f"{item.get('actor', '')} {item.get('action_kind', '')} "
                    f"{item.get('action_target', '')}"
                    for item in list(ongoing_actions)[:6]
                    if isinstance(item, dict)
                ),
            ],
        )
        self._append(
            routes,
            "goals",
            [
                f"{item.get('goal_id', '')} {item.get('title', '')} "
                f"{item.get('description', '')} {item.get('source_ref', '')}"
                for item in private_goals.get("active", [])[:6]
                if isinstance(item, dict)
            ],
        )
        self._append(
            routes,
            "commitments",
            [
                *(
                    f"义务 {item.get('obligation_id', '')} {item.get('title', '')} "
                    f"{item.get('creditor', '')}"
                    for item in private_obligations.get("active", [])[:6]
                    if isinstance(item, dict)
                ),
                *(
                    f"协议 {item.get('agreement_id', '')} {item.get('title', '')} "
                    f"{item.get('summary', '')} {' '.join(item.get('parties', []) or [])}"
                    for item in private_agreements.get("pending", [])[:6]
                    if isinstance(item, dict)
                ),
                *(
                    f"日程 {item.get('commitment_id', '')} {item.get('title', '')} "
                    f"{item.get('location', '')} {item.get('due_step', '')}"
                    for item in private_schedule.get("active", [])[:6]
                    if isinstance(item, dict)
                ),
                *(
                    f"路线受阻 {item.get('route_source', '')} "
                    f"{item.get('route_target', '')} {item.get('destination', '')} "
                    f"{item.get('obligation_id', '')}"
                    for item in private_navigation.get("active", [])[:6]
                    if isinstance(item, dict)
                ),
            ],
        )
        self._append(
            routes,
            "claims",
            [
                f"{item.get('claim_id', '')} {item.get('statement', '')} "
                f"{' '.join(item.get('evidence_refs', []) or [])} "
                f"{' '.join(item.get('subjects', []) or [])}"
                for item in private_knowledge.get("claims", [])[:8]
                if isinstance(item, dict)
            ],
        )
        self._append(
            routes,
            "social",
            [
                *(
                    f"{item.get('actor', '')} "
                    f"{' '.join(item.get('viewer_toward_actor_states', []) or [])} "
                    f"{' '.join(item.get('toward_viewer_states', []) or [])} "
                    f"{' '.join(item.get('relationship_bits', []) or [])}"
                    for item in relationship_context.get("visible_relations", [])[:8]
                    if isinstance(item, dict)
                ),
                *(
                    f"{item.get('toward', '')} {item.get('kind', '')} "
                    f"{item.get('reason', '')}"
                    for item in private_sentiments.get("active", [])[:8]
                    if isinstance(item, dict)
                ),
            ],
        )
        self._append(
            routes,
            "reflection",
            [
                current_plan,
                private_cognition.get("current_focus", ""),
                *(private_cognition.get("commitments", []) or [])[:6],
                *(
                    str(item.get("statement", ""))
                    for item in private_cognition.get("beliefs", [])[:6]
                    if isinstance(item, dict)
                ),
            ],
        )
        return routes[: self.MAX_ROUTES]

    def retrieve(
        self,
        memory: Any,
        routes: Iterable[MemoryQueryRoute],
        *,
        current_step: int = 0,
    ) -> tuple[List[str], Dict[str, Any]]:
        trace = {"queries": [], "errors": []}
        route_list = list(routes)
        batch_results = None
        detailed = False
        if route_list and hasattr(memory, "retrieve_many_detailed"):
            try:
                batch_results = memory.retrieve_many_detailed(
                    [route.query for route in route_list],
                    n_results=3,
                )
                detailed = True
            except Exception as exc:
                trace["errors"].append(
                    f"detailed_batch:{type(exc).__name__}:{str(exc)[:200]}"
                )
        if batch_results is None and route_list and hasattr(memory, "retrieve_many"):
            try:
                batch_results = memory.retrieve_many(
                    [route.query for route in route_list], n_results=3
                )
            except Exception as exc:
                trace["errors"].append(
                    f"batch:{type(exc).__name__}:{str(exc)[:200]}"
                )
        candidates: Dict[str, Dict[str, Any]] = {}
        for index, route in enumerate(route_list):
            if batch_results is not None and index < len(batch_results):
                results = batch_results[index]
            else:
                try:
                    results = memory.retrieve(route.query, n_results=3)
                except Exception as exc:
                    trace["errors"].append(
                        f"{route.route}:{type(exc).__name__}:{str(exc)[:200]}"
                    )
                    continue
            considered = 0
            for raw in results or []:
                item = raw if detailed and isinstance(raw, dict) else {
                    "content": raw,
                    "metadata": {},
                    "distance": None,
                }
                content = " ".join(
                    str(item.get("content", "") or "").split()
                ).strip()
                if not content:
                    continue
                content = content[: self.MAX_RESULT_CHARS]
                key = content.casefold()
                metadata = item.get("metadata", {})
                metadata = metadata if isinstance(metadata, dict) else {}
                score = self._score_result(
                    route=route.route,
                    metadata=metadata,
                    distance=item.get("distance"),
                    current_step=current_step,
                )
                existing = candidates.get(key)
                if existing is None or score > existing["score"]:
                    candidates[key] = {
                        "content": content,
                        "score": score,
                        "route": route.route,
                        "step": metadata.get("step"),
                        "salience": metadata.get("salience", 1.0),
                    }
                considered += 1
            trace["queries"].append(
                {
                    "route": route.route,
                    "query": route.query,
                    "candidate_count": considered,
                }
            )
        ranked = sorted(
            candidates.values(),
            key=lambda item: (-item["score"], item["content"].casefold()),
        )
        memories = []
        selected = []
        total_chars = 0
        for item in ranked:
            content = item["content"]
            if total_chars + len(content) > self.MAX_TOTAL_RESULT_CHARS:
                continue
            memories.append(content)
            total_chars += len(content)
            selected.append(
                {
                    "route": item["route"],
                    "score": round(item["score"], 6),
                    "step": item["step"],
                    "salience": item["salience"],
                }
            )
            if len(memories) >= self.MAX_RESULTS:
                break
        trace["result_count"] = len(memories)
        trace["result_chars"] = total_chars
        trace["selected"] = selected
        return memories, trace

    def _score_result(
        self,
        *,
        route: str,
        metadata: Dict[str, Any],
        distance: Any,
        current_step: int,
    ) -> float:
        try:
            salience = min(8.0, max(0.0, float(metadata.get("salience", 1.0))))
        except (TypeError, ValueError):
            salience = 1.0
        try:
            memory_step = int(metadata.get("step", current_step))
            age = max(0, int(current_step) - memory_step)
        except (TypeError, ValueError):
            age = 0
        recency = 1.0 / (1.0 + age / 12.0)
        try:
            relevance = 1.0 / (1.0 + max(0.0, float(distance)))
        except (TypeError, ValueError):
            relevance = 0.5
        return (
            self.ROUTE_PRIORITIES.get(route, 0.8)
            + salience * 0.75
            + recency
            + relevance
        )

    def _append(
        self,
        routes: List[MemoryQueryRoute],
        route: str,
        parts: Iterable[Any],
    ) -> None:
        cleaned = []
        seen = set()
        for raw in parts:
            text = " ".join(str(raw or "").split()).strip()
            if not text or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            cleaned.append(text)
        query = " | ".join(cleaned)[: self.MAX_QUERY_CHARS]
        if query:
            routes.append(MemoryQueryRoute(route=route, query=query))
