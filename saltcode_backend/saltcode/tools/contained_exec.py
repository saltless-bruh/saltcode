"""``saltcode.tools.contained_exec`` — run one allowlisted command inside the container.

The channel REQ-SEC-007 AC1 needs and nothing wider. Pi ships built-in `write`, `edit`
and `bash`; left live they mutate the tree and run commands *outside* the sandbox, which
is the interactive-mode hole DD-13 exists to close. The extension overrides all three, and
every one of those overrides has to reach the container the backend owns — which, until
this module, nothing exposed. `harness/sandbox.py::run_in_container` existed with no CLI
surface, so the overrides could only refuse (G-029).

Two modes, because the two things the built-ins do are different:

* **exec** (`--argv-json`) runs a caller-supplied argv. It goes through
  :func:`~saltcode.harness.command_allowlist.check_command` exactly like every other
  contained command — REQ-SEC-002's list is the whole authorisation, and this entrypoint
  deliberately offers no way to widen it.
* **copy-in** (`--write-path` + `--content-file`) lands a file inside the writable root.
  The argv here is **constructed by this module** (`cp <staged> <target>`), never by the
  caller, so a one-entry allowlist is passed for that call. That is not a hole in
  REQ-SEC-002: the allowlist exists to stop *caller-supplied* commands, and the caller
  supplies no command here — only a destination path and some bytes, both checked.

**What `--sandbox` means, and the tradeoff it carries.** It is the container's writable
root. In Phase 2 that is the disposable worktree and the live tree stays untouched. In
*interactive* mode the extension passes the project directory, because a `write` the human
asked for has to land in the project or it is not a write. The container's other
guarantees are unchanged either way — no network, no `$HOME`, no credentials, isolated PID
namespace, memory/CPU/time ceilings, auto-cleanup — so what interactive mode gives up
against Phase 2 is the read-only *project*, not containment. Stated here rather than
buried, because a reader who assumes `--sandbox` is always disposable would be wrong.

Usage::

    python -m saltcode.tools.contained_exec --sandbox /tmp/wt --argv-json '["pytest","-q"]'
    python -m saltcode.tools.contained_exec --sandbox /repo \\
        --write-path src/thing.py --content-file .saltcode/scratch/write-1.txt

Exit codes: ``0`` the command ran and exited 0; ``1`` it ran and exited non-zero, was
refused by the allowlist, timed out, or the write path was rejected — all *verdicts*, since
a refusal is the expected outcome of asking for something the contract forbids; ``2``
usage; ``3`` internal (no containment backend, unreadable input).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.harness.audit_log import append_entry
from saltcode.harness.sandbox import (
    ContainedResult,
    ContainerLimits,
    NoContainmentBackendError,
    run_in_container,
)
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    fail,
    parse_cli,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "contained_exec"

FORBIDDEN_PATH_RE = re.compile(r"(?:^|/)tests/", re.IGNORECASE)
"""REQ-BLD-003, the same pattern `diffs/apply.py` uses. Copied deliberately: the write
boundary is enforced in three places (`.claude/rules/privacy-boundary.md`), and each has
to hold on its own rather than importing the others' opinion."""

