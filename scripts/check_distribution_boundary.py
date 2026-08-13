#!/usr/bin/env python3
"""Build staged wheels offline and verify the core/content package boundary."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", "build", "dist"}
    return {
        name
        for name in names
        if name in ignored or name.endswith((".egg-info", ".pyc"))
    }


def _build(source: Path, output: Path) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output),
            str(source),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(
            (completed.stdout + "\n" + completed.stderr).splitlines()[-40:]
        )
        raise RuntimeError(f"wheel build failed for {source}:\n{detail}")
    wheels = sorted(output.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel for {source}, found {len(wheels)}")
    return wheels[0]


def _names(wheel: Path) -> list[str]:
    with ZipFile(wheel) as archive:
        return sorted(archive.namelist())


def check_distribution_boundary(
    project_root: Path = PROJECT_ROOT,
    *,
    work_root: Path | None = None,
) -> dict:
    owner = None
    if work_root is None:
        owner = tempfile.TemporaryDirectory(prefix="story-engine-wheel-boundary-")
        work_root = Path(owner.name)
    else:
        work_root.mkdir(parents=True, exist_ok=True)

    try:
        core_stage = work_root / "core-source"
        content_stage = work_root / "content-source"
        core_output = work_root / "core-wheel"
        content_output = work_root / "content-wheel"
        core_output.mkdir(parents=True, exist_ok=True)
        content_output.mkdir(parents=True, exist_ok=True)

        core_stage.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_root / "pyproject.toml", core_stage / "pyproject.toml")
        shutil.copy2(project_root / "README.md", core_stage / "README.md")
        shutil.copytree(
            project_root / "src" / "config",
            core_stage / "src" / "config",
            ignore=_ignore,
        )
        shutil.copytree(
            project_root / "src" / "story_engine",
            core_stage / "src" / "story_engine",
            ignore=_ignore,
        )
        shutil.copytree(
            project_root / "src" / "story_engine_content",
            content_stage,
            ignore=_ignore,
        )

        core_wheel = _build(core_stage, core_output)
        content_wheel = _build(content_stage, content_output)
        core_names = _names(core_wheel)
        content_names = _names(content_wheel)

        errors = []
        if not any(name.startswith("src/story_engine/") for name in core_names):
            errors.append("core wheel contains no story_engine package")
        if any(name.startswith("src/story_engine_content/") for name in core_names):
            errors.append("core wheel contains story_engine_content")
        if not any(
            name.startswith("src/story_engine_content/") for name in content_names
        ):
            errors.append("content wheel contains no story_engine_content package")
        if any(name.startswith("src/story_engine/") for name in content_names):
            errors.append("content wheel copies story_engine implementation")
        if any(name.startswith("src/config/") for name in content_names):
            errors.append("content wheel copies core config package")
        if errors:
            raise RuntimeError("; ".join(errors))

        return {
            "core_wheel": core_wheel.name,
            "content_wheel": content_wheel.name,
            "core_entry_count": len(core_names),
            "content_entry_count": len(content_names),
            "core_content_entry_count": sum(
                name.startswith("src/story_engine_content/") for name in core_names
            ),
            "content_core_entry_count": sum(
                name.startswith("src/story_engine/") for name in content_names
            ),
            "content_config_entry_count": sum(
                name.startswith("src/config/") for name in content_names
            ),
        }
    finally:
        if owner is not None:
            owner.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = check_distribution_boundary(
            args.project_root.resolve(),
            work_root=args.work_root.resolve() if args.work_root else None,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"distribution boundary check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
