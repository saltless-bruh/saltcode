"""LanceDB tables behind the Lightweight Brain and the cache ladder (task 5.1).

Four tables live in one database under `.saltcode/cache/lancedb` (design §15, §17):

======================= ============================================================
Table                   Holds
======================= ============================================================
``spec_cache``          exact `sha256(normalized_goal + scope_fingerprint)` → tasks.json
``semantic_cache``      embedded `goal + scope` → tasks.json, for the fuzzy tier
``notes``               Atomic Notes, auto-RAG'd ≤5 at session open then FROZEN
``skills``              vectorized skill retrieval (the backend's own store)
======================= ============================================================

**The embedding dimension is recorded, never guessed.** The three vector tables are
created with a fixed `list_size`, so their width is decided once and is thereafter a
property of the store. That width is written to `_meta.json` beside the database, and
:func:`check_vector_dimension` compares every vector against it before it reaches
LanceDB. The alternative — inferring the width per call and falling back to a constant
when the embedding endpoint is unreachable — builds a cache at a width the real model
never produces: writes then fail with an opaque Arrow error, and any rows written in the
meantime are silently unusable. A dimension that cannot be established is an error
(:class:`EmbeddingDimensionUnavailableError`), and one that disagrees with the store is an
error naming both sides (:class:`EmbeddingDimensionMismatchError`) — which is also the signal
REQ-CAL-001 AC4 wants when the embedding model changes under a calibrated threshold.

:func:`init_db` therefore performs **no** embedding call once the tables exist; it only
probes when a vector table must actually be created. It is called on the lookup path of
every cache tier, so a probe here is a network round-trip per cache hit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import lancedb
import pyarrow as pa

from saltcode.config import settings
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient

_pa: Any = pa
"""pyarrow ships no type information (no ``py.typed``, no stubs, and its core is a
compiled extension), so under ``pyright --strict`` every ``pa.field``/``pa.string`` is an
unknown member. Going through one explicitly-``Any`` alias keeps the strict lane clean
without scattering per-line suppressions, and matches how this package already handles
the equally untyped LanceDB surface."""

STORE_SCHEMA_VERSION = "2"
"""Bumped only when the table layout changes incompatibly.

**2** — `semantic_cache` gained a `key` column so its row identity is a hex digest.
Deletes previously interpolated the raw goal text into a LanceDB SQL predicate with
hand-rolled quote doubling; `spec_cache` had always keyed on a digest for exactly this
reason, and the two tiers now agree."""

SPEC_CACHE_COMPATIBLE_VERSIONS = frozenset({"1", "2"})
"""Store schema versions whose ``spec_cache`` layout is identical to the current one.

The version is one number for the whole store, but the tiers do not fail together. The
2 bump touched `semantic_cache` alone, so refusing to open a v1 store for an exact
lookup would take down the tier design §11.2 calls "zero API" — the tier whose entire
value is being available when the embedding side is not. So a caller that asked for no
vector tables is checked against this set instead of against equality; a future bump
that does change `spec_cache` simply does not get added here, and the guard bites
again. Every version is still rejected for a vector caller, which is the strict reading.
"""

SPEC_CACHE_TABLE = "spec_cache"
SEMANTIC_CACHE_TABLE = "semantic_cache"
NOTES_TABLE = "notes"
SKILLS_TABLE = "skills"

VECTOR_TABLES = (SEMANTIC_CACHE_TABLE, NOTES_TABLE, SKILLS_TABLE)
"""The tables whose schema pins a vector width, and so depend on the embedding model."""

ALL_TABLES = (SPEC_CACHE_TABLE, *VECTOR_TABLES)


class MemoryStoreError(RuntimeError):
    """Base class for LanceDB store failures."""


class EmbeddingDimensionUnavailableError(MemoryStoreError):
    """The vector width could not be established, so no table may be created.

    Raised instead of defaulting to a plausible constant: a store built at the wrong
    width accepts no vectors from the real model, and the failure surfaces far from
    its cause.
    """


class StoreSchemaVersionMismatchError(MemoryStoreError):
    """The store on disk was built by a different major layout of this package.

    Reported rather than silently rebuilt: the cache is cheap to regenerate, but
    deleting a user's directory without being asked is not this code's decision.
    """


class EmbeddingDimensionMismatchError(MemoryStoreError):
    """A vector's width disagrees with the width this store was created at.

    Almost always means the embedding model changed. Per REQ-CAL-001 AC4 the
    calibration must then be re-run, so this is reported rather than absorbed.
    """


def get_db_path(workspace_path: Path | str) -> Path:
    """The LanceDB directory for a workspace."""
    return Path(workspace_path) / ".saltcode" / "cache" / "lancedb"


def get_meta_path(workspace_path: Path | str) -> Path:
    """The sidecar recording what embedding model this store's vectors came from."""
    return get_db_path(workspace_path) / "_meta.json"


