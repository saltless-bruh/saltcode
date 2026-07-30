"""Task 5.4/5.5 — the Atomic-Notes freeze and vectorized skills retrieval.

The freeze tests deliberately span **separate interpreters**. REQ-GLB-004 requires
segment 3 of the prefix to be byte-identical across all Phase-1 calls in a session, and
under Pi each backend call is its own `pi.exec` process — so an in-process assertion
would pass against an implementation that cannot hold the invariant in production. That
is the exact hole this task closed, and only a subprocess test can keep it closed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from saltcode.memory.atomic_notes import (
    DEFAULT_SESSION_ID,
    MAX_NOTES,
    add_note,
    clear_session_notes,
    frozen_notes_path,
    get_frozen_notes,
    initialize_session_notes,
    read_frozen_notes,
)
from saltcode.memory.skills import add_skill, search_skills
from saltcode.providers.embeddings import EmbeddingClient

DOUBLE_SOURCE = textwrap.dedent(
    """
    from saltcode.providers.embeddings import EmbeddingClient


    class AxisEmbedding(EmbeddingClient):
        def embed_query(self, text):
            lowered = text.lower()
            if "auth" in lowered or "login" in lowered:
                return [1.0, 0.0, 0.0]
            if "database" in lowered or "sql" in lowered:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

        def embed_documents(self, texts):
            return [self.embed_query(t) for t in texts]
    """
)


class AxisEmbedding(EmbeddingClient):
    """Same double as the subprocesses use, so both sides rank identically."""

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        if "auth" in lowered or "login" in lowered:
            return [1.0, 0.0, 0.0]
        if "database" in lowered or "sql" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


@pytest.fixture
def embedding() -> AxisEmbedding:
    return AxisEmbedding()


@pytest.fixture(autouse=True)
def _thaw() -> None:
    """The in-process cache is global; a leak between tests would fake the freeze."""
    clear_session_notes()


def run_in_fresh_interpreter(workspace: Path, body: str) -> str:
    """Run `body` in a new Python process and return its stdout.

    Deliberately not `multiprocessing`: a forked child inherits the parent's module
    globals, which is precisely the state under test.
    """
    script = DOUBLE_SOURCE + textwrap.dedent(body).replace("{WORKSPACE}", str(workspace))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"subprocess failed:\n{completed.stderr}"
    return completed.stdout


# --------------------------------------------------------------------------------------
# 5.4 — auto-RAG ≤5 then FREEZE
# --------------------------------------------------------------------------------------


def test_at_most_five_notes_are_returned(tmp_path: Path, embedding: AxisEmbedding) -> None:
    """REQ-MEM-001 AC2."""
    for i in range(9):
        add_note(tmp_path, f"auth note {i}", {"i": i}, embedding)

    notes = initialize_session_notes(tmp_path, "auth", embedding)
    assert 0 < len(notes) <= MAX_NOTES


def test_an_empty_notes_table_freezes_an_empty_set(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """A fresh project has no notes; that is a valid freeze, not a missing one."""
    assert initialize_session_notes(tmp_path, "goal", embedding) == []
    assert read_frozen_notes(tmp_path) == []


def test_a_note_added_mid_session_does_not_join_the_frozen_set(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """REQ-MEM-001 AC1 — the set does not change until the session ends."""
    for i in range(3):
        add_note(tmp_path, f"auth note {i}", {"i": i}, embedding)

    first = initialize_session_notes(tmp_path, "auth", embedding)
    add_note(tmp_path, "auth note LATE", {"late": True}, embedding)
    second = initialize_session_notes(tmp_path, "auth", embedding)

    assert first == second
    assert "auth note LATE" not in second


def test_a_new_session_id_re_rags(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_note(tmp_path, "auth note one", None, embedding)
    first = initialize_session_notes(tmp_path, "auth", embedding, session_id="session-a")

    add_note(tmp_path, "auth note two", None, embedding)
    second = initialize_session_notes(tmp_path, "auth", embedding, session_id="session-b")

    assert len(second) > len(first)


def test_clearing_the_session_thaws_the_freeze(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_note(tmp_path, "auth note one", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding)
    assert frozen_notes_path(tmp_path).exists()

    clear_session_notes(tmp_path)
    assert not frozen_notes_path(tmp_path).exists()
    assert get_frozen_notes(tmp_path) is None


def test_two_workspaces_do_not_share_a_frozen_set(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The previous global was keyed by nothing, so the first workspace won."""
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()

    add_note(alpha, "auth note alpha", None, embedding)
    add_note(beta, "auth note beta", None, embedding)

    assert initialize_session_notes(alpha, "auth", embedding) == ["auth note alpha"]
    assert initialize_session_notes(beta, "auth", embedding) == ["auth note beta"]


