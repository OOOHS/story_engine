"""Re-export from session layer. Prefer: from src.story_engine.session import setup_scenario, create_session."""
from src.story_engine.session.scenario_loader import setup_scenario, create_gm

__all__ = ["setup_scenario", "create_gm"]
