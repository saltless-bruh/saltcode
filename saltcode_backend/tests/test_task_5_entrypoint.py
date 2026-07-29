"""Task 5.7 — `saltcode.tools.cache_lookup` over a real subprocess.

Every test here spawns the interpreter the way Pi will
(`pi.exec("python", ["-m", "saltcode.tools.cache_lookup", ...])`) and asserts on the
JSON contract and exit code, because that pair *is* the interface the extension's tool
wrapper binds to (REQ-EXT-004).

The semantic tier needs an embedding endpoint and this host has none, so the tests that
exercise it stand up a **real HTTP server on loopback** speaking the OpenAI embeddings
shape and point the subprocess at it with `SALTNITOR_URL`. That is the pattern
`tests/test_task_2_providers.py` established: a stub that is genuinely served, never a
patched client — the subprocess has its own interpreter and cannot see a monkeypatch.
No test touches the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from saltcode.contracts.tasks import Task, TasksFile
from saltcode.memory.spec_cache import store_spec
from saltcode.thresholds import DEFAULTS, THRESHOLD_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]


class _EmbeddingHandler(BaseHTTPRequestHandler):
    """Answers /v1/embeddings with three orthogonal axes, matching the in-process double."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length", "0"))
        payload: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")
        texts: list[str] = list(payload.get("input", []))

        data: list[dict[str, Any]] = []
        for index, text in enumerate(texts):
            lowered = str(text).lower()
            if "auth" in lowered or "login" in lowered or "signup" in lowered:
                vector = [1.0, 0.0, 0.0]
            elif "database" in lowered or "sql" in lowered:
                vector = [0.0, 1.0, 0.0]
            else:
                vector = [0.0, 0.0, 1.0]
            data.append({"index": index, "embedding": vector})

        body = json.dumps({"data": data}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature is fixed
        """Silence the default stderr access log."""


@pytest.fixture
def embedding_server() -> Iterator[str]:
    """A real embeddings endpoint on loopback, so 'it works offline' is proven."""
    server = HTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def run_tool(*args: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any], str]:
    """Invoke the entrypoint exactly as Pi would, and parse its stdout contract."""
    child_env = dict(os.environ)
    child_env.setdefault("SALTCODE_OFFLINE", "1")
    if env:
        child_env.update(env)

    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.cache_lookup", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=child_env,
        timeout=180,
        check=False,
    )
    payload: dict[str, Any] = json.loads(completed.stdout)
    return completed.returncode, payload, completed.stderr


def one_task_file(task_id: str = "T1", files: list[str] | None = None) -> TasksFile:
    return TasksFile(
        schema_version="1.0",
        tasks=[
            Task(
                id=task_id,
                description="a task",
                files_affected=files if files is not None else ["src/auth.py"],
                acceptance_criteria=["it works"],
                depends_on=[],
                complexity="low",
            )
        ],
    )


# --------------------------------------------------------------------------------------
# The Done-when leg: a hit/miss verdict via subprocess
# --------------------------------------------------------------------------------------


def test_an_exact_hit_exits_zero_and_returns_the_plan(tmp_path: Path) -> None:
    """REQ-CACHE-001 AC1 — reuse tasks.json with zero API calls."""
    store_spec(tmp_path, "implement auth", one_task_file())

    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "implement auth", "--scope", "src/auth.py")

    assert code == 0
    assert payload["verdict"] == "exact_hit"
    assert payload["reuse_authorized"] is True
    assert payload["tasks"]["tasks"][0]["id"] == "T1"
    assert payload["scope_source"] == "provided"


def test_a_miss_exits_one_with_a_verdict_not_a_crash(tmp_path: Path) -> None:
    """Exit 1 is the negative *verdict* — stdout still holds valid JSON."""
    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "never planned this")

    assert code == 1
    assert payload["ok"] is True
    assert payload["verdict"] == "miss"
    assert payload["reuse_authorized"] is False
    assert "fire Phase 1" in payload["detail"]


