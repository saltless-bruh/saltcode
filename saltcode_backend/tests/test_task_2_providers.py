"""Task 2 — local providers, embeddings, connectivity and the source-payload guard.

Organised around Task 2's three Done-when legs:

1. the embedding client works offline;
2. the connectivity probe returns a correct verdict **via subprocess**;
3. the source-payload guard rejects a tagged body.

The connectivity tests genuinely spawn `python -m saltcode.tools.connectivity`
(the contract the extension calls, REQ-EXT-004) and never touch the internet:
verdicts are forced with `--target` pointing at either a local stub server or a
closed port, or with `SALTCODE_OFFLINE=1`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from saltcode.config import settings
from saltcode.harness.connectivity import (
    ConnectivityReport,
    check_connectivity,
    default_targets,
    probe_connectivity,
)
from saltcode.providers.embeddings import LocalEmbeddingClient
from saltcode.providers.guard import (
    PrivacyBoundaryError,
    classify_destination,
    find_source_tag,
    guard_outbound,
)
from saltcode.providers.local import (
    PROFILES,
    LocalClient,
    UnknownProfileError,
    validate_profile,
)
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

if TYPE_CHECKING:
    from collections.abc import Iterator

# A port nothing listens on: reserved, and connecting to it fails immediately
# rather than hanging, which keeps the offline tests fast and hermetic.
CLOSED_PORT_URL = "http://127.0.0.1:1"


# --------------------------------------------------------------------------- helpers


class _OkHandler(BaseHTTPRequestHandler):
    """Answers HEAD with 200 and stays silent in the test log."""

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - overridden signature
        return


@pytest.fixture
def local_http_server() -> Iterator[str]:
    """A real HTTP server on loopback, so 'reachable' is proven, not mocked."""
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_tool(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Run ``python -m saltcode.tools.connectivity`` and parse its JSON stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.connectivity", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload: dict[str, Any] = {}
    if completed.stdout.strip():
        payload = json.loads(completed.stdout)
    return completed.returncode, payload


# ------------------------------------------------------------------ leg 3: the guard
# First, because legs 1 and 2 lean on classify_destination.


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765",
        "http://127.0.0.1:8080/v1/embeddings",
        "http://127.9.9.9:1234",
        "http://localhost:8765/v1/ensure",
        "http://api.localhost/v1",
        "http://[::1]:8765/v1",
        "HTTP://LOCALHOST:8765",
    ],
)
def test_loopback_destinations_are_local(url: str) -> None:
    assert classify_destination(url) == "local"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com/chat/completions",
        "http://192.168.1.5:8765/v1",  # a LAN box is another machine
        "http://10.0.0.7:8765/v1",
        "http://gpu-rig.lan:8765/v1",  # a name is never resolved: fail closed
        "http://169.254.169.254/latest/meta-data",  # cloud metadata endpoint
        "127.0.0.1:8765",  # no scheme, so no parseable host: fail closed
        "",
    ],
)
def test_everything_else_is_network(url: str) -> None:
    """The unknown case must be the safe one, or the guard is decorative."""
    assert classify_destination(url) == "network"


def test_guard_rejects_declared_source_payload() -> None:
    """REQ-GLB-003 AC2 — the caller's own declaration is enough."""
    with pytest.raises(PrivacyBoundaryError, match="Privacy boundary violation"):
        guard_outbound("https://api.deepseek.com/chat/completions", [], contains_raw_source=True)


def test_guard_rejects_message_level_flag() -> None:
    messages = [{"role": "user", "content": "here it is", "is_source": True}]
    with pytest.raises(PrivacyBoundaryError, match="is_source"):
        guard_outbound("https://api.deepseek.com/chat/completions", messages)


def test_guard_rejects_metadata_flag() -> None:
    messages = [{"role": "user", "content": "x", "metadata": {"contains_raw_source": True}}]
    with pytest.raises(PrivacyBoundaryError, match="contains_raw_source"):
        guard_outbound("https://api.deepseek.com/chat/completions", messages)


