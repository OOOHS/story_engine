"""
Configuration module.
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    _COMPONENT_ENV_CANDIDATES = {
        "game_master": [
            {
                "api_key": "GM_API_KEY",
                "model": "GM_MODEL",
                "base_url": "GM_MODEL_BASE_URL",
            }
        ],
        "agent": [
            {
                "api_key": "ACTOR_API_KEY",
                "model": "ACTOR_MODEL",
                "base_url": "ACTOR_MODEL_BASE_URL",
            }
        ],
        "narrator": [
            {
                "api_key": "NARRATOR_API_KEY",
                "model": "NARRATOR_MODEL",
                "base_url": "NARRATOR_MODEL_BASE_URL",
            },
            {
                "api_key": "GM_API_KEY",
                "model": "GM_MODEL",
                "base_url": "GM_MODEL_BASE_URL",
            },
        ],
    }
    _COMPONENT_CONFIG_FALLBACKS = {
        "narrator": "game_master",
    }

    def __init__(self):
        self._config = self._load_config()

        # General Settings
        llm_cfg = self._config.get("llm", {})
        self.llm_timeout = llm_cfg.get("timeout", 120)
        self.llm_num_retries = llm_cfg.get("num_retries", 2)
        
        sys_cfg = self._config.get("system", {})
        self.chromadb_persist_dir = sys_cfg.get("chromadb_persist_dir", "./data/chromadb")

    def _load_config(self) -> Dict[str, Any]:
        """Loads the config.yaml file from the same directory."""
        try:
            config_path = Path(__file__).parent / "config.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            else:
                print(f"Warning: Config file not found at {config_path}")
        except Exception as e:
            print(f"Error loading config.yaml: {e}")
        return {}

    def get_component_config(self, component_name: str, api_key_env: Optional[str] = None) -> Dict[str, Any]:
        """
        Get LLM config for a component and inject API key.
        Structure: components -> <name> -> llm
        
        Args:
            component_name: The key in the config file (e.g., 'game_master', 'agent').
            api_key_env: Optional environment variable name for the API key. 
                         If not provided, defaults to checking known mappings or OPENAI_API_KEY.
        """
        config_key = component_name
        components = self._config.get("components", {})
        if config_key not in components:
            config_key = self._COMPONENT_CONFIG_FALLBACKS.get(component_name, component_name)

        comp_cfg = components.get(config_key, {})
        
        # Start with component-specific LLM config, fallback to empty dict
        llm_cfg = comp_cfg.get("llm", {}).copy()

        env_candidates = self._COMPONENT_ENV_CANDIDATES.get(
            component_name,
            self._COMPONENT_ENV_CANDIDATES.get(config_key, []),
        )

        model_override = self._first_env_value(env_candidates, "model")
        if model_override:
            llm_cfg["model"] = model_override

        base_url_override = self._first_env_value(env_candidates, "base_url")
        if base_url_override:
            llm_cfg["base_url"] = base_url_override

        # Priority: Explicit api_key_env > Component env mapping > YAML > Global OPENAI_API_KEY
        env_key = os.getenv(api_key_env) if api_key_env else None
        if not env_key:
            env_key = self._first_env_value(env_candidates, "api_key")

        if env_key:
            llm_cfg["api_key"] = env_key
        elif "api_key" not in llm_cfg or not llm_cfg["api_key"]:
            llm_cfg["api_key"] = os.getenv("OPENAI_API_KEY")

        return llm_cfg

    def _first_env_value(self, candidates: Any, field: str) -> Optional[str]:
        for mapping in candidates or []:
            env_name = mapping.get(field)
            if not env_name:
                continue
            env_value = os.getenv(env_name)
            if env_value:
                return env_value
        return None

config = Config()
