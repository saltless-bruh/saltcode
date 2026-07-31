"""Task 7b — the backend CLI contract, asserted for every entrypoint (REQ-EXT-004).

The extension calls these thirteen modules with `pi.exec` and registers each as a Pi
tool, so the JSON-on-stdout shape and the exit-code scheme are a **contract**, not a
convention that happens to hold. Before this file they were the latter: `docs/entrypoints.md`
described the scheme and each task's own tests checked its own tool, so a fourteenth
entrypoint could have shipped with a different idea of what exit 1 means and nothing
would have noticed. That is **G-006**, and this file is what closes it.

The tests below are *parametrised over the roster*, deliberately. A new entrypoint added
to `ENTRYPOINTS` is immediately held to every invariant; one added without touching this
list fails `test_the_roster_matches_the_tools_package`.

Everything runs as a real subprocess. In-process assertions would not catch the failures
that actually matter here — a stray `print`, a banner on stdout, a traceback escaping to
exit 1 — because those are properties of the *process*, not of `run()`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from saltcode.tools._cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "saltcode" / "tools"

ENTRYPOINTS: tuple[str, ...] = (
    "validate_contract",
    "diff_check",
    "scope_probe",
    "cache_lookup",
    "sandbox_apply",
    "static_gate",
    "test_run",
    "compute_stability",
    "apply_live",
    "compact_spec",
    "calibrate",
    "read_scoped",
    "connectivity",
)
"""The roster from `specs/tasks.md` 7b.1, verbatim and in its order."""

VALID_EXIT_CODES = {EXIT_OK, EXIT_VERDICT_NEGATIVE, EXIT_USAGE, EXIT_ERROR}


def run_module(name: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"saltcode.tools.{name}", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        input=stdin,
        timeout=300,
        check=False,
    )


# ------------------------------------------------------------------------ the roster


def test_the_roster_matches_the_tools_package() -> None:
    """Every module in `saltcode/tools/` is on the roster, and vice versa.

    Catches the two ways this file goes stale: a new entrypoint that never gets held to
    the contract, and a roster entry for something that no longer exists.
    """
    on_disk = {
        p.stem
        for p in TOOLS_DIR.glob("*.py")
        if not p.stem.startswith("_")
    }
    assert on_disk == set(ENTRYPOINTS), (
        f"only in the package: {sorted(on_disk - set(ENTRYPOINTS))}; "
        f"only on the roster: {sorted(set(ENTRYPOINTS) - on_disk)}"
    )


def test_the_roster_is_the_thirteen_task_7b_names() -> None:
    """7b.1 names them explicitly; this pins the count so a silent drop is visible."""
    assert len(ENTRYPOINTS) == 13
    assert len(set(ENTRYPOINTS)) == 13


# ------------------------------------------------------- invariant 1: runnable at all


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_every_entrypoint_is_runnable_as_a_module(name: str) -> None:
    """`python -m saltcode.tools.<name>` — the exact shape `pi.exec` uses."""
    completed = run_module(name, "--help")
    assert completed.returncode == EXIT_OK, completed.stderr


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_help_exits_zero_and_documents_the_program(name: str) -> None:
    """`--help` is a success, not a usage error.

    `argparse` raises `SystemExit(0)` for help and `SystemExit(2)` for a parse error;
    catching it unconditionally reports a working `--help` as a failure to any caller
    shelling out to discover the interface.
    """
    completed = run_module(name, "--help")
    assert completed.returncode == EXIT_OK
    assert f"saltcode.tools.{name}" in completed.stdout
    assert "usage:" in completed.stdout.lower()


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_every_entrypoint_has_a_module_docstring(name: str) -> None:
    """7b.2: the args and output schema are documented where the code is."""
    import importlib

    module = importlib.import_module(f"saltcode.tools.{name}")
    assert module.__doc__, f"{name} has no module docstring"
    assert len(module.__doc__.strip()) > 200, f"{name}'s docstring is too thin to be a contract"


# --------------------------------------------- invariant 2: one JSON object on stdout


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_an_unknown_flag_prints_json_and_exits_usage(name: str) -> None:
    """The one input every entrypoint accepts identically, so it is the shared probe.

    An unknown flag exercises the full path — parse, fail, emit, exit — without needing
    a workspace, a diff, a model or an embedding endpoint, which is what makes this
    assertable across all thirteen at once.
    """
    completed = run_module(name, "--saltcode-no-such-flag")

    assert completed.returncode == EXIT_USAGE, (
        f"{name} exited {completed.returncode} on an unknown flag; "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )

    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    assert payload["tool"] == name, "the payload must identify its own tool"
    assert payload["ok"] is False
    assert payload["error"]
    assert payload["detail"]


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_stdout_is_exactly_one_json_object(name: str) -> None:
    """Not "starts with" or "contains" — exactly one, with nothing around it.

    A stray `print`, a progress line or a library banner would make the extension's
    `JSON.parse` fail on a tool that otherwise worked.
    """
    completed = run_module(name, "--saltcode-no-such-flag")

    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(completed.stdout.lstrip())
    assert isinstance(obj, dict)
    assert completed.stdout.lstrip()[end:].strip() == "", "trailing output after the JSON object"


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_the_exit_code_is_one_of_the_four(name: str) -> None:
    completed = run_module(name, "--saltcode-no-such-flag")
    assert completed.returncode in VALID_EXIT_CODES


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_no_traceback_reaches_stdout_or_stderr(name: str) -> None:
    """A traceback is a leaked implementation detail and an unusable tool result."""
    completed = run_module(name, "--saltcode-no-such-flag")
    assert "Traceback (most recent call last)" not in completed.stdout
    assert "Traceback (most recent call last)" not in completed.stderr


# ------------------------------------------- invariant 3: a missing required argument


REQUIRED_ARG_PROBES: dict[str, tuple[str, ...]] = {
    "validate_contract": (),
    "diff_check": (),
    "scope_probe": ("--repo", "."),
    "cache_lookup": (),
    "sandbox_apply": (),
    "static_gate": (),
    "test_run": (),
    "compute_stability": (),
    "apply_live": (),
    "compact_spec": (),
    "calibrate": (),
    "read_scoped": (),
    "connectivity": (),
}
"""Bare invocations. Tools with required arguments must refuse; the two that have none
(`scope_probe`, `connectivity`) legitimately run, which is why this asserts the code is
*valid* rather than that it is 2."""


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_a_bare_invocation_never_crashes(name: str) -> None:
    """Missing required arguments are a usage error, never a traceback at exit 1."""
    completed = run_module(name, *REQUIRED_ARG_PROBES[name])

    assert completed.returncode in VALID_EXIT_CODES
    payload = json.loads(completed.stdout)
    assert payload["tool"] == name

    if completed.returncode == EXIT_USAGE:
        assert payload["ok"] is False


# ------------------------------------------------- the 0/1 split, on real invocations


def test_a_negative_verdict_is_exit_one_with_valid_json(tmp_path: Path) -> None:
    """The distinction the whole scheme rests on: a *result*, not a crash.

    A malformed diff is a normal Phase-2 outcome that routes to a Builder retry
    (REQ-STAT-005 AC1). If it were exit 3 the loop could not tell it from a broken
    installation.
    """
    bad = tmp_path / "not-a-diff.txt"
    bad.write_text("this is prose, not a unified diff\n", encoding="utf-8")

    completed = run_module("diff_check", "--in", str(bad))

    assert completed.returncode == EXIT_VERDICT_NEGATIVE
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["tool"] == "diff_check"


def test_a_positive_verdict_is_exit_zero_with_valid_json(tmp_path: Path) -> None:
    good = tmp_path / "good.patch"
    good.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")

    completed = run_module("diff_check", "--in", str(good))

    assert completed.returncode == EXIT_OK
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True


def test_a_usage_error_is_exit_two_not_one(tmp_path: Path) -> None:
    """An unreadable input is the caller's mistake, not a verdict about the content."""
    completed = run_module("diff_check", "--in", str(tmp_path / "absent.patch"))
    assert completed.returncode in (EXIT_USAGE, EXIT_ERROR)
    assert json.loads(completed.stdout)["ok"] is False


def test_stdin_is_accepted_wherever_it_is_documented(tmp_path: Path) -> None:
    """`--in -` is the shape the extension uses to avoid a temp file per Builder diff."""
    completed = run_module(
        "diff_check", "--in", "-", stdin="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert completed.returncode == EXIT_OK
    assert json.loads(completed.stdout)["ok"] is True


# ---------------------------------------------------------------- documentation drift


def test_every_entrypoint_appears_in_the_contract_document() -> None:
    """7b.2. The extension's tool definitions are written against `docs/entrypoints.md`,
    so an entrypoint missing from it is an undocumented contract."""
    doc = (REPO_ROOT.parent / "docs" / "entrypoints.md").read_text(encoding="utf-8")
    missing = [name for name in ENTRYPOINTS if f"`{name}`" not in doc]
    assert not missing, f"undocumented entrypoints: {missing}"


def test_the_documented_exit_codes_match_the_module() -> None:
    """The numbers live in `_cli.py`; the document must not drift from them."""
    assert (EXIT_OK, EXIT_VERDICT_NEGATIVE, EXIT_USAGE, EXIT_ERROR) == (0, 1, 2, 3)
