"""Making the gate's tools reachable *inside* the container (task 9.1).

The security container binds almost nothing (G-C05: binding whole-root left unix
sockets reachable and DNS answering, so the bind list is an explicit allowlist). That
is exactly right for containment and exactly wrong for the static gate, whose whole job
is to run `pyright`, `ruff`, `tsc`, `eslint`, `cargo` or `go` — none of which live under
`/usr` when they come from a virtualenv, `node_modules/.bin`, rustup or a Go install.

So each runner resolves its program on the host first, then asks for two things: the
directory to bind **read-only** into the container, and the `bin` directory to prepend
to `PATH`. Read-only matters — a toolchain the analysed code can rewrite is a toolchain
that can be made to report `clean`.

Resolution order deliberately puts `sys.prefix/bin` ahead of `PATH`, the same fix Task 4
needed for `pyright-langserver`: `pip` installs console scripts beside the interpreter,
so an installed `ruff` is invisible whenever the venv is not activated — which is
precisely how `pi.exec` will invoke this backend.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolchainBinding:
    """Where a program lives on the host and what the container needs to see it."""

    program: str
    executable: Path
    bind_root: Path
    """Bound read-only into the container. The *prefix*, not the binary: a Python
    console script is a shim that imports its package from `../lib`, and `cargo`
    needs its toolchain tree, so binding the file alone yields a tool that starts
    and immediately fails to find itself."""
    bin_dir: Path
    """Prepended to the container's `PATH`."""


def _venv_bin() -> Path:
    """The `bin` directory beside the running interpreter."""
    return Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")


def _candidate_dirs(workspace: Path) -> list[Path]:
    """Where to look for a program, most-specific first.

    A project's own `node_modules/.bin` outranks a global install: a repo pinning
    `typescript@5.4` must be checked by *its* `tsc`, not by whatever is on `PATH`.
    """
    return [
        workspace / "node_modules" / ".bin",
        _venv_bin(),
    ]


def _bind_root_for(executable: Path) -> Path:
    """The directory to bind so the program can find its own runtime.

    `<prefix>/bin/tool` → `<prefix>`; anything else → the containing directory. A
    program under `/usr/bin` needs no bind at all, since `/usr` is already in the
    container's read-only bind list — but returning it is harmless and keeps the
    caller from special-casing.
    """
    parent = executable.parent
    if parent.name in ("bin", "Scripts"):
        return parent.parent
    return parent


def resolve_tool(program: str, workspace: Path | str) -> ToolchainBinding | None:
    """Find `program` on the host, or ``None`` when it is not installed.

    ``None`` is a real answer, not an error: a repo with no `eslint` should get a
    reported "tool not available" from the gate rather than a container that fails
    to exec. What must never happen is a *pass* — see `gate.py`, where a missing
    tool is `unavailable`, never `clean`.
    """
    for directory in _candidate_dirs(Path(workspace).resolve()):
        candidate = directory / program
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve()
            return ToolchainBinding(
                program=program,
                executable=resolved,
                bind_root=_bind_root_for(resolved),
                bin_dir=resolved.parent,
            )

    found = shutil.which(program)
    if found is None:
        return None

    resolved = Path(found).resolve()
    return ToolchainBinding(
        program=program,
        executable=resolved,
        bind_root=_bind_root_for(resolved),
        bin_dir=resolved.parent,
    )


def container_env_for(
    bindings: list[ToolchainBinding], base_env: dict[str, str]
) -> dict[str, str]:
    """`base_env` with every binding's `bin` directory prepended to `PATH`.

    Order is preserved and duplicates dropped, so a repo-local tool stays ahead of a
    global one after de-duplication.
    """
    env = dict(base_env)
    seen: set[str] = set()
    prefix: list[str] = []
    for binding in bindings:
        entry = str(binding.bin_dir)
        if entry not in seen:
            seen.add(entry)
            prefix.append(entry)

    existing = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*prefix, existing]) if existing else os.pathsep.join(prefix)
    return env


def ro_binds_for(bindings: list[ToolchainBinding]) -> list[Path]:
    """The de-duplicated read-only bind roots for a set of bindings."""
    roots: list[Path] = []
    seen: set[str] = set()
    for binding in bindings:
        key = str(binding.bind_root)
        if key not in seen:
            seen.add(key)
            roots.append(binding.bind_root)
    return roots
