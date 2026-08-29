from copy import deepcopy
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from src.story_engine.core.component import Component


class NavigationProblem(BaseModel):
    problem_id: str
    status: str = "active"
    route_source: str
    route_target: str
    destination: str
    discovered_at: str
    discovered_step: int
    alternative_path: List[str] = Field(default_factory=list)
    failure_rule: str = ""
    reason: str = ""


class NavigationState(Component):
    """Private actionable route problems without choosing the actor's response."""

    problems: Dict[str, NavigationProblem] = Field(default_factory=dict)

    def record(self, problem: NavigationProblem) -> None:
        self.problems[problem.problem_id] = problem

    def resolve_departed(self, current_location: str) -> None:
        for problem in self.problems.values():
            if problem.status == "active" and problem.discovered_at != current_location:
                problem.status = "resolved"

    def private_snapshot(self) -> Dict[str, Any]:
        active = [
            item.model_dump()
            for item in self.problems.values()
            if item.status == "active"
        ]
        active.sort(
            key=lambda item: (
                item.get("steps_remaining") is None,
                item.get("steps_remaining", 10**9),
                item["problem_id"],
            )
        )
        return {"active": active, "active_count": len(active)}

    def next_wakeup(self) -> str:
        active = self.private_snapshot()["active"]
        return str(active[0]["problem_id"]) if active else ""

    def restore_from(self, snapshot: "NavigationState") -> None:
        self.problems = deepcopy(snapshot.problems)
