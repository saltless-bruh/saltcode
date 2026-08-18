"""Atomic Notes: auto-RAG ≤5 at session open, then FROZEN (task 5.4).

Segment 3 of the cacheable prefix (design §15). REQ-MEM-001 caps the set at five notes
and freezes it for the session; REQ-GLB-004 then requires that segment to be
**byte-identical across ALL Phase-1 calls in that session**, because a prefix that
shifts mid-session invalidates the provider-side cache the whole cost model rests on.

**The freeze is on disk, not in a variable.** The previous implementation held the
frozen set in a module-level global. Under Pi that cannot work: the backend is invoked
as `pi.exec("python", ["-m", "saltcode.tools.<x>"])` — a fresh interpreter per call — so
the global is empty every time and each call would re-run the RAG. Whenever the notes
table changed, or the vector search returned ties in a different order, segment 3 would
change and REQ-GLB-004 would break silently: nothing errors, the prefix cache just stops
hitting and the bill goes up.

So the frozen set is written to `.saltcode/cache/frozen_notes.json`, tagged with the
session id the caller supplies. A call carrying the same session id reads the file back
verbatim; a new session id re-RAGs and overwrites. The specs do not name a mechanism —
design §11.1 step 3 says only "auto-RAG ≤5 Atomic Notes, then FREEZE for the session" —
and this is the smallest reading consistent with the rest: Task 6.1 has the extension
"pull frozen notes … from the backend/Code-Wiki", and Task 7b.1's entrypoint list has no
notes entrypoint, so disk is the intended channel. *(Maintainer decision, 2026-07-29.)*
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from saltcode.memory.lancedb_store import (
    NOTES_TABLE,
    check_vector_dimension,
    init_db,
)
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient

MAX_NOTES = 5
"""REQ-MEM-001 AC2. Enforced on the way out as well as in the query, so a future
change to the search cannot quietly raise the cap."""

DEFAULT_SESSION_ID = "default"
"""Used when the caller names no session — a single-session workspace, which is the
interactive case."""

FROZEN_NOTES_FILE = "frozen_notes.json"

_in_process_cache: dict[tuple[str, str], list[str]] = {}
"""A same-process shortcut only. Keyed by (workspace, session_id) — the old global was
keyed by nothing, so two workspaces in one process shared a frozen set. The file on disk
is the authority; this only avoids re-reading it within a single interpreter."""


def frozen_notes_path(workspace_path: Path | str) -> Path:
    """Where the frozen set for a workspace lives."""
    return Path(workspace_path) / ".saltcode" / "cache" / FROZEN_NOTES_FILE


def _cache_key(workspace_path: Path | str, session_id: str) -> tuple[str, str]:
    return (str(Path(workspace_path).resolve()), session_id)


def _read_frozen_from_disk(
    workspace_path: Path | str, session_id: str
) -> list[str] | None:
    """Read this session's frozen set from disk **without touching the cache**.

    Separate from :func:`read_frozen_notes` because a *query* with a side effect
    cannot safely be used as a probe inside a mutator: `clear_session_notes` needs
    to ask "is this freeze mine?" without re-populating the very cache entry it is
    about to drop.
    """
    path = frozen_notes_path(workspace_path)
    if not path.exists():
        return None
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None

    record = cast("dict[str, Any]", loaded)
    if record.get("session_id") != session_id:
        return None

    notes_raw = record.get("notes")
    if not isinstance(notes_raw, list):
        return None

    return [str(n) for n in cast("list[Any]", notes_raw)][:MAX_NOTES]


def read_frozen_notes(
    workspace_path: Path | str, session_id: str = DEFAULT_SESSION_ID
) -> list[str] | None:
    """The frozen set for this session, or ``None`` if this session has not frozen one.

    A file written by a *different* session is not this session's freeze, so it reads
    as absent — that is what makes a new session re-RAG. A successful disk read is
    memoised in the process cache.
    """
    cached = _in_process_cache.get(_cache_key(workspace_path, session_id))
    if cached is not None:
        return list(cached)

    notes = _read_frozen_from_disk(workspace_path, session_id)
    if notes is None:
        return None

    _in_process_cache[_cache_key(workspace_path, session_id)] = list(notes)
    return notes


def _write_frozen_notes(
    workspace_path: Path | str, session_id: str, goal: str, notes: list[str]
) -> None:
    path = frozen_notes_path(workspace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "goal": goal,
                "frozen_at": datetime.now(UTC).isoformat(),
                "notes": notes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _in_process_cache[_cache_key(workspace_path, session_id)] = list(notes)


def clear_session_notes(
    workspace_path: Path | str | None = None, session_id: str | None = None
) -> None:
    """Thaw the frozen set, so the next `initialize_session_notes` re-runs the RAG.

    With no workspace, only the in-process cache is dropped (what the pre-Task-5
    signature did). With a workspace, the on-disk freeze is removed too — which is
    what actually ends a session.
    """
    if workspace_path is None:
        _in_process_cache.clear()
        return

    key_prefix = str(Path(workspace_path).resolve())
    for key in [k for k in _in_process_cache if k[0] == key_prefix and (session_id is None or k[1] == session_id)]:
        del _in_process_cache[key]

    path = frozen_notes_path(workspace_path)
    if not path.exists():
        return
    # Deliberately the NON-caching read: `read_frozen_notes` memoises on success, so
    # using it as the ownership probe would re-populate the very key the loop above
    # just dropped, and `unlink` would then leave a cache entry with no file behind
    # it — `initialize_session_notes` would return the stale frozen set instead of
    # re-RAGing, so the thaw would silently not happen.
    if session_id is not None and _read_frozen_from_disk(workspace_path, session_id) is None:
        # A freeze belonging to a different session is not ours to delete.
        return
    path.unlink()


def get_frozen_notes(
    workspace_path: Path | str | None = None, session_id: str = DEFAULT_SESSION_ID
) -> list[str] | None:
    """The current frozen set without running a RAG, or ``None`` if nothing is frozen."""
    if workspace_path is None:
        return None
    return read_frozen_notes(workspace_path, session_id)


def add_note(
    workspace_path: Path | str,
    content: str,
    metadata: dict[str, Any] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> None:
    """Add a note to the store.

    Deliberately does **not** disturb a frozen set: notes written during a session
    become eligible at the next session open, never mid-session (REQ-MEM-001 AC1).
    """
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()

    vector = client.embed_query(content)
    db: Any = init_db(workspace_path, client)
    check_vector_dimension(workspace_path, vector)
    table: Any = db.open_table(NOTES_TABLE)

    table.add([{"vector": vector, "content": content, "metadata": json.dumps(metadata or {})}])


def initialize_session_notes(
    workspace_path: Path | str,
    goal: str,
    embedding_client: EmbeddingClient | None = None,
    session_id: str = DEFAULT_SESSION_ID,
) -> list[str]:
    """Return this session's frozen notes, running the auto-RAG once if needed.

    Idempotent within a session and across processes: the second call — in this
    interpreter or the next one — returns the first call's list byte for byte.
    """
    existing = read_frozen_notes(workspace_path, session_id)
    if existing is not None:
        return existing

    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()

    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table(NOTES_TABLE)

    if int(table.count_rows()) == 0:
        _write_frozen_notes(workspace_path, session_id, goal, [])
        return []

    query_vector = client.embed_query(goal)
    check_vector_dimension(workspace_path, query_vector)

    # Cosine, to match the semantic cache and the "cosine" the specs describe. The
    # previous default (L2) ranked by a different geometry than the one being reported.
    results: list[dict[str, Any]] = (
        table.search(query_vector).metric("cosine").limit(MAX_NOTES).to_list()
    )

    notes = [str(r["content"]) for r in results][:MAX_NOTES]
    _write_frozen_notes(workspace_path, session_id, goal, notes)
    return notes
