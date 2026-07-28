"""Task 4 — the MCP server over stdio and the per-language LSP backends.

Two things no other test covers:

* **The server as a server.** Everything else calls `outline`/`where_is` as
  Python functions. Here the module is launched as `python -m
  saltcode.mcp.lsp_ast_server`, spoken to over stdio with the MCP SDK, and
  asked for its tool list — which is what the MCP client extension will do
  (REQ-MCP-004 AC1).
* **A real language server.** `pyright-langserver` ships in the backend's own
  environment, so the LSP path — not the regex fallback — is exercised end to
  end (REQ-MCP-003).

The body-leak assertion (REQ-MCP-001 AC1) is made against the fixture's one
statement line, `greet("World")`. It appears nowhere except inside a function
body, so a response containing it could only have come from the source text.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from saltcode.mcp.lsp_backends import (
    LSP_COMMANDS,
    LSPBackend,
    get_lsp_backend,
    get_lsp_command,
    resolve_executable,
)

FIXTURE_DIR = (Path(__file__).parent / "fixtures" / "sample_project").resolve()
BODY_LINE = 'greet("World")'
"""Present only inside `Greeter.run` — the canary for a body leak."""

PYRIGHT_LANGSERVER = Path(sys.executable).parent / "pyright-langserver"
needs_pyright = pytest.mark.skipif(
    not PYRIGHT_LANGSERVER.is_file(),
    reason="pyright-langserver is not installed beside this interpreter",
)


# ------------------------------------------------------- per-language dispatch (4.2)


@pytest.mark.parametrize(
    ("language", "program"),
    [
        ("python", "pyright-langserver"),
        ("typescript", "typescript-language-server"),
        ("javascript", "typescript-language-server"),
        ("rust", "rust-analyzer"),
        ("go", "gopls"),
    ],
)
def test_each_language_maps_to_its_server(language: str, program: str) -> None:
    """REQ-MCP-003 — including AC1's "WHEN the repo is Rust… rust-analyzer"."""
    command = get_lsp_command(language)
    assert Path(command[0]).name == program


def test_language_matching_is_case_insensitive() -> None:
    assert get_lsp_command("Python") == get_lsp_command("python")


def test_an_unsupported_language_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported language"):
        get_lsp_command("cobol")


def test_the_command_table_covers_every_configured_language() -> None:
    """`ProjectConfig.language` and `LSP_COMMANDS` must not drift apart."""
    from saltcode.mcp.lsp_backends import ProjectConfig

    configured = set(ProjectConfig.model_fields["language"].annotation.__args__)  # type: ignore[union-attr]
    assert configured == set(LSP_COMMANDS)


def test_a_server_beside_the_interpreter_is_preferred_over_path(tmp_path: Path) -> None:
    """`pip install` puts console scripts next to `sys.executable`, not on PATH.

    Resolving via PATH alone left an installed `pyright-langserver` invisible
    whenever the venv was not activated — which is how `pi.exec` will run us.
    """
    assert resolve_executable("pyright-langserver") == str(PYRIGHT_LANGSERVER) or not PYRIGHT_LANGSERVER.is_file()


def test_an_unknown_program_resolves_to_its_bare_name() -> None:
    assert resolve_executable("definitely-not-installed-xyz") == "definitely-not-installed-xyz"


# ----------------------------------------------------------- graceful degradation


