"""Task 3.2 — the lookup-time scope fingerprint (REQ-CACHE-002).

Covers Task 3's fifth Done-when leg: *the scope probe returns a sorted module
list via subprocess.*

The contract settled with the maintainer on 2026-07-28: enumerate the repo's
source modules and return sorted relative paths. The goal string is not an
input — it is hashed into the cache key separately, and letting it filter the
fingerprint would make two phrasings of one goal key differently for an
identical tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from saltcode.harness.scope_probe import (
    ALL_SOURCE_EXTENSIONS,
    IGNORED_DIRECTORIES,
    run_scope_probe,
    source_extensions,
)
from saltcode.tools._cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE


def run_tool(*args: str) -> tuple[int, dict[str, Any]]:
    """Run ``python -m saltcode.tools.scope_probe`` and parse its JSON stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.scope_probe", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {}
    if completed.stdout.strip():
        payload = json.loads(completed.stdout)
    return completed.returncode, payload


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A small polyglot repo with the usual noise directories."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(): pass\n", encoding="utf-8")
    (tmp_path / "src" / "db.py").write_text("def query(): pass\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")

    for noisy in ("node_modules", "__pycache__", ".git", ".venv", "dist"):
        (tmp_path / noisy).mkdir()
        (tmp_path / noisy / "ignored.py").write_text("pass\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------------ the probe


def test_returns_sorted_relative_paths(workspace: Path) -> None:
    assert run_scope_probe(workspace) == ["main.py", "src/auth.py", "src/db.py", "web/app.ts"]


def test_result_is_sorted_and_stable(workspace: Path) -> None:
    """The fingerprint feeds a hash, so ordering must not depend on the filesystem."""
    first = run_scope_probe(workspace)
    assert first == sorted(first)
    assert first == run_scope_probe(workspace)


def test_noise_directories_are_skipped(workspace: Path) -> None:
    modules = run_scope_probe(workspace)
    assert not any(part in IGNORED_DIRECTORIES for path in modules for part in Path(path).parts)


def test_non_source_files_are_excluded(workspace: Path) -> None:
    assert "README.md" not in run_scope_probe(workspace)


def test_the_goal_is_not_an_input(workspace: Path) -> None:
    """Two phrasings of one goal must not key differently for the same tree.

    The old implementation token-matched filenames out of the goal, so
    "fix auth.py" and "fix authentication" produced different fingerprints for
    an identical repo — and therefore different cache keys.
    """
    import inspect

    parameters = inspect.signature(run_scope_probe).parameters
    assert "goal" not in parameters


def test_language_narrows_the_extension_set(workspace: Path) -> None:
    assert run_scope_probe(workspace, language="python") == ["main.py", "src/auth.py", "src/db.py"]
    assert run_scope_probe(workspace, language="typescript") == ["web/app.ts"]


def test_unknown_language_falls_back_to_every_extension() -> None:
    """Narrowing on a guess would silently drop half a polyglot tree from the key."""
    assert source_extensions("cobol") == ALL_SOURCE_EXTENSIONS
    assert source_extensions(None) == ALL_SOURCE_EXTENSIONS


def test_project_config_selects_the_language(tmp_path: Path) -> None:
    (tmp_path / "saltcode.toml").write_text(
        '[project]\nlanguage = "typescript"\ntest_framework = "jest"\n', encoding="utf-8"
    )
    (tmp_path / "a.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export {};\n", encoding="utf-8")
    assert run_scope_probe(tmp_path) == ["b.ts"]


def test_empty_repo_yields_an_empty_fingerprint(tmp_path: Path) -> None:
    """REQ-CACHE-002 AC3 — the key then degrades to goal-only."""
    assert run_scope_probe(tmp_path) == []


def test_a_missing_directory_is_empty_not_an_error() -> None:
    assert run_scope_probe("/nonexistent/workspace") == []


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    """A link out of the tree would put foreign paths in the key; a cycle would hang."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "mod.py").write_text("pass\n", encoding="utf-8")

    outside = tmp_path.parent / "outside-tree"
    outside.mkdir(exist_ok=True)
    (outside / "foreign.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    (tmp_path / "self").symlink_to(tmp_path, target_is_directory=True)

    assert run_scope_probe(tmp_path) == ["real/mod.py"]


def test_nested_modules_are_found(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("pass\n", encoding="utf-8")
    assert run_scope_probe(tmp_path) == ["a/b/c/deep.py"]


def test_the_probe_makes_no_model_or_lsp_call(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """"A tool call, NOT a Phase-1 fire" — so nothing here may reach a model.

    Guarded structurally: any outbound HTTP would have to go through httpx, and
    an LSP session through get_lsp_backend. Both are made to explode.
    """
    import httpx

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the scope probe must not make a network or LSP call")

    monkeypatch.setattr(httpx.Client, "post", explode)
    monkeypatch.setattr(httpx.Client, "get", explode)
    monkeypatch.setattr(httpx, "head", explode)
    monkeypatch.setattr("saltcode.mcp.lsp_backends.get_lsp_backend", explode)

    assert run_scope_probe(workspace)


# ------------------------------------------------------------------- the entrypoint


def test_entrypoint_returns_a_sorted_module_list(workspace: Path) -> None:
    code, out = run_tool("--repo", str(workspace))
    assert code == EXIT_OK
    assert out["verdict"] == "probed"
    assert out["scope"] == ["main.py", "src/auth.py", "src/db.py", "web/app.ts"]
    assert out["count"] == 4


def test_entrypoint_honours_an_explicit_scope(workspace: Path) -> None:
    """REQ-CACHE-002 AC2 — given a scope, use it directly; do not probe."""
    code, out = run_tool("--repo", str(workspace), "--scope", "src/db.py", "--scope", "src/auth.py")
    assert code == EXIT_OK
    assert out["verdict"] == "provided"
    assert out["scope"] == ["src/auth.py", "src/db.py"], "sorted, so argument order cannot change the key"


def test_entrypoint_deduplicates_an_explicit_scope(workspace: Path) -> None:
    code, out = run_tool("--repo", str(workspace), "--scope", "a.py", "--scope", "a.py")
    assert out["scope"] == ["a.py"]


def test_entrypoint_reports_an_empty_repo_as_success(tmp_path: Path) -> None:
    code, out = run_tool("--repo", str(tmp_path))
    assert code == EXIT_OK, "an empty fingerprint is AC3's expected answer, not a failure"
    assert out["verdict"] == "empty"
    assert out["scope"] == []


def test_entrypoint_filters_by_language(workspace: Path) -> None:
    code, out = run_tool("--repo", str(workspace), "--language", "python")
    assert code == EXIT_OK
    assert out["scope"] == ["main.py", "src/auth.py", "src/db.py"]


def test_entrypoint_rejects_a_missing_repo() -> None:
    code, out = run_tool("--repo", "/nonexistent/workspace")
    assert code == EXIT_ERROR
    assert out["error"] == "InputError"


def test_entrypoint_rejects_an_unknown_language() -> None:
    code, _ = run_tool("--language", "cobol")
    assert code == EXIT_USAGE
