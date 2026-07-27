from typing import Any

import httpx

from saltcode.config import settings
from saltcode.providers.base import LLMClient


class OracleRefusalError(Exception):
    """Exception raised when the Saltnitor control API refuses to load a profile (e.g., due to OOM)."""
    pass


class LocalClient(LLMClient):
    """LLMClient provider implementation for local serving (Saltnitor / llama.cpp)."""

    def __init__(
        self,
        base_url: str | None = None,
        fallback_url: str | None = None,
        default_model: str | None = None,
    ):
        self.base_url = (base_url or settings.saltnitor_url).rstrip("/")
        self.fallback_url = (fallback_url or settings.llamacpp_fallback_url).rstrip("/")
        self.default_model = default_model or settings.local_default_model

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        contains_raw_source: bool = False,  # noqa: ARG002
    ) -> str:
        # Local providers are exempt from the raw source guard (raw bodies stay on-box)
        model_to_use = model or self.default_model

        use_fallback = False
        ensure_endpoint = f"{self.base_url}/v1/ensure"

        # 1. Attempt to ensure profile residency via Saltnitor (REQ-MOD-005)
        try:
            with httpx.Client() as client:
                response = client.post(
                    ensure_endpoint,
                    json={"profile": model_to_use},
                    timeout=15.0
                )
                
                # Check for refusal / OOM (REQ-MOD-005 AC1)
                if response.status_code != 200:
                    raise OracleRefusalError(
                        f"Saltnitor refused profile load with status {response.status_code}: {response.text}"
                    )
                
                try:
                    resp_data = response.json()
                    if isinstance(resp_data, dict):
                        from typing import cast
                        resp_dict = cast(dict[str, Any], resp_data)
                        if resp_dict.get("status") == "refused" or "error" in resp_dict:
                            reason = resp_dict.get("reason") or resp_dict.get("error") or "OOM"
                            raise OracleRefusalError(f"Saltnitor refused profile load: {reason}")
                except ValueError:
                    # Status is 200 but not JSON; proceed
                    pass

        except (httpx.ConnectError, httpx.ConnectTimeout):
            # Saltnitor control API is offline/down, fallback to direct llama.cpp
            use_fallback = True

        # 2. Perform completion request
        completion_url = (
            f"{self.fallback_url}/v1/chat/completions"
            if use_fallback
            else f"{self.base_url}/v1/chat/completions"
        )

        payload: dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
            "temperature": 0.7 if thinking else 0.0,
        }

        if json_schema:
            payload["response_format"] = {
                "type": "json_object",
                "schema": json_schema
            }

        try:
            with httpx.Client() as client:
                response = client.post(
                    completion_url,
                    json=payload,
                    timeout=120.0  # Local models can take longer to generate
                )
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Local completion call failed with status code {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Local completion call failed: {e}") from e