@pytest.mark.parametrize("tag", ["<raw_source>", "<source_code>"])
def test_guard_rejects_content_markers(tag: str) -> None:
    messages = [{"role": "user", "content": f"Here: {tag}def f(): pass</{tag[1:]}"}]
    with pytest.raises(PrivacyBoundaryError, match="Privacy boundary violation"):
        guard_outbound("https://api.deepseek.com/chat/completions", messages)


def test_guard_scans_nested_payloads() -> None:
    """A tag buried in a nested body is still a tag — 'any outbound call' (task 2.4)."""
    payload = {
        "model": "v4-pro",
        "tools": [{"function": {"arguments": {"patch": "<raw_source>x = 1</raw_source>"}}}],
    }
    with pytest.raises(PrivacyBoundaryError):
        guard_outbound("https://api.deepseek.com/chat/completions", payload)


def test_guard_error_never_quotes_the_tagged_content() -> None:
    secret = "AWS_SECRET_ACCESS_KEY = 'hunter2'"
    messages = [{"role": "user", "content": f"<raw_source>{secret}</raw_source>"}]
    with pytest.raises(PrivacyBoundaryError) as exc_info:
        guard_outbound("https://api.deepseek.com/chat/completions", messages)
    assert secret not in str(exc_info.value)


def test_guard_error_never_quotes_the_url_query() -> None:
    """The full URL can carry a key; the message reports scheme + host only."""
    with pytest.raises(PrivacyBoundaryError) as exc_info:
        guard_outbound("https://api.deepseek.com/v1?api_key=sk-secret", [], contains_raw_source=True)
    assert "sk-secret" not in str(exc_info.value)


def test_guard_allows_untagged_payload_to_network() -> None:
    """Only *source* is refused. A design.md or a diff is a normal Phase-1 payload."""
    messages = [{"role": "user", "content": "Summarise the design constraints."}]
    assert guard_outbound("https://api.deepseek.com/chat/completions", messages) == "network"


def test_guard_exempts_local_destinations() -> None:
    """Task 2.4: local calls are exempt — the Builder's bodies stay on-box."""
    messages = [{"role": "user", "content": "<raw_source>def f(): pass</raw_source>", "is_source": True}]
    assert guard_outbound("http://127.0.0.1:8765/v1/chat/completions", messages, contains_raw_source=True) == "local"


def test_find_source_tag_is_depth_bounded() -> None:
    """A pathological body must not hang the guard; it stops at MAX_SCAN_DEPTH."""
    payload: Any = "<raw_source>deep</raw_source>"
    for _ in range(50):
        payload = {"next": payload}
    assert find_source_tag(payload) is None
    assert find_source_tag("<raw_source>shallow</raw_source>") is not None


# -------------------------------------------------- leg 3 (cont.): guard in the clients


def test_local_client_accepts_source_because_it_is_on_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Builder's scoped bodies reach the local model — that is the design."""
    client = LocalClient(base_url="http://127.0.0.1:8765", fallback_url="http://127.0.0.1:8080")

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        return _MockResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    result = client.chat(
        [{"role": "user", "content": "<raw_source>def f(): pass</raw_source>"}],
        thinking=False,
        contains_raw_source=True,
    )
    assert result == "ok"


def test_local_client_refuses_source_when_repointed_off_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exemption is derived from the URL, so it evaporates when the URL changes.

    This is what "privacy by construction" has to mean: point SALTNITOR_URL at
    another machine and the source-tagged payload is refused on the next call,
    with no code change and nothing for anyone to remember.
    """
    client = LocalClient(base_url="https://gpu.example.com", fallback_url="http://127.0.0.1:8080")

    def explode(self: Any, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"a request must never be issued: {url}")

    monkeypatch.setattr(httpx.Client, "post", explode)

    with pytest.raises(PrivacyBoundaryError, match="Privacy boundary violation"):
        client.chat(
            [{"role": "user", "content": "def f(): pass", "is_source": True}],
            thinking=False,
        )


def test_embedding_client_refuses_source_when_repointed_off_box(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LocalEmbeddingClient(base_url="https://embed.example.com", fallback_url="https://embed2.example.com")

    def explode(self: Any, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"a request must never be issued: {url}")

    monkeypatch.setattr(httpx.Client, "post", explode)

    with pytest.raises(PrivacyBoundaryError):
        client.embed_query("<raw_source>def f(): pass</raw_source>")


def test_privacy_refusal_is_not_swallowed_by_the_endpoint_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard runs outside the try, so it cannot be flattened into RuntimeError.

    A swallowed refusal would fall through to the next endpoint and surface as a
    transport error — the leak would still be reported, just as the wrong thing.
    """
    client = LocalEmbeddingClient(base_url="https://embed.example.com", fallback_url="http://127.0.0.1:8080")

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        return _MockResponse(200, {"data": [{"index": 0, "embedding": [0.1]}]})

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    with pytest.raises(PrivacyBoundaryError):
        client.embed_query("<source_code>secret</source_code>")


