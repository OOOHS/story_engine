"""Story content package.

Importing the schema must not silently load a bundled story.  Applications
choose concrete content explicitly, while the engine depends only on the
story-agnostic configuration types.
"""

from .config import NarrationConfig, ScenarioConfig

__all__ = ["NarrationConfig", "ScenarioConfig"]
