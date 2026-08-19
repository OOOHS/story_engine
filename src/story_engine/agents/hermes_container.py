import json
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Tuple

from src.story_engine.agents.hermes_runtime import HermesCharacterAgent


BEGIN_MARKER = "===STORY_AGENT_JSON_BEGIN==="
END_MARKER = "===STORY_AGENT_JSON_END==="
_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]*$")
_NETWORK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
MAX_STDOUT_CHARS = 2_000_000


class HermesInvocationBudgetExceeded(RuntimeError):
    """Raised before a Hermes invocation would exceed the Host budget."""


class HermesInvocationBudget:
    """Thread-safe Host-owned limit on actual Hermes container invocations."""

    def __init__(self, maximum: int) -> None:
        self.maximum = int(maximum)
        if self.maximum < 0:
            raise ValueError("Hermes invocation budget must not be negative")
        self._consumed = 0
        self._exhausted = self.maximum == 0
        self._lock = threading.Lock()

    def consume(self) -> int:
        with self._lock:
            if self._consumed >= self.maximum:
                self._exhausted = True
                raise HermesInvocationBudgetExceeded(
                    "Hermes invocation budget exhausted "
                    f"({self._consumed}/{self.maximum})"
                )
            self._consumed += 1
            if self._consumed >= self.maximum:
                self._exhausted = True
            return self._consumed

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "configured": self.maximum,
                "consumed": self._consumed,
                "remaining": max(0, self.maximum - self._consumed),
                "exhausted": self._exhausted,
            }


@dataclass(frozen=True)
class HermesContainerConfig:
    """Host-owned container policy. Scenario content cannot widen it."""

    image: str = "hermes-story:latest"
    docker_binary: str = "docker"
    timeout_seconds: float = 180.0
    network_mode: str = "bridge"
    # Default production subjects need Hermes' native memory tool for
    # long-term recall; Host no longer retrieves relevant memories for them.
    # Callers may widen or narrow this allowlist explicitly.
    allowed_toolsets: Tuple[str, ...] = ("memory",)
    environment_keys: Tuple[str, ...] = (
        "OPENAI_API_KEY",
        "IKUN_API_KEY",
        "HERMES_BASE_URL",
        "HERMES_MODEL",
        "HERMES_PROVIDER",
        "HERMES_TRACE",
    )
    entrypoint_path: str = ""
    config_path: str = ""
    invocation_budget: HermesInvocationBudget | None = None
    persistent_subject: bool = True


