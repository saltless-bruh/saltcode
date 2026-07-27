---
name: saltcode-auditor
description: Guides the Auditor agent to detect gaming behavior and compute multi-pass stability metrics for faithfulness verification of Builder diffs.
---

# Saltcode Auditor Skill

Use this when auditing a completed task's unified diff in Phase 2.
Contract: REQ-AUD-001..005, REQ-CON-006. Shape: `specs/design.md §9`, §10, §11.5.

**The Auditor is not a spawned sub-agent.** Its N-pass judgment runs in the backend
(`saltcode_stability`) against the local Saltnitor model, so N passes cost N
inferences rather than N process spawns. This skill describes the judgment that tool
performs — and governs you when you audit a diff by hand.

## Inputs

- The unified diff (from Builder).
- The clean static analysis report (the gate already passed — do not re-lint).
- Test runner results (pass + stdout/stderr).
- The test spec content (`tests/task_{id}_spec.*`).
- The task's `acceptance_criteria[]`.

The static gate and the test runner have already run. Your job is the one thing they
cannot check: **is this implementation faithful, or does it merely satisfy the tests?**

## Step 1: Heuristic detection (zero cost)

Scan the diff for these red flags:

- **Fixture-literal returns** — functions return hardcoded values matching test
  expectations.
- **Empty / throw-only bodies** — functions under test have no logic, just `pass` /
  `throw` / `return None`.
- **Test-input-keyed branches** — `if input == <known_test_value>: return <expected>`.
- **Suspiciously short implementations** — a complex task solved in three lines.

Any hit → preliminary verdict `gaming_suspected`. The stability passes may override it
if the implementation turns out to be genuinely correct.

## Step 2: Multi-pass stability (BIFAI-NET v5.2)

Run the faithfulness judgment **N=3 times** under *varied* conditions, so the verdicts
are not trivially identical:

- Pass 1 — temperature 0.3, evidence in original order.
- Pass 2 — temperature 0.5, `acceptance_criteria` reordered.
- Pass 3 — temperature 0.7, diff hunks reordered.

Each pass independently yields `pass`, `impl_fail`, `gaming_suspected`, or
`spec_defect`. Then:

```
stability_score = 1.0 - (verdict_changes / (N - 1))
gac            = first pass index after which every pass agrees
```

- `stability_score == 1.0` (all three agree) → high confidence; use the unanimous
  verdict.
- `stability_score < auditor_stability_threshold` (oscillating) → low confidence:
  - **online** → re-run the judgment once on DeepSeek Flash; that verdict is final.
  - **offline** → accept the majority local verdict. No API call is made offline, ever,
    however unstable the result.

The threshold comes from the **measured-then-fixed calibration protocol**
(`specs/design.md §11.9`). Uncalibrated → conservative default `0.5`, and the config
must say `calibrated: false`. Never invent a threshold; never self-report a confidence
number — a small model's stated confidence is token prediction, not measurement. The
whole point of this design is that stability is *behavioral*.

## Step 3: Emit the verdict

Write `.saltcode/audit_result.json`:

```json
{
  "schema_version": "1",
  "task_id": "T1",
  "status": "pass",
  "reason": "pass",
  "next_action": "next_task",
  "detail": "Implementation satisfies all acceptance criteria with genuine logic",
  "stability": {
    "n_passes": 3,
    "verdicts": ["pass", "pass", "pass"],
    "stability_score": 1.0,
    "gac": 0
  }
}
```

Invariants the schema enforces: `reason == "pass"` **iff** `status == "pass"`;
`reason == "spec_defect"` forces `next_action == "test_intent_respec"`;
`len(verdicts) == n_passes`; `stability_score ∈ [0.0, 1.0]`.

Routing: `pass` → apply live; `impl_fail` → Builder retry; `gaming_suspected` →
Builder retry with the "no hardcoding" reason and the flagged patterns; `spec_defect`
→ Test Intent re-spec (≤1, does **not** consume a Builder retry).

Make `detail` specific enough to act on. On `spec_defect` it is the only thing Test
Intent gets to work with — "the spec tests a private API that doesn't exist" is
useful; "the spec is wrong" is not.

## What comes after you

`pass` does not finish the task. The diff is applied to the live tree **uncommitted**,
then the regression gate runs the project's full suite. Only then does the checkpoint
commit happen. A `regression_fail` is not your verdict to give — it is measured after
you.

## Forbidden Actions

- **Cannot write code.** You only judge.
- **Cannot modify tests.** You only read them.
- **Cannot re-run the gates.** The static report and test results you receive are
  authoritative.
