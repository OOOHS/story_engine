import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.story_engine.agents import HermesInvocationBudget
from src.story_engine.agents.hermes_runtime import HermesCharacterAgent
from src.story_engine.components.host_rule_narrative import (
    HostRuleNarrativeRenderer,
)
from src.story_engine.components.host_rule_simulation import (
    HostRuleSimulationControl,
)
from src.story_engine_content.evaluation.minimal_investigation import (
    build_minimal_investigation_scenario,
)
from src.story_engine.evaluation.hermes import (
    HermesEpisodeConfig,
    create_hermes_episode_session,
)


def _marked(content, agent_id):
    return (
        "vendor logs\n===STORY_AGENT_JSON_BEGIN===\n"
        + json.dumps(
            {
                "protocol_version": 1,
                "agent_id": agent_id,
                "content": content,
            },
            ensure_ascii=False,
        )
        + "\n===STORY_AGENT_JSON_END===\n"
    )


def test_any_scenario_can_be_rebound_to_registered_hermes_characters():
    scenario = build_minimal_investigation_scenario()
    original_runtimes = [character.agent_runtime for character in scenario.characters]
    calls = []

    def runner(command, **kwargs):
        request = json.loads(kwargs["input"])
        calls.append((command, request))
        return SimpleNamespace(
            returncode=0,
            stdout=_marked(
                json.dumps({
                    "action": "保持警觉。",
                }, ensure_ascii=False),
                request["agent_id"],
            ),
            stderr="",
        )

    session = create_hermes_episode_session(
        scenario,
        seed="hermes-bind",
        config=HermesEpisodeConfig(
            image="registry.local/hermes-story:test",
            allowed_toolsets=("file",),
            requested_toolsets=("file", "terminal"),
            environment_keys=("OPENAI_API_KEY",),
        ),
        command_runner=runner,
    )

    assert [character.agent_runtime for character in scenario.characters] == (
        original_runtimes
    )
    assert all(
        character.agent_runtime == "hermes"
        for character in session.scenario.characters
    )
    assert len(session.runner.agent_registry) == len(session.scenario.characters)
    assert all(
        isinstance(registered.runtime, HermesCharacterAgent)
        for registered in session.runner.agent_registry.agents()
    )
    gm = session.entities["WorldHost"]
    assert isinstance(
        gm.get_component("SimulationControl"), HostRuleSimulationControl
    )
    assert isinstance(
        gm.get_component("NarrativeRenderer"), HostRuleNarrativeRenderer
    )

    session.run_step()

    assert len(calls) == len(session.scenario.characters)
    assert all(call[1]["enabled_toolsets"] == ["file"] for call in calls)
    assert all("OPENAI_API_KEY" in call[0] for call in calls)
    assert all("terminal" not in call[1]["enabled_toolsets"] for call in calls)


def test_hermes_episode_launcher_parses_host_policy_without_environment_values():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_hermes_episode_sweep.py"
    )
    spec = importlib.util.spec_from_file_location("hermes_episode_launcher", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args([
        "--scenario-factory",
        "demo:scenario",
        "--output",
        "artifacts/hermes",
        "--allowed-toolsets",
        "file,planning,file",
        "--requested-toolsets",
        "planning,terminal",
        "--environment-keys",
        "OPENAI_API_KEY,HERMES_MODEL,bad-key",
        "--stop-on-closure",
        "--require-goal-anchor",
        "--allow-unexercised-agents",
        "--allow-material-change-closure",
        "--allow-actionable-critical-needs-closure",
        "--max-agent-decisions",
        "7",
        "--strict-quality",
    ])

    assert module.parse_csv(args.allowed_toolsets) == ("file", "planning")
    assert module.parse_csv(args.requested_toolsets) == ("planning", "terminal")
    assert module.parse_csv(args.environment_keys) == (
        "OPENAI_API_KEY",
        "HERMES_MODEL",
        "bad-key",
    )
    assert args.stop_on_closure is True
    assert args.require_goal_anchor is True
    assert args.allow_unexercised_agents is True
    assert args.allow_material_change_closure is True
    assert args.allow_actionable_critical_needs_closure is True
    assert args.max_agent_decisions == 7
    assert args.strict_quality is True
    assert args.entrypoint_path.endswith("docker/hermes-story/entrypoint.py")
    assert args.config_path.endswith("docker/hermes-story/config.yaml")


def test_scenario_factory_may_optionally_accept_the_episode_seed():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_hermes_episode_sweep.py"
    )
    spec = importlib.util.spec_from_file_location("hermes_episode_factory", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    without_seed = module.invoke_scenario_factory(
        build_minimal_investigation_scenario, 3
    )
    with_seed = module.invoke_scenario_factory(
        lambda seed: (
            calls.append(seed) or build_minimal_investigation_scenario()
        ),
        "alpha",
    )

    assert without_seed.name == with_seed.name
    assert calls == ["alpha"]


def test_launcher_image_preflight_fails_once_before_episode_execution():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_hermes_episode_sweep.py"
    )
    spec = importlib.util.spec_from_file_location("hermes_preflight", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def missing(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="No such image: hermes-story:latest",
        )

    try:
        module.preflight_image("docker", "hermes-story:latest", command_runner=missing)
    except RuntimeError as exc:
        assert "No such image" in str(exc)
    else:
        raise AssertionError("missing image must fail preflight")

    assert calls == [[
        "docker", "image", "inspect", "hermes-story:latest",
        "--format", "{{.Id}}",
    ]]


def test_launcher_preflights_project_shell_file_and_records_digest(tmp_path):
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_hermes_episode_sweep.py"
    )
    spec = importlib.util.spec_from_file_location("hermes_shell_preflight", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shell = tmp_path / "entrypoint.py"
    shell.write_bytes(b"project-owned-shell\n")

    resolved, digest = module.preflight_shell_file(str(shell), "entrypoint")

    assert resolved == str(shell.resolve())
    assert digest == hashlib.sha256(b"project-owned-shell\n").hexdigest()
    with pytest.raises(ValueError, match="missing"):
        module.preflight_shell_file(str(tmp_path / "missing.py"), "entrypoint")


def test_invocation_budget_snapshot_is_json_artifact_safe(tmp_path):
    from src.story_engine.evaluation.sweep import EpisodeSweepReport

    budget = HermesInvocationBudget(0)
    report = EpisodeSweepReport(
        requested_seeds=(),
        steps_per_episode=0,
        episodes=(),
        metrics={
            "agent_goal_adoption_count": 3,
            "agent_goal_refinement_count": 2,
            "active_open_agent_goal_count": 1,
            "goal_resolution_count": 2,
        },
        metadata={"hermes_invocation_budget": budget.snapshot()},
    )

    target = report.write_directory(tmp_path / "sweep")
    summary = json.loads(target.read_text(encoding="utf-8"))
    review = (target.parent / "review.md").read_text(encoding="utf-8")

    assert summary["metadata"]["hermes_invocation_budget"] == {
        "configured": 0,
        "consumed": 0,
        "remaining": 0,
        "exhausted": True,
    }
    assert "## Hermes 调用预算" in review
    assert "| Agent 目标采用数 | 3 |" in review
    assert "| 开放目标细化数 | 2 |" in review
    assert "| 结尾开放目标数 | 1 |" in review
    assert "| 目标结算数 | 2 |" in review
    assert "| 0 | 0 | 0 | yes |" in review
    assert "OPENAI_API_KEY" not in review
