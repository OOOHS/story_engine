import pytest

from src.story_engine.components.plot_state import PlotState
from src.story_engine.scenarios.config import PlotEntityConfig, PlotStageConfig


def _authored_plot_state() -> PlotState:
    return PlotState.from_configs(
        [
            PlotEntityConfig(
                plot_id="succession_crisis",
                title="Succession Crisis",
                description="谁能继承王位尚无定论。",
                max_clock=4,
                stages=[PlotStageConfig(label="calm", summary="表面平静")],
            )
        ]
    )


def test_from_configs_stays_backward_compatible_with_thread_defaults():
    plot_state = _authored_plot_state()
    snapshot = plot_state.get_snapshot()["succession_crisis"]

    assert snapshot["opened_reason"] == "authored"
    assert snapshot["sunset_after_idle_steps"] is None
    assert snapshot["candidate_beats"] == []
    assert snapshot["status"] == "active"


def test_create_thread_requires_provenance_and_rejects_duplicates():
    plot_state = _authored_plot_state()

    thread = plot_state.create_thread(
        "southern_drought",
        "南方旱情",
        "粮仓存量连续走低",
        opened_reason="粮食存量连续40步低于阈值",
        current_step=100,
        participants=["粮仓总管"],
    )

    assert thread["last_advanced_step"] == 100
    assert thread["status"] == "active"
    assert thread["opened_reason"] == "粮食存量连续40步低于阈值"

    with pytest.raises(ValueError):
        plot_state.create_thread(
            "southern_drought", "dup", "", opened_reason="x", current_step=101
        )

    with pytest.raises(ValueError):
        plot_state.create_thread("no_reason", "x", "", opened_reason="", current_step=1)


def test_create_thread_is_never_retroactive():
    plot_state = _authored_plot_state()

    thread = plot_state.create_thread(
        "rumor_of_famine",
        "饥荒的传闻",
        "",
        opened_reason="连续三名角色抱怨粮价",
        current_step=42,
    )

    # A thread's clock and last_advanced_step both start at the step it was
    # opened -- it cannot claim prior, unwitnessed progress.
    assert thread["clock"] == 0
    assert thread["current_stage"] == 0
    assert thread["last_advanced_step"] == 42


def test_candidate_beats_register_and_consume():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought", "南方旱情", "", opened_reason="x", current_step=1
    )

    registered = plot_state.register_candidate_beat(
        "southern_drought",
        {"beat_id": "visitor_letter", "description": "一封求援信"},
    )
    duplicate = plot_state.register_candidate_beat(
        "southern_drought",
        {"beat_id": "visitor_letter", "description": "dup"},
    )

    assert registered is True
    assert duplicate is False
    assert len(plot_state.get_snapshot()["southern_drought"]["candidate_beats"]) == 1

    consumed = plot_state.consume_beat("southern_drought", "visitor_letter")
    assert consumed is True
    assert plot_state.get_snapshot()["southern_drought"]["candidate_beats"] == []


def test_register_candidate_beat_is_a_noop_for_unknown_or_sunset_thread():
    plot_state = _authored_plot_state()

    assert plot_state.register_candidate_beat(
        "nonexistent", {"beat_id": "x", "description": "x"}
    ) is False


def test_open_hooks_add_and_resolve():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought", "南方旱情", "", opened_reason="x", current_step=1
    )

    added = plot_state.add_open_hook("southern_drought", "谁截留了赈灾粮")
    duplicate = plot_state.add_open_hook("southern_drought", "谁截留了赈灾粮")
    resolved = plot_state.resolve_open_hook("southern_drought", "谁截留了赈灾粮")

    assert added is True
    assert duplicate is False
    assert resolved is True
    assert plot_state.get_snapshot()["southern_drought"]["open_hooks"] == []


def test_apply_updates_records_last_advanced_step_only_on_real_change():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought", "南方旱情", "", opened_reason="x", current_step=1
    )

    plot_state.apply_updates(
        [{"plot_id": "southern_drought", "advance": 1}], current_step=105
    )
    assert plot_state.get_snapshot()["southern_drought"]["last_advanced_step"] == 105

    # A no-op update (advance=0, stage_shift=0) must not touch provenance.
    plot_state.apply_updates(
        [{"plot_id": "southern_drought", "advance": 0, "stage_shift": 0}],
        current_step=999,
    )
    assert plot_state.get_snapshot()["southern_drought"]["last_advanced_step"] == 105


def test_decay_idle_threads_exempts_authored_plots_and_sunsets_runtime_ones():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought",
        "南方旱情",
        "",
        opened_reason="x",
        current_step=105,
        sunset_after_idle_steps=40,
    )

    at_budget = plot_state.decay_idle_threads(current_step=105 + 40)
    assert at_budget == []

    past_budget = plot_state.decay_idle_threads(current_step=105 + 41)
    assert past_budget == ["southern_drought"]
    assert plot_state.get_snapshot()["southern_drought"]["status"] == "sunset"
    # Authored plot has sunset_after_idle_steps=None and is never touched.
    assert plot_state.get_snapshot()["succession_crisis"]["status"] == "active"


def test_sunset_thread_rejects_new_candidate_beats():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought",
        "南方旱情",
        "",
        opened_reason="x",
        current_step=1,
        sunset_after_idle_steps=1,
    )
    plot_state.decay_idle_threads(current_step=10)

    assert plot_state.register_candidate_beat(
        "southern_drought", {"beat_id": "too_late", "description": "x"}
    ) is False


def test_get_pressure_packets_skips_sunset_threads():
    plot_state = _authored_plot_state()
    plot_state.create_thread(
        "southern_drought",
        "南方旱情",
        "",
        opened_reason="x",
        current_step=1,
        sunset_after_idle_steps=1,
    )
    plot_state.decay_idle_threads(current_step=10)

    packet_ids = {packet["plot_id"] for packet in plot_state.get_pressure_packets()}
    assert "southern_drought" not in packet_ids
    assert "succession_crisis" in packet_ids
