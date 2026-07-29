"""The exact tier of the cache ladder (task 5.2, REQ-CACHE-002).

`key = sha256(normalized_goal + scope_fingerprint)` → `tasks.json`. A hit reuses the
plan with **zero API calls** and stops the ladder (REQ-CACHE-001 AC1).

**The two fingerprints are deliberately different objects.** At store time, after
Phase 1, the fingerprint is `sorted(tasks.json[*].files_affected)` — the files the plan
actually touches. At lookup time there is no plan yet, so it is either an explicit
`--scope` or the scope probe's enumeration of the repo's source modules. Those sets
rarely coincide: a plan that edits three files will not key the same as a repo
containing forty. That is **Trade C5** — "the scope-fingerprint key lowers the hit rate;
safer, accepted" — because the alternative, goal text alone, collides on "rate-limit the
LOGIN endpoint" versus "rate-limit the SIGNUP endpoint" and reuses the wrong plan.

*Confirmed deliberately by the maintainer on 2026-07-29 (closing G-004).* The
consequence is that an exact hit effectively requires the caller to pass the same
`--scope` it used before, so :func:`lookup_spec_result` reports the fingerprint, its
source and the computed key — a miss you can explain is a miss `/sprint` (Task 8.1) can
do something about, where a bare ``None`` is just a cache that never seems to work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from saltcode.contracts.tasks import TasksFile
from saltcode.harness.phase_gate import compute_spec_hash
from saltcode.harness.scope_probe import run_scope_probe
from saltcode.memory.lancedb_store import SPEC_CACHE_TABLE, init_db

ScopeSource = Literal["provided", "probed", "empty"]
"""Where the lookup-time fingerprint came from — REQ-CACHE-002 AC2 (explicit `--scope`,
used directly with no probe), the probe, or nothing at all (AC3: an empty repo degrades
the key to goal-only)."""


def scope_fingerprint_for_tasks(tasks_file: TasksFile) -> list[str]:
    """The store-time fingerprint: every path any task touches, sorted and de-duplicated."""
    files: set[str] = set()
    for task in tasks_file.tasks:
        files.update(task.files_affected)
    return sorted(files)


def resolve_lookup_scope(
    workspace_path: Path | str, scope: list[str] | None
) -> tuple[list[str], ScopeSource]:
    """Resolve the lookup-time fingerprint and say where it came from.

    An explicit scope is used directly, with no probe (REQ-CACHE-002 AC2) — the caller
    is telling us the answer, and measuring it again could only disagree. It is sorted
    and de-duplicated so the caller's argument order cannot change the key.
    """
    if scope is not None:
        return sorted(set(scope)), "provided"

    probed = run_scope_probe(workspace_path)
    if not probed:
        return [], "empty"
    return probed, "probed"


class SpecCacheResult:
    """The outcome of an exact lookup, with the inputs that produced it."""

    def __init__(
        self,
        *,
        tasks: TasksFile | None,
        key: str,
        scope_fingerprint: list[str],
        scope_source: ScopeSource,
        detail: str,
    ) -> None:
        self.tasks = tasks
        self.key = key
        self.scope_fingerprint = scope_fingerprint
        self.scope_source = scope_source
        self.detail = detail

    @property
    def hit(self) -> bool:
        return self.tasks is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": self.hit,
            "key": self.key,
            "scope_fingerprint": self.scope_fingerprint,
            "scope_source": self.scope_source,
            "detail": self.detail,
        }


def store_spec(workspace_path: Path | str, goal: str, tasks_file: TasksFile) -> str:
    """Store a plan under the store-time key and return that key.

    Returns:
        The `sha256(normalized_goal + files_affected)` the plan was filed under.
    """
    scope_fingerprint = scope_fingerprint_for_tasks(tasks_file)
    key = compute_spec_hash(goal, scope_fingerprint)

    # vectors=False: the exact tier stores no vector, so it must not require an
    # embedding endpoint to be reachable (design §11.2 tier 1 is "zero API").
    db: Any = init_db(workspace_path, vectors=False)
    table: Any = db.open_table(SPEC_CACHE_TABLE)

    # The key is a hex digest, so it cannot carry a quote into the predicate.
    table.delete(f"key = '{key}'")
    table.add([{"key": key, "tasks_json": tasks_file.model_dump_json()}])
    return key


def lookup_spec_result(
    workspace_path: Path | str,
    goal: str,
    scope: list[str] | None = None,
) -> SpecCacheResult:
    """Look up a plan by exact key, reporting the fingerprint and key that were used."""
    scope_fingerprint, scope_source = resolve_lookup_scope(workspace_path, scope)
    key = compute_spec_hash(goal, scope_fingerprint)

    db: Any = init_db(workspace_path, vectors=False)
    table: Any = db.open_table(SPEC_CACHE_TABLE)

    rows: list[dict[str, Any]] = table.search().where(f"key = '{key}'").limit(1).to_list()
    if not rows:
        return SpecCacheResult(
            tasks=None,
            key=key,
            scope_fingerprint=scope_fingerprint,
            scope_source=scope_source,
            detail=(
                f"no entry for this key ({len(scope_fingerprint)} paths in the "
                f"{scope_source} scope fingerprint)"
            ),
        )

    tasks, reason = _load_cached_tasks(rows[0])
    return SpecCacheResult(
        tasks=tasks,
        key=key,
        scope_fingerprint=scope_fingerprint,
        scope_source=scope_source,
        detail="exact hit" if tasks is not None else reason,
    )


def _load_cached_tasks(row: dict[str, Any]) -> tuple[TasksFile | None, str]:
    """Validate a cached row, refusing an unsupported major `schema_version`.

    REQ-GLB-005 says readers reject unknown major versions rather than guess. Applying
    that to the cache matters as much as to the on-disk contracts: a row written by a
    future Saltcode is not a plan this build can execute, and the difference between
    "no entry" and "an entry this build must not run" is exactly what the operator
    needs to see. Same major-version rule as `contracts/io.py::load_contract`.
    """
    try:
        data: Any = json.loads(str(row["tasks_json"]))
    except (KeyError, ValueError) as exc:
        return None, f"cached entry is not readable JSON ({type(exc).__name__}: {exc})"

    if not isinstance(data, dict):
        return None, "cached entry is not a JSON object"

    typed = cast("dict[str, Any]", data)
    expected_major = str(TasksFile.model_fields["schema_version"].default or "1").split(".")[0]
    found_major = str(typed.get("schema_version", "1")).split(".")[0]
    if found_major != expected_major:
        return None, (
            f"cached entry has unsupported major schema_version '{found_major}' "
            f"(this build reads '{expected_major}') — REQ-GLB-005"
        )

    try:
        return TasksFile.model_validate(typed), "exact hit"
    except Exception as exc:  # noqa: BLE001 - a poisoned row is a miss, with a reason
        return None, f"cached entry failed validation ({type(exc).__name__}: {exc})"


def lookup_spec(
    workspace_path: Path | str,
    goal: str,
    scope: list[str] | None = None,
) -> TasksFile | None:
    """Backwards-compatible view of :func:`lookup_spec_result`."""
    return lookup_spec_result(workspace_path, goal, scope).tasks