def test_a_missing_language_server_falls_back_rather_than_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo whose server is not installed still gets symbols from the AST path."""
    (tmp_path / "saltcode.toml").write_text(
        '[project]\nlanguage = "python"\ntest_framework = "pytest"\n', encoding="utf-8"
    )
    (tmp_path / "mod.py").write_text("class Thing:\n    def go(self):\n        pass\n", encoding="utf-8")

    monkeypatch.setattr(
        "saltcode.mcp.lsp_backends.get_lsp_command", lambda _lang: ["definitely-not-installed-xyz"]
    )
    assert get_lsp_backend(tmp_path) is None

    from saltcode.mcp.lsp_backends import fallback_outline

    outline_text = fallback_outline(str(tmp_path / "mod.py"), "python")
    assert "Thing" in outline_text and "go" in outline_text


def test_a_server_that_exits_immediately_is_reported_not_swallowed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression: stderr was DEVNULL, so a broken server degraded in silence.

    A rustup *shim* for an uninstalled `rust-analyzer` component exits 1 with a
    precise explanation; discarding it left "handshake failed: ." in the log,
    since `concurrent.futures.TimeoutError` also stringifies to nothing.
    """
    script = tmp_path / "broken-server"
    script.write_text("#!/bin/sh\necho 'component not installed' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)

    backend = LSPBackend([str(script)], str(tmp_path))
    with caplog.at_level("WARNING"):
        backend.start()

    assert not backend.running
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "exited immediately" in logged
    assert "component not installed" in logged, "the server's own explanation must reach the log"


# ------------------------------------------------------- the real LSP path (4.1/4.2)


@needs_pyright
def test_the_lsp_path_produces_an_outline_with_no_body() -> None:
    """REQ-MCP-003 + REQ-MCP-001 AC1 against a genuine language server."""
    backend = LSPBackend([str(PYRIGHT_LANGSERVER), "--stdio"], str(FIXTURE_DIR))
    backend.start()
    assert backend.running, "pyright-langserver failed to hand shake"

    try:
        import saltcode.mcp.lsp_ast_server as server

        os.environ["SALTCODE_WORKSPACE"] = str(FIXTURE_DIR)
        original = (server._backend, server._backend_initialized)
        server._backend, server._backend_initialized = backend, True
        try:
            outline_text = server.outline("main.py")
            where = server.where_is("Greeter")
        finally:
            server._backend, server._backend_initialized = original
    finally:
        backend.stop()

    assert "Greeter" in outline_text
    assert "run" in outline_text
    assert BODY_LINE not in outline_text, "the outline leaked a statement from inside a body"
    assert BODY_LINE not in where
    assert "main.py" in where


@needs_pyright
def test_an_empty_lsp_result_falls_back_instead_of_returning_nothing() -> None:
    """Regression: a live-but-unhelpful server left the caller worse off.

    `documentSymbol` on a document the server was never told about comes back
    empty, and the empty render was returned as the answer — so installing a
    language server made `outline` *worse* than not having one. The document is
    now opened first, and an empty render falls through to the AST path.
    """
    backend = LSPBackend([str(PYRIGHT_LANGSERVER), "--stdio"], str(FIXTURE_DIR))
    backend.start()
    assert backend.running

    try:
        import saltcode.mcp.lsp_ast_server as server

        os.environ["SALTCODE_WORKSPACE"] = str(FIXTURE_DIR)
        original = (server._backend, server._backend_initialized)
        server._backend, server._backend_initialized = backend, True
        try:
            # Neutralise didOpen so the server stays ignorant of the document —
            # the exact condition that used to yield an empty outline.
            backend.did_open = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
            outline_text = server.outline("main.py")
        finally:
            server._backend, server._backend_initialized = original
    finally:
        backend.stop()

    assert "Greeter" in outline_text, "an empty LSP result must fall through to the AST path"


# ------------------------------------------------ the server over stdio (4.1, 4.6)


def _call_over_stdio() -> tuple[list[str], str]:
    """Launch the MCP server as a subprocess and use it the way a client would."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run() -> tuple[list[str], str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "saltcode.mcp.lsp_ast_server"],
            env={**os.environ, "SALTCODE_WORKSPACE": str(FIXTURE_DIR)},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            result = await session.call_tool("outline", {"file_path": "main.py"})
            text = "".join(getattr(block, "text", "") for block in result.content)
            return sorted(tool.name for tool in listed.tools), text

    return asyncio.run(run())


@pytest.mark.skipif(shutil.which("python") is None and not sys.executable, reason="no interpreter")
def test_the_server_registers_exactly_the_three_ast_tools() -> None:
    """REQ-MCP-001 / REQ-MCP-004 AC1 — what the MCP client extension will bridge.

    Exactly three: anything else appearing here would be bridged into Pi as
    `mcp_saltcode-lsp_*` and handed to Scout, which must never hold a body-
    reading tool. The scoped read is deliberately *not* an MCP tool — it is the
    broker's, gated per role.
    """
    tools, _ = _call_over_stdio()
    assert tools == ["find_references", "outline", "where_is"]
    assert "read_file" not in tools


def test_an_outline_over_stdio_carries_symbols_and_no_body() -> None:
    _, text = _call_over_stdio()
    assert "Greeter" in text
    assert "run" in text
    assert BODY_LINE not in text, "an MCP response leaked a statement from inside a body"
