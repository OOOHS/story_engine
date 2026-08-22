from src.story_engine.evaluation import EpisodeRunner, EpisodeSweepRunner
from src.story_engine_content.evaluation.minimal_service import (
    CLIENT,
    COMPENSATION,
    PARCEL,
    PAYMENT,
    build_minimal_service_scenario,
    create_minimal_service_session,
)


def test_minimal_service_seed_has_no_storylet_or_plot_script():
    scenario = build_minimal_service_scenario()

    assert scenario.storylets == []
    assert scenario.plot_entities == []
    assert scenario.plot_rules == []
    assert len(scenario.characters) == 2
    assert all(character.agent_runtime == "service-policy" for character in scenario.characters)


def test_service_episode_can_breach_deliver_late_refund_and_settle_compensation():
    session = create_minimal_service_session(7)
    report = EpisodeRunner().run(session, steps=14)

    assert report.authoritative is True
    assert report.metrics["goal_resolution_count"] == 2
    assert report.metrics["goal_achievement_count"] == 1
    assert report.metrics["goal_failure_count"] == 1
    assert report.metrics["commitment_resolution_count"] == 2
    assert report.metrics["agreement_creation_count"] == 2
    assert report.metrics["obligation_creation_count"] == 1
    assert report.metrics["motive_handoff_count"] >= 3
    assert report.metrics["motivated_action_count"] >= 3
    assert report.metrics["rejected_motive_ref_count"] == 0
    assert "unattributed_agreement" not in report.quality_flags
    assert "unattributed_obligation" not in report.quality_flags
    changes = [
        change
        for step in report.steps
        for change in step.irreversible_changes
    ]
    assert "agreement_resolved:escrow_delivery:settled" in changes
    assert "agreement_performance_resolved:escrow_delivery:breached" in changes
    assert "obligation_resolved:承运人:deliver_parcel:breached" in changes
    assert "agreement_resolved:breach_compensation:settled" in changes
    handoffs = [item for step in report.steps for item in step.causal_handoffs]
    assert any(
        item.startswith("agreement:escrow_delivery<-resolved_action:step:")
        and item.endswith(":actor:委托人")
        for item in handoffs
    )
    assert any(
        item.startswith(
            "agreement_resolution:escrow_delivery:settled"
            "<-resolved_action:step:"
        )
        and item.endswith(":actor:承运人")
        for item in handoffs
    )
    assert (
        "agreement_resolution:escrow_delivery:settled"
        "<-agreement:escrow_delivery"
        in handoffs
    )
    assert (
        "obligation:承运人:deliver_parcel"
        "<-agreement_resolution:escrow_delivery:settled"
        in handoffs
    )
    assert (
        "obligation:承运人:deliver_parcel<-agreement:escrow_delivery"
        in handoffs
    )
    # The courier's action is attributed to the reason he gave for it, at
    # whatever step he finally moved -- not to a step number baked into this
    # test, and not to a motive the Host guessed on his behalf.
    delivery_actions = [
        item.split("<-", 1)[0]
        for item in handoffs
        if item.endswith("<-goal:承运人:earn_payment")
    ]
    assert delivery_actions
    assert any(
        item.startswith("world_event:object:")
        and item.split("<-", 1)[1] in delivery_actions
        for item in handoffs
    )
    assert (
        "world_event:obligation:承运人:deliver_parcel:breached"
        "<-obligation:承运人:deliver_parcel"
        in handoffs
    )
    assert (
        "agreement_performance_resolution:escrow_delivery:breached"
        "<-obligation:承运人:deliver_parcel"
        in handoffs
    )
    assert (
        "agreement_performance_resolution:escrow_delivery:breached"
        "<-agreement_resolution:escrow_delivery:settled"
        in handoffs
    )
    assert (
        "world_event:agreement:escrow_delivery:performance:breached"
        "<-agreement_performance_resolution:escrow_delivery:breached"
        in handoffs
    )
    assert any(
        item.startswith(
            "agreement_escrow_resolution:escrow_delivery:"
        )
        and item.endswith(
            "<-agreement_performance_resolution:escrow_delivery:breached"
        )
        for item in handoffs
    )
    assert any(
        item.startswith(
            "world_event:agreement:escrow_delivery:escrow:refunded"
            "<-agreement_escrow_resolution:escrow_delivery:"
        )
        and item.endswith(":refunded")
        for item in handoffs
    )
    assert any(
        item.startswith("world_event:exchange:")
        and "contract:breach_compensation" in item
        and item.endswith(
            "<-agreement_resolution:breach_compensation:settled"
        )
        for item in handoffs
    )
    # One link shorter than when the Host scored motives: a character now
    # cites the single reason she acted for, not every term that happened to
    # weigh positively.
    assert report.metrics["max_causal_chain_depth"] >= 7
    assert report.metrics["cross_step_causal_handoff_count"] >= 1
    assert report.metrics["max_causal_span_steps"] >= 2
    assert report.metrics["causal_arc_present"] is True

    scene = session.entities["GameMaster"].get_component("SceneState")
    assert scene.get_object_state(PARCEL)["owner"] == CLIENT
    assert scene.get_object_state(PAYMENT)["owner"] == CLIENT
    assert scene.get_object_state(COMPENSATION)["owner"] == CLIENT


def test_service_multi_seed_sweep_keeps_world_state_connected_to_action():
    sweep = EpisodeSweepRunner().run(
        create_minimal_service_session,
        seeds=[0, 2, 4],
        steps=8,
        quiet=True,
        metadata={"scenario": "minimal-service-motive-regression"},
    )

    assert sweep.authoritative is True
    assert sweep.quality_flags == ()
    assert sweep.metrics["motivated_episode_count"] == 3
    assert sweep.metrics["motivated_episode_rate"] == 1.0
    assert sweep.metrics["motivated_action_count"] >= 6
    assert sweep.metrics["motive_handoff_count"] >= 6
    assert sweep.metrics["rejected_motive_ref_count"] == 0
    assert sweep.metrics["unique_action_trace_count"] >= 2
