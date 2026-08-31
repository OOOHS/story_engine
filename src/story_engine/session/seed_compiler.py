"""Compile a small author seed into a validated :class:`ScenarioConfig`.

The engine intentionally keeps the authoritative world schema explicit.  That
does not mean an author has to hand write every field before a game can start,
though.  This module is the deliberately conservative bridge between an
author-facing seed and the schema consumed by the ECS bootstrap:

* a ``ScenarioConfig`` is accepted unchanged (deep copied);
* JSON/YAML mappings can provide either a full scenario or a small seed
  mapping; and
* plain text may use a tiny line-oriented vocabulary (``地点:``, ``角色:``,
  ``物品:``, ``规则:`` and ``初始状态:``).  Unrecognised prose remains the
  opening premise instead of being hallucinated into hidden world state.

This is intentionally not a semantic LLM parser.  It is a deterministic
bootstrap compiler that makes the first end-to-end loop reliable.  A future
semantic compiler can target the same mapping API and still be checked by the
same ScenarioConfig validation and bootstrap invariants.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.story_engine.scenarios.config import CharacterConfig, ScenarioConfig


class ScenarioSeedError(ValueError):
    """Raised when an author seed cannot be compiled into a runnable world."""


class SeedDraft(BaseModel):
    """Strict, author-facing intermediate schema.

    This model is deliberately much smaller than ``ScenarioConfig``.  Unknown
    keys fail loudly instead of being silently discarded by Pydantic; the
    deterministic compiler then decides which facts are safe to materialize.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    title: str | None = None
    description: str | None = None
    environment: str | None = None
    premise: str | None = None
    seed: str | None = None
    text: str | None = None
    prompt: str | None = None
    initial_state: str | None = None
    locations: Any = Field(default_factory=list)
    location: Any = Field(default_factory=list)
    characters: Any = Field(default_factory=list)
    character: Any = Field(default_factory=list)
    player: Any = None
    objects: Any = Field(default_factory=list)
    object: Any = Field(default_factory=list)
    rules: Any = Field(default_factory=list)
    goals: Any = Field(default_factory=list)
    initial_world_objects: Any = Field(default_factory=dict)
    initial_scene_flags: Any = Field(default_factory=dict)
    public_scene_fields: Any = Field(default_factory=list)
    private_scene_fields: Any = Field(default_factory=list)
    default_agent_runtime: str | None = None
    physics_profile: str | None = None
    simulation_mode: str | None = None
    narration_mode: str | None = None
    narrative_director_enabled: bool | None = None
    metadata: Any = Field(default_factory=dict)


@dataclass(frozen=True)
class CompiledSeed:
    """Result object for callers that need compiler diagnostics."""

    scenario: ScenarioConfig
    unresolved: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.model_dump(mode="json"),
            "unresolved": [dict(item) for item in self.unresolved],
            "warnings": list(self.warnings),
        }


_KEY_ALIASES = {
    "title": "name",
    "name": "name",
    "标题": "name",
    "名称": "name",
    "description": "description",
    "描述": "description",
    "environment": "environment",
    "世界": "environment",
    "环境": "environment",
    "设定": "environment",
    "premise": "premise",
    "seed": "premise",
    "故事": "premise",
    "初始状态": "initial_state",
    "开场": "initial_state",
    "initial_state": "initial_state",
    "地点": "locations",
    "场景": "locations",
    "locations": "locations",
    "location": "locations",
    "角色": "characters",
    "人物": "characters",
    "characters": "characters",
    "character": "characters",
    "玩家": "player",
    "player": "player",
    "物品": "objects",
    "道具": "objects",
    "objects": "objects",
    "object": "objects",
    "规则": "rules",
    "法则": "rules",
    "rules": "rules",
    "目标": "goals",
    "goals": "goals",
}

_SCENARIO_FIELDS = frozenset(ScenarioConfig.model_fields)
_CHARACTER_FIELDS = frozenset(CharacterConfig.model_fields)
# ``location``/``activity`` are consumed by the small seed compiler and are
# intentionally not persisted on CharacterConfig itself.
_CHARACTER_SEED_FIELDS = _CHARACTER_FIELDS | frozenset(
    {"location", "activity", "_location", "_activity"}
)
_DIRECTIVE_RE = re.compile(r"^\s*([^:：]{1,32})\s*[:：]\s*(.*?)\s*$")
_PLAYER_MARKERS = ("玩家", "主角", "player", "protagonist")


