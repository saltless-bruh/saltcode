import os
from pathlib import Path

import pytest

from saltcode.mcp.broker import ToolBroker
from saltcode.mcp.lsp_ast_server import find_references, outline, where_is
from saltcode.mcp.lsp_backends import (
    fallback_find_references,
    fallback_outline,
    fallback_where_is,
    load_project_config,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_project"


def test_load_project_config() -> None:
    config = load_project_config(FIXTURE_DIR)
    assert config.language == "python"
    assert config.test_framework == "pytest"
    assert config.test_runner_cmd == "pytest"


def test_fallback_outline_python() -> None:
    py_file = FIXTURE_DIR / "main.py"
    outline_str = fallback_outline(str(py_file), "python")

    # Assert symbol outlines are returned
    assert "- Class: Greeter" in outline_str
    assert "  - Method: run" in outline_str

    # Ensure no raw file body/contents are included
    assert 'greet("World")' not in outline_str


def test_fallback_outline_typescript() -> None:
    ts_file = FIXTURE_DIR / "index.ts"
    outline_str = fallback_outline(str(ts_file), "typescript")

    assert "- Class: Greeter" in outline_str
    assert "- Method: run" in outline_str

    assert 'greet("World");' not in outline_str


def test_fallback_where_is_python() -> None:
    loc_greet = fallback_where_is(FIXTURE_DIR, "greet", "main.py", "python")
    assert "file:utils.py" in loc_greet
    assert "line:1" in loc_greet

    loc_greeter = fallback_where_is(FIXTURE_DIR, "Greeter", None, "python")
    assert "file:main.py" in loc_greeter
    assert "line:3" in loc_greeter


def test_fallback_find_references_python() -> None:
    refs = fallback_find_references(FIXTURE_DIR, "greet", "main.py", "python")
    # Should find references in main.py and utils.py
    assert any("file:main.py" in r for r in refs)
    assert any("file:utils.py" in r for r in refs)


def test_mcp_tools_no_raw_body() -> None:
    # Set the environment variable for workspace path to match fixture directory
    os.environ["SALTCODE_WORKSPACE"] = str(FIXTURE_DIR)

    # Test outline tool
    out = outline("main.py")
    assert "- Class: Greeter" in out
    assert "def run" not in out

    # Test where_is tool
    loc = where_is("Greeter", "main.py")
    assert "file:main.py" in loc
    assert "line:" in loc

    # Test find_references tool
    refs = find_references("greet", "main.py")
    assert len(refs) >= 1
    assert all("file:" in r for r in refs)


def test_tool_broker_roles() -> None:
    # Scout role: should get AST tools, but NO read_file
    scout_broker = ToolBroker("Scout", FIXTURE_DIR)
    scout_tools = scout_broker.get_tools()
    assert "outline" in scout_tools
    assert "where_is" in scout_tools
    assert "find_references" in scout_tools
    assert "read_file" not in scout_tools

    # Builder role: should get AST tools AND read_file
    builder_broker = ToolBroker("Builder", FIXTURE_DIR, files_affected=["main.py"])
    builder_tools = builder_broker.get_tools()
    assert "outline" in builder_tools
    assert "read_file" in builder_tools

    # Architect role: should get AST tools, but NO read_file
    arch_broker = ToolBroker("Architect", FIXTURE_DIR)
    arch_tools = arch_broker.get_tools()
    assert "outline" in arch_tools
    assert "read_file" not in arch_tools


def test_tool_broker_scoped_read_file() -> None:
    # Scoped reads restricted to files_affected
    broker = ToolBroker("Builder", FIXTURE_DIR, files_affected=["main.py"])

    # Authorize read of main.py
    content = broker.read_file("main.py")
    assert "class Greeter" in content

    # Unauthorized read of utils.py (not in files_affected)
    with pytest.raises(PermissionError, match="not in the allowed task.files_affected"):
        broker.read_file("utils.py")

    # Unauthorized read of file outside workspace (traversal prevention)
    with pytest.raises(PermissionError, match="outside the workspace directory"):
        broker.read_file("../../../pyproject.toml")

    # Non-builder role calling read_file directly
    non_builder_broker = ToolBroker("Scout", FIXTURE_DIR)
    with pytest.raises(PermissionError, match="not authorized to read file bodies"):
        non_builder_broker.read_file("main.py")


def test_network_egress_blocking() -> None:
    # The modified socket connection should raise PermissionError on external connections
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allowed: localhost
    import contextlib
    with contextlib.suppress(ConnectionRefusedError, OSError):
        s.connect(("127.0.0.1", 12345))

    # Denied: external host
    with pytest.raises(PermissionError, match="blocked by Saltcode privacy boundary"):
        s.connect(("8.8.8.8", 80))