def test_the_goal_is_normalized_the_same_way_at_both_ends(tmp_path: Path) -> None:
    store_spec(tmp_path, "Implement Auth", one_task_file())
    code, payload, _ = run_tool(
        "--repo", str(tmp_path), "--goal", "  implement auth  ", "--scope", "src/auth.py"
    )
    assert code == 0
    assert payload["verdict"] == "exact_hit"


def test_scope_order_does_not_change_the_verdict(tmp_path: Path) -> None:
    store_spec(tmp_path, "goal", one_task_file(files=["a.py", "b.py"]))

    code, payload, _ = run_tool(
        "--repo", str(tmp_path), "--goal", "goal", "--scope", "b.py", "--scope", "a.py"
    )
    assert code == 0
    assert payload["verdict"] == "exact_hit"


# --------------------------------------------------------------------------------------
# Usage and error handling
# --------------------------------------------------------------------------------------


def test_a_missing_goal_is_a_usage_error(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.cache_lookup", "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 2


def test_an_empty_goal_is_a_usage_error(tmp_path: Path) -> None:
    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "   ")
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"] == "InputError"


def test_a_missing_repo_is_an_internal_error(tmp_path: Path) -> None:
    code, payload, _ = run_tool("--repo", str(tmp_path / "nope"), "--goal", "goal")
    assert code == 3
    assert payload["ok"] is False


def test_stdout_is_exactly_one_json_object(tmp_path: Path) -> None:
    """The extension parses stdout wholesale; a stray print would break the contract."""
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.cache_lookup", "--repo", str(tmp_path), "--goal", "g"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=180,
        check=False,
    )
    parsed = json.loads(completed.stdout)
    assert parsed["tool"] == "cache_lookup"


# --------------------------------------------------------------------------------------
# The ladder: order, short-circuit, and the semantic tier
# --------------------------------------------------------------------------------------


def test_an_exact_hit_never_consults_the_semantic_tier(tmp_path: Path) -> None:
    """REQ-CACHE-001 AC1 — stop at the first hit.

    Proven by the absence of an embedding endpoint: if the ladder had continued, the
    semantic tier would have reported itself unavailable.
    """
    store_spec(tmp_path, "implement auth", one_task_file())
    code, payload, _ = run_tool(
        "--repo", str(tmp_path), "--goal", "implement auth", "--scope", "src/auth.py"
    )
    assert code == 0
    assert "semantic" not in payload


def test_a_semantic_candidate_is_not_authorized_for_reuse(
    tmp_path: Path, embedding_server: str
) -> None:
    """REQ-CACHE-003 AC1 — reuse requires an Architect confirmation the backend cannot run."""
    from saltcode.memory.semantic_cache import store_semantic_spec
    from saltcode.providers.embeddings import LocalEmbeddingClient

    client = LocalEmbeddingClient(base_url=embedding_server, fallback_url=embedding_server)
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], one_task_file(), client)

    code, payload, _ = run_tool(
        "--repo",
        str(tmp_path),
        "--goal",
        "add auth to login",
        "--scope",
        "src/auth.py",
        env={"SALTNITOR_URL": embedding_server, "LLAMACPP_FALLBACK_URL": embedding_server},
    )

    assert code == 0
    assert payload["verdict"] == "semantic_candidate"
    assert payload["reuse_authorized"] is False
    # Uncalibrated bars force maximum skepticism (REQ-CACHE-003 AC5).
    assert payload["confirmation"] == "full"
    assert payload["semantic"]["status"] == "ran"
    assert payload["tasks"]["tasks"][0]["id"] == "T1"


