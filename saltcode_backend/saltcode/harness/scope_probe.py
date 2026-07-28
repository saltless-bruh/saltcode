"""Lookup-time scope fingerprint for the spec cache (task 3.2, REQ-CACHE-002).

The Spec-Cache key is `sha256(normalized_goal + scope_fingerprint)`. After
Phase 1 the fingerprint is `sorted(tasks.json[*].files_affected)` — but *before*
Phase 1 there is no `tasks.json`, which is the chicken-and-egg the scope probe
exists to break. It answers "what is in scope here?" cheaply enough to run
before every sprint: **a filesystem walk, no LSP session, no model call**. It is
a tool call, never a Phase-1 fire, so the one-fire-per-sprint invariant holds
(REQ-ORC-001).

**Amended 2026-07-28 (maintainer decision).** Task 3.2, REQ-CACHE-002 and design
§11.2 all described this as "a single `outline` MCP call". That could not be
implemented as written: `outline(file_path)` takes one file and returns that
file's symbols, so no single call yields a repo-wide module list. The intent the
proposal states — *cheap, a tool call, not a Phase-1 fire* — is preserved
exactly; only the mechanism description was wrong. The probe enumerates modules
instead, which also makes the store-time and lookup-time fingerprints the same
*kind* of object (a sorted path list), and makes AC3 fall out naturally: an
empty repo yields an empty fingerprint and the key degrades to goal-only.

The goal string is deliberately **not** an input. It is already hashed into the
cache key separately, and letting it filter the fingerprint would make two
phrasings of one goal produce different keys for an identical tree.
"""

from __future__ import annotations

from pathlib import Path

IGNORED_DIRECTORIES = frozenset(
    {
        ".git", ".hg", ".svn",
        ".saltcode",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        ".venv", "venv", "env",
        "node_modules", "bower_components",
        "build", "dist", "out", "target", "vendor",
        ".next", ".nuxt", ".svelte-kit",
        "coverage", "htmlcov",
        ".idea", ".vscode",
    }
)
"""Skipped wholesale — the bulk of a repo's file count and none of its identity.
Walking `node_modules` would also make the probe slow enough to stop being cheap."""

LANGUAGE_EXTENSIONS: dict[str, frozenset[str]] = {
    "python": frozenset({".py", ".pyi"}),
    "typescript": frozenset({".ts", ".tsx"}),
    "javascript": frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
}

ALL_SOURCE_EXTENSIONS: frozenset[str] = frozenset[str]().union(*LANGUAGE_EXTENSIONS.values())


def source_extensions(language: str | None) -> frozenset[str]:
    """Extensions that count as source. Unknown or absent language → all of them.

    Falling back to the union rather than to a guess keeps a polyglot repo's
    fingerprint complete; narrowing it would silently drop half the tree from
    the cache key.
    """
    if language is None:
        return ALL_SOURCE_EXTENSIONS
    return LANGUAGE_EXTENSIONS.get(language.lower(), ALL_SOURCE_EXTENSIONS)


def detect_language(workspace: Path) -> str | None:
    """Read `language` from project config, or ``None`` when there is no config."""
    try:
        from saltcode.mcp.lsp_backends import load_project_config

        return load_project_config(workspace).language
    except Exception:
        # No config, unreadable config, invalid language — all mean the same
        # thing here: fall back to every known source extension.
        return None


def run_scope_probe(
    workspace_path: Path | str,
    *,
    language: str | None = None,
    extensions: frozenset[str] | None = None,
) -> list[str]:
    """Return the workspace's source modules as sorted, repo-relative POSIX paths.

    Args:
        workspace_path: The target repository.
        language: Overrides project config; ``None`` reads `saltcode.toml`.
        extensions: Overrides the language's extension set entirely.

    Returns:
        Sorted relative paths, e.g. ``["src/auth.py", "src/db.py"]``. Empty when
        the repo has no source files, which REQ-CACHE-002 AC3 expects: the
        fingerprint is empty and the key degrades to goal-only.
    """
    workspace = Path(workspace_path).resolve()
    if not workspace.is_dir():
        return []

    wanted = extensions if extensions is not None else source_extensions(language or detect_language(workspace))

    modules: list[str] = []
    stack: list[Path] = [workspace]
    while stack:
        directory = stack.pop()
        try:
            children = list(directory.iterdir())
        except OSError:
            # An unreadable directory is skipped, not fatal: a fingerprint that
            # is missing one subtree is still better than no sprint at all.
            continue

        for child in children:
            if child.is_symlink():
                # Not followed: a symlink out of the tree would put foreign paths
                # in the fingerprint, and a cycle would not terminate.
                continue
            if child.is_dir():
                if child.name not in IGNORED_DIRECTORIES:
                    stack.append(child)
            elif child.suffix in wanted:
                modules.append(child.relative_to(workspace).as_posix())

    return sorted(modules)
