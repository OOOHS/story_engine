import pytest

from src.story_engine.simulation import (
    CheckModifier,
    HostCheckResolver,
    ProbabilityCheck,
)
from src.story_engine.simulation.randomness import DeterministicRandomStreams
from src.story_engine.scenarios.config import ScenarioConfig
from src.story_engine.session import create_session


def test_world_check_uses_fixed_difficulty_and_replayable_host_roll():
    resolver = HostCheckResolver(DeterministicRandomStreams("story-seed"))
    check = ProbabilityCheck(
        check_id="force_door",
        actor="甲",
        difficulty="hard",
        modifiers=(
            CheckModifier("strong", 0.15, "角色拥有权威 strength capability"),
        ),
    )

    first = resolver.resolve(check, step=3, world_version=8)
    second = resolver.resolve(check, step=3, world_version=8)

    assert first.probability == pytest.approx(0.5)
    assert first.roll == second.roll
    assert first.success == second.success
    assert first.trace["stream"] == "world"
    assert first.trace["roll_key"] == "force_door|甲|3|8"


def test_observation_check_has_an_independent_random_stream():
    resolver = HostCheckResolver(DeterministicRandomStreams(91))
    world = resolver.resolve(
        ProbabilityCheck("notice_wire", "甲", stream="world"),
        step=1,
        world_version=2,
    )
    observation = resolver.resolve(
        ProbabilityCheck("notice_wire", "甲", stream="observation"),
        step=1,
        world_version=2,
    )

    assert world.roll != observation.roll


def test_agent_supplied_unbounded_modifier_cannot_enter_probability_check():
    resolver = HostCheckResolver(DeterministicRandomStreams(1))

    with pytest.raises(ValueError, match="delta"):
        resolver.resolve(
            ProbabilityCheck(
                "invent_success",
                "甲",
                modifiers=(CheckModifier("agent_claim", 9.0, "我一定成功"),),
            ),
            step=0,
            world_version=0,
        )


def test_session_exposes_the_seed_needed_for_replay():
    scenario = ScenarioConfig(
        name="seed",
        default_agent_runtime="llm",
        description="seed",
        environment="room",
        initial_state="empty",
    )

    session = create_session(scenario, random_seed="replay-42")

    assert session.random_seed == "replay-42"