def test_an_unrelated_goal_falls_through_to_phase_1(
    tmp_path: Path, embedding_server: str
) -> None:
    from saltcode.memory.semantic_cache import store_semantic_spec
    from saltcode.providers.embeddings import LocalEmbeddingClient

    client = LocalEmbeddingClient(base_url=embedding_server, fallback_url=embedding_server)
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], one_task_file(), client)

    code, payload, _ = run_tool(
        "--repo",
        str(tmp_path),
        "--goal",
        "reverse a string helper",
        "--scope",
        "src/util.py",
        env={"SALTNITOR_URL": embedding_server, "LLAMACPP_FALLBACK_URL": embedding_server},
    )

    assert code == 1
    assert payload["verdict"] == "miss"
    assert payload["semantic"]["status"] == "ran"
    assert payload["semantic"]["hit"] is False


def test_an_unreachable_embedding_endpoint_degrades_instead_of_failing(tmp_path: Path) -> None:
    """A dead endpoint must still yield a usable miss, not a failed tool call.

    `/sprint` has to decide whether to fire Phase 1; an exception leaves it unable to.
    """
    code, payload, _ = run_tool(
        "--repo",
        str(tmp_path),
        "--goal",
        "some goal",
        # Port 1 is reserved and never listening.
        env={"SALTNITOR_URL": "http://127.0.0.1:1", "LLAMACPP_FALLBACK_URL": "http://127.0.0.1:1"},
    )

    assert code == 1
    assert payload["ok"] is True
    assert payload["verdict"] == "miss"
    assert payload["semantic"]["status"] == "unavailable"


def test_exact_only_skips_the_semantic_tier(tmp_path: Path) -> None:
    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "some goal", "--exact-only")
    assert code == 1
    assert payload["semantic"]["status"] == "skipped"


# --------------------------------------------------------------------------------------
# Explainability and calibration reporting
# --------------------------------------------------------------------------------------


def test_a_miss_reports_the_key_and_fingerprint_it_used(tmp_path: Path) -> None:
    """G-004: `/sprint` can only reuse a --scope it can see."""
    (tmp_path / "auth.py").write_text("x = 1", encoding="utf-8")

    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "goal")
    assert code == 1
    assert payload["scope_source"] == "probed"
    assert payload["scope_fingerprint"] == ["auth.py"]
    assert len(payload["key"]) == 64
    assert "no entry" in payload["exact"]["detail"]


def test_an_empty_repo_reports_a_goal_only_key(tmp_path: Path) -> None:
    """REQ-CACHE-002 AC3."""
    code, payload, _ = run_tool("--repo", str(tmp_path), "--goal", "goal")
    assert code == 1
    assert payload["scope_source"] == "empty"
    assert payload["scope_fingerprint"] == []


def test_uncalibrated_thresholds_are_reported_and_warned(tmp_path: Path) -> None:
    """REQ-CAL-001 AC2 — the extension surfaces this at session open."""
    code, payload, stderr = run_tool("--repo", str(tmp_path), "--goal", "goal")

    assert code == 1
    thresholds = payload["thresholds"]
    assert thresholds["fully_calibrated"] is False
    assert set(thresholds["uncalibrated"]) == set(THRESHOLD_NAMES)
    for name in THRESHOLD_NAMES:
        assert thresholds["values"][name]["value"] == DEFAULTS[name]
        assert thresholds["values"][name]["calibrated"] is False
    assert "UNCALIBRATED" in stderr


def test_calibrated_thresholds_are_reported_as_such(tmp_path: Path) -> None:
    directory = tmp_path / ".saltcode" / "calibration"
    directory.mkdir(parents=True)
    (directory / "thresholds.json").write_text(
        json.dumps({"thresholds": {name: DEFAULTS[name] for name in THRESHOLD_NAMES}}),
        encoding="utf-8",
    )

    code, payload, stderr = run_tool("--repo", str(tmp_path), "--goal", "goal")
    assert code == 1
    assert payload["thresholds"]["fully_calibrated"] is True
    assert payload["thresholds"]["values"]["semantic_cosine_threshold"]["source"] == "calibration"
    assert "UNCALIBRATED" not in stderr