def compile_scenario_seed(
    seed: ScenarioConfig | Mapping[str, Any] | str,
    *,
    runtime: str = "hermes",
    simulation_mode: str | None = None,
    narration_mode: str | None = None,
) -> ScenarioConfig:
    """Compile ``seed`` into a fresh, validated :class:`ScenarioConfig`.

    ``runtime`` is the only runtime name inserted for synthesized characters;
    it is still resolved by ``Runner.register_agent`` and therefore does not
    create a silent fallback.  ``simulation_mode`` and ``narration_mode`` are
    optional scenario presentation policies (``llm`` or ``rules``) and are
    useful for an offline smoke profile.
    """

    runtime = _normalise_runtime(runtime)
    if isinstance(seed, ScenarioConfig):
        scenario = seed.model_copy(deep=True)
    elif isinstance(seed, Mapping):
        scenario = _compile_mapping(
            seed,
            runtime=runtime,
            simulation_mode=simulation_mode,
            narration_mode=narration_mode,
        )
    elif isinstance(seed, str):
        text = seed.strip()
        if not text:
            raise ScenarioSeedError("scenario seed must be a non-empty string")
        document = _parse_document_mapping(text)
        if document is not None:
            scenario = _compile_mapping(
                document,
                runtime=runtime,
                simulation_mode=simulation_mode,
                narration_mode=narration_mode,
                source_text=text,
            )
        else:
            scenario = _compile_text(
                text,
                runtime=runtime,
                simulation_mode=simulation_mode,
                narration_mode=narration_mode,
            )
    else:
        raise ScenarioSeedError(
            "scenario seed must be ScenarioConfig, a mapping, or a non-empty string"
        )

    # Explicit mode arguments are an application choice and should win over
    # values embedded in a seed mapping.  Keep this check here as well for the
    # ScenarioConfig branch, where pydantic would otherwise accept arbitrary
    # extra metadata but not these optional policies on older snapshots.
    updates: dict[str, Any] = {}
    if simulation_mode is not None:
        updates["simulation_mode"] = _normalise_mode(simulation_mode, "simulation_mode")
    if narration_mode is not None:
        updates["narration_mode"] = _normalise_mode(narration_mode, "narration_mode")
    if updates:
        scenario = _copy_with_optional_fields(scenario, updates)
    return scenario


# Short aliases make the application entry point readable and give callers a
# stable name while the compiler grows.
compile_seed = compile_scenario_seed


def compile_seed_report(
    seed: ScenarioConfig | Mapping[str, Any] | str,
    *,
    runtime: str = "hermes",
    simulation_mode: str | None = None,
    narration_mode: str | None = None,
) -> CompiledSeed:
    """Compile a seed and retain non-authoritative onboarding diagnostics."""

    scenario = compile_scenario_seed(
        seed,
        runtime=runtime,
        simulation_mode=simulation_mode,
        narration_mode=narration_mode,
    )
    warnings: list[str] = []
    if scenario.metadata.get("seed_format") == "text":
        warnings.append(
            "未被明确声明的文本只作为开场前提保留，尚未物化为角色、地点或物品。"
        )
    return CompiledSeed(scenario=scenario, unresolved=(), warnings=tuple(warnings))


def compile_scenario_seed_file(
    path: str | Path,
    *,
    runtime: str = "hermes",
    simulation_mode: str | None = None,
    narration_mode: str | None = None,
) -> ScenarioConfig:
    """Read UTF-8 seed text from ``path`` and compile it."""

    target = Path(path).expanduser()
    if not target.is_file():
        raise ScenarioSeedError(f"scenario seed file is not a file: {target}")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioSeedError(f"cannot read scenario seed file: {target}") from exc
    return compile_scenario_seed(
        text,
        runtime=runtime,
        simulation_mode=simulation_mode,
        narration_mode=narration_mode,
    )


load_or_compile_scenario = compile_scenario_seed


def _compile_mapping(
    raw: Mapping[str, Any],
    *,
    runtime: str,
    simulation_mode: str | None,
    narration_mode: str | None,
    source_text: str = "",
) -> ScenarioConfig:
    data: dict[str, Any] = dict(raw)
    nested = data.get("scenario")
    if isinstance(nested, Mapping):
        merged = dict(nested)
        merged.update({key: value for key, value in data.items() if key != "scenario"})
        data = merged
    data = _canonicalize_seed_mapping(data)

    # A complete schema should go through pydantic unchanged.  This preserves
    # authored content such as claims, relations and affordances.
    if _looks_like_complete_scenario(data):
        _validate_complete_mapping(data)
        try:
            scenario = ScenarioConfig.model_validate(data)
        except ValidationError as exc:
            raise ScenarioSeedError(_format_validation_error(exc)) from exc
        # Pydantic validates each field independently, but cannot express
        # cross-references such as an actor standing in a declared location
        # or a tangible object having exactly one placement.  Run the same
        # deterministic structural checks for a full mapping as for the small
        # seed vocabulary; otherwise a caller could bypass the compiler's
        # authority boundary merely by supplying all schema keys.
        _validate_seed_structure(scenario.model_dump(mode="python"))
        return scenario

    try:
        SeedDraft.model_validate(data)
    except ValidationError as exc:
        raise ScenarioSeedError(_format_validation_error(exc)) from exc

    premise = _first_text(data, "premise", "seed", "故事", "text", "prompt")
    parsed = _parse_text_directives(premise) if premise else {}
    # Explicit mapping fields override anything inferred from the prose field.
    parsed.update(data)
    return _build_scenario_from_parts(
        parsed,
        runtime=runtime,
        simulation_mode=simulation_mode,
        narration_mode=narration_mode,
        source_text=source_text or premise,
        source_format="mapping",
    )