class HermesContainerConversation:
    """Runs one Hermes turn through a Docker stdin/stdout black-box boundary."""

    def __init__(
        self,
        agent_id: str,
        host_config: HermesContainerConfig,
        requested_toolsets: Iterable[str] = (),
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        if not _IMAGE_PATTERN.fullmatch(host_config.image):
            raise ValueError(f"Unsafe Hermes image name: {host_config.image}")
        if not _NETWORK_PATTERN.fullmatch(host_config.network_mode):
            raise ValueError(f"Unsafe Hermes network mode: {host_config.network_mode}")
        if float(host_config.timeout_seconds) <= 0:
            raise ValueError("Hermes timeout_seconds must be positive")
        self.agent_id = str(agent_id)
        if not self.agent_id.strip():
            raise ValueError("Hermes agent_id must not be empty")
        self.host_config = host_config
        self.requested_toolsets = tuple(dict.fromkeys(
            str(item).strip()
            for item in requested_toolsets
            if str(item).strip()
        ))
        allowed = set(host_config.allowed_toolsets)
        self.enabled_toolsets = tuple(
            item for item in self.requested_toolsets if item in allowed
        )
        self._run = command_runner or subprocess.run
        self._process_factory = subprocess.Popen if command_runner is None else None
        self._subject_process = None
        self._subject_lock = threading.Lock()
        self._bind_mounts = tuple(
            mount
            for source, destination in (
                (host_config.entrypoint_path, "/opt/story-entrypoint.py"),
                (host_config.config_path, "/root/.hermes/config.yaml"),
            )
            if (mount := self._validate_bind_mount(source, destination)) is not None
        )

    def run_conversation(self, prompt: str) -> Dict[str, Any]:
        return self._run_request({"prompt": str(prompt)})

    def run_subject_turn(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(packet, dict) or packet.get("subject_protocol_version") != 1:
            raise ValueError("Hermes subject packet has an unsupported protocol version")
        if str(packet.get("subject_id", "")) != self.agent_id:
            raise ValueError("Hermes subject packet agent_id does not match the conversation")
        request_payload = {"subject_packet": packet}
        if self.host_config.persistent_subject and self._process_factory is not None:
            return self._run_persistent_subject_request(request_payload)
        return self._run_request(request_payload)

    def _run_request(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps(self._request_object(request_payload), ensure_ascii=False)
        command = self.build_command()
        if self.host_config.invocation_budget is not None:
            self.host_config.invocation_budget.consume()
        completed = self._run(
            command,
            input=payload,
            text=True,
            capture_output=True,
            timeout=float(self.host_config.timeout_seconds),
            check=False,
        )
        if int(getattr(completed, "returncode", 1)) != 0:
            stderr = str(getattr(completed, "stderr", "") or "").strip()
            raise RuntimeError(
                f"Hermes container failed with exit code {completed.returncode}: "
                f"{stderr[-1000:]}"
            )
        stdout = str(getattr(completed, "stdout", "") or "")
        if len(stdout) > MAX_STDOUT_CHARS:
            raise ValueError("Hermes output exceeds the Host protocol size limit")
        return self.parse_output(stdout)

    def _run_persistent_subject_request(
        self, request_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        with self._subject_lock:
            process = self._ensure_subject_process()
            if self.host_config.invocation_budget is not None:
                self.host_config.invocation_budget.consume()
            payload = json.dumps(
                self._request_object(request_payload),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            try:
                process.stdin.write(payload + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError("Hermes subject process closed its input") from exc

            deadline = time.monotonic() + float(self.host_config.timeout_seconds)
            output = []
            total_chars = 0
            saw_begin = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close()
                    raise subprocess.TimeoutExpired(
                        self.build_command(subject_server=True),
                        float(self.host_config.timeout_seconds),
                    )
                readable, _, _ = select.select([process.stdout], [], [], remaining)
                if not readable:
                    continue
                line = process.stdout.readline()
                if line == "":
                    returncode = process.poll()
                    stderr = ""
                    if returncode is not None and process.stderr is not None:
                        stderr = str(process.stderr.read() or "").strip()
                    raise RuntimeError(
                        "Hermes subject process exited"
                        + (f" with code {returncode}" if returncode is not None else "")
                        + (f": {stderr[-1000:]}" if stderr else "")
                    )
                total_chars += len(line)
                if total_chars > MAX_STDOUT_CHARS:
                    self.close()
                    raise ValueError("Hermes output exceeds the Host protocol size limit")
                output.append(line)
                if BEGIN_MARKER in line:
                    saw_begin = True
                if saw_begin and END_MARKER in line:
                    return self.parse_output("".join(output))

    def _ensure_subject_process(self):
        process = self._subject_process
        if process is not None and process.poll() is None:
            return process
        command = self.build_command(subject_server=True)
        process = self._process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            process.terminate()
            raise RuntimeError("Hermes subject process did not expose stdin/stdout")
        self._subject_process = process
        return process

    def close(self) -> None:
        process = self._subject_process
        self._subject_process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def _request_object(self, request_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocol_version": 1,
            "agent_id": self.agent_id,
            **request_payload,
            "enabled_toolsets": list(self.enabled_toolsets),
        }

    def build_command(self, *, subject_server: bool = False) -> list[str]:
        command = [
            self.host_config.docker_binary,
            "run",
            "--rm",
            "-i",
            "--network",
            self.host_config.network_mode,
        ]
        for key in self.host_config.environment_keys:
            safe_key = str(key).strip()
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", safe_key):
                # Docker inherits the named value. It is never read or printed
                # by Story Engine and never appears in the command string.
                command.extend(["-e", safe_key])
        for source, destination in self._bind_mounts:
            command.extend([
                "--mount",
                f"type=bind,src={source},dst={destination},readonly",
            ])
        command.append(self.host_config.image)
        if subject_server:
            command.append("--subject-server")
        return command

    @staticmethod
    def _validate_bind_mount(
        source: str, destination: str
    ) -> tuple[str, str] | None:
        raw = str(source or "").strip()
        if not raw:
            return None
        if any(token in raw for token in (",", "\n", "\r")):
            raise ValueError("Unsafe Hermes bind-mount source path")
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Hermes bind-mount source is not a file: {path}")
        return str(path), destination

    def parse_output(self, stdout: str) -> Dict[str, Any]:
        if stdout.count(BEGIN_MARKER) != 1 or stdout.count(END_MARKER) != 1:
            raise ValueError("Hermes output must contain exactly one Story Agent JSON envelope")
        start = stdout.find(BEGIN_MARKER)
        end = stdout.find(END_MARKER, start + len(BEGIN_MARKER))
        if start < 0 or end < 0 or end < start:
            raise ValueError("Hermes output is missing the Story Agent JSON markers")
        raw = stdout[start + len(BEGIN_MARKER) : end].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Hermes marker payload is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Hermes marker payload must be an object")
        if data.get("protocol_version") != 1:
            raise ValueError("Hermes marker payload has an unsupported protocol version")
        if str(data.get("agent_id", "")) != self.agent_id:
            raise ValueError("Hermes marker payload agent_id does not match the request")
        if not isinstance(data.get("content"), str) or not data["content"].strip():
            raise ValueError("Hermes marker payload must contain non-empty text content")
        return data


def default_hermes_runtime_factories(
    config: HermesContainerConfig | None = None,
    command_runner: Callable[..., Any] | None = None,
) -> Dict[str, Callable[..., Any]]:
    """Convenience wiring for non-evaluation callers (console, web) that just
    want the standard Hermes container runtime registered under "hermes",
    without the evaluation-only deterministic GM swap that
    ``create_hermes_episode_session`` performs.

    Bundled/production content declares ``agent_runtime="hermes"`` per
    character (and a scenario-level ``default_agent_runtime`` for
    runtime-spawned characters); this only supplies the matching factory.
    With no Docker install and no vendored ``hermes-agent`` snapshot in this
    environment, invoking a character backed by this factory will fail
    loudly at the ``docker run`` step -- that is the intended fail-fast
    behavior, not a bug in this helper.
    """
    return {
        "hermes": make_hermes_container_runtime_factory(
            config or HermesContainerConfig(),
            command_runner=command_runner,
        )
    }


def make_hermes_container_runtime_factory(
    host_config: HermesContainerConfig,
    command_runner: Callable[..., Any] | None = None,
):
    """Build a Runner-compatible factory with subject-owned deliberation."""

    def factory(entity, runtime_config):
        requested_toolsets = runtime_config.get("enabled_toolsets", [])
        if not isinstance(requested_toolsets, (list, tuple)):
            requested_toolsets = []
        # The subject needs its native memory tool even when content forgot
        # to ask; other requests still have to pass the Host allowlist.
        requested_toolsets = tuple(
            dict.fromkeys(["memory", *[str(item) for item in requested_toolsets]])
        )

        def conversation_factory(character_entity, character_config):
            del character_config
            return HermesContainerConversation(
                agent_id=character_entity.id,
                host_config=host_config,
                requested_toolsets=requested_toolsets,
                command_runner=command_runner,
            )

        return HermesCharacterAgent(
            conversation_factory=conversation_factory,
            config=dict(runtime_config),
        )

    return factory
