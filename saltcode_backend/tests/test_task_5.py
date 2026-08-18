"""Task 5 — the carried-over standalone-build tests, corrected for v9.

Kept as the smoke-level pass over the memory layer; the leg-by-leg proofs live in
`test_task_5_caches.py`, `test_task_5_notes.py` and `test_task_5_entrypoint.py`.

Two assertions here were wrong before this task and are corrected:

* `test_spec_cache` claimed the probe finds `src/auth.py` "since goal mentions auth.py".
  That was the goal-filtering probe removed by **G-C03** — the goal is no longer an
  input to the fingerprint at all. The old assertion passed only because the temporary
  repo happened to contain exactly one source file, so the probe's answer coincided with
  the plan's `files_affected`. It is rewritten to say what actually holds.
* `test_calibration_warning` mutated the process-wide `settings.calibrated`, which
  leaked into any test running after it. Calibration is now per threshold and per
  workspace (`saltcode.thresholds`), so the check is workspace-scoped.
"""

import logging
from pathlib import Path

import pytest

from saltcode.config import check_calibration, settings
from saltcode.contracts.tasks import Task, TasksFile
from saltcode.memory.atomic_notes import (
    add_note,
    clear_session_notes,
    get_frozen_notes,
    initialize_session_notes,
)
from saltcode.memory.lancedb_store import init_db
from saltcode.memory.semantic_cache import lookup_semantic, store_semantic_spec
from saltcode.memory.skills import add_skill, search_skills
from saltcode.memory.spec_cache import lookup_spec, lookup_spec_result, store_spec
from saltcode.providers.embeddings import EmbeddingClient


# A deterministic mock embedding client for unit testing offline
class MockEmbeddingClient(EmbeddingClient):
    def embed_query(self, text: str) -> list[float]:
        # Return perpendicular unit vectors for distinct categories
        text_lower = text.lower()
        if "auth" in text_lower or "login" in text_lower or "signup" in text_lower:
            return [1.0, 0.0, 0.0]
        if "database" in text_lower or "sql" in text_lower:
            return [0.0, 1.0, 0.0]
        if "new" in text_lower:
            return [1.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


@pytest.fixture
def mock_embedding() -> MockEmbeddingClient:
    return MockEmbeddingClient()


@pytest.fixture(autouse=True)
def _thaw_session_notes() -> None:
    """The frozen-notes in-process cache is global; leaking it between tests fakes a freeze."""
    clear_session_notes()


def test_init_db(tmp_path: Path, mock_embedding: MockEmbeddingClient) -> None:
    db = init_db(tmp_path, mock_embedding)
    assert "spec_cache" in db
    assert "semantic_cache" in db
    assert "notes" in db
    assert "skills" in db


def test_spec_cache(tmp_path: Path) -> None:
    tasks_file = TasksFile(
        schema_version="1.0",
        tasks=[
            Task(
                id="T1",
                description="Task 1",
                files_affected=["src/auth.py"],
                acceptance_criteria=[],
                depends_on=[],
                complexity="low",
            )
        ],
    )

    goal = "implement auth.py"
    store_spec(tmp_path, goal, tasks_file)

    # Exact lookup works when the caller passes the scope the plan was stored under.
    res = lookup_spec(tmp_path, goal, ["src/auth.py"])
    assert res is not None
    assert res.tasks[0].id == "T1"

    # Lookup with a different scope misses (REQ-CACHE-002 AC1).
    assert lookup_spec(tmp_path, goal, ["src/signup.py"]) is None

    # With no --scope the probe supplies the fingerprint. It enumerates the repo's
    # source modules and ignores the goal entirely (G-C03), so this hits only because
    # the probe's answer happens to equal the plan's files_affected — one file, the
    # same one. That coincidence is exactly what Trade C5 says will usually NOT hold;
    # test_trade_c5_the_two_fingerprints_differ_by_design covers the general case.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("pass", encoding="utf-8")

    probed = lookup_spec_result(tmp_path, goal)
    assert probed.scope_source == "probed"
    assert probed.scope_fingerprint == ["src/auth.py"]
    assert probed.tasks is not None
    assert probed.tasks.tasks[0].id == "T1"

    # A differently-phrased goal over the same tree is a different key — the goal is
    # hashed in separately, and the probe never sees it.
    assert lookup_spec(tmp_path, "implement authentication") is None


def test_semantic_cache(tmp_path: Path, mock_embedding: MockEmbeddingClient) -> None:
    tasks_file = TasksFile(
        schema_version="1.0",
        tasks=[
            Task(
                id="T1",
                description="Semantic Task",
                files_affected=["src/auth.py"],
                acceptance_criteria=[],
                depends_on=[],
                complexity="low",
            )
        ],
    )

    # Store multiple entries to test Prior Cluster Density (PCD)
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], tasks_file, mock_embedding)
    store_semantic_spec(tmp_path, "implement signup auth", ["src/auth.py"], tasks_file, mock_embedding)
    store_semantic_spec(tmp_path, "optimize sql database queries", ["src/db.py"], tasks_file, mock_embedding)

    match_tasks, pcd = lookup_semantic(
        tmp_path, "implement user login and auth", ["src/auth.py"], mock_embedding
    )

    assert match_tasks is not None
    assert match_tasks.tasks[0].id == "T1"

    # Two of the three cached specs sit on the auth axis.
    assert pcd == pytest.approx(2 / 3)

    unrelated_tasks, _ = lookup_semantic(
        tmp_path, "reverse string utility", ["src/utils.py"], mock_embedding
    )
    assert unrelated_tasks is None


