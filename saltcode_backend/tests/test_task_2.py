from typing import Any

import httpx
import pytest

from saltcode.config import settings
from saltcode.harness.connectivity import check_connectivity
from saltcode.providers.base import LLMClient
from saltcode.providers.deepseek import DeepSeekClient
from saltcode.providers.embeddings import LocalEmbeddingClient
from saltcode.providers.local import LocalClient, OracleRefusalError


# 1. Test Base Interface
def test_base_interface() -> None:
    # Cannot instantiate abstract class
    with pytest.raises(TypeError):
        LLMClient()  # type: ignore

    class DummyClient(LLMClient):
        def chat(
            self,
            messages: list[dict[str, Any]],
            *,
            thinking: bool,
            json_schema: dict[str, Any] | None = None,
            model: str | None = None,
            contains_raw_source: bool = False,
        ) -> str:
            return "dummy"

    client = DummyClient()
    assert client.chat([], thinking=False) == "dummy"


# 2. Test DeepSeek Client Privacy Guard (REQ-GLB-003)
def test_deepseek_privacy_guard() -> None:
    client = DeepSeekClient(api_key="mock-key")

    # Rejected via contains_raw_source parameter
    with pytest.raises(ValueError, match="Privacy boundary violation"):
        client.chat([], thinking=False, contains_raw_source=True)

    # Rejected via is_source message metadata
    with pytest.raises(ValueError, match="Privacy boundary violation"):
        client.chat([{"role": "user", "content": "hello", "is_source": True}], thinking=False)

    # Rejected via metadata dict
    with pytest.raises(ValueError, match="Privacy boundary violation"):
        client.chat([{"role": "user", "content": "hello", "metadata": {"contains_raw_source": True}}], thinking=False)

    # Rejected via tags in content
    with pytest.raises(ValueError, match="Privacy boundary violation"):
        client.chat([{"role": "user", "content": "Here is <raw_source>print('hello')</raw_source>"}], thinking=False)

    # Rejected via another tag
    with pytest.raises(ValueError, match="Privacy boundary violation"):
        client.chat([{"role": "user", "content": "Here is <source_code>def f(): pass</source_code>"}], thinking=False)


# 3. Test DeepSeek Client Message Ordering Check
def test_deepseek_prefix_cache_ordering() -> None:
    client = DeepSeekClient(api_key="mock-key")

    # User-before-system is prefix-cache-unfriendly and must be rejected before
    # any request is issued.
    with pytest.raises(ValueError) as exc_info:
        client.chat(
            [
                {"role": "user", "content": "user query"},
                {"role": "system", "content": "system prompt"},
            ],
            thinking=False,
            contains_raw_source=False
        )
    assert "Prefix-cache-unfriendly ordering" in str(exc_info.value)


# 4. Test DeepSeek Client Payload Construction & Param Stripping (Thinking Mode)
def test_deepseek_payload_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DeepSeekClient(api_key="mock-key")
    
    # Store sent payload
    captured_payload: dict[str, Any] = {}

    class MockResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "mock-response"}}]}

    def mock_post(self, url: str, **kwargs: Any) -> MockResponse:
        nonlocal captured_payload
        captured_payload = kwargs.get("json", {})
        return MockResponse()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    # Non-thinking call
    res = client.chat([{"role": "user", "content": "hello"}], thinking=False)
    assert res == "mock-response"
    assert captured_payload["model"] == settings.deepseek_chat_model
    assert "temperature" in captured_payload
    assert captured_payload["temperature"] == 0.2

    # Thinking call: reasoning model selected and temp/top_p stripped
    captured_payload.clear()
    res_think = client.chat([{"role": "user", "content": "hello"}], thinking=True)
    assert res_think == "mock-response"
    assert captured_payload["model"] == settings.deepseek_reasoning_model
    assert "temperature" not in captured_payload


# 5. Test Local Client Saltnitor Flow (REQ-MOD-005)
def test_local_client_saltnitor_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LocalClient()
    ensure_called = False
    completion_called = False
    captured_profile = None

    class MockResponse:
        def __init__(self, status_code: int, json_data: dict[str, Any]):
            self.status_code = status_code
            self._json_data = json_data
            self.text = str(json_data)

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return self._json_data

    def mock_post(self, url: str, **kwargs: Any) -> MockResponse:
        nonlocal ensure_called, completion_called, captured_profile
        if url.endswith("/v1/ensure"):
            ensure_called = True
            captured_profile = kwargs.get("json", {}).get("profile")
            return MockResponse(200, {"status": "success"})
        if url.endswith("/v1/chat/completions"):
            completion_called = True
            return MockResponse(200, {"choices": [{"message": {"content": "local-response"}}]})
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    res = client.chat([{"role": "user", "content": "hello"}], thinking=False, model="B")
    assert res == "local-response"
    assert ensure_called
    assert completion_called
    assert captured_profile == "B"


