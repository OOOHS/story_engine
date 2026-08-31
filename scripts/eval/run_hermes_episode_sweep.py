#!/usr/bin/env python3
"""Run arbitrary ScenarioConfig seeds through the Hermes container boundary."""

import argparse
import hashlib
import inspect
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval.run_episode_sweep import (  # noqa: E402
    load_factory,
    parse_metadata,
    parse_seeds,
    report_exit_code,
)
from src.story_engine.evaluation import (  # noqa: E402
    EpisodeClosurePolicy,
    EpisodeSweepRunner,
)
from src.story_engine.evaluation.hermes import (  # noqa: E402
    HermesEpisodeConfig,
    create_hermes_episode_session,
    normalized_toolsets,
)
from src.story_engine.agents import HermesInvocationBudget  # noqa: E402
from src.story_engine.scenarios.config import ScenarioConfig  # noqa: E402


DEFAULT_ENV_KEYS = (
    "OPENAI_API_KEY",
    "IKUN_API_KEY",
    "HERMES_BASE_URL",
    "HERMES_MODEL",
    "HERMES_PROVIDER",
    "HERMES_TRACE",
)
DEFAULT_ENTRYPOINT = PROJECT_ROOT / "docker" / "hermes-story" / "entrypoint.py"
DEFAULT_CONFIG = PROJECT_ROOT / "docker" / "hermes-story" / "config.yaml"


def invoke_scenario_factory(
    factory: Callable[..., Any], seed: int | str
) -> ScenarioConfig:
    signature = inspect.signature(factory)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
        and parameter.default is parameter.empty
    ]
    scenario = factory(seed) if positional else factory()
    if not isinstance(scenario, ScenarioConfig):
        raise TypeError("scenario factory must return ScenarioConfig")
    return scenario


def parse_csv(value: str) -> tuple[str, ...]:
    return normalized_toolsets(str(value).split(","))


