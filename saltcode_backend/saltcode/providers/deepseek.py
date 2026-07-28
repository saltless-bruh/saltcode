from typing import Any

import httpx

from saltcode.config import settings
from saltcode.providers.base import LLMClient
from saltcode.providers.guard import guard_outbound


class DeepSeekClient(LLMClient):
    """LLMClient provider implementation for the DeepSeek API.

    **Migrating out (v9).** Agent-facing provider management moves to the
    extension's `pi.registerProvider` (design DD-2, tasks 11/13). This module is
    kept only until that lands: port it, do not extend it.
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = base_url or settings.deepseek_base_url

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        contains_raw_source: bool = False,
    ) -> str:
        # 1. Privacy Boundary Guard (REQ-GLB-003)
        # The checks that used to be inlined here now live in providers/guard.py,
        # so every outbound call in the backend is refused on the same terms
        # rather than on whichever ones its own author remembered (task 2.4).
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        guard_outbound(endpoint, messages, contains_raw_source=contains_raw_source)

        # 2. Validate prefix-cache-friendly message ordering
        # System messages must come first. If a system message is found after a user/assistant message, warn/error.
        has_seen_non_system = False
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "system":
                if has_seen_non_system:
                    raise ValueError(
                        f"Prefix-cache-unfriendly ordering: system message found at index {idx} "
                        f"after a non-system message."
                    )
            else:
                has_seen_non_system = True

        # 3. Model selection based on thinking flag
        if model:
            model_to_use = model
        elif thinking:
            model_to_use = settings.deepseek_reasoning_model
        else:
            model_to_use = settings.deepseek_chat_model

        # 4. Construct payload
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Setup parameters
        # Note: deepseek-reasoner (or reasoning models) do not support temperature, top_p, etc.
        # We check if we are in thinking mode or using a reasoning model name.
        is_reasoner = "reasoner" in model_to_use.lower() or "r1" in model_to_use.lower() or thinking

        payload: dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
        }

        if is_reasoner:
            # Omit temperature, top_p, response_format for reasoning models to prevent API errors
            pass
        else:
            payload["temperature"] = 0.2  # Default to deterministic chat
            if json_schema:
                # DeepSeek API supports JSON mode via type="json_object"
                payload["response_format"] = {"type": "json_object"}

        # 5. Execute API request
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured.")

        try:
            with httpx.Client() as client:
                response = client.post(endpoint, headers=headers, json=payload, timeout=60.0)
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"DeepSeek API call failed with status code {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"DeepSeek API call failed: {e}") from e
