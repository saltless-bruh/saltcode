import hashlib
from pathlib import Path
from typing import Any

from saltcode.contracts.evaluator_report import EvaluatorReport
from saltcode.contracts.tasks import TasksFile


def compute_spec_hash(goal: str, scope_fingerprint: list[str]) -> str:
    """Computes a SHA-256 hash of the normalized goal + scope fingerprint."""
    normalized_goal = goal.strip().lower()
    scope_str = ",".join(sorted(scope_fingerprint))
    combined = f"{normalized_goal}:{scope_str}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def check_and_advance_phase(
    evaluator_report: EvaluatorReport | dict[str, Any],
    _tasks_file: TasksFile | list[Any] | dict[str, Any],
    goal: str,
    scope_fingerprint: list[str],
    workspace_path: Path | str
) -> bool:
    """Checks the EvaluatorReport status.

    If status == "pass":
    1. Locks the spec (writes .saltcode/.spec_locked).
    2. Computes the spec hash for Spec Cache key.
    3. Returns True to auto-advance to Phase 2.
    """
    status = getattr(evaluator_report, "status", None)
    if status is None and isinstance(evaluator_report, dict):
        status = evaluator_report.get("status")

    if status != "pass":
        return False

    workspace = Path(workspace_path)
    dot_saltcode = workspace / ".saltcode"
    dot_saltcode.mkdir(parents=True, exist_ok=True)

    # 1. Spec Locks
    lock_file = dot_saltcode / ".spec_locked"
    lock_file.write_text("LOCKED", encoding="utf-8")

    # 2. Spec hash is computed and stored
    spec_hash = compute_spec_hash(goal, scope_fingerprint)
    hash_file = dot_saltcode / ".spec_hash"
    hash_file.write_text(spec_hash, encoding="utf-8")

    return True


def is_spec_locked(workspace_path: Path | str) -> bool:
    """Checks if the specification is currently locked."""
    return (Path(workspace_path) / ".saltcode" / ".spec_locked").exists()


def unlock_spec(workspace_path: Path | str) -> None:
    """Unlocks the specification (e.g. for a new sprint)."""
    lock_file = Path(workspace_path) / ".saltcode" / ".spec_locked"
    if lock_file.exists():
        lock_file.unlink()
