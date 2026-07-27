from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Abstract base class defining the provider-agnostic interface for LLM client providers."""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        contains_raw_source: bool = False,
    ) -> str:
        """Sends a chat completion request to the model provider.

        Args:
            messages: A list of message dictionaries (e.g. [{"role": "user", "content": "..."}]).
            thinking: Whether to enable thinking/reasoning mode.
            json_schema: Optional JSON schema to enforce structured output.
            model: Optional model or profile identifier override.
            contains_raw_source: True if the payload contains raw source code from the repository.
                                 Must be rejected by construction for API-routed providers.

        Returns:
            The raw text response from the model.
        """
        pass