def _compile_text(
    text: str,
    *,
    runtime: str,
    simulation_mode: str | None,
    narration_mode: str | None,
) -> ScenarioConfig:
    parts = _parse_text_directives(text)
    return _build_scenario_from_parts(
        parts,
        runtime=runtime,
        simulation_mode=simulation_mode,
        narration_mode=narration_mode,
        source_text=text,
        source_format="text",
    )


def _build_scenario_from_parts(
    raw: Mapping[str, Any],
    *,
    runtime: str,
    simulation_mode: str | None,
    narration_mode: str | None,
    source_text: str,
    source_format: str = "mapping",
) -> ScenarioConfig:
    data = dict(raw)
    premise = _first_text(data, "premise", "seed", "故事", "text", "prompt")
    if not premise:
        premise = _first_text(data, "initial_state", "description", "environment")
    source_text = str(source_text or premise or "").strip()

    name = _first_text(data, "name", "title", "标题", "名称")
    if not name:
        name = _derive_name(source_text)
    description = _first_text(data, "description", "描述") or source_text
    environment = _first_text(data, "environment", "世界", "环境", "设定") or source_text
    initial_state = _first_text(data, "initial_state", "初始状态", "开场") or source_text
    if not initial_state:
        raise ScenarioSeedError("scenario seed does not contain an opening premise")

    locations, edges = _normalise_locations(data.get("locations", data.get("地点")))
    world_objects = _normalise_world_objects(
        data.get("initial_world_objects"),
        locations=locations,
        edges=edges,
    )
    # Small seed mappings may use ``objects``/``物品`` instead of the full
    # world_objects field.
    object_specs = data.get("objects", data.get("物品", []))
    _merge_object_specs(world_objects, object_specs, locations)

    characters = _normalise_characters(
        data.get("characters", data.get("角色", data.get("人物", []))),
        runtime=runtime,
    )
    player_hint = data.get("player", data.get("玩家"))
    _apply_player_hint(characters, player_hint, runtime=runtime)

    inferred_player_name = _infer_player_name(source_text)
    if not characters:
        characters = [
            _character(
                name=inferred_player_name or "玩家",
                role="玩家",
                personality="保持警觉，根据所见事实行动。",
                goals=_normalise_goals(data.get("goals", data.get("目标")))
                or ["探索当前局面"],
                is_player=True,
                runtime=runtime,
            )
        ]
    elif not any(bool(item.get("is_player")) for item in characters):
        # A seed with named characters but no explicit player remains playable:
        # the first declared character is the controllable protagonist.
        characters[0]["is_player"] = True

    # Character/object declarations are allowed to introduce their own place;
    # this is explicit author data, not a guessed connection.
    for item in characters:
        location = str(item.pop("_location", "") or "").strip()
        if location:
            if location not in locations:
                locations.append(location)
                world_objects.setdefault(location, {"is_location": True, "connected_to": []})
            item["location"] = location
    if not locations:
        locations = ["起始场景"]
        world_objects.setdefault(locations[0], {"is_location": True, "connected_to": []})
    _ensure_location_objects(world_objects, locations, edges)

    actor_states: dict[str, dict[str, Any]] = {}
    for item in characters:
        name_value = str(item.get("name", "")).strip()
        location = str(item.pop("location", "") or "").strip() or locations[0]
        if location not in locations:
            locations.append(location)
            world_objects.setdefault(location, {"is_location": True, "connected_to": []})
        state = {"location": location}
        activity = item.pop("_activity", "")
        if activity:
            state["activity"] = activity
        actor_states[name_value] = state

    # Preserve the complete authored schema fields when supplied, while
    # allowing the small seed vocabulary to fill only the structural minimum.
    payload: dict[str, Any] = {
        "name": name[:160],
        "description": description[:8000],
        "environment": environment[:12000],
        "default_agent_runtime": str(
            data.get("default_agent_runtime") or runtime
        ).strip(),
        "physics_profile": data.get("physics_profile", "mundane"),
        "rules": _normalise_string_list(data.get("rules", data.get("规则", []))),
        "initial_state": initial_state[:12000],
        "initial_scene_flags": deepcopy(data.get("initial_scene_flags", {}))
        if isinstance(data.get("initial_scene_flags", {}), Mapping)
        else {},
        "public_scene_fields": _normalise_string_list(data.get("public_scene_fields", [])),
        "private_scene_fields": _normalise_string_list(data.get("private_scene_fields", [])),
        "initial_world_objects": world_objects,
        "initial_actor_states": actor_states,
        "characters": characters,
    }
    for field in _SCENARIO_FIELDS:
        if field in payload or field not in data:
            continue
        # ``locations``, ``objects`` and the other seed-only aliases are not
        # ScenarioConfig fields and are intentionally not copied through.
        payload[field] = deepcopy(data[field])

    metadata = dict(data.get("metadata", {})) if isinstance(data.get("metadata"), Mapping) else {}
    metadata.update(
        {
            "seed_compiler": "deterministic-v1",
            "seed_format": source_format,
            "seed_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        }
    )
    metadata.setdefault(
        "compiler_note",
        "未被结构化声明的内容保留在开场文本中，未自动写入隐藏世界事实。",
    )
    payload["metadata"] = {str(key): str(value) for key, value in metadata.items()}

    if simulation_mode is not None:
        payload["simulation_mode"] = _normalise_mode(simulation_mode, "simulation_mode")
    if narration_mode is not None:
        payload["narration_mode"] = _normalise_mode(narration_mode, "narration_mode")

    _validate_seed_structure(payload)
    try:
        return ScenarioConfig.model_validate(payload)
    except ValidationError as exc:
        raise ScenarioSeedError(_format_validation_error(exc)) from exc


def _looks_like_complete_scenario(data: Mapping[str, Any]) -> bool:
    required = {
        "name",
        "description",
        "environment",
        "initial_state",
        "default_agent_runtime",
        "characters",
        "initial_actor_states",
        "initial_world_objects",
    }
    return required.issubset(data)


def _validate_complete_mapping(data: Mapping[str, Any]) -> None:
    """Reject fields that permissive Pydantic models would otherwise drop.

    ``ScenarioConfig`` is kept permissive for legacy content, but the
    author-facing seed boundary must be lossless: a typo in a complete
    JSON/YAML document should fail before a session or agent is started.
    Character records receive the same treatment because nested Pydantic
    models also ignore unknown keys by default.
    """

    unknown = sorted(
        str(key) for key in data.keys() if str(key) not in _SCENARIO_FIELDS
    )
    if unknown:
        raise ScenarioSeedError(
            "scenario seed invalid at <root>: unknown field(s): "
            + ", ".join(unknown)
        )

    raw_characters = data.get("characters", [])
    if isinstance(raw_characters, Mapping):
        character_items = [
            {
                "name": name,
                **(
                    dict(value)
                    if isinstance(value, Mapping)
                    else value.model_dump(mode="python")
                    if isinstance(value, CharacterConfig)
                    else {}
                ),
            }
            for name, value in raw_characters.items()
        ]
    elif isinstance(raw_characters, (list, tuple)):
        character_items = list(raw_characters)
    else:
        raise ScenarioSeedError(
            "scenario seed invalid at characters: expected a list or mapping"
        )

    for index, item in enumerate(character_items):
        if isinstance(item, CharacterConfig):
            item = item.model_dump(mode="python")
        if not isinstance(item, Mapping):
            raise ScenarioSeedError(
                f"scenario seed invalid at characters.{index}: expected an object"
            )
        unknown_character = sorted(
            str(key)
            for key in item.keys()
            if str(key) not in _CHARACTER_SEED_FIELDS
        )
        if unknown_character:
            raise ScenarioSeedError(
                f"scenario seed invalid at characters.{index}: "
                "unknown field(s): " + ", ".join(unknown_character)
            )


def _validate_seed_structure(payload: Mapping[str, Any]) -> None:
    """Check cross-references that Pydantic cannot express."""

    world = payload.get("initial_world_objects", {})
    actors = payload.get("initial_actor_states", {})
    characters = payload.get("characters", [])
    if not isinstance(world, Mapping) or not isinstance(actors, Mapping):
        raise ScenarioSeedError("seed world and actor state must be mappings")
    locations = {
        str(name).strip()
        for name, state in world.items()
        if isinstance(state, Mapping)
        and bool(state.get("is_location", True))
        and str(name).strip()
    }
    if not locations:
        raise ScenarioSeedError("seed must declare at least one location")
    character_items = [item for item in characters if isinstance(item, Mapping)]
    character_names = {
        str(item.get("name", "")).strip() for item in character_items
    }
    if "" in character_names:
        raise ScenarioSeedError("every seed character needs a non-empty name")
    if len(character_names) != len(character_items):
        raise ScenarioSeedError("seed character names must be unique")
    players = [
        str(item.get("name", "")).strip()
        for item in character_items
        if bool(item.get("is_player", False))
    ]
    if len(players) > 1:
        raise ScenarioSeedError(
            "seed must declare at most one player character: "
            + ", ".join(players)
        )
    world_names = {str(name).strip() for name in world if str(name).strip()}
    collisions = sorted(world_names.intersection(character_names))
    if collisions:
        raise ScenarioSeedError(
            "world-object and character names must be distinct: "
            + ", ".join(collisions)
        )
    for name, state in world.items():
        if not isinstance(state, Mapping) or not bool(state.get("is_location", True)):
            continue
        connected = state.get("connected_to", [])
        if connected is None:
            continue
        if not isinstance(connected, (list, tuple)):
            raise ScenarioSeedError(f"location connected_to must be a list: {name}")
        unknown = sorted(
            str(item).strip()
            for item in connected
            if str(item).strip() and str(item).strip() not in locations
        )
        if unknown:
            raise ScenarioSeedError(
                f"location {name} references unknown destinations: {unknown}"
            )
    for name, state in actors.items():
        if not isinstance(state, Mapping):
            raise ScenarioSeedError(f"actor state must be a mapping: {name}")
        location = str(state.get("location", "")).strip()
        if not location or location not in locations:
            raise ScenarioSeedError(
                f"actor {name} has an unknown or empty location: {location or '<empty>'}"
            )
    actor_names = {str(name).strip() for name in actors if str(name).strip()}
    # ECS keeps actor bodies and world objects in separate registries, but a
    # shared identifier would make natural-language target binding and
    # evidence references ambiguous.  Reject it at bootstrap instead of
    # letting later POV projections guess which entity the author meant.
    object_names = {
        str(name).strip()
        for name, state in world.items()
        if str(name).strip()
        and isinstance(state, Mapping)
        and not bool(state.get("is_location", True))
    }
    actor_collisions = sorted(object_names.intersection(actor_names))
    if actor_collisions:
        raise ScenarioSeedError(
            "object and character names must be distinct: "
            + ", ".join(actor_collisions)
        )
    if not character_names.issubset(actor_names):
        raise ScenarioSeedError(
            "every seed character needs an actor state: "
            + ", ".join(sorted(character_names.difference(actor_names)))
        )
    orphan_actors = sorted(actor_names.difference(character_names))
    if orphan_actors:
        raise ScenarioSeedError(
            "initial actor states without declared characters: "
            + ", ".join(orphan_actors)
        )
    for name, state in world.items():
        if not isinstance(state, Mapping) or bool(state.get("is_location", True)):
            continue
        placements = [
            key
            for key in ("owner", "location", "container")
            if str(state.get(key) or "").strip()
        ]
        if len(placements) > 1:
            raise ScenarioSeedError(
                f"object {name} has multiple placements: {placements}"
            )
        if not placements:
            raise ScenarioSeedError(
                f"object {name} needs an explicit owner, location, or container"
            )
        placement = placements[0]
        target = str(state.get(placement) or "").strip()
        if placement == "location" and target not in locations:
            raise ScenarioSeedError(f"object {name} references unknown location: {target}")
        if placement == "owner" and target not in actor_names:
            raise ScenarioSeedError(f"object {name} references unknown owner: {target}")
        if placement == "container":
            container_state = world.get(target)
            if not isinstance(container_state, Mapping) or bool(container_state.get("is_location", True)):
                raise ScenarioSeedError(f"object {name} references unknown container: {target}")

    claims = payload.get("claims", [])
    if isinstance(claims, (list, tuple)):
        for claim in claims:
            if not isinstance(claim, Mapping):
                continue
            refs = list(claim.get("supporting_evidence", []) or []) + list(
                claim.get("refuting_evidence", []) or []
            )
            unknown = sorted(
                str(item).strip() for item in refs if str(item).strip() not in world_names
            )
            if unknown:
                raise ScenarioSeedError(
                    f"claim {claim.get('claim_id', '<unknown>')} references unknown evidence: {unknown}"
                )


def _parse_document_mapping(text: str) -> Mapping[str, Any] | None:
    stripped = text.lstrip()
    candidates: list[Any] = []
    if stripped.startswith(("{", "[")):
        try:
            candidates.append(json.loads(text))
        except (TypeError, ValueError):
            pass
    if stripped.startswith("---") or ":" in text:
        try:
            candidates.append(yaml.safe_load(text))
        except yaml.YAMLError:
            pass
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _canonicalize_seed_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the small seed vocabulary before strict Draft validation."""

    data: dict[str, Any] = {}
    for key, value in raw.items():
        normalized_key = _KEY_ALIASES.get(str(key).strip().casefold())
        if normalized_key is None:
            normalized_key = _KEY_ALIASES.get(str(key).strip(), str(key).strip())
        if normalized_key in data:
            # Singular/plural aliases are convenient, but silently choosing
            # one of two different declarations makes a seed non-auditable.
            # Equal duplicate values remain harmless; conflicting values fail
            # before any world object is materialized.
            try:
                equal = data[normalized_key] == value
            except Exception:
                equal = False
            if not equal:
                raise ScenarioSeedError(
                    "conflicting seed declarations for field: "
                    + str(normalized_key)
                )
        # Singular aliases are accepted as a convenience but never overwrite
        # an explicitly plural declaration.
        if normalized_key == "characters" and "characters" in data:
            continue
        if normalized_key == "objects" and "objects" in data:
            continue
        if normalized_key == "locations" and "locations" in data:
            continue
        data[normalized_key] = value
    if "characters" not in data and "character" in data:
        data["characters"] = data.pop("character")
    if "objects" not in data and "object" in data:
        data["objects"] = data.pop("object")
    if "locations" not in data and "location" in data:
        data["locations"] = data.pop("location")
    return data


def _parse_text_directives(text: str) -> dict[str, Any]:
    parts: dict[str, Any] = {"premise": text}
    prose_lines: list[str] = []
    characters: list[Any] = []
    locations: list[str] = []
    objects: list[Any] = []
    rules: list[str] = []
    current_character: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = re.sub(r"^\s*[-*•]\s*", "", raw_line).strip()
        if not line:
            continue
        match = _DIRECTIVE_RE.match(line)
        if not match:
            prose_lines.append(line)
            continue
        raw_key, value = match.groups()
        key = _KEY_ALIASES.get(raw_key.strip().casefold(), _KEY_ALIASES.get(raw_key.strip(), ""))
        value = value.strip()
        if not key:
            prose_lines.append(line)
            continue
        if key == "name":
            parts["name"] = value
        elif key in {"environment", "description"}:
            parts[key] = value
        elif key == "initial_state":
            parts["initial_state"] = value
        elif key == "locations":
            locations.extend(_split_values(value))
        elif key == "characters":
            characters.extend(_split_character_entries(value))
            current_character = characters[-1] if characters and isinstance(characters[-1], dict) else None
        elif key == "player":
            parts["player"] = value
            if value:
                for item in characters:
                    if isinstance(item, Mapping) and str(item.get("name", "")).strip() == value.strip():
                        item["is_player"] = True
        elif key == "objects":
            objects.extend(_split_object_entries(value))
        elif key == "rules":
            rules.extend(_split_values(value, keep_semicolon=True))
        elif key == "goals":
            if current_character is not None:
                current_character["goals"] = _normalise_goals(value)
            else:
                parts["goals"] = _normalise_goals(value)
        else:
            prose_lines.append(line)

    parts["characters"] = characters
    parts["locations"] = locations
    parts["objects"] = objects
    parts["rules"] = rules
    if prose_lines:
        # Keep the original text as the premise for fidelity, but use
        # non-directive prose as a concise description when available.
        parts.setdefault("description", " ".join(prose_lines))
    return parts


def _split_character_entries(value: str) -> list[dict[str, Any]]:
    # A pipe-delimited declaration is one character.  Semicolon-delimited
    # declarations without pipes are accepted for compact Chinese seeds.
    chunks = [value]
    if "|" not in value and "｜" not in value:
        chunks = [item for item in re.split(r"\s*；\s*", value) if item.strip()]
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        item = _parse_character_entry(chunk)
        if item:
            result.append(item)
    return result


def _parse_character_entry(value: str) -> dict[str, Any] | None:
    raw = value.strip()
    if not raw:
        return None
    # Support 角色：名字（身份；性格；目标；玩家；地点）.
    bracket = re.search(r"[（(](.*)[）)]", raw)
    if bracket and ("|" not in raw and "｜" not in raw):
        name = raw[: bracket.start()].strip()
        parts = [name, *[item.strip() for item in re.split(r"\s*[;；]\s*", bracket.group(1))]]
    else:
        parts = [item.strip() for item in re.split(r"\s*[|｜]\s*", raw)]
        if len(parts) == 1:
            parts = [item.strip() for item in re.split(r"\s*[;；]\s*", raw)]
    name = _clean_token(parts[0] if parts else "")
    if not name:
        return None
    role = _clean_token(parts[1] if len(parts) > 1 else "角色") or "角色"
    personality = _clean_token(parts[2] if len(parts) > 2 else "根据所见事实行动。") or "根据所见事实行动。"
    goals = _normalise_goals(parts[3] if len(parts) > 3 else []) or ["探索当前局面"]
    rest = " ".join(parts[4:])
    is_player = any(marker in rest.casefold() for marker in _PLAYER_MARKERS)
    location = _extract_labeled_token(rest, ("地点", "位置", "location", "at"))
    if not location and len(parts) > 4:
        candidate = _clean_token(parts[4])
        if candidate and not is_player and not any(marker in candidate.casefold() for marker in _PLAYER_MARKERS):
            location = candidate
    activity = _extract_labeled_token(rest, ("活动", "activity"))
    return {
        "name": name,
        "role": role,
        "personality": personality,
        "goals": goals,
        "is_player": is_player,
        "agent_runtime": "",
        "_location": location,
        "_activity": activity,
    }


def _split_object_entries(value: str) -> list[dict[str, Any]]:
    chunks = [value]
    if "|" not in value and "｜" not in value:
        chunks = [item for item in re.split(r"\s*；\s*", value) if item.strip()]
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        parts = [item.strip() for item in re.split(r"\s*[|｜]\s*", chunk)]
        if len(parts) == 1:
            parts = [item.strip() for item in re.split(r"\s*[,，]\s*", chunk)]
        name = _clean_token(parts[0] if parts else "")
        if not name:
            continue
        kind = _clean_token(parts[1] if len(parts) > 1 else "item") or "item"
        location = _clean_token(parts[2] if len(parts) > 2 else "")
        flags = " ".join(parts[3:]).casefold()
        result.append(
            {
                "name": name,
                "kind": kind,
                "location": location,
                "hidden": any(token in flags for token in ("hidden", "隐藏", "secret", "秘密")),
                "portable": not any(token in flags for token in ("fixed", "固定", "不可移动")),
            }
        )
    return result


def _normalise_characters(raw: Any, *, runtime: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        iterable = []
        for name, value in raw.items():
            if isinstance(value, Mapping):
                iterable.append({"name": name, **dict(value)})
            else:
                iterable.append({"name": name, "role": str(value)})
    elif isinstance(raw, (list, tuple)):
        iterable = list(raw)
    else:
        iterable = _split_character_entries(str(raw))

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in iterable:
        if isinstance(item, str):
            parsed = _parse_character_entry(item)
            item = parsed or {}
        elif isinstance(item, CharacterConfig):
            item = item.model_dump(mode="python")
        if not isinstance(item, Mapping):
            continue
        unknown_fields = sorted(
            str(key)
            for key in item.keys()
            if str(key) not in _CHARACTER_SEED_FIELDS
        )
        if unknown_fields:
            raise ScenarioSeedError(
                "character declaration contains unknown field(s): "
                + ", ".join(unknown_fields)
            )
        value = dict(item)
        name = _clean_token(value.get("name"))
        if not name:
            continue
        if name in seen:
            raise ScenarioSeedError(f"duplicate character name in seed: {name}")
        seen.add(name)
        value["name"] = name
        value["role"] = _clean_token(value.get("role")) or "角色"
        value["personality"] = _clean_token(value.get("personality")) or "根据所见事实行动。"
        value["goals"] = _normalise_goals(value.get("goals")) or ["探索当前局面"]
        value["is_player"] = bool(value.get("is_player", False))
        value["agent_runtime"] = _clean_token(value.get("agent_runtime")) or runtime
        # Internal parser keys are removed later, but keeping them here makes
        # mapping and text declarations share one normalization path.
        value.setdefault("_location", _clean_token(value.get("location")))
        value.setdefault("_activity", _clean_token(value.get("activity")))
        value.pop("location", None)
        value.pop("activity", None)
        result.append(value)
    return result


def _normalise_world_objects(
    raw: Any,
    *,
    locations: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for name, state in raw.items():
            key = str(name).strip()
            if not key:
                continue
            result[key] = deepcopy(dict(state)) if isinstance(state, Mapping) else {}
    _ensure_location_objects(result, locations, edges)
    return result


def _merge_object_specs(
    world_objects: dict[str, dict[str, Any]],
    raw: Any,
    locations: list[str],
) -> None:
    if raw is None:
        return
    if isinstance(raw, Mapping):
        iterable = [{"name": key, **(dict(value) if isinstance(value, Mapping) else {})} for key, value in raw.items()]
    elif isinstance(raw, (list, tuple)):
        iterable = list(raw)
    else:
        iterable = _split_object_entries(str(raw))
    for item in iterable:
        if isinstance(item, str):
            parsed = _split_object_entries(item)
            item = parsed[0] if parsed else {}
        if not isinstance(item, Mapping):
            continue
        name = _clean_token(item.get("name"))
        if not name:
            continue
        location = _clean_token(item.get("location"))
        if location and location not in locations:
            locations.append(location)
            world_objects.setdefault(location, {"is_location": True, "connected_to": []})
        state = dict(item)
        state.pop("name", None)
        state.setdefault("is_location", False)
        state.setdefault("kind", "item")
        state.setdefault("portable", True)
        state.setdefault("hidden", False)
        if location:
            state["location"] = location
        world_objects[name] = state


def _normalise_locations(raw: Any) -> tuple[list[str], list[tuple[str, str]]]:
    if raw is None:
        return [], []
    if isinstance(raw, Mapping):
        values = list(raw.keys())
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = _split_values(str(raw))
    locations: list[str] = []
    edges: list[tuple[str, str]] = []
    for value in values:
        text = _clean_token(value)
        if not text:
            continue
        # Explicit topology uses A->B or A>B.  A plain hyphen is left alone,
        # because it is common in place names.
        match = re.match(r"^(.+?)\s*(?:->|>)\s*(.+)$", text)
        if match:
            left, right = (_clean_token(part) for part in match.groups())
            if left and right:
                locations.extend([left, right])
                edges.append((left, right))
            continue
        locations.append(text)
    deduped = list(dict.fromkeys(locations))
    return deduped, edges


def _ensure_location_objects(
    world_objects: dict[str, dict[str, Any]],
    locations: Sequence[str],
    edges: Sequence[tuple[str, str]],
) -> None:
    adjacency: dict[str, list[str]] = {str(item): [] for item in locations}
    for left, right in edges:
        if left in adjacency and right in adjacency:
            if right not in adjacency[left]:
                adjacency[left].append(right)
            if left not in adjacency[right]:
                adjacency[right].append(left)
    for location in locations:
        state = world_objects.setdefault(str(location), {})
        # Existing authored location metadata wins; a seed compiler only adds
        # the minimum spatial identity and explicit topology.
        state.setdefault("is_location", True)
        if adjacency.get(str(location)):
            existing = state.get("connected_to", [])
            if not isinstance(existing, list):
                existing = []
            state["connected_to"] = list(dict.fromkeys([*existing, *adjacency[str(location)]]))


def _apply_player_hint(
    characters: list[dict[str, Any]],
    hint: Any,
    *,
    runtime: str,
) -> None:
    if hint is None:
        return
    if isinstance(hint, Mapping):
        hint = hint.get("name") or hint.get("character")
    value = _clean_token(hint)
    if not value:
        return
    for item in characters:
        if item.get("name") == value:
            item["is_player"] = True
            return
    # A player name without a separate character declaration is a useful
    # shorthand; synthesize the body and let the normal location pass place it.
    characters.insert(
        0,
        _character(
            name=value,
            role="玩家",
            personality="保持警觉，根据所见事实行动。",
            goals=["探索当前局面"],
            is_player=True,
            runtime=runtime,
        ),
    )


def _character(
    *,
    name: str,
    role: str,
    personality: str,
    goals: list[str],
    is_player: bool,
    runtime: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "personality": personality,
        "goals": goals,
        "is_player": is_player,
        "agent_runtime": runtime,
        "_location": "",
        "_activity": "",
    }


def _normalise_goals(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item for item in _split_values(raw) if item]
    if isinstance(raw, (list, tuple)):
        return [_clean_token(item) for item in raw if _clean_token(item)]
    return [_clean_token(raw)] if _clean_token(raw) else []


def _normalise_string_list(raw: Any, *, keep_semicolon: bool = False) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return _split_values(raw, keep_semicolon=keep_semicolon)
    if isinstance(raw, (list, tuple)):
        return [_clean_token(item) for item in raw if _clean_token(item)]
    return [_clean_token(raw)] if _clean_token(raw) else []


def _split_values(value: str, *, keep_semicolon: bool = False) -> list[str]:
    pattern = r"\s*[、,，]+\s*"
    if not keep_semicolon:
        pattern = r"\s*[、,，;；]+\s*"
    return [item.strip() for item in re.split(pattern, str(value)) if item.strip()]


def _extract_labeled_token(text: str, labels: Sequence[str]) -> str:
    if not text:
        return ""
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[=:：]\s*([^,，;；|｜]+)", text, re.I)
    return _clean_token(match.group(1)) if match else ""


def _infer_player_name(text: str) -> str:
    patterns = (
        r"(?:你是|玩家(?:角色)?(?:是|为))\s*(?:一名|一个|一位)?\s*([^，。；,\n]+)",
        r"\bYou are\s+(?:a|an)?\s*([A-Z][A-Za-z0-9 _-]{1,48})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = _clean_token(match.group(1))
            value = re.sub(r"(?:，|,)?\s*(?:负责|正在|并且|并|是)\s*.*$", "", value)
            # A role can be the only identity supplied by a free-form seed
            # (for example, ``你是守夜人``).  Keep it as the player name;
            # stripping a dictionary of occupations turns an otherwise
            # meaningful protagonist into the generic fallback ``玩家``.
            value = re.sub(r"^(?:一名|一个|一位)\s*", "", value)
            if value and len(value) <= 80:
                return value
    return ""


def _derive_name(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return "未命名故事"
    first = re.split(r"[。！？!?\n]", compact, maxsplit=1)[0].strip()
    return (first or compact)[:80]


def _first_text(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split()).strip()
    return ""


def _clean_token(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().strip("\"'“”‘’").split())


def _normalise_runtime(value: Any) -> str:
    runtime = _clean_token(value)
    if not runtime:
        raise ScenarioSeedError("scenario runtime name must be non-empty")
    if len(runtime) > 80:
        raise ScenarioSeedError("scenario runtime name is too long")
    return runtime


def _normalise_mode(value: Any, field: str) -> str:
    mode = _clean_token(value).casefold().replace("-", "_")
    aliases = {"llm": "llm", "model": "llm", "rules": "rules", "host_rule": "rules", "offline": "rules"}
    if mode not in aliases:
        raise ScenarioSeedError(f"{field} must be 'llm' or 'rules'")
    return aliases[mode]


def _copy_with_optional_fields(scenario: ScenarioConfig, updates: Mapping[str, Any]) -> ScenarioConfig:
    # These fields are added by the application in newer workspaces.  Keeping
    # this helper tolerant lets a seed compiler remain importable while a
    # partially upgraded checkout is being migrated.
    available = set(ScenarioConfig.model_fields)
    if not set(updates).issubset(available):
        unknown = sorted(set(updates).difference(available))
        raise ScenarioSeedError(
            "ScenarioConfig does not expose optional seed policy fields: "
            + ", ".join(unknown)
        )
    return scenario.model_copy(update=dict(updates), deep=True)


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "scenario seed failed validation"
    first = errors[0]
    location = ".".join(str(item) for item in first.get("loc", ())) or "scenario"
    message = str(first.get("msg", "invalid value"))
    return f"scenario seed invalid at {location}: {message}"


__all__ = [
    "CompiledSeed",
    "SeedDraft",
    "ScenarioSeedError",
    "compile_seed",
    "compile_seed_report",
    "compile_scenario_seed",
    "compile_scenario_seed_file",
    "load_or_compile_scenario",
]
