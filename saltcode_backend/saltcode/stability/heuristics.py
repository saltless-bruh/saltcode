"""Zero-cost anti-gaming heuristics over a Builder diff (task 10.2, REQ-AUD-001).

The failure these exist to catch is specific: the static gate is clean, the task spec's
tests are green, and the code does not implement the task. It satisfies the *tests*.
Every gate before the Auditor is by construction blind to this — `pyright` type-checks a
function that returns a hardcoded fixture, and the spec test passes because that fixture
is what it asserts.

REQ-AUD-001 names three patterns, and this module implements exactly those three:

1. **Return literals matching test fixtures** — `return "alice@example.com"` where the
   spec asserts that address.
2. **Empty or throw-only bodies under test** — a function whose whole body is `pass`,
   `todo!()`, `throw new Error(...)`.
3. **Branches keyed on known test inputs** — `if user_id == 42:` where 42 is the spec's
   fixture, i.e. a special case for the test rather than an implementation.

**They are heuristics, and are treated as such.** REQ-AUD-001 AC1 makes a flag
*suspicion*, not a verdict: the flags are handed to the judgment and a `pass` clears
them. That division is deliberate. These run on added diff lines with no parser and no
type information, across four languages, so they are cheap and imprecise — the wrong
place to make a final decision, and the right place to make the model look harder.

**Only added lines are examined.** A `-` line is what the Builder removed; flagging it
would report the pre-existing code's sins as the Builder's.

**Literals must be distinctive to count.** Matching on `0`, `1`, `""` or `true` would
flag essentially every diff, and a detector that always fires carries no information —
it would turn the Auditor's `gaming_suspected` route into the default and burn the
retry budget on correct code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MIN_STRING_LITERAL_LENGTH = 3
"""Below this a string literal is not evidence of anything — `""`, `"a"`, `"id"` appear
in unrelated code constantly."""

MIN_NUMERIC_LITERAL_VALUE = 10
"""Numbers below this (and single digits generally) are indices, flags and lengths, not
fixtures. `42` is a fixture; `1` is a loop bound."""

HeuristicName = str

FIXTURE_RETURN = "fixture_literal_return"
EMPTY_BODY = "empty_or_throw_only_body"
TEST_KEYED_BRANCH = "test_input_keyed_branch"

# The two branches must be disjoint: `\\.` and `(?!\1).` both match a backslash, so
# an unterminated literal containing a run of escapes makes the engine explore every
# partition of that run — measured at ~4x per 4 extra backslashes. The input is a
# model-produced diff, so a pathological line is reachable, not hypothetical.
_STRING_LITERAL_RE = re.compile(r"""(['"])(?P<body>(?:\\.|(?!\1)[^\\])*)\1""")
_NUMBER_LITERAL_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")

_RETURN_RE = re.compile(r"^\s*(?:return|Ok\(|=>)\s*(?P<value>.+?)\s*;?\s*$")

_EMPTY_BODY_RE = re.compile(
    r"^\s*(?:"
    r"pass"
    r"|\.\.\."
    r"|return\s*(?:None|null|nil|undefined)?\s*;?"
    r"|raise\s+NotImplementedError"
    r"|throw\s+new\s+\w*Error"
    r"|todo!\(\)|unimplemented!\(\)|panic!\("
    r"|// ?TODO|# ?TODO"
    r")\s*;?\s*$"
)

_SIGNATURE_RE = re.compile(
    r"^\s*(?:"
    r"(?:async\s+)?def\s+\w+"
    r"|(?:export\s+)?(?:async\s+)?function\s+\w+"
    r"|(?:pub\s+)?(?:async\s+)?fn\s+\w+"
    r"|func\s+\w+"
    r"|\w+\s*\([^)]*\)\s*(?::\s*[\w<>\[\], |]+)?\s*(?:=>|\{)"
    r")"
)

_BRANCH_RE = re.compile(r"^\s*(?:if|elif|else\s+if|match|switch|case|when)\b(?P<cond>.*)$")


@dataclass(frozen=True)
class HeuristicFlag:
    """One suspicion, with the line that raised it.

    Carries the evidence rather than just a name: REQ-AUD-003 routes
    `gaming_suspected` to a Builder retry "with the flagged patterns", so the retry
    prompt needs the actual line to be actionable rather than accusatory.
    """

    name: HeuristicName
    line: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "line": self.line.strip()[:200], "detail": self.detail}


@dataclass
class HeuristicReport:
    """Every flag raised over a diff."""

    flags: list[HeuristicFlag] = field(default_factory=list[HeuristicFlag])

    @property
    def fired(self) -> bool:
        return bool(self.flags)

    @property
    def names(self) -> list[str]:
        """The distinct heuristic names that fired, in first-seen order."""
        seen: list[str] = []
        for flag in self.flags:
            if flag.name not in seen:
                seen.append(flag.name)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "fired": self.fired,
            "names": self.names,
            "flags": [f.to_dict() for f in self.flags],
        }

    def summary(self) -> str:
        """A sentence for the Builder retry and the judgment prompt."""
        if not self.fired:
            return "no anti-gaming heuristic fired"
        return f"{len(self.flags)} anti-gaming flag(s): {', '.join(self.names)}"


