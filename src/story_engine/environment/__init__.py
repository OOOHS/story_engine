"""World-environment services with lazy public imports.

Keeping this package initializer lightweight prevents low-level lifecycle and
motivation modules from importing the full Runner -> Systems graph merely to
reference one environment service.
"""

from typing import Any

__all__ = [
    "Dispatcher",
    "HostTopologyTransaction",
    "HostWorldEditTransaction",
    "HostMutationTransaction",
    "Runner",
]


def __getattr__(name: str) -> Any:
    if name == "Dispatcher":
        from .dispatcher import Dispatcher

        return Dispatcher
    if name == "Runner":
        from .runner import Runner

        return Runner
    if name == "HostTopologyTransaction":
        from .topology import HostTopologyTransaction

        return HostTopologyTransaction
    if name == "HostWorldEditTransaction":
        from .world_edits import HostWorldEditTransaction

        return HostWorldEditTransaction
    if name == "HostMutationTransaction":
        from .host_mutations import HostMutationTransaction

        return HostMutationTransaction
    raise AttributeError(name)
