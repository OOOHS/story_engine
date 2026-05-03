"""
LLM Provider.
"""
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import litellm
from src.config.config import config

litellm.drop_params = True
litellm.suppress_debug_info = True
litellm.set_verbose = False

class LLMProvider:
    def __init__(
        self,
        model: str = "gpt-3.5-turbo",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        num_retries: Optional[int] = None,
        **kwargs
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout if timeout is not None else config.llm_timeout
        self.num_retries = num_retries if num_retries is not None else config.llm_num_retries
        self.config = kwargs
        self._normalize_openai_compatible_gateway()

    def _normalize_openai_compatible_gateway(self) -> None:
        """
        Support OpenAI-compatible proxy gateways that expose custom models like
        `claude-sonnet-4-6` behind a custom base_url.
        """
        base_url = (self.base_url or "").strip()
        model = (self.model or "").strip()
        if not base_url or not model:
            return

        # If the caller provides a custom gateway plus a provider-less model name,
        # LiteLLM cannot infer the provider. Treat it as OpenAI-compatible.
        if "/" not in model and not self._is_builtin_openai_model(model):
            self.model = f"openai/{model}"
            model = self.model

        if not model.startswith("openai/"):
            return

        parsed = urlparse(base_url)
        if parsed.path.rstrip("/").endswith("/v1"):
            return
        self.base_url = base_url.rstrip("/") + "/v1"

    def _is_builtin_openai_model(self, model: str) -> bool:
        return model.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        if (not self.api_key) and (not self.base_url) and (
            self.model.startswith("gpt-")
            or self.model.startswith("openai/")
            or self.model.startswith("o1")
            or self.model.startswith("o3")
            or self.model.startswith("o4")
        ):
            return {
                "content": f"[LLM disabled] Missing api_key/base_url for model={self.model}.",
                "role": "assistant",
            }
        
        call_kwargs = {
            "model": self.model,
            "messages": messages,
            **self.config,
            **kwargs
        }

        call_kwargs.setdefault("timeout", self.timeout)
        call_kwargs.setdefault("num_retries", self.num_retries)
        
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = "auto"
            
        if self.api_key: call_kwargs["api_key"] = self.api_key
        if self.base_url: call_kwargs["base_url"] = self.base_url
            
        try:
            response = litellm.completion(**call_kwargs)
            result = {"content": response.choices[0].message.content, "role": response.choices[0].message.role}
            if hasattr(response.choices[0].message, "tool_calls") and response.choices[0].message.tool_calls:
                 result["tool_calls"] = [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in response.choices[0].message.tool_calls]
            return result
        except Exception as e:
            return {"content": f"[LLM error {type(e).__name__}] {e}", "role": "assistant"}
