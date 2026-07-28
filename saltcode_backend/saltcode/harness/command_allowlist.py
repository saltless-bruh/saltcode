"""What may run inside the container, and nothing else (task 3.3, REQ-SEC-002).

The allowlist is the second half of containment. The container decides *what a
command can reach*; this decides *which commands run at all*. Both are needed:
`rm -rf /` contained still destroys the sandbox and burns a task, and a
`curl | sh` inside a network-less namespace is still a command nobody approved.

**Entries are command *phrases*, not binaries.** REQ-SEC-002 lists `cargo test`,
`cargo check` and `cargo clippy` separately, so matching `cargo` alone would
silently admit `cargo publish`. An entry of *n* words matches only when the
first *n* argv elements match, longest entry first.

**Shell strings are refused outright.** `pytest; rm -rf ~` has argv[0] ==
"pytest" under any naive check, so the allowlist never sees a shell string it
would have to out-parse. A command arrives as argv, or it does not run. A string
is accepted only when it splits into one simple command carrying no shell
metacharacter — that is a convenience for config files, not a parser.

**Fail closed.** Anything unrecognised is refused. Notably `python -m pytest` is
*not* on the default list: `python` can run arbitrary code, so admitting it
would admit everything. Projects that need it add it to `[security] allowed_commands`
deliberately (AC2), which is the point — the decision is recorded, not assumed.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWLIST: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("jest",),
    ("cargo", "test"),
    ("go", "test"),
    ("pyright",),
    ("ruff",),
    ("tsc",),
    ("eslint",),
    ("cargo", "check"),
    ("cargo", "clippy"),
    ("go", "build"),
    ("go", "vet"),
    ("git", "apply"),
)
"""Verbatim from REQ-SEC-002. Overridable per project, never silently extended."""

SHELL_METACHARACTERS = frozenset(";&|<>`$(){}[]!*?~\n\r\\\"'")
"""Any of these in a command string means it is not a single simple command."""


class CommandRefusedError(PermissionError):
    """A command was not on the allowlist. Raised where a refusal must be fatal."""


@dataclass(frozen=True)
class AllowlistDecision:
    """The verdict, with the argv the caller should actually execute.

    ``argv`` is populated only when ``allowed`` — there is nothing to run
    otherwise, and returning a parsed form of a refused command invites someone
    to run it anyway.
    """

    allowed: bool
    reason: str
    argv: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()


def normalize_allowlist(entries: Sequence[str] | Sequence[Sequence[str]] | None) -> tuple[tuple[str, ...], ...]:
    """Accept `["cargo test", ...]` or `[["cargo","test"], ...]`; return the tuple form."""
    if entries is None:
        return DEFAULT_ALLOWLIST

    normalized: list[tuple[str, ...]] = []
    for entry in entries:
        parts = tuple(shlex.split(entry)) if isinstance(entry, str) else tuple(entry)
        if parts:
            normalized.append(parts)
    return tuple(normalized)


def _split_string_command(command: str) -> tuple[tuple[str, ...] | None, str]:
    """Split a command string, refusing anything that is not one simple command."""
    found = SHELL_METACHARACTERS.intersection(command)
    if found:
        listed = " ".join(sorted(found))
        return None, f"command string contains shell metacharacters ({listed}); pass argv instead"

    try:
        parts = tuple(shlex.split(command))
    except ValueError as exc:
        return None, f"command string could not be parsed: {exc}"

    if not parts:
        return None, "empty command"
    return parts, ""


def _program_name(argv0: str) -> str:
    """The bare program name: `/usr/bin/pytest` and `pytest.exe` both match `pytest`."""
    name = Path(argv0).name
    return name[:-4] if name.endswith(".exe") else name


def check_command(
    command: Sequence[str] | str,
    allowlist: Sequence[Sequence[str]] | None = None,
) -> AllowlistDecision:
    """Decide whether ``command`` may run.

    Args:
        command: argv (preferred) or a single-command string.
        allowlist: Entries to match against; defaults to :data:`DEFAULT_ALLOWLIST`.

    Returns:
        An :class:`AllowlistDecision`. Callers log every refusal (REQ-SEC-002 AC1).
    """
    entries = normalize_allowlist(allowlist) if allowlist is not None else DEFAULT_ALLOWLIST

    if isinstance(command, str):
        parts, error = _split_string_command(command)
        if parts is None:
            return AllowlistDecision(allowed=False, reason=error)
    else:
        parts = tuple(command)

    if not parts:
        return AllowlistDecision(allowed=False, reason="empty command")

    # argv[0] may be an absolute path; the rest must match literally.
    candidate = (_program_name(parts[0]), *parts[1:])

    # Longest entry first, so `cargo test` is preferred over a hypothetical `cargo`.
    for entry in sorted(entries, key=len, reverse=True):
        if len(entry) <= len(candidate) and tuple(candidate[: len(entry)]) == tuple(entry):
            return AllowlistDecision(
                allowed=True,
                reason=f"matches allowlist entry {' '.join(entry)!r}",
                argv=parts,
                matched=tuple(entry),
            )

    shown = " ".join(candidate[:2])
    return AllowlistDecision(
        allowed=False,
        reason=(
            f"command {shown!r} is not on the allowlist "
            f"({len(entries)} entries; configure [security] allowed_commands to extend it)"
        ),
    )
