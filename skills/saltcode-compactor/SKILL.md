---
name: saltcode-compactor
description: Guides the Spec Compactor to shrink design.md every five sprints by stripping completed and obsolete content, while leaving the HARD CONSTRAINTS block byte-identical.
---

# Saltcode Compactor Skill

Use this when `saltcode_compact_spec` invokes you — once per five sprints (REQ-CMP-001).

`design.md` accumulates. Sections describe work that shipped three sprints ago; open
questions get answered and the answer never replaces the question. You remove that, and
**nothing else**.

You run on Flash with thinking `off` (REQ-CMP-001 AC2). This is not a reasoning task. It
is a careful deletion task, and the difference matters: a compactor that starts reasoning
about what a constraint "really means" is a compactor about to lose one.

## The one invariant

**The `## HARD CONSTRAINTS` block comes out byte-identical** (REQ-CMP-001 AC1).

Not "semantically preserved". Not "reworded but equivalent". Byte for byte, every line,
including whitespace and ordering.

You may not:
- drop a constraint you believe is satisfied, obsolete, or redundant;
- merge two that overlap;
- reword one for clarity, grammar, or brevity;
- reorder the list;
- move constraints into another section.

HARD CONSTRAINTS are removed **only by an explicit human edit**. Not by you, ever, for any
reason.

This is checked mechanically: `saltcode_compact_spec` discards whatever you did to that
block, splices the original bytes back in, and verifies. Your output is treated as
untrusted for exactly this reason — a paraphrased constraint reads as correct, which is
what makes it dangerous. If the check cannot be satisfied the compaction is **refused** and
the file is left untouched.

So the block being intact is not an achievement. Damaging it just wastes the run.

## What to strip (REQ-CMP-001 AC3)

Only content **outside** `## HARD CONSTRAINTS`, and only where it relates to completed or
obsolete work:

- sections describing components that are built, tested, and checkpointed;
- resolved open questions — keep the decision, drop the deliberation;
- superseded approaches, where a later section replaced an earlier one;
- narrative about how something came to be decided, once the decision is stated.

## What to keep

- **Everything in `## HARD CONSTRAINTS`.**
- `## Non-Goals` — a non-goal is not "completed", it is permanently binding, and dropping
  one re-opens scope somebody deliberately closed.
- Any decision a future sprint still depends on, even if the work implementing it is done.
- Anything describing work not yet finished.
- Anything you are unsure about. **Uncertainty means keep.** The cost of keeping a stale
  paragraph is a few tokens; the cost of dropping a live decision is a sprint that
  rebuilds something the design already settled.

## Output

The full compacted `design.md`. Preserve heading structure and the document's voice — you
are removing, not rewriting. A section that survives should survive unchanged, not
summarised.

Do not add anything: no summary of what you removed, no "compacted on" note, no changelog.

## Escalation

Refuse and report, rather than guessing, when:

- `design.md` has no `## HARD CONSTRAINTS` block — that is a malformed input, not licence
  to proceed without one;
- there is more than one such block;
- you cannot tell whether a section is still live.

The third is not really an escalation — keep it and move on. But if a *document* is
structured such that you cannot tell live from dead anywhere in it, say so instead of
guessing repeatedly.

## Common mistakes to avoid

- Touching the constraints block at all. Copy it through.
- Stripping `## Non-Goals` as "completed".
- Rewriting surviving prose to be tighter. Not your job.
- Removing a decision because its implementation is finished — the decision still binds.
- Compacting aggressively to show a large byte saving. The measure of a good run is that
  nothing needed was lost, not that the file got small.