def read_store_meta(workspace_path: Path | str) -> dict[str, Any] | None:
    """Return the store metadata, or ``None`` when it has not been written yet."""
    path = get_meta_path(workspace_path)
    if not path.exists():
        return None
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt sidecar must not take the store down: the dimension is
        # recoverable from the table schema itself (see _stored_dimension).
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None


def write_store_meta(workspace_path: Path | str, dimension: int, embedding_model: str | None) -> None:
    """Record the width and model this store's vector tables were created at."""
    path = get_meta_path(workspace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "dimension": dimension,
                "embedding_model": embedding_model,
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def probe_vector_dimension(embedding_client: EmbeddingClient | None = None) -> int:
    """Ask the embedding model how wide its vectors are.

    Called **only** when a vector table must be created — never on a lookup.

    Raises:
        EmbeddingDimensionUnavailableError: The endpoint is unreachable or answered with
            an empty vector. Creating tables at a guessed width would poison the store.
    """
    client: EmbeddingClient
    try:
        client = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        vector = client.embed_query("dimension probe")
    except Exception as exc:
        raise EmbeddingDimensionUnavailableError(
            "Cannot determine the embedding dimension, so the LanceDB vector tables "
            f"cannot be created. The embedding endpoint must be reachable the first time "
            f"the cache is used. Underlying error: {type(exc).__name__}: {exc}"
        ) from exc

    if not vector:
        raise EmbeddingDimensionUnavailableError(
            "The embedding endpoint returned an empty vector, so the dimension is unknown."
        )
    return len(vector)


def _embedding_model_name(embedding_client: EmbeddingClient | None) -> str | None:
    """The model identifier to record, when the client exposes one.

    Test doubles and other in-process clients need not have a model name; the width
    check does not depend on it, so ``None`` is a legitimate answer.
    """
    name = getattr(embedding_client, "model_name", None) if embedding_client is not None else settings.embedding_model
    return str(name) if isinstance(name, str) else None


def _stored_dimension(db: Any, workspace_path: Path | str) -> int | None:
    """The width this store already uses, from the sidecar or the table schema itself.

    The schema is the authority — the sidecar can be missing (a store created before
    it existed) or corrupt, and in both cases the tables still know their own width.
    """
    for table_name in VECTOR_TABLES:
        if table_name not in db:
            continue
        try:
            table: Any = db.open_table(table_name)
            field: Any = table.schema.field("vector")
            size = int(field.type.list_size)
        except Exception:
            continue
        if size > 0:
            return size

    meta = read_store_meta(workspace_path)
    if meta is not None:
        dimension = meta.get("dimension")
        if isinstance(dimension, int) and dimension > 0:
            return dimension
    return None


def check_vector_dimension(workspace_path: Path | str, vector: list[float]) -> None:
    """Refuse a vector whose width disagrees with the store's.

    Called on every write and query path that carries a vector. It costs nothing —
    the vector is already in hand — and turns a change of embedding model from an
    opaque Arrow error (or worse, silently meaningless neighbours) into a named
    failure that says which model the store was built with.

    Raises:
        EmbeddingDimensionMismatchError: The widths differ.
    """
    meta = read_store_meta(workspace_path)
    if meta is None:
        return
    expected = meta.get("dimension")
    if not isinstance(expected, int) or expected == len(vector):
        return
    raise EmbeddingDimensionMismatchError(
        f"This cache stores {expected}-dimensional vectors (embedding model "
        f"{meta.get('embedding_model')!r}) but was handed a {len(vector)}-dimensional one. "
        "The embedding model has changed: re-run calibration (REQ-CAL-001 AC4) and rebuild "
        f"{get_db_path(workspace_path)}."
    )


def _spec_cache_schema() -> Any:
    return _pa.schema(
        [
            _pa.field("key", _pa.string()),
            _pa.field("tasks_json", _pa.string()),
        ]
    )


def _semantic_cache_schema(dimension: int) -> Any:
    return _pa.schema(
        [
            # A sha256 of the normalized goal+scope. Row identity is a hex digest so
            # deletes never interpolate free text into a SQL predicate.
            _pa.field("key", _pa.string()),
            _pa.field("vector", _pa.list_(_pa.float32(), list_size=dimension)),
            _pa.field("goal", _pa.string()),
            _pa.field("scope_fingerprint", _pa.string()),
            _pa.field("tasks_json", _pa.string()),
        ]
    )


def _notes_schema(dimension: int) -> Any:
    return _pa.schema(
        [
            _pa.field("vector", _pa.list_(_pa.float32(), list_size=dimension)),
            _pa.field("content", _pa.string()),
            _pa.field("metadata", _pa.string()),
        ]
    )


def _skills_schema(dimension: int) -> Any:
    return _pa.schema(
        [
            _pa.field("vector", _pa.list_(_pa.float32(), list_size=dimension)),
            _pa.field("name", _pa.string()),
            _pa.field("content", _pa.string()),
            _pa.field("metadata", _pa.string()),
        ]
    )


def init_db(
    workspace_path: Path | str,
    embedding_client: EmbeddingClient | None = None,
    *,
    vectors: bool = True,
) -> Any:
    """Open the workspace's LanceDB store, creating any missing table.

    Performs **no** embedding call when the tables it needs already exist — this runs
    on the lookup path of both cache tiers, and a probe here would be a network
    round-trip per cache hit.

    Args:
        vectors: Whether the caller needs the vector tables. The exact spec-cache tier
            does not, and must not: design §11.2 tier 1 is "zero API", so a lookup
            there has to keep working when the embedding endpoint is down — that is
            precisely the situation in which a cached plan is most valuable. Callers
            that embed anything leave this at ``True``. The store-schema check is
            relaxed to match (see :data:`SPEC_CACHE_COMPATIBLE_VERSIONS`): an
            endpoint-independent tier that still refused to open on a version bump it
            was not affected by would only have moved the outage.

    Raises:
        EmbeddingDimensionUnavailableError: A vector table must be created but the
            embedding dimension cannot be established.
        StoreSchemaVersionMismatchError: The store on disk was built by a layout this
            build cannot read — any different version for a vector caller, or one
            outside :data:`SPEC_CACHE_COMPATIBLE_VERSIONS` when ``vectors`` is false.
    """
    db_path = get_db_path(workspace_path)
    db_path.mkdir(parents=True, exist_ok=True)
    db: Any = lancedb.connect(str(db_path))

    meta = read_store_meta(workspace_path)
    if meta is not None:
        found = str(meta.get("schema_version", "1"))
        readable = found == STORE_SCHEMA_VERSION or (
            not vectors and found in SPEC_CACHE_COMPATIBLE_VERSIONS
        )
        if not readable:
            raise StoreSchemaVersionMismatchError(
                f"This cache was built with store schema {found!r} but this build uses "
                f"{STORE_SCHEMA_VERSION!r}. The cache is safe to regenerate — delete "
                f"{db_path} and it will be rebuilt on the next sprint — but nothing here "
                "will remove it for you."
            )

    if SPEC_CACHE_TABLE not in db:
        # No vectors, so no dimension needed. `exist_ok` because `name not in db`
        # followed by `create_table` is a check-then-act, and the entrypoints run as
        # separate processes (`pi.exec`) — two first-use lookups on one workspace can
        # both see the table missing, and the loser would raise instead of reading.
        db.create_table(SPEC_CACHE_TABLE, schema=_spec_cache_schema(), exist_ok=True)

    if not vectors:
        return db

    missing = [name for name in VECTOR_TABLES if name not in db]
    if not missing:
        return db

    dimension = _stored_dimension(db, workspace_path)
    if dimension is None:
        dimension = probe_vector_dimension(embedding_client)

    builders = {
        SEMANTIC_CACHE_TABLE: _semantic_cache_schema,
        NOTES_TABLE: _notes_schema,
        SKILLS_TABLE: _skills_schema,
    }
    for name in missing:
        db.create_table(name, schema=builders[name](dimension), exist_ok=True)

    if read_store_meta(workspace_path) is None:
        write_store_meta(workspace_path, dimension, _embedding_model_name(embedding_client))

    return db
