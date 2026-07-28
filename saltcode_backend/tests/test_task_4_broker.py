"""Task 4 — the scoped-read broker, tool exposure, and the egress guard.

Covers the Done-when legs that are provable in the backend: no raw body in an
AST response, a Scout scoped-read rejected, a Builder read inside
`task.files_affected` allowed and outside rejected, and the network-egress
assertion. The remaining leg — the server reachable as `mcp_saltcode-lsp_*` and
gated at `tool_call` — needs an MCP client extension and the extension's own
handler (Task 13.3), so it is Task 20.1's to prove.

Three of these are regression tests for defects found in the pre-existing
broker, each of which read a file the Builder had no right to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from saltcode.mcp.broker import AST_TOOLS, ScopeViolationError, ToolBroker, normalize_role
from saltcode.mcp.lsp_ast_server import _is_loopback, install_egress_guard

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace plus the two things a path check must not be fooled by:
    a sibling directory sharing its name prefix, and an escaping symlink."""
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "in_scope.py").write_text("SCOPED\n", encoding="utf-8")
    (ws / "out_of_scope.py").write_text("NOT SCOPED\n", encoding="utf-8")

    sibling = tmp_path / "repo-evil"
    sibling.mkdir()
    (sibling / "secret.py").write_text("SIBLING SECRET\n", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "creds.txt").write_text("OUTSIDE SECRET\n", encoding="utf-8")
    (ws / "link.txt").symlink_to(outside / "creds.txt")

    nested = ws / "pkg"
    nested.mkdir()
    (nested / "mod.py").write_text("NESTED\n", encoding="utf-8")
    (nested / "up.txt").symlink_to(outside / "creds.txt")
    return ws


# ------------------------------------------------------- tool exposure (design §5.6)


def test_scout_gets_ast_tools_and_no_scoped_read(workspace: Path) -> None:
    """REQ-ORC-007 AC1, REQ-SCT-001 — Scout never sees a file body."""
    tools = ToolBroker("Scout", workspace).get_tools()
    assert set(tools) == set(AST_TOOLS)
    assert "read_file" not in tools


def test_builder_gets_ast_tools_and_the_scoped_read(workspace: Path) -> None:
    """REQ-ORC-007 AC2."""
    tools = ToolBroker("Builder", workspace, files_affected=["in_scope.py"]).get_tools()
    assert set(tools) == {*AST_TOOLS, "read_file"}


@pytest.mark.parametrize("role", ["Architect", "Planner", "Test Intent", "Evaluator", "Auditor"])
def test_every_other_agent_gets_no_tools(role: str, workspace: Path) -> None:
    """Design §5.6: "others: none".

    Architect, Planner, Test Intent and Evaluator work purely from typed JSON
    contracts on disk (design §7), so they need no tools at all. An earlier
    broker handed the AST tools to every role, which is not a body leak but is
    a least-privilege breach: absent capability beats blocked capability.
    """
    assert ToolBroker(role, workspace).get_tools() == {}


def test_an_unrecognised_role_fails_closed(workspace: Path) -> None:
    assert ToolBroker("typo-role", workspace).get_tools() == {}


@pytest.mark.parametrize("spelling", ["Test Intent", "test-intent", "test_intent", "  TEST INTENT  "])
def test_role_spelling_is_normalised(spelling: str) -> None:
    assert normalize_role(spelling) == "test_intent"


# -------------------------------------------------------------- the scoped read


def test_a_read_inside_files_affected_succeeds(workspace: Path) -> None:
    broker = ToolBroker("Builder", workspace, files_affected=["in_scope.py"])
    assert broker.read_file("in_scope.py") == "SCOPED\n"


def test_a_read_outside_files_affected_is_rejected(workspace: Path) -> None:
    """REQ-BLD-002 AC1 — in the workspace is not the same as in scope."""
    broker = ToolBroker("Builder", workspace, files_affected=["in_scope.py"])
    with pytest.raises(PermissionError, match="not in the allowed task.files_affected"):
        broker.read_file("out_of_scope.py")


def test_scout_is_denied_the_scoped_read_outright(workspace: Path) -> None:
    """REQ-SCT-001 AC1 — not merely absent from its toolset; refused if called."""
    broker = ToolBroker("Scout", workspace)
    with pytest.raises(PermissionError, match="not authorized to read file bodies"):
        broker.read_file("in_scope.py")


def test_a_traversal_read_is_rejected(workspace: Path) -> None:
    broker = ToolBroker("Builder", workspace, files_affected=["in_scope.py"])
    with pytest.raises(PermissionError, match="outside the workspace directory"):
        broker.read_file("../../etc/passwd")


# ------------------------------------------------- regressions: three real escapes


def test_a_sibling_directory_sharing_the_name_prefix_is_rejected(workspace: Path) -> None:
    """Regression: the containment check was a *string* prefix test.

    `/tmp/x/repo-evil/secret.py`.startswith(`/tmp/x/repo`) is True, so a
    directory merely named like the workspace passed a check meant to confine
    reads to it. Containment is now component-wise.
    """
    with pytest.raises(ScopeViolationError, match="outside the workspace directory"):
        ToolBroker("Builder", workspace, files_affected=["../repo-evil/secret.py"])


