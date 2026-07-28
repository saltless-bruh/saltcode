"""Per-agent tool exposure and the Builder's scoped read (task 4.4).

The broker answers two questions: *which tools does this agent get*, and *may it
read this path*. Design §5.6 fixes the first — Scout gets the AST tools and no
scoped read; the Builder gets the AST tools plus `read_file` limited to
`task.files_affected`; **every other agent gets nothing**, because Architect,
Planner, Test Intent and Evaluator work purely from typed JSON contracts on disk
(design §7). Absent capability beats blocked capability.

This is **defense in depth, not the primary gate.** Under v9 an agent's real
allowlist lives in its sub-agent definition's frontmatter (Task 7.2) with
`pi.on("tool_call")` as the backstop (Task 13.3). The broker is the third layer,
and it exists precisely so a hole in one of the others does not become a leak
(`.claude/rules/privacy-boundary.md`).

**Path containment is done on resolved paths.** Two ways an earlier version let
a read escape, both fixed here and both regression-tested:

* `abs_path.startswith(workspace)` is a *string* prefix test, so a sibling
  directory named `work-evil` passes a check meant to confine reads to `work`.
  Containment is now `Path.is_relative_to`, which compares path components.
* `os.path.abspath` does not follow symlinks, so a link inside the workspace
  pointing at `/etc/shadow` resolved to an in-workspace path and was read.
  Everything is now `Path.resolve()`d — links included — before comparison.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.mcp.lsp_ast_server import find_references, outline, where_is

if TYPE_CHECKING:
    from collections.abc import Callable

AST_TOOLS: dict[str, Callable[..., Any]] = {
    "outline": outline,
    "where_is": where_is,
    "find_references": find_references,
}
"""Symbols and ASTs only — never a file body (REQ-MCP-001)."""

AST_TOOL_ROLES = frozenset({"scout", "builder"})
"""Design §5.6. Everyone else gets no tools at all, so an unrecognised role
fails closed rather than inheriting a default set."""

SCOPED_READ_ROLES = frozenset({"builder"})
"""Only the Builder may see a file body, and only inside `task.files_affected`
(REQ-BLD-002, REQ-ORC-007 AC2). It runs on a local model, so bodies stay on-box."""


class ScopeViolationError(PermissionError):
    """A path outside the workspace or outside `task.files_affected`.

    Subclasses :class:`PermissionError` so existing handlers still catch it.
    """


def normalize_role(agent_role: str) -> str:
    """`"Test Intent"`, `"test-intent"` and `"test_intent"` are the same role."""
    return agent_role.strip().lower().replace(" ", "_").replace("-", "_")


class ToolBroker:
    """Resolves an agent's tool set and enforces the Builder's read scope."""

    def __init__(
        self,
        agent_role: str,
        workspace_path: Path | str,
        files_affected: list[str] | None = None,
    ) -> None:
        self.agent_role = agent_role
        self.role = normalize_role(agent_role)
        self.workspace_path = Path(workspace_path).resolve()

        # Validated at construction, not at read time: a `files_affected` entry
        # pointing out of the workspace is a defective task (the Planner emitted
        # it), and it should surface as that rather than as a puzzling refusal
        # on whichever read happens to hit it first.
        self.files_affected: list[Path] = []
        for entry in files_affected or []:
            resolved = self._resolve(entry)
            if not self._contained(resolved):
                raise ScopeViolationError(
                    f"Access denied: task.files_affected entry {entry!r} resolves to {resolved}, "
                    f"which is outside the workspace directory {self.workspace_path}."
                )
            self.files_affected.append(resolved)

    def _resolve(self, path: str | Path) -> Path:
        """Absolute, symlink-resolved path for ``path`` relative to the workspace."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_path / candidate
        return candidate.resolve()

    def _contained(self, resolved: Path) -> bool:
        """True when ``resolved`` is genuinely inside the workspace.

        Component-wise, never a string prefix: `/repo-evil` is not inside `/repo`.
        """
        return resolved == self.workspace_path or resolved.is_relative_to(self.workspace_path)

    def get_tools(self) -> dict[str, Callable[..., Any]]:
        """The tools this agent may hold, per design §5.6."""
        tools: dict[str, Callable[..., Any]] = {}
        if self.role in AST_TOOL_ROLES:
            tools.update(AST_TOOLS)
        if self.role in SCOPED_READ_ROLES:
            tools["read_file"] = self.read_file
        return tools

    def read_file(self, path: str) -> str:
        """Read a file body, if this agent may and this path is in scope.

        Raises:
            PermissionError: The role may not read bodies, or the path escapes
                the workspace, or it is not in `task.files_affected`.
            FileNotFoundError: In scope, but not on disk.
            IsADirectoryError: In scope, but a directory.
        """
        if self.role not in SCOPED_READ_ROLES:
            raise ScopeViolationError(
                f"Role '{self.agent_role}' is not authorized to read file bodies."
            )

        resolved = self._resolve(path)

        if not self._contained(resolved):
            raise ScopeViolationError(
                f"Access denied: '{path}' resolves outside the workspace directory."
            )

        if resolved not in self.files_affected:
            raise ScopeViolationError(
                f"Access denied: path '{path}' is not in the allowed task.files_affected list."
            )

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if resolved.is_dir():
            raise IsADirectoryError(f"Not a file: {path}")

        return resolved.read_text(encoding="utf-8", errors="ignore")