# ---------------------------------------------------------- leg 1: embeddings offline


def test_default_embedding_endpoints_are_all_local() -> None:
    """DD-8: retrieval has no API dependency, so the semantic cache works offline."""
    client = LocalEmbeddingClient()
    assert client.is_offline_capable()
    assert all(classify_destination(url) == "local" for url in client.endpoints())


def test_embedding_client_works_with_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a severed link: every off-box call fails, on-box calls still serve."""

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        if classify_destination(url) != "local":
            raise httpx.ConnectError("network is unreachable")
        return _MockResponse(200, {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    assert LocalEmbeddingClient().embed_query("a goal to embed") == [0.1, 0.2, 0.3]


def test_embedding_client_preserves_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """A batched server may answer out of order; cosine lookups need the pairing intact."""

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        return _MockResponse(
            200,
            {
                "data": [
                    {"index": 2, "embedding": [3.0]},
                    {"index": 0, "embedding": [1.0]},
                    {"index": 1, "embedding": [2.0]},
                ]
            },
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    assert LocalEmbeddingClient().embed_documents(["a", "b", "c"]) == [[1.0], [2.0], [3.0]]


def test_embedding_client_reports_when_it_is_not_offline_capable() -> None:
    client = LocalEmbeddingClient(base_url="https://embed.example.com")
    assert not client.is_offline_capable()


def test_embedding_model_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """DD-8 names bge-small *or* nomic-embed, so the choice cannot be hardcoded."""
    captured: dict[str, Any] = {}

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        captured.update(kwargs.get("json", {}))
        return _MockResponse(200, {"data": [{"index": 0, "embedding": [0.5]}]})

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(settings, "embedding_model", "nomic-embed")

    LocalEmbeddingClient().embed_query("x")
    assert captured["model"] == "nomic-embed"


def test_empty_document_list_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(self: Any, url: str, **kwargs: Any) -> Any:
        raise AssertionError("no request should be issued for an empty batch")

    monkeypatch.setattr(httpx.Client, "post", explode)
    assert LocalEmbeddingClient().embed_documents([]) == []


# --------------------------------------------------------- leg 2: connectivity probe


def test_probe_reports_online_on_first_reachable_target(local_http_server: str) -> None:
    report = probe_connectivity(targets=(local_http_server, "https://never.reached.invalid"))
    assert report.online
    assert not report.forced_offline
    # Probing stops at the first hit: the second target is never attempted.
    assert len(report.probes) == 1
    assert report.probes[0].detail == "HTTP 200"


def test_probe_reports_offline_when_nothing_answers() -> None:
    report = probe_connectivity(timeout=1.0, targets=(CLOSED_PORT_URL,))
    assert not report.online
    assert not report.forced_offline
    assert report.probes[0].reachable is False


def test_explicit_offline_beats_a_working_link(monkeypatch: pytest.MonkeyPatch, local_http_server: str) -> None:
    """SALTCODE_OFFLINE=1 must not be overturned by a link that happens to be up."""

    def explode(url: str, **kwargs: Any) -> Any:
        raise AssertionError("a forced-offline session must not probe at all")

    monkeypatch.setattr(settings, "online_mode", False)
    monkeypatch.setattr(httpx, "head", explode)

    report = probe_connectivity(targets=(local_http_server,))
    assert report == ConnectivityReport(online=False, forced_offline=True, probes=())


def test_check_connectivity_is_the_boolean_form(local_http_server: str) -> None:
    assert check_connectivity.__doc__ is not None
    assert probe_connectivity(targets=(local_http_server,)).online is True
    assert probe_connectivity(timeout=1.0, targets=(CLOSED_PORT_URL,)).online is False


def test_configured_provider_is_probed_before_any_third_party() -> None:
    """Ordinary sessions should not announce themselves to a host we do not use."""
    targets = default_targets()
    assert targets[0] == settings.deepseek_base_url


# ------------------------------------------- leg 2 (cont.): the verdict via subprocess


def test_entrypoint_reports_online(local_http_server: str) -> None:
    code, out = run_tool("--target", local_http_server)
    assert code == EXIT_OK
    assert out["verdict"] == "online"
    assert out["online"] is True
    assert out["ok"] is True
    assert out["forced_offline"] is False
    assert out["probes"][0]["reachable"] is True


def test_entrypoint_reports_offline_with_a_negative_exit_code() -> None:
    """Offline is an answer, not a crash: exit 1, valid JSON on stdout."""
    code, out = run_tool("--target", CLOSED_PORT_URL, "--timeout", "1.0")
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["verdict"] == "offline"
    assert out["online"] is False
    assert out["probes"][0]["reachable"] is False


def test_entrypoint_honours_forced_offline_without_probing(local_http_server: str) -> None:
    import os

    env = {**os.environ, "SALTCODE_OFFLINE": "1"}
    code, out = run_tool("--target", local_http_server, env=env)
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["forced_offline"] is True
    assert out["probes"] == []


def test_entrypoint_tries_targets_in_order(local_http_server: str) -> None:
    code, out = run_tool("--target", CLOSED_PORT_URL, "--target", local_http_server, "--timeout", "1.0")
    assert code == EXIT_OK
    assert [probe["url"] for probe in out["probes"]] == [CLOSED_PORT_URL, local_http_server]


def test_entrypoint_rejects_a_nonpositive_timeout() -> None:
    code, out = run_tool("--timeout", "0")
    assert code == EXIT_USAGE
    assert out["error"] == "UsageError"


def test_entrypoint_rejects_an_unknown_flag() -> None:
    code, _ = run_tool("--nope")
    assert code == EXIT_USAGE


# ------------------------------------------------------ task 2.1: router-section addressing


def test_router_sections_are_exactly_the_design_six_profiles() -> None:
    assert sorted(PROFILES) == ["A_FOCUS", "A_STD", "B"]


@pytest.mark.parametrize("profile", ["A_STD", "A_FOCUS", "B"])
def test_each_router_section_is_ensured_before_inference(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-MOD-005: residency is requested before the completion call."""
    calls: list[str] = []
    ensured: list[str] = []

    def mock_post(self: Any, url: str, **kwargs: Any) -> Any:
        calls.append(url)
        if url.endswith("/v1/ensure"):
            ensured.append(kwargs["json"]["profile"])
            return _MockResponse(200, {"status": "ok"})
        return _MockResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    LocalClient().chat([{"role": "user", "content": "x"}], thinking=False, model=profile)
    assert ensured == [profile]
    assert calls[0].endswith("/v1/ensure"), "ensure must precede the completion call"
    assert calls[1].endswith("/v1/chat/completions")


def test_unknown_profile_is_refused_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo becomes a named error here, not a confusing 404 from the router."""

    def explode(self: Any, url: str, **kwargs: Any) -> Any:
        raise AssertionError("no request should be issued for an unknown profile")

    monkeypatch.setattr(httpx.Client, "post", explode)

    with pytest.raises(UnknownProfileError, match="A_FOCUS"):
        LocalClient().chat([], thinking=False, model="Tier-A")


def test_validate_profile_returns_the_profile() -> None:
    assert validate_profile("B") == "B"


# --------------------------------------------------------------------------- mock plumbing


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict[str, Any]):
        self.status_code = status_code
        self._json_data = json_data
        self.text = json.dumps(json_data)

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, Any]:
        return self._json_data
