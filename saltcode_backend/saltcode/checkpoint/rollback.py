"""Rollback: return the tree to a named checkpoint (task 17.3 · REQ-CKP-009).

`/rollback last` and `/rollback <task_id>` both land here. The backend resolves the
checkpoint, decides whether the reset is safe, performs `git reset --hard <sha>`, rewinds
the checkpoint ledger so `latest_record` agrees with the tree again, and logs the whole
thing to `.saltcode/audit_log.jsonl` (AC1).

**What "refuse on a dirty tree" actually protects.** `git reset --hard` destroys *tracked*
modifications and moves the branch; it leaves untracked files alone. So the check is not
"is `git status` empty" — that would refuse on a stray build artifact that the reset could
not have harmed — it is "will this reset destroy something nobody agreed to lose":

* tracked, modified/staged/deleted files **outside** `.saltcode/` → **blocks**, because
  that is either the human's own editing or an `apply_live` still awaiting a verdict;
* the same under `.saltcode/` → does not block; that is Saltcode's own bookkeeping and
  rewinding it is the point of the operation;
* untracked files → never block, and are reported as *surviving* so the message does not
  imply a loss that will not happen;
* commits between the target and HEAD → reported, because a rollback loses those too and
  AC2's warning would be half a warning without them.

`force=True` overrides the refusal. That is not a loophole — it is the loop's normal
path: after a failed regression the extension *knows* the dirty tree is the uncommitted
apply it just made and that discarding it is the specified routing (REQ-CKP-002 AC2). The
backend cannot tell that apply from a human's edits, so the caller who knows says so.

**Not contained**, for the reason `checkpoint.py` records for `git commit`: a reset
executes no model-written code, and containing it would require `git reset` on the
REQ-SEC-002 allowlist for no gain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from saltcode.checkpoint.checkpoint import (
    CHECKPOINTS_FILE,
    CheckpointError,
    CheckpointRecord,
    git_command,
    read_records,
    require_repo,
)
from saltcode.harness.audit_log import append_entry

ROLLED_BACK_FILE = ".saltcode/checkpoints.rolled_back.jsonl"
"""Where superseded checkpoint records go.

They are moved rather than deleted. `latest_record` has to stop returning a checkpoint
whose commit is no longer in HEAD's history — otherwise REQ-CKP-008's resume check
compares HEAD against a sha that was rolled away and FLAGs a human over Saltcode's own
action. But the records are still evidence of work that happened, so they are preserved
here with the rollback that superseded them.
"""

BLOCKING_PREVIEW = 5
"""How many blocking paths the refusal message names before eliding the rest."""

MIN_STATUS_ENTRY = 4
"""``git status --porcelain -z`` entries are ``XY<space><path>`` — shorter is malformed."""

LOSS_WARNING = (
    "git reset --hard discards every uncommitted change to tracked files and drops any "
    "commit made after the target checkpoint. Work that was never committed cannot be "
    "recovered afterwards."
)
"""REQ-CKP-009 AC2. Carried in every payload, refusal and success alike."""


@dataclass(frozen=True)
class TreeState:
    """What a `git reset --hard` here would and would not destroy."""

    blocking: tuple[str, ...] = field(default_factory=tuple)
    """Tracked, dirty paths outside `.saltcode/` — destroyed by the reset."""

    saltcode_paths: tuple[str, ...] = field(default_factory=tuple)
    """Tracked, dirty paths under `.saltcode/` — Saltcode's own bookkeeping."""

    untracked: tuple[str, ...] = field(default_factory=tuple)
    """Untracked paths. `reset --hard` leaves these in place; reported, never blocking."""

    @property
    def clean_enough(self) -> bool:
        return not self.blocking


@dataclass(frozen=True)
class RollbackResult:
    """The outcome, in the shape the entrypoint emits."""

    ok: bool
    detail: str
    target: CheckpointRecord | None = None
    head_before: str = ""
    head_after: str = ""
    dropped_commits: tuple[str, ...] = field(default_factory=tuple)
    superseded_tasks: tuple[str, ...] = field(default_factory=tuple)
    tree: TreeState = field(default_factory=TreeState)
    forced: bool = False
    warning: str = LOSS_WARNING

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "target": self.target.to_json() if self.target is not None else None,
            "head_before": self.head_before,
            "head_after": self.head_after,
            "dropped_commits": list(self.dropped_commits),
            "superseded_tasks": list(self.superseded_tasks),
            "blocking_paths": list(self.tree.blocking),
            "saltcode_paths": list(self.tree.saltcode_paths),
            "untracked_paths": list(self.tree.untracked),
            "forced": self.forced,
            "warning": self.warning,
        }