def test_a_symlink_out_of_the_workspace_is_rejected(workspace: Path) -> None:
    """Regression: `os.path.abspath` does not follow symlinks.

    A link inside the workspace pointing at a file outside it produced an
    in-workspace path string, so both checks passed and the target was read.
    """
    with pytest.raises(ScopeViolationError, match="outside the workspace"):
        ToolBroker("Builder", workspace, files_affected=["link.txt"])


def test_a_nested_symlink_escape_is_rejected(workspace: Path) -> None:
    with pytest.raises(ScopeViolationError, match="outside the workspace"):
        ToolBroker("Builder", workspace, files_affected=["pkg/up.txt"])


def test_an_escaping_entry_is_refused_at_construction(workspace: Path) -> None:
    """A bad `files_affected` is a defective task, so it surfaces immediately.

    Waiting until the read makes it look like a puzzling permission failure on
    whichever path happened to be tried first.
    """
    with pytest.raises(ScopeViolationError, match="task.files_affected entry"):
        ToolBroker("Builder", workspace, files_affected=["in_scope.py", "../repo-evil/secret.py"])


def test_an_absolute_in_scope_path_still_works(workspace: Path) -> None:
    broker = ToolBroker("Builder", workspace, files_affected=[str(workspace / "in_scope.py")])
    assert broker.read_file(str(workspace / "in_scope.py")) == "SCOPED\n"


def test_a_symlink_that_stays_inside_the_workspace_is_allowed(workspace: Path) -> None:
    """Resolving must not over-block: an internal link is still in scope."""
    (workspace / "alias.py").symlink_to(workspace / "in_scope.py")
    broker = ToolBroker("Builder", workspace, files_affected=["alias.py"])
    assert broker.read_file("alias.py") == "SCOPED\n"


def test_a_missing_file_in_scope_is_not_a_permission_error(workspace: Path) -> None:
    broker = ToolBroker("Builder", workspace, files_affected=["in_scope.py"])
    (workspace / "in_scope.py").unlink()
    with pytest.raises(FileNotFoundError):
        broker.read_file("in_scope.py")


def test_a_directory_in_scope_is_refused(workspace: Path) -> None:
    broker = ToolBroker("Builder", workspace, files_affected=["pkg"])
    with pytest.raises(IsADirectoryError):
        broker.read_file("pkg")


# ------------------------------------------------------ no raw body in AST output


def test_no_ast_tool_returns_a_file_body() -> None:
    """REQ-MCP-001 AC1 — the whole point of giving API-routed agents the AST.

    Asserted against a line the fixture contains only inside a function body, so
    a response carrying it could only have come from the source text.
    """
    import os

    os.environ["SALTCODE_WORKSPACE"] = str(FIXTURE_DIR)
    body_only = (FIXTURE_DIR / "main.py").read_text(encoding="utf-8")
    body_lines = [
        line.strip()
        for line in body_only.splitlines()
        if line.startswith("        ") and line.strip() and not line.strip().startswith(("def ", "class ", "#"))
    ]
    assert body_lines, "the fixture must contain at least one indented body line to test against"

    from saltcode.mcp.lsp_ast_server import find_references, outline, where_is

    responses = [
        outline("main.py"),
        where_is("Greeter"),
        "\n".join(find_references("Greeter", "main.py")),
    ]
    for response in responses:
        for line in body_lines:
            assert line not in response, f"an AST response leaked a body line: {line!r}"


# ------------------------------------------------------------------ egress guard


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.53", "localhost", "LOCALHOST", "::1", "api.localhost"]
)
def test_loopback_hosts_are_allowed(host: str) -> None:
    assert _is_loopback(host)


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "192.168.1.5", "10.0.0.7", "api.deepseek.com", "example.com", ""]
)
def test_everything_else_is_refused(host: str) -> None:
    """REQ-MCP-002 — the unknown case must be the refusing one."""
    assert not _is_loopback(host)


def test_an_external_connect_raises() -> None:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(PermissionError, match="blocked by Saltcode privacy boundary"):
            sock.connect(("8.8.8.8", 80))
    finally:
        sock.close()


def test_connect_ex_is_guarded_too() -> None:
    """Regression: only `connect` was patched.

    `connect_ex` returns an errno instead of raising, so a caller preferring it
    had an unguarded path straight to the network.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(PermissionError, match="blocked by Saltcode privacy boundary"):
            sock.connect_ex(("8.8.8.8", 80))
    finally:
        sock.close()


def test_installing_the_guard_twice_does_not_recurse() -> None:
    """Regression: re-installing captured the wrapper as "the original".

    The next connect would then recurse until the stack blew.
    """
    import socket

    install_egress_guard()
    install_egress_guard()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(PermissionError):
            sock.connect(("8.8.8.8", 80))
    finally:
        sock.close()