COPY_ALLOWLIST: tuple[tuple[str, ...], ...] = (("cp",),)
"""The one-entry allowlist for copy-in. The argv is built below, not by the caller."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.contained_exec",
        description="Run one allowlisted command, or land one file, inside the security container.",
    )
    parser.add_argument(
        "--sandbox",
        required=True,
        help="The container's writable root: a Phase-2 worktree, or the project in interactive mode.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Workspace whose .saltcode/audit_log.jsonl receives the record (default: --sandbox).",
    )
    parser.add_argument(
        "--argv-json",
        dest="argv_json",
        default=None,
        metavar="JSON",
        help='The command as a JSON array of strings, e.g. \'["pytest","-q"]\'. Exec mode.',
    )
    parser.add_argument(
        "--write-path",
        default=None,
        help="Destination path relative to --sandbox. Copy-in mode.",
    )
    parser.add_argument(
        "--content-file",
        default=None,
        help="File holding the bytes to land at --write-path. Copy-in mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Override the container time limit, in seconds.",
    )
    return parser


def _payload(result: ContainedResult, mode: str, **extra: Any) -> dict[str, Any]:
    verdict = (
        "refused"
        if result.refused
        else "timed_out"
        if result.timed_out
        else "ok"
        if result.exit_code == 0
        else "failed"
    )
    return {
        "tool": TOOL,
        "ok": result.ok,
        "mode": mode,
        "verdict": verdict,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "backend": result.backend,
        "container_id": result.container_id,
        "refused": result.refused,
        "timed_out": result.timed_out,
        "limits_enforced": result.limits_enforced,
        "detail": result.reason
        or (
            f"exited {result.exit_code} inside the {result.backend} container"
            if not result.refused
            else result.reason
        ),
        **extra,
    }


def _resolve_write_target(sandbox: Path, write_path: str) -> tuple[Path | None, str]:
    """Confine the destination to the writable root, and refuse `tests/**`.

    Resolved before the container is built, so a rejected path costs nothing and is
    reported as a refusal rather than surfacing as a failed `cp`.
    """
    if FORBIDDEN_PATH_RE.search(write_path):
        return None, (
            f"refused: {write_path} is under tests/**. Test specs are written once by Test "
            "Intent and are immutable to every other agent and to interactive use (REQ-BLD-003)."
        )

    candidate = (sandbox / write_path).resolve()
    try:
        candidate.relative_to(sandbox)
    except ValueError:
        return None, (
            f"refused: {write_path} resolves to {candidate}, outside the writable root "
            f"{sandbox}. The container's writable area is the boundary, not a suggestion."
        )
    return candidate, ""


def run(cli_args: Sequence[str] | None = None) -> int:  # noqa: PLR0911 - one return per outcome
    parser = build_parser()
    args, code = parse_cli(parser, cli_args, TOOL)
    if args is None:
        return code

    sandbox = Path(args.sandbox)
    if not sandbox.is_dir():
        return fail(
            TOOL, "InputError", f"--sandbox is not a directory: {args.sandbox}", code=EXIT_USAGE
        )
    sandbox = sandbox.resolve()
    repo = Path(args.repo).resolve() if args.repo else sandbox

    exec_mode = args.argv_json is not None
    copy_mode = args.write_path is not None or args.content_file is not None

    if exec_mode == copy_mode:
        return fail(
            TOOL,
            "InputError",
            "give either --argv-json (exec mode) or both --write-path and --content-file "
            "(copy-in mode), not neither and not both.",
            code=EXIT_USAGE,
        )

    argv: list[str] = []
    if exec_mode:
        argv, problem = _parse_argv_json(args.argv_json)
        if problem:
            return fail(TOOL, "InputError", problem, code=EXIT_USAGE)

    limits: ContainerLimits | None = None
    if args.timeout is not None:
        # Only the time ceiling is caller-tunable. Memory and CPU stay wherever project
        # config put them: a caller who could raise those could make a contained run
        # indistinguishable from an uncontained one.
        configured = ContainerLimits.from_settings()
        limits = ContainerLimits(
            memory_mb=configured.memory_mb,
            cpus=configured.cpus,
            timeout_seconds=args.timeout,
        )

    try:
        if copy_mode:
            return _run_copy_in(args, sandbox, repo, limits)
        return _run_exec(argv, sandbox, repo, limits)
    except NoContainmentBackendError as exc:
        # Never a verdict. REQ-SEC-005: with no containment there is no safe way to run
        # this at all, and falling back to the host is the one thing that must not happen.
        return fail(TOOL, "NoContainmentBackendError", str(exc), code=EXIT_ERROR)


def _parse_argv_json(raw: str) -> tuple[list[str], str]:
    """Decode `--argv-json`, refusing anything that is not a non-empty list of strings.

    A JSON array rather than a repeated `--arg` because argparse cannot tell an option
    value of `-rf` from an option: `--arg -rf` parses as a flag, and the callers that
    matter here — a `bash` override forwarding `pytest -q` or `rm -rf /` — pass exactly
    that shape. Getting it wrong turns a refusal into a usage error, which is a *worse*
    failure than it looks: the command is not refused, it is not understood.
    """
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"--argv-json is not valid JSON: {exc}"
    if not isinstance(decoded, list) or not decoded:
        return [], "--argv-json must be a non-empty JSON array of strings"
    words: list[str] = []
    for word in decoded:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(word, str):
            return [], "--argv-json must contain only strings"
        words.append(word)
    return words, ""


def _run_exec(
    argv: list[str], sandbox: Path, repo: Path, limits: ContainerLimits | None
) -> int:
    result = run_in_container(argv, sandbox=sandbox, workspace=repo, limits=limits)
    emit(_payload(result, "exec", argv=argv))
    return EXIT_OK if result.ok else EXIT_VERDICT_NEGATIVE


def _run_copy_in(
    args: argparse.Namespace, sandbox: Path, repo: Path, limits: ContainerLimits | None
) -> int:
    if args.write_path is None or args.content_file is None:
        return fail(
            TOOL,
            "InputError",
            "copy-in mode needs both --write-path and --content-file.",
            code=EXIT_USAGE,
        )

    content_file = Path(args.content_file)
    if not content_file.is_file():
        return fail(
            TOOL, "InputError", f"--content-file is not a file: {args.content_file}", code=EXIT_USAGE
        )
    content_file = content_file.resolve()

    target, refusal = _resolve_write_target(sandbox, args.write_path)
    if target is None:
        append_entry(
            repo,
            command=["contained-write", args.write_path],
            cwd=sandbox,
            exit_code=None,
            refused=True,
            reason=refusal,
        )
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "mode": "copy_in",
                "verdict": "refused",
                "exit_code": 126,
                "stdout": "",
                "stderr": refusal,
                "backend": "none",
                "container_id": "",
                "refused": True,
                "timed_out": False,
                "limits_enforced": False,
                "write_path": args.write_path,
                "detail": refusal,
            }
        )
        return EXIT_VERDICT_NEGATIVE

    # The parent has to exist for `cp` to land the file, and creating it here rather than
    # inside the container keeps the contained argv to a single fixed command.
    target.parent.mkdir(parents=True, exist_ok=True)

    result = run_in_container(
        # Binds are same-path (`--ro-bind-try HOST HOST`), so the staged file is reachable
        # inside the container at the path it already has. No remapping to reason about.
        ["cp", str(content_file), str(target)],
        sandbox=sandbox,
        workspace=repo,
        limits=limits,
        # Fixed argv, so a one-entry allowlist authorises exactly this and nothing else.
        allowlist=COPY_ALLOWLIST,
        extra_ro_binds=[content_file.parent],
    )
    emit(_payload(result, "copy_in", write_path=args.write_path, bytes=content_file.stat().st_size))
    return EXIT_OK if result.ok else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