# ------------------------------------------------------------------------ inspection


def inspect_tree(repo: Path) -> TreeState:
    """Classify the working tree by what a hard reset would actually destroy."""
    status = git_command(repo, ["status", "--porcelain=v1", "-z"])
    if status.returncode != 0:
        raise CheckpointError(f"git status failed: {status.stderr.strip()}")

    blocking: list[str] = []
    saltcode_paths: list[str] = []
    untracked: list[str] = []

    fields = [entry for entry in status.stdout.split("\0") if entry]
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < MIN_STATUS_ENTRY:
            continue
        code, path = entry[:2], entry[3:]
        # A rename record is followed by its origin path as a separate NUL-delimited
        # field; consume it so it is not read back as a status entry of its own.
        if ("R" in code or "C" in code) and index < len(fields):
            index += 1
        if code == "??":
            untracked.append(path)
        elif path.startswith(".saltcode/"):
            saltcode_paths.append(path)
        else:
            blocking.append(path)

    return TreeState(
        blocking=tuple(blocking),
        saltcode_paths=tuple(saltcode_paths),
        untracked=tuple(untracked),
    )


def resolve_target(
    workspace: Path | str, selector: str, *, sprint_id: str | None = None
) -> CheckpointRecord | None:
    """Resolve `last` or a task id to a checkpoint record.

    A task id resolves to that task's **most recent** checkpoint: a task retried after a
    rollback has more than one, and the newest is the one whose commit is still reachable.
    """
    records = read_records(workspace)
    if sprint_id is not None:
        records = [record for record in records if record.sprint_id == sprint_id]
    if not records:
        return None
    if selector == "last":
        return records[-1]
    for record in reversed(records):
        if record.task_id == selector:
            return record
    return None


def _dropped_commits(repo: Path, target_sha: str) -> tuple[str, ...]:
    """Commit subjects between the target and HEAD, newest first. Empty is fine."""
    listed = git_command(repo, ["log", "--oneline", f"{target_sha}..HEAD"])
    if listed.returncode != 0:
        return ()
    return tuple(line for line in listed.stdout.splitlines() if line.strip())


# --------------------------------------------------------------------------- the reset