def added_lines(diff: str) -> list[str]:
    """The diff's added lines, without the `+` marker and without file headers.

    `+++ b/path` starts with `+` and is not a code line; excluding it matters because
    a path can easily contain a spec literal.
    """
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
    return out


def extract_fixture_literals(spec_content: str) -> set[str]:
    """The distinctive literals a task spec asserts on.

    These are what a gaming implementation hardcodes. Short strings and small numbers
    are dropped — see :data:`MIN_STRING_LITERAL_LENGTH` and
    :data:`MIN_NUMERIC_LITERAL_VALUE`.
    """
    literals: set[str] = set()

    for match in _STRING_LITERAL_RE.finditer(spec_content):
        body = match.group("body")
        if len(body) >= MIN_STRING_LITERAL_LENGTH:
            literals.add(body)

    for match in _NUMBER_LITERAL_RE.finditer(spec_content):
        raw = match.group(1)
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - the regex only matches numerals
            continue
        if abs(value) >= MIN_NUMERIC_LITERAL_VALUE:
            literals.add(raw)
    return literals


def _mentions_literal(text: str, literals: set[str]) -> str | None:
    """The first fixture literal appearing in ``text``, or ``None``.

    Longest first, so a report names the most specific match rather than an incidental
    substring of it. The literal itself breaks ties, because `literals` is a set and
    string hashing is randomised per process — without it, two equal-length fixtures
    would put a different one in `detail` on each run, and the emitted JSON would stop
    being reproducible.
    """
    for literal in sorted(literals, key=lambda s: (-len(s), s)):
        if literal and literal in text:
            return literal
    return None


def check_fixture_literal_returns(lines: list[str], literals: set[str]) -> list[HeuristicFlag]:
    """Return statements handing back a value the spec asserts (REQ-AUD-001)."""
    flags: list[HeuristicFlag] = []
    for line in lines:
        match = _RETURN_RE.match(line)
        if match is None:
            continue
        found = _mentions_literal(match.group("value"), literals)
        if found is not None:
            flags.append(
                HeuristicFlag(
                    name=FIXTURE_RETURN,
                    line=line,
                    detail=f"returns the spec fixture {found!r} rather than computing it",
                )
            )
    return flags


def check_empty_or_throw_only_bodies(lines: list[str]) -> list[HeuristicFlag]:
    """Functions whose entire added body is a stub.

    A signature immediately followed by exactly one stub line, with nothing else added
    for that function. Line-oriented rather than parsed, so it sees the shape of a stub
    without needing four language front ends.
    """
    flags: list[HeuristicFlag] = []
    for i, line in enumerate(lines):
        if _SIGNATURE_RE.match(line) is None:
            continue
        body = [candidate for candidate in lines[i + 1 : i + 4] if candidate.strip()]
        if not body:
            continue
        if _EMPTY_BODY_RE.match(body[0]) is None:
            continue
        # Anything real after the stub means it is a guard clause, not the whole body.
        rest = [c for c in body[1:] if _SIGNATURE_RE.match(c) is None]
        if any(_EMPTY_BODY_RE.match(c) is None for c in rest):
            continue
        flags.append(
            HeuristicFlag(
                name=EMPTY_BODY,
                line=line,
                detail=f"body is only {body[0].strip()!r}; the task spec passes against a stub",
            )
        )
    return flags


def check_test_keyed_branches(lines: list[str], literals: set[str]) -> list[HeuristicFlag]:
    """Conditionals that special-case a value the spec supplies as input."""
    flags: list[HeuristicFlag] = []
    for line in lines:
        match = _BRANCH_RE.match(line)
        if match is None:
            continue
        found = _mentions_literal(match.group("cond"), literals)
        if found is not None:
            flags.append(
                HeuristicFlag(
                    name=TEST_KEYED_BRANCH,
                    line=line,
                    detail=f"branches on the spec fixture {found!r}, so the test takes a special path",
                )
            )
    return flags


def run_heuristics(diff: str, spec_content: str) -> HeuristicReport:
    """Run all three REQ-AUD-001 heuristics over a diff.

    Args:
        diff: The Builder's unified diff. Only added lines are examined.
        spec_content: The task spec, mined for the fixtures a gaming implementation
            would hardcode. With no spec, the two literal-matching heuristics have
            nothing to match on and only the stub-body check runs — reported honestly
            rather than treated as "nothing suspicious".
    """
    lines = added_lines(diff)
    literals = extract_fixture_literals(spec_content)

    flags = [
        *check_fixture_literal_returns(lines, literals),
        *check_empty_or_throw_only_bodies(lines),
        *check_test_keyed_branches(lines, literals),
    ]
    return HeuristicReport(flags=flags)