# 6. Test Local Client Saltnitor OOM Refusal (REQ-MOD-005 AC1)
def test_local_client_saltnitor_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LocalClient()

    class MockResponse:
        status_code = 500
        text = "Out of Memory"
        def raise_for_status(self) -> None:
            pass

    def mock_post_fail(self, url: str, **kwargs: Any) -> MockResponse:
        if url.endswith("/v1/ensure"):
            return MockResponse()
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post_fail)

    with pytest.raises(OracleRefusalError, match="Saltnitor refused profile load"):
        client.chat([{"role": "user", "content": "hello"}], thinking=False)

    # Test refusal via JSON payload response
    class MockOOMJSONResponse:
        status_code = 200
        text = "Refused"
        def json(self) -> dict[str, Any]:
            return {"status": "refused", "reason": "VRAM OOM"}
        def raise_for_status(self) -> None:
            pass

    def mock_post_json_refuse(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/v1/ensure"):
            return MockOOMJSONResponse()
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post_json_refuse)
    with pytest.raises(OracleRefusalError, match="Saltnitor refused profile load: VRAM OOM"):
        client.chat([{"role": "user", "content": "hello"}], thinking=False)


# 7. Test Local Client Fallback Path
def test_local_client_fallback_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LocalClient()
    completion_called_on_fallback = False

    class MockResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "fallback-response"}}]}

    def mock_post(self, url: str, **kwargs: Any) -> MockResponse:
        nonlocal completion_called_on_fallback
        if url.endswith("/v1/ensure"):
            # Simulate connection error to Saltnitor
            raise httpx.ConnectError("Connection refused")
        if url.startswith(settings.llamacpp_fallback_url) and url.endswith("/v1/chat/completions"):
            completion_called_on_fallback = True
            return MockResponse()
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    res = client.chat([{"role": "user", "content": "hello"}], thinking=False)
    assert res == "fallback-response"
    assert completion_called_on_fallback


# 8. Test Local Embeddings
def test_local_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    embed_client = LocalEmbeddingClient()
    primary_called = False
    fallback_called = False

    class MockResponse:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, Any]:
            return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}

    # First test: primary endpoint works
    def mock_post_primary(self, url: str, **kwargs: Any) -> MockResponse:
        nonlocal primary_called
        if url.startswith(settings.saltnitor_url):
            primary_called = True
            return MockResponse()
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post_primary)
    emb = embed_client.embed_query("test text")
    assert emb == [0.1, 0.2, 0.3]
    assert primary_called

    # Second test: primary endpoint down, fallback works
    primary_called = False
    def mock_post_fallback(self, url: str, **kwargs: Any) -> MockResponse:
        nonlocal primary_called, fallback_called
        if url.startswith(settings.saltnitor_url):
            primary_called = True
            raise httpx.ConnectError("Saltnitor down")
        if url.startswith(settings.llamacpp_fallback_url):
            fallback_called = True
            return MockResponse()
        raise ValueError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx.Client, "post", mock_post_fallback)
    emb_doc = embed_client.embed_documents(["doc1"])
    assert emb_doc == [[0.1, 0.2, 0.3]]
    assert primary_called
    assert fallback_called


# 9. Test Connectivity Probe (REQ-GATE-001)
def test_connectivity_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Both down -> offline (False)
    def mock_head_fail(url: str, **kwargs: Any) -> Any:
        raise httpx.RequestError("Connection timed out")

    monkeypatch.setattr(httpx, "head", mock_head_fail)
    assert not check_connectivity()

    # 2. At least one up -> online (True)
    class MockHeadResponse:
        status_code = 200

    def mock_head_success(url: str, **kwargs: Any) -> MockHeadResponse:
        if "google.com" in url:
            return MockHeadResponse()
        raise httpx.RequestError("Failed")

    monkeypatch.setattr(httpx, "head", mock_head_success)
    assert check_connectivity()
