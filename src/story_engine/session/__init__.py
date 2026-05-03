"""
Session layer: binds a Runner to a Scenario and provides a single entry point for running the sandbox.
Use create_session(scenario) to build a runnable session; then run with a Driver (e.g. ConsoleDriver).
"""
from .session import Session, create_session
from .scenario_loader import setup_scenario
from .console_driver import ConsoleDriver

__all__ = ["Session", "create_session", "setup_scenario", "ConsoleDriver"]
