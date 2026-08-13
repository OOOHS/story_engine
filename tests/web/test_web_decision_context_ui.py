from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = PROJECT_ROOT / "src" / "story_engine" / "web" / "static"


def test_web_ui_has_one_bounded_player_awareness_surface():
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "awareness",
        "awarenessLocation",
        "awarenessObservations",
        "visibleActors",
        "visibleObjects",
        "activeGoals",
    ):
        assert html.count(f'id="{element_id}"') == 1
    assert 'aria-live="polite"' in html


def test_awareness_renderer_uses_only_manual_decision_projection_fields():
    source = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "function renderDecisionContext" in source
    assert "player.decision_context" in source
    for field in (
        "pending_world_events",
        "pending_event_responses",
        "passive_observations",
        "active_observation_results",
        "visible_actors",
        "visible_objects",
        "active_goals",
    ):
        assert f"context.{field}" in source
    assert ".textContent = value" in source
    assert "context.beliefs" not in source
    assert "context.secrets" not in source
    assert "context.private_agreements" not in source
    assert "context.relationship_context" not in source


def test_awareness_layout_has_stable_desktop_and_mobile_tracks():
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert ".awareness {" in css
    assert "grid-template-columns: minmax(280px, 1.3fr) minmax(320px, 1fr);" in css
    assert ".awareness__facts" in css
    assert "@media (max-width: 620px)" in css
    assert "overflow-wrap: anywhere;" in css