def test_the_frozen_set_survives_a_new_interpreter(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """REQ-GLB-004 across processes — the leg an in-memory global cannot pass.

    Under Pi each backend call is a separate `pi.exec` process. If the freeze lived
    only in a module global, every call would re-run the RAG, segment 3 would drift as
    the notes table grew, and the prefix cache would quietly stop hitting.
    """
    for i in range(3):
        add_note(tmp_path, f"auth note {i}", {"i": i}, embedding)

    first = run_in_fresh_interpreter(
        tmp_path,
        """
        import json
        from saltcode.memory.atomic_notes import initialize_session_notes
        notes = initialize_session_notes("{WORKSPACE}", "auth", AxisEmbedding(), session_id="s1")
        print(json.dumps(notes))
        """,
    )

    # A note lands between the two calls, exactly as it would mid-sprint.
    add_note(tmp_path, "auth note LATE", {"late": True}, embedding)

    second = run_in_fresh_interpreter(
        tmp_path,
        """
        import json
        from saltcode.memory.atomic_notes import initialize_session_notes
        notes = initialize_session_notes("{WORKSPACE}", "auth", AxisEmbedding(), session_id="s1")
        print(json.dumps(notes))
        """,
    )

    assert first == second, "segment 3 must be byte-identical across Phase-1 calls"
    assert "LATE" not in second


def test_a_different_session_in_a_new_interpreter_re_rags(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    add_note(tmp_path, "auth note one", None, embedding)

    first = json.loads(
        run_in_fresh_interpreter(
            tmp_path,
            """
            import json
            from saltcode.memory.atomic_notes import initialize_session_notes
            print(json.dumps(initialize_session_notes("{WORKSPACE}", "auth", AxisEmbedding(), session_id="s1")))
            """,
        )
    )
    add_note(tmp_path, "auth note two", None, embedding)
    second = json.loads(
        run_in_fresh_interpreter(
            tmp_path,
            """
            import json
            from saltcode.memory.atomic_notes import initialize_session_notes
            print(json.dumps(initialize_session_notes("{WORKSPACE}", "auth", AxisEmbedding(), session_id="s2")))
            """,
        )
    )

    assert len(second) == len(first) + 1


def test_a_freeze_from_another_session_reads_as_absent(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    add_note(tmp_path, "auth note", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding, session_id="mine")

    clear_session_notes()  # drop only the in-process cache, keep the file
    assert read_frozen_notes(tmp_path, "someone-elses") is None
    assert read_frozen_notes(tmp_path, "mine") == ["auth note"]


def test_a_corrupt_freeze_file_is_treated_as_absent(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    add_note(tmp_path, "auth note", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding)

    clear_session_notes()
    frozen_notes_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_frozen_notes(tmp_path) is None

    # ...and the next session open simply re-freezes.
    assert initialize_session_notes(tmp_path, "auth", embedding) == ["auth note"]


def test_the_freeze_record_names_its_session(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_note(tmp_path, "auth note", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding, session_id="sprint-7")

    record = json.loads(frozen_notes_path(tmp_path).read_text(encoding="utf-8"))
    assert record["session_id"] == "sprint-7"
    assert record["goal"] == "auth"
    assert record["notes"] == ["auth note"]
    assert record["frozen_at"]


def test_the_default_session_id_is_stable(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_note(tmp_path, "auth note", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding)
    assert read_frozen_notes(tmp_path, DEFAULT_SESSION_ID) == ["auth note"]


# --------------------------------------------------------------------------------------
# 5.5 — vectorized skills retrieval
# --------------------------------------------------------------------------------------


def test_skills_retrieval_ranks_by_similarity(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_skill(tmp_path, "Auth Review", "How to review auth code.", {"description": "auth login"}, embedding)
    add_skill(tmp_path, "SQL Tuning", "How to tune queries.", {"description": "database sql"}, embedding)

    results = search_skills(tmp_path, "auth login help", limit=1, embedding_client=embedding)
    assert len(results) == 1
    assert results[0]["name"] == "Auth Review"
    assert "review auth code" in results[0]["content"]


def test_the_reported_score_is_cosine_similarity(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The search used to run on L2 while labelling the score 'cosine similarity'.

    On these orthogonal axes an exact match has cosine similarity 1.0, so a score of
    1.0 is only reachable if the search really did use the cosine metric.
    """
    add_skill(tmp_path, "Auth Review", "auth", {"description": "auth login"}, embedding)

    results = search_skills(tmp_path, "auth login", limit=1, embedding_client=embedding)
    assert results[0]["score"] == pytest.approx(1.0)


def test_an_empty_skills_table_returns_nothing(tmp_path: Path, embedding: AxisEmbedding) -> None:
    assert search_skills(tmp_path, "anything", embedding_client=embedding) == []


def test_re_adding_a_skill_replaces_it(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_skill(tmp_path, "Auth Review", "first version", {"description": "auth"}, embedding)
    add_skill(tmp_path, "Auth Review", "second version", {"description": "auth"}, embedding)

    results = search_skills(tmp_path, "auth", limit=5, embedding_client=embedding)
    assert len(results) == 1
    assert results[0]["content"] == "second version"


def test_skill_metadata_round_trips(tmp_path: Path, embedding: AxisEmbedding) -> None:
    add_skill(tmp_path, "Auth Review", "body", {"description": "auth", "owner": "scout"}, embedding)
    results = search_skills(tmp_path, "auth", limit=1, embedding_client=embedding)
    assert results[0]["metadata"]["owner"] == "scout"


# ============================================================================
# Review follow-ups (CodeRabbit, 2026-07-29)
# ============================================================================


def test_clearing_a_named_session_actually_thaws_it(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The ownership guard must not re-cache the key it is about to delete.

    `clear_session_notes` dropped the in-process key, then called the *caching*
    `read_frozen_notes` to check ownership — which put the key straight back — and
    then unlinked the file. The cache outlived the file, so the thaw silently did
    not happen inside that interpreter.
    """
    add_note(tmp_path, "auth note one", None, embedding)
    first = initialize_session_notes(tmp_path, "auth", embedding, session_id="s1")
    assert first == ["auth note one"]

    clear_session_notes(tmp_path, session_id="s1")

    assert not frozen_notes_path(tmp_path).exists()
    assert get_frozen_notes(tmp_path, "s1") is None, "the in-process cache outlived the file"


def test_a_thawed_session_re_rags_in_the_same_process(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The consequence that actually bites: a stale cache returns the OLD frozen set."""
    add_note(tmp_path, "auth note one", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding, session_id="s1")

    clear_session_notes(tmp_path, session_id="s1")
    add_note(tmp_path, "auth note two", None, embedding)

    refrozen = initialize_session_notes(tmp_path, "auth", embedding, session_id="s1")
    assert len(refrozen) == 2, f"expected a genuine re-RAG, got {refrozen}"


def test_clearing_a_foreign_session_leaves_it_alone(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The guard's actual purpose must survive the fix."""
    add_note(tmp_path, "auth note", None, embedding)
    initialize_session_notes(tmp_path, "auth", embedding, session_id="mine")

    clear_session_notes(tmp_path, session_id="someone-elses")

    assert frozen_notes_path(tmp_path).exists()
    clear_session_notes()  # drop the process cache only
    assert read_frozen_notes(tmp_path, "mine") == ["auth note"]