def preflight_image(
    docker_binary: str,
    image: str,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
) -> str:
    completed = command_runner(
        [docker_binary, "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        stderr = " ".join(
            str(getattr(completed, "stderr", "") or "").split()
        )[:500]
        raise RuntimeError(
            f"Hermes image preflight failed for {image}: {stderr or 'not available'}"
        )
    image_id = str(getattr(completed, "stdout", "") or "").strip()
    if not image_id:
        raise RuntimeError(f"Hermes image preflight returned no id for {image}")
    return image_id


def preflight_shell_file(path: str, label: str) -> tuple[str, str]:
    raw = str(path or "").strip()
    if not raw:
        return "", ""
    if any(token in raw for token in (",", "\n", "\r")):
        raise ValueError(f"unsafe Hermes {label} bind path")
    source = Path(raw).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Hermes {label} bind file is missing: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return str(source), digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind every character in a ScenarioConfig to the project-owned "
            "Hermes Docker adapter and write multi-seed Episode artifacts."
        )
    )
    parser.add_argument(
        "--scenario-factory",
        required=True,
        help="Python callable returning ScenarioConfig, optionally accepting seed.",
    )
    parser.add_argument("--image", default="hermes-story:latest")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--network", default="bridge")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--entrypoint-path",
        default=str(DEFAULT_ENTRYPOINT),
        help="Project-owned entrypoint bind-mounted read-only; empty disables mount.",
    )
    parser.add_argument(
        "--config-path",
        default=str(DEFAULT_CONFIG),
        help="Project-owned Hermes config bind-mounted read-only; empty disables mount.",
    )
    parser.add_argument("--allowed-toolsets", default="")
    parser.add_argument("--requested-toolsets", default="")
    parser.add_argument(
        "--environment-keys",
        default=",".join(DEFAULT_ENV_KEYS),
        help="Names inherited by docker -e; values are never read by this launcher.",
    )
    parser.add_argument("--seeds", default="0,1,2")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument(
        "--max-agent-decisions",
        type=int,
        default=40,
        help=(
            "Hard Host limit on actual Hermes container invocations across "
            "all seeds and replay runs."
        ),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify-replay", action="store_true")
    parser.add_argument(
        "--strict-quality",
        action="store_true",
        help=(
            "Return exit code 3 when the authoritative Hermes sweep has any "
            "structural quality flag; default behavior only reports flags."
        ),
    )
    parser.add_argument("--stop-on-closure", action="store_true")
    parser.add_argument("--closure-stable-steps", type=int, default=2)
    parser.add_argument("--closure-minimum-steps", type=int, default=0)
    parser.add_argument(
        "--require-goal-anchor",
        action="store_true",
        help="Require at least one authored Host-verifiable goal before closure.",
    )
    parser.add_argument(
        "--allow-unexercised-agents",
        action="store_true",
        help="Allow closure before every autonomous non-dormant Agent has decided once.",
    )
    parser.add_argument(
        "--allow-material-change-closure",
        action="store_true",
        help="Allow chapter-style closure on a step that still changes material state.",
    )
    parser.add_argument(
        "--allow-actionable-critical-needs-closure",
        action="store_true",
        help="Allow closure while an autonomous Agent has visible critical-need relief.",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--metadata", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scenario_factory = load_factory(args.scenario_factory)
        seeds = parse_seeds(args.seeds)
        metadata = parse_metadata(args.metadata)
        invocation_budget = HermesInvocationBudget(args.max_agent_decisions)
        policy = HermesEpisodeConfig(
            image=args.image,
            docker_binary=args.docker_binary,
            timeout_seconds=max(1.0, float(args.timeout)),
            network_mode=args.network,
            allowed_toolsets=parse_csv(args.allowed_toolsets),
            requested_toolsets=parse_csv(args.requested_toolsets),
            environment_keys=parse_csv(args.environment_keys),
            entrypoint_path=args.entrypoint_path,
            config_path=args.config_path,
            invocation_budget=invocation_budget,
        )
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        print(f"Hermes episode configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        image_id = preflight_image(policy.docker_binary, policy.image)
        entrypoint_path, entrypoint_sha256 = preflight_shell_file(
            policy.entrypoint_path, "entrypoint"
        )
        config_path, config_sha256 = preflight_shell_file(
            policy.config_path, "config"
        )
        policy = replace(
            policy,
            entrypoint_path=entrypoint_path,
            config_path=config_path,
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Hermes episode preflight error: {exc}", file=sys.stderr)
        return 2

    def session_factory(seed):
        scenario = invoke_scenario_factory(scenario_factory, seed)
        return create_hermes_episode_session(
            scenario,
            seed=seed,
            config=policy,
        )

    report = EpisodeSweepRunner().run(
        session_factory,
        seeds=seeds,
        steps=max(0, int(args.steps)),
        verify_replay=bool(args.verify_replay),
        quiet=bool(args.quiet),
        metadata={
            "scenario_factory": args.scenario_factory,
            "runtime": "hermes-container",
            "image": policy.image,
            "image_id": image_id,
            "allowed_toolsets": list(policy.allowed_toolsets),
            "requested_toolsets": list(policy.requested_toolsets),
            "entrypoint_bind_mounted": bool(policy.entrypoint_path),
            "entrypoint_sha256": entrypoint_sha256,
            "config_bind_mounted": bool(policy.config_path),
            "config_sha256": config_sha256,
            **metadata,
        },
        closure_policy=(
            EpisodeClosurePolicy(
                stable_steps=args.closure_stable_steps,
                minimum_steps=args.closure_minimum_steps,
                require_goal_anchor=bool(args.require_goal_anchor),
                require_all_autonomous_agents_exercised=not bool(
                    args.allow_unexercised_agents
                ),
                require_stable_material_state=not bool(
                    args.allow_material_change_closure
                ),
                require_no_actionable_critical_needs=not bool(
                    args.allow_actionable_critical_needs_closure
                ),
            )
            if args.stop_on_closure
            else None
        ),
    )
    budget_snapshot = invocation_budget.snapshot()
    report = replace(
        report,
        metadata={
            **report.metadata,
            "hermes_invocation_budget": budget_snapshot,
        },
    )
    target = report.write_directory(args.output)
    print(json.dumps({
        "summary": str(target),
        "review": str(target.parent / "review.md"),
        "authoritative": report.authoritative,
        "quality_flags": list(report.quality_flags),
        "completed_episode_count": report.metrics.get("completed_episode_count", 0),
        "failure_count": report.metrics.get("failure_count", 0),
        "closure_reached_rate": report.metrics.get("closure_reached_rate"),
        "agent_goal_adoption_count": report.metrics.get(
            "agent_goal_adoption_count", 0
        ),
        "agent_goal_refinement_count": report.metrics.get(
            "agent_goal_refinement_count", 0
        ),
        "active_open_agent_goal_count": report.metrics.get(
            "active_open_agent_goal_count", 0
        ),
        "goal_resolution_count": report.metrics.get("goal_resolution_count", 0),
        "event_motive_reference_rate": report.metrics.get(
            "event_motive_reference_rate"
        ),
        "event_motive_selection_rate": report.metrics.get(
            "event_motive_selection_rate"
        ),
        "urgent_attention_motive_available_decision_count": report.metrics.get(
            "urgent_attention_motive_available_decision_count", 0
        ),
        "strict_quality": bool(args.strict_quality),
        "strict_quality_failed": bool(
            args.strict_quality and report.quality_flags
        ),
        "hermes_invocation_budget": budget_snapshot,
    }, ensure_ascii=False))
    return report_exit_code(report, strict_quality=bool(args.strict_quality))


if __name__ == "__main__":
    raise SystemExit(main())
