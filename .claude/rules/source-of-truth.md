# Source of Truth — Read the Proposal Before You Write Code

This rule governs every other rule. It applies to design questions, implementation
tasks, reviews, and refactors alike.

## The hierarchy

1. **`docs/proposal/Proposal_Pi_v8.md`** + **`docs/proposal/Proposal_Pi_v9.md`** —
   together *the source of truth*. v8 is the framework (agents, cadence, gates,
   cost model, tradeoffs); v9 is the Pi re-hosting addendum. **Where they conflict,
   v9 wins** — v9's changelog marks each amendment `[RE-HOSTED]`, `[REVISED]`,
   `[ADDED in V9]`, `[KEEP from V8]`.
2. **`specs/requirements.md`** — the binding contract. Code satisfies a REQ id or it
   is not done. Acceptance criteria are the definition of "works".
3. **`specs/design.md`** — the blueprint. What to build, how the pieces fit, which
   Pi API sits where, what the tree looks like.
4. **`specs/tasks.md`** — the ordered build and the progress ledger.

`specs/legacy/` is the archived pre-v9 spec set. **Never** implement from it, cite it
as a requirement, or "restore" behavior from it. It exists for provenance only.

## Before starting any task

Do this in order, every time. It is cheap; guessing is not.

1. Read the task in `specs/tasks.md` — its sub-steps, its `deps`, its **Satisfies**
   REQ ids, and its **Done when** gate.
2. Read every REQ id under **Satisfies** in `specs/requirements.md`. Those acceptance
   criteria are what you are building toward, not your idea of the feature.
3. Read the `specs/design.md` section the task points at (`§n`) for the shape.
4. If any of the three is ambiguous, thin, or silent on something you need — **go to
   the proposal.** It carries the reasoning the specs compress away.

Do not start writing code before step 4 resolves. "I'll figure it out from the
existing code" is how the v9 architecture gets quietly rebuilt as v8.

## When the code and the proposal disagree

The proposal wins as *intent*; that does not mean silently rewriting working code.

- **Code contradicts the proposal** → the code is a defect or a pending migration.
  Say so explicitly, name the proposal section, and ask before changing scope.
- **Proposal is ambiguous** → the requirement is the defect. Per
  `specs/requirements.md`: *"Ambiguity is a defect: if an implementer cannot tell
  whether a requirement passes, the requirement — not the code — is fixed first."*
  Surface it; do not resolve it by inventing behavior.
- **Proposal is silent** → say it is silent, propose the smallest reading consistent
  with the surrounding design, and flag the assumption in your response.

## Amending the specs

`specs/` is derived from `docs/proposal/`. A change in intent belongs in the proposal
first, then flows down to requirements → design → tasks. Never edit `specs/` to match
code that drifted; that erases the contract instead of fixing the breach.

## Progress ledger

When a task's **Done when** gate passes, check its box in `specs/tasks.md` in the same
change that delivers it. An unchecked box means not done — no matter what the code says.
