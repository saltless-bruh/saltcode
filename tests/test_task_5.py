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
from saltcode.memory.spec_cache import lookup_spec, store_spec
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
    
    # Store spec with goal and scope
    goal = "implement auth.py"
    store_spec(tmp_path, goal, tasks_file)
    
    # Exact lookup works
    res = lookup_spec(tmp_path, goal, ["src/auth.py"])
    assert res is not None
    assert res.tasks[0].id == "T1"
    
    # Lookup with different scope misses
    res_diff = lookup_spec(tmp_path, goal, ["src/signup.py"])
    assert res_diff is None
    
    # Empty/fallback scope works
    # If we lookup without scope, it falls back to scope probe
    # Write a dummy file in tmp_path to simulate scope probe matching
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("pass", encoding="utf-8")
    
    # Since goal mentions auth.py, the scope probe finds src/auth.py
    res_fallback = lookup_spec(tmp_path, "implement auth.py")
    assert res_fallback is not None
    assert res_fallback.tasks[0].id == "T1"


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
    # Entry 1: Auth
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], tasks_file, mock_embedding)
    # Entry 2: Similar Auth
    store_semantic_spec(tmp_path, "implement signup auth", ["src/auth.py"], tasks_file, mock_embedding)
    # Entry 3: database optimization (outlier)
    store_semantic_spec(tmp_path, "optimize sql database queries", ["src/db.py"], tasks_file, mock_embedding)
    
    # Query similar to auth: "implement user login and auth"
    match_tasks, pcd = lookup_semantic(
        tmp_path, "implement user login and auth", ["src/auth.py"], mock_embedding
    )
    
    # Verify we got a hit because mock_embedding returns similar vectors for similar text
    assert match_tasks is not None
    assert match_tasks.tasks[0].id == "T1"
    
    # PCD should be computed: count of similar items (2 out of 3 total) -> ~0.66
    assert 0.0 < pcd <= 1.0
    assert pcd >= 0.5  # Auth has a higher neighborhood density
    
    # Query completely unrelated: "reverse string utility"
    unrelated_tasks, pcd_unrelated = lookup_semantic(
        tmp_path, "reverse string utility", ["src/utils.py"], mock_embedding
    )
    # Similarity should be too low to return tasks
    assert unrelated_tasks is None


def test_atomic_notes(tmp_path: Path, mock_embedding: MockEmbeddingClient) -> None:
    # Clear any previous session frozen state
    clear_session_notes()
    assert get_frozen_notes() is None
    
    # Add 7 notes to check the limit <= 5
    for i in range(7):
        add_note(tmp_path, f"Atomic note content {i}", {"id": i}, mock_embedding)
        
    # Open session with goal
    notes = initialize_session_notes(tmp_path, "query goal", mock_embedding)
    
    # Verify note count is <= 5
    assert len(notes) <= 5
    assert len(notes) > 0
    
    # Add an 8th note to the database
    add_note(tmp_path, "New atomic note", {"id": 8}, mock_embedding)
    
    # Query again within the session - should return the EXACT same frozen notes
    notes_second = initialize_session_notes(tmp_path, "query goal", mock_embedding)
    assert notes == notes_second
    assert "New atomic note" not in notes_second
    
    # Clear session notes and query again - now it should include the new note/re-evaluate
    clear_session_notes()
    assert get_frozen_notes() is None
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
    
    # Search python refactoring
    results = search_skills(tmp_path, "Python refactoring clean code", limit=1, embedding_client=mock_embedding)
    assert len(results) == 1
    assert results[0]["name"] == "Python Refactoring"
    assert "Instructions for refactoring Python" in results[0]["content"]


def test_calibration_warning(caplog: pytest.LogCaptureFixture) -> None:
    # Test uncalibrated warning
    settings.calibrated = False
    
    with caplog.at_level(logging.WARNING):
        check_calibration()
        
    warning_found = any(
        "Thresholds (stability, cosine, PCD bars) are UNCALIBRATED" in record.message
        for record in caplog.records
    )
    assert warning_found
    
    # Clear log and test calibrated does not warn
    caplog.clear()
    settings.calibrated = True
    
    with caplog.at_level(logging.WARNING):
        check_calibration()
        
    assert len(caplog.records) == 0
