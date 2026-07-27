from abc import ABC, abstractmethod

import httpx

from saltcode.config import settings


class EmbeddingClient(ABC):
    """Abstract base class defining the interface for embedding generation."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Generates embedding representation for a query string."""
        pass

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generates embedding representations for a list of document strings."""
        pass


class LocalEmbeddingClient(EmbeddingClient):
    """Local embedding generator client using Saltnitor / llama.cpp embeddings endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        fallback_url: str | None = None,
        model_name: str = "bge-small",
    ):
        self.base_url = (base_url or settings.saltnitor_url).rstrip("/")
        self.fallback_url = (fallback_url or settings.llamacpp_fallback_url).rstrip("/")
        self.model_name = model_name

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        # Primary endpoint uses /v1/embeddings under Saltnitor or llama.cpp fallback
        urls = [
            f"{self.base_url}/v1/embeddings",
            f"{self.fallback_url}/v1/embeddings",
        ]

        last_error = None
        for url in urls:
            try:
                with httpx.Client() as client:
                    response = client.post(
                        url,
                        json={"input": texts, "model": self.model_name},
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    # Standard response format is: {"data": [{"embedding": [...]}, ...]}
                    # Sort by index to guarantee ordering matches the input texts
                    sorted_data = sorted(data["data"], key=lambda x: x.get("index", 0))
                    return [item["embedding"] for item in sorted_data]
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(
            f"Failed to generate embeddings from local endpoints. Last error: {last_error}"
        )

    def embed_query(self, text: str) -> list[float]:
        return self._get_embeddings([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._get_embeddings(texts)
