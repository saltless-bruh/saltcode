# Follow the Task List · Never Work Around a Block

Two rules with one shape: **when reality and the plan disagree, stop and ask — do not
improvise.** They bind harder than any instinct to be helpful by making progress.

## 1. Follow `specs/tasks.md` as written

The task list is the instruction, not a suggestion to be interpreted.

- Execute the task's sub-steps **in order, as written**. Do not reorder them, merge
  them, skip one that looks redundant, or fold in work from a later task.
- Do not "improve" a task. A step that looks suboptimal is still the step.
- Do not silently substitute a different approach because it seems cleaner, faster, or
  more idiomatic.
- Do not expand scope. Adjacent breakage you notice is **reported**, not fixed, unless
  the task says to fix it.

### When the task is wrong

If a task contains a **contradiction, error, flaw, problem, or issue** — it conflicts
with `specs/requirements.md` or `specs/design.md`, contradicts the proposal,
references something that does not exist, is ambiguous enough that two readings give
different code, or simply cannot be done as written — then:

1. **Stop.** Do not start the affected work.
2. State the problem precisely: quote the task text, name the conflicting REQ id or
   design section.
3. Give **your suggested fix**, and any real alternatives, with a recommendation.
4. **Ask the human for their opinion, and wait.**

Finish everything in the task that does *not* depend on the answer first, so the
question arrives with the rest of the work already done.

Never resolve a spec defect by inventing behaviour. Per `specs/requirements.md`:
*"Ambiguity is a defect: if an implementer cannot tell whether a requirement passes,
the requirement — not the code — is fixed first."*

## 2. Never work around a block

A **block** is anything that stops the task being completed as specified: a missing
tool or binary, an uninstalled dependency, an absent credential or API key, a
permission or trust prompt, an unreachable service, a failing gate you did not cause,
a spec that does not say what to do.

**Do not route around it.** Specifically, do not:

- substitute a weaker command, tool, or library because the specified one is missing;
- skip a verification step and report the task done anyway;
- stub, mock, or fake a result to make a gate pass;
- disable, loosen, or exclude a check to get past it;
- guess a value that should have come from config, a credential, or the human;
- quietly narrow the task to the part that happens to work.

Instead:

1. **Stop at the block.**
2. Say plainly **what is blocked** and **what you were trying to do**.
3. Say **what you need** to get past it — the exact command, credential, decision, or
   permission.
4. Offer options with a recommendation, then **ask and wait**.

Everything not blocked still gets finished. Report exactly what was completed and what
was left, and never describe a task as done when a leg of its **Done when** gate was
skipped or unverified — say which leg, and why.

## Why

Improvising past a defect buries it: the code ships, the spec still says something
else, and the divergence surfaces later as a bug nobody can trace to a decision.
Working around a block does the same thing to the environment — a green run that
proves nothing. A blocked task reported honestly costs one message. A worked-around
one costs a debugging session, and the trust that the ledger means what it says.
