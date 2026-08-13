#!/usr/bin/env python3
"""Host launcher for multi-seed Story Engine episode audits."""

import argparse
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.story_engine.evaluation import (  # noqa: E402
    EpisodeClosurePolicy,
    EpisodeSweepRunner,
)


def load_factory(reference: str) -> Callable[[int | str], Any]:
    module_name, separator, attribute = str(reference).partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must use module.path:callable_name")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError(f"factory is not callable: {reference}")
    return factory


def parse_seeds(value: str) -> list[int | str]:
    seeds = []
    for raw in str(value).split(","):
        token = raw.strip()
        if not token:
            continue
        seeds.append(int(token) if re.fullmatch(r"-?\d+", token) else token)
    if not seeds:
        raise ValueError("at least one seed is required")
    return seeds


def parse_metadata(items: list[str]) -> dict[str, str]:
    metadata = {}
    for item in items:
        key, separator, value = str(item).partition("=")
        key = key.strip()
        if not separator or not key:
            raise ValueError("metadata must use key=value")
        metadata[key] = value.strip()
    return metadata


def report_exit_code(report: Any, *, strict_quality: bool = False) -> int:
    if not bool(getattr(report, "authoritative", False)):
        return 2
    if strict_quality and tuple(getattr(report, "quality_flags", ()) or ()):
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one story/session factory across deterministic seeds and write "
            "launcher-friendly Episode artifacts."
        )
    )
    parser.add_argument(
        "--factory",
        required=True,
        help="Python callable as module.path:factory; called once with each seed.",
    )
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify-replay", action="store_true")
    parser.add_argument(
        "--strict-quality",
        action="store_true",
        help=(
            "Return exit code 3 when the authoritative sweep has any structural "
            "quality flag; default behavior only reports flags."
        ),
    )
    parser.add_argument(
        "--stop-on-closure",
        action="store_true",
        help="Stop each Episode after host-audited closure remains stable.",
    )
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
    parser.add_argument(
        "--require-plot-closure",
        action="store_true",
        help="Also require every Plot clock to reach its terminal value.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-system episode console output; artifacts remain complete.",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        factory = load_factory(args.factory)
        seeds = parse_seeds(args.seeds)
        metadata = parse_metadata(args.metadata)
    except (ImportError, AttributeError, ValueError) as exc:
        print(f"episode sweep configuration error: {exc}", file=sys.stderr)
        return 2
    report = EpisodeSweepRunner().run(
        factory,
        seeds=seeds,
        steps=max(0, int(args.steps)),
        verify_replay=bool(args.verify_replay),
        quiet=bool(args.quiet),
        metadata={"factory": args.factory, **metadata},
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
                require_resolved_plots=bool(args.require_plot_closure),
            )
            if args.stop_on_closure
            else None
        ),
    )
    target = report.write_directory(args.output)
    print(
        json.dumps(
            {
                "summary": str(target),
                "review": str(target.parent / "review.md"),
                "authoritative": report.authoritative,
                "quality_flags": list(report.quality_flags),
                "completed_episode_count": report.metrics.get(
                    "completed_episode_count", 0
                ),
                "failure_count": report.metrics.get("failure_count", 0),
                "event_motive_reference_rate": report.metrics.get(
                    "event_motive_reference_rate"
                ),
                "event_motive_selection_rate": report.metrics.get(
                    "event_motive_selection_rate"
                ),
                "closure_reached_rate": report.metrics.get(
                    "closure_reached_rate"
                ),
                "strict_quality": bool(args.strict_quality),
                "strict_quality_failed": bool(
                    args.strict_quality and report.quality_flags
                ),
            },
            ensure_ascii=False,
        )
    )
    return report_exit_code(report, strict_quality=bool(args.strict_quality))


if __name__ == "__main__":
    raise SystemExit(main())