def test_atomic_notes(tmp_path: Path, mock_embedding: MockEmbeddingClient) -> None:
    assert get_frozen_notes(tmp_path) is None

    # Add 7 notes to check the limit <= 5
    for i in range(7):
        add_note(tmp_path, f"Atomic note content {i}", {"id": i}, mock_embedding)

    notes = initialize_session_notes(tmp_path, "query goal", mock_embedding)

    assert 0 < len(notes) <= 5

    # A note added mid-session must not join the frozen set.
    add_note(tmp_path, "New atomic note", {"id": 8}, mock_embedding)

    notes_second = initialize_session_notes(tmp_path, "query goal", mock_embedding)
    assert notes == notes_second
    assert "New atomic note" not in notes_second

    # Ending the session thaws the freeze, and the next open re-RAGs.
    clear_session_notes(tmp_path)
    assert get_frozen_notes(tmp_path) is None
    notes_reset = initialize_session_notes(tmp_path, "New atomic note", mock_embedding)
    assert "New atomic note" in notes_reset


def test_skills(tmp_path: Path, mock_embedding: MockEmbeddingClient) -> None:
    add_skill(
        tmp_path,
        "Python Refactoring",
        "Instructions for refactoring Python code using ruff and pyright.",
        {"description": "Python, refactor, quality"},
        mock_embedding,
    )
    add_skill(
        tmp_path,
        "Rust Optimization",
        "Instructions for optimizing Rust borrow checkers.",
        {"description": "Rust, optimization, performance"},
        mock_embedding,
    )

    results = search_skills(tmp_path, "Python refactoring clean code", limit=1, embedding_client=mock_embedding)
    assert len(results) == 1
    assert results[0]["name"] == "Python Refactoring"
    assert "Instructions for refactoring Python" in results[0]["content"]


def test_calibration_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """REQ-CAL-001 AC2, workspace-scoped.

    The process-wide `settings.calibrated` path is kept working for pre-Task-5 callers
    and is covered below, but the per-threshold resolution is what the ladder uses.
    """
    with caplog.at_level(logging.WARNING):
        check_calibration(tmp_path)
    assert any("UNCALIBRATED" in record.message for record in caplog.records)

    caplog.clear()
    directory = tmp_path / ".saltcode" / "calibration"
    directory.mkdir(parents=True)
    (directory / "thresholds.json").write_text(
        '{"thresholds": {"auditor_stability_threshold": 0.62, '
        '"semantic_cosine_threshold": 0.88, '
        '"pcd_low_density_bar": 0.25, "pcd_high_density_bar": 0.75}}',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        check_calibration(tmp_path)
    assert not caplog.records


def test_process_wide_calibration_warning_still_works(caplog: pytest.LogCaptureFixture) -> None:
    original = settings.calibrated
    try:
        settings.calibrated = False
        with caplog.at_level(logging.WARNING):
            check_calibration()
        assert any("UNCALIBRATED" in record.message for record in caplog.records)

        caplog.clear()
        settings.calibrated = True
        with caplog.at_level(logging.WARNING):
            check_calibration()
        assert not caplog.records
    finally:
        # Restore, so this cannot leak into another test the way it used to.
        settings.calibrated = original
