import json

from src.story_engine.evaluation import EpisodeClosurePolicy, SoakRunner
from src.story_engine_content.evaluation.minimal_goal_growth import (
    ACTOR,
    create_minimal_goal_growth_session,
)


def test_soak_runner_continues_world_after_closure_and_audits_stability(tmp_path):
    session = create_minimal_goal_growth_session("soak")
    report = SoakRunner().run(
        session,
        steps=12,
        sample_every=3,
        closure_policy=EpisodeClosurePolicy(stable_steps=2),
        quiet=True,
    )

    assert report.authoritative is True
    assert report.quality_flags == ()
    assert report.metrics["completed_steps"] == 12
    assert report.metrics["closure_reached"] is True
    assert report.metrics["steps_to_first_closure"] < 12
    assert report.metrics["goal_adoption_count"] == 1
    assert report.metrics["goal_refinement_count"] == 0
    assert report.metrics["goal_resolution_count"] >= 2
    assert report.metrics["max_active_agent_goal_count"] <= 1
    assert report.metrics["final_pending_action_count"] == 0
    assert report.metrics["final_memory_counts"][ACTOR]["total"] >= 1
    assert len(report.samples) == 5

    target = report.write_json(tmp_path / "soak.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["metrics"]["closure_reached"] is True
    assert payload["samples"][-1]["index"] == 11

    for entity in session.entities.values():
        memory = entity.get_component("Memory")
        if memory is not None and hasattr(memory, "list_memories"):
            records = memory.list_memories()
            memory.delete_memories([item["id"] for item in records])
