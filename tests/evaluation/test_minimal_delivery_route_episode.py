from src.story_engine.evaluation import EpisodeRunner
from src.story_engine_content.evaluation.minimal_delivery_route import (
    COURIER,
    PARCEL,
    PAYMENT,
    WAREHOUSE,
    build_minimal_delivery_route_scenario,
    create_minimal_delivery_route_session,
)


def test_delivery_route_seed_has_no_authored_narrative_or_agreement_template():
    scenario = build_minimal_delivery_route_scenario()

    assert scenario.storylets == []
    assert scenario.plot_entities == []
    assert scenario.plot_rules == []
    assert scenario.agreement_offer_templates == []
    assert len(scenario.initial_world_objects) == 5


def test_agents_form_and_fulfill_paid_cross_location_delivery_from_world_state():
    session = create_minimal_delivery_route_session(17)
    report = EpisodeRunner().run(session, steps=12)

    assert report.authoritative is True
    scene = session.entities["GameMaster"].get_component("SceneState")
    assert scene.get_object_state(PARCEL)["owner"] in (None, "")
    assert scene.get_object_state(PARCEL)["location"] == WAREHOUSE
    assert scene.get_object_state(PAYMENT)["owner"] == COURIER
    agreements = session.runner.agreement_registry.to_book().agreements.values()
    agreement = next(iter(agreements))
    assert agreement.status == "settled"
    assert agreement.performance_status == "fulfilled"
