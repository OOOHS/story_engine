#!/usr/bin/env python3
"""Host launcher for long-horizon Story Engine stability audits."""

import argparse
import importlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.story_engine.evaluation import (  # noqa: E402
    EpisodeClosurePolicy,
    SoakRunner,
)


def load_factory(reference):
    module_name, separator, attribute = str(reference).partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must use module.path:callable_name")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise ValueError(f"factory is not callable: {reference}")
    return factory


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run one Session beyond Episode closure and audit stability."
    )
    parser.add_argument("--factory", required=True)
    parser.add_argument("--seed", default="soak")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--sample-every", type=int, default=10)
    parser.add_argument("--closure-stable-steps", type=int, default=2)
    parser.add_argument("--closure-minimum-steps", type=int, default=0)
    parser.add_argument("--require-plot-closure", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--output", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        factory = load_factory(args.factory)
        seed = int(args.seed) if str(args.seed).lstrip("-").isdigit() else args.seed
        session = factory(seed)
    except (ImportError, AttributeError, ValueError) as exc:
        print(f"soak configuration error: {exc}", file=sys.stderr)
        return 2
    report = SoakRunner().run(
        session,
        steps=max(0, int(args.steps)),
        sample_every=max(1, int(args.sample_every)),
        closure_policy=EpisodeClosurePolicy(
            stable_steps=args.closure_stable_steps,
            minimum_steps=args.closure_minimum_steps,
            require_resolved_plots=bool(args.require_plot_closure),
        ),
        quiet=bool(args.quiet),
    )
    target = report.write_json(args.output)
    print(
        json.dumps(
            {
                "report": str(target),
                "authoritative": report.authoritative,
                "quality_flags": list(report.quality_flags),
                "closure_reached": report.metrics.get("closure_reached"),
                "completed_steps": report.metrics.get("completed_steps"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.authoritative else 2


if __name__ == "__main__":
    raise SystemExit(main())