def rollback(
    workspace: Path | str,
    selector: str,
    *,
    sprint_id: str | None = None,
    force: bool = False,
) -> RollbackResult:
    """Reset the tree to a checkpoint and rewind the ledger.

    Args:
        workspace: The live tree.
        selector: ``"last"`` or a task id.
        sprint_id: Restrict the search to one sprint.
        force: Proceed despite blocking dirty paths or a non-ancestor target. The caller
            asserting it knows what the dirty tree is — see the module docstring.

    Returns:
        A :class:`RollbackResult`. A refusal is ``ok=False``, not an exception: "this
        would destroy your edits" is an answer the loop routes on.

    Raises:
        CheckpointError: git is missing, the path is not a repository, or a git command
            failed in a way that leaves the outcome unknown.
    """
    repo = Path(workspace).resolve()
    require_repo(repo)

    target = resolve_target(repo, selector, sprint_id=sprint_id)
    if target is None:
        known = ", ".join(sorted({record.task_id for record in read_records(repo)})) or "none"
        return RollbackResult(
            ok=False,
            detail=(
                f"no checkpoint matches {selector!r}"
                + (f" in sprint {sprint_id}" if sprint_id else "")
                + f". Checkpointed tasks: {known}."
            ),
        )

    head = git_command(repo, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        raise CheckpointError(f"could not read HEAD: {head.stderr.strip()}")
    head_before = head.stdout.strip()

    known_sha = git_command(repo, ["cat-file", "-e", f"{target.commit_sha}^{{commit}}"])
    if known_sha.returncode != 0:
        # Not forceable. A sha this repository has never seen is not a risk decision the
        # caller can take responsibility for; it is a wrong repository or a corrupt ledger.
        return RollbackResult(
            ok=False,
            detail=(
                f"checkpoint {target.task_id} names commit {target.commit_sha}, which does "
                "not exist in this repository. The ledger and the repository disagree — "
                "reconcile them by hand rather than resetting somewhere arbitrary."
            ),
            target=target,
            head_before=head_before,
        )

    tree = inspect_tree(repo)
    ancestor = git_command(repo, ["merge-base", "--is-ancestor", target.commit_sha, "HEAD"])
    is_ancestor = ancestor.returncode == 0
    dropped = _dropped_commits(repo, target.commit_sha) if is_ancestor else ()

    if not force and not tree.clean_enough:
        return RollbackResult(
            ok=False,
            detail=(
                f"refusing to roll back to {target.task_id}: {len(tree.blocking)} tracked "
                f"file(s) outside .saltcode/ have uncommitted changes "
                f"({', '.join(tree.blocking[:BLOCKING_PREVIEW])}"
                + (", …" if len(tree.blocking) > BLOCKING_PREVIEW else "")
                + "). Commit or stash them, or pass --force if these are an uncommitted "
                "apply you mean to discard."
            ),
            target=target,
            head_before=head_before,
            dropped_commits=dropped,
            tree=tree,
        )

    if not force and not is_ancestor:
        return RollbackResult(
            ok=False,
            detail=(
                f"refusing to roll back to {target.task_id}: {target.commit_sha[:12]} is not "
                "an ancestor of HEAD, so this would not rewind the branch — it would move it "
                "onto unrelated history. Pass --force if that is genuinely what you want."
            ),
            target=target,
            head_before=head_before,
            tree=tree,
        )

    # Read the ledger BEFORE the reset. If the project happens to track `.saltcode/`, the
    # reset restores that directory to the target commit's state — which is the state
    # *before* this checkpoint's own record was appended, so reading afterwards can find an
    # empty or truncated ledger and rewind nothing. Found by running this end to end: the
    # first rollback deleted the whole checkpoint history. `checkpoint.py` now also keeps
    # `.saltcode/` out of its commits; this ordering makes the rewind correct even where
    # some other tool put it under version control.
    records_before = read_records(repo)

    reset = git_command(repo, ["reset", "--hard", target.commit_sha])
    if reset.returncode != 0:
        raise CheckpointError(f"git reset --hard failed: {reset.stderr.strip()}")

    head_now = git_command(repo, ["rev-parse", "HEAD"])
    head_after = head_now.stdout.strip() if head_now.returncode == 0 else ""

    superseded = rewind_ledger(repo, target, records_before)

    result = RollbackResult(
        ok=True,
        detail=(
            f"tree reset to checkpoint {target.task_id} ({target.commit_sha[:12]}). "
            + (
                f"{len(dropped)} later commit(s) dropped. "
                if dropped
                else "No later commits were dropped. "
            )
            + (
                f"{len(superseded)} later checkpoint record(s) moved to {ROLLED_BACK_FILE}. "
                if superseded
                else ""
            )
            + LOSS_WARNING
        ),
        target=target,
        head_before=head_before,
        head_after=head_after,
        dropped_commits=dropped,
        superseded_tasks=superseded,
        tree=tree,
        forced=force,
    )

    append_entry(
        repo,
        command=["git", "reset", "--hard", target.commit_sha],
        cwd=repo,
        exit_code=reset.returncode,
        stdout=reset.stdout,
        stderr=reset.stderr,
        reason=f"rollback to checkpoint {target.task_id}",
        extra={
            "action": "rollback",
            "task_id": target.task_id,
            "head_before": head_before,
            "head_after": head_after,
            "dropped_commits": list(dropped),
            "superseded_tasks": list(superseded),
            "forced": force,
        },
    )
    return result


def rewind_ledger(
    workspace: Path | str,
    target: CheckpointRecord,
    records: list[CheckpointRecord] | None = None,
) -> tuple[str, ...]:
    """Truncate `checkpoints.jsonl` after `target`, preserving the rest elsewhere.

    Args:
        workspace: The live tree.
        target: The checkpoint the tree was reset to.
        records: The ledger as it stood before the reset. Pass it explicitly whenever a
            `git reset --hard` has already run — see :func:`rollback` for why re-reading
            from disk at that point can silently rewind nothing.

    Returns the task ids that were superseded, oldest first.
    """
    repo = Path(workspace)
    if records is None:
        records = read_records(repo)
    cut = None
    for index in range(len(records) - 1, -1, -1):
        if records[index].commit_sha == target.commit_sha:
            cut = index
            break
    if cut is None:
        return ()

    kept, superseded = records[: cut + 1], records[cut + 1 :]

    ledger = repo / CHECKPOINTS_FILE
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "".join(json.dumps(record.to_json(), sort_keys=True) + "\n" for record in kept),
        encoding="utf-8",
    )

    if not superseded:
        return ()

    archive = repo / ROLLED_BACK_FILE
    with archive.open("a", encoding="utf-8") as handle:
        for record in superseded:
            handle.write(
                json.dumps(
                    {**record.to_json(), "superseded_by_rollback_to": target.commit_sha},
                    sort_keys=True,
                )
                + "\n"
            )

    return tuple(record.task_id for record in superseded)
