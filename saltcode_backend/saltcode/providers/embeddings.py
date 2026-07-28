"""Local embedding client for the semantic cache and notes RAG (task 2.2, DD-8).

Embeddings are deliberately *not* an API dependency: the semantic cache
(REQ-CACHE-003) and the Atomic-Notes auto-RAG (REQ-MEM-001) both run at session
open, and Saltcode's offline path (design §12) is a first-class mode, not a
degraded one. So retrieval is served by a local model — `bge-small` or
`nomic-embed` — behind the same OpenAI-compatible surface Saltnitor already
speaks, with direct llama.cpp at `:8080` as the fallback.

:meth:`LocalEmbeddingClient.is_offline_capable` reports whether *every*
configured endpoint is on-box. It is what makes "works offline" a fact the test
suite checks rather than a property of the default config that quietly stops
holding when someone edits an environment variable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from saltcode.config import settings
from saltcode.providers.guard import classify_destination, guard_outbound

EMBEDDING_TIMEOUT_SECONDS = 30.0


class EmbeddingClient(ABC):
    """Abstract base class defining the interface for embedding generation."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Generates embedding representation for a query string."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generates embedding representations for a list of document strings."""


class LocalEmbeddingClient(EmbeddingClient):
    """Local embedding generator using the Saltnitor / llama.cpp embeddings endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        fallback_url: str | None = None,
        model_name: str | None = None,
    ):
        self.base_url = (base_url or settings.saltnitor_url).rstrip("/")
        self.fallback_url = (fallback_url or settings.llamacpp_fallback_url).rstrip("/")
        self.model_name = model_name or settings.embedding_model

    def endpoints(self) -> tuple[str, ...]:
        """The endpoints tried in order: Saltnitor first, then direct llama.cpp."""
        return (
            f"{self.base_url}/v1/embeddings",
            f"{self.fallback_url}/v1/embeddings",
        )

    def is_offline_capable(self) -> bool:
        """True when every configured endpoint is on-box, so no link is needed (DD-8)."""
        return all(classify_destination(url) == "local" for url in self.endpoints())

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        last_error: Exception | None = None

        for url in self.endpoints():
            # Outside the try: a privacy refusal is not a transport failure and
            # must not be retried against the next endpoint or flattened into
            # the RuntimeError below.
            guard_outbound(url, texts)

            try:
                with httpx.Client() as client:
                    response = client.post(
                        url,
                        json={"input": texts, "model": self.model_name},
                        timeout=EMBEDDING_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()
                    data = response.json()
                    # Standard response shape: {"data": [{"index": n, "embedding": [...]}, ...]}.
                    # Sorted by index because the ordering must match `texts`, and
                    # a batched server is free to answer out of order.
                    sorted_data = sorted(data["data"], key=lambda item: _index_of(item))
                    return [item["embedding"] for item in sorted_data]
            except Exception as e:
                # Any failure here means one thing: try the next endpoint.
                last_error = e
                continue

        raise RuntimeError(f"Failed to generate embeddings from local endpoints. Last error: {last_error}")

    def embed_query(self, text: str) -> list[float]:
        return self._get_embeddings([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._get_embeddings(texts)


def _index_of(item: Any) -> int:
    """Sort key for an embeddings response entry; entries without an index keep server order."""
    return int(item.get("index", 0))
