/**
 * Task 13.5 — the retry budget, the loop caps, and state that survives a reload
 * (REQ-FAIL-001, REQ-FAIL-002, REQ-EVL-003, REQ-ORC-001, REQ-ORC-005, REQ-EXT-006).
 *
 * The budget is the mechanism that turns "never spin silently" from an intention into a
 * property, so the tests walk the whole ladder rather than sampling it: three failures
 * flag, the third attempt is on Tier B, and a `spec_defect` moves the run forward without
 * costing an attempt. Off-by-one anywhere in that is either a wasted retry or an infinite
 * loop, and both look reasonable from inside a single step.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let budget;
let state;

before(async () => {
  budget = await load("saltcode/budget.ts");
  state = await load("saltcode/state.ts");
});

test("the shared budget is 3 and every gate failure spends the same one", () => {
  // REQ-FAIL-001: diff-format + static + test + impl_fail + gaming + regression share it.
  let b = budget.startTask("T1", "low");
  const kinds = ["diff_format", "static", "test"];

  const first = budget.recordFailure(b, kinds[0]);
  assert.equal(first.result.outcome, "retry");
  assert.equal(first.result.attemptsLeft, 2);
  b = first.budget;

  const second = budget.recordFailure(b, kinds[1]);
  assert.equal(second.result.outcome, "retry");
  assert.equal(second.result.attemptsLeft, 1);
  b = second.budget;

  const third = budget.recordFailure(b, kinds[2]);
  assert.equal(third.result.outcome, "flag_human");
  assert.match(third.result.reason, /budget of 3 is spent/);
});

test("two Tier-A failures put the third attempt on Tier B", () => {
  // REQ-FAIL-001 AC2 / REQ-MOD-002.
  let b = budget.startTask("T1", "medium");
  assert.equal(budget.tierForNextAttempt(b), "A");

  b = budget.recordFailure(b, "impl_fail").budget;
  assert.equal(budget.tierForNextAttempt(b), "A", "one failure is still Tier A");

  b = budget.recordFailure(b, "impl_fail").budget;
  assert.equal(budget.tierForNextAttempt(b), "B", "the sub-cap is 2, so attempt 3 escalates");
});

test("a high-complexity task runs all three attempts on Tier B", () => {
  let b = budget.startTask("T1", "high");
  assert.equal(budget.tierForNextAttempt(b), "B");
  b = budget.recordFailure(b, "test").budget;
  assert.equal(budget.tierForNextAttempt(b), "B");
});

test("spec_defect does not consume a retry", () => {
  // REQ-FAIL-001 AC3. It says the *tests* are wrong; charging the Builder for that sends
  // it to fix code whose ruler is the thing under suspicion.
  const b = budget.startTask("T1", "low");
  const transition = budget.recordFailure(b, "spec_defect");
  assert.equal(transition.result.outcome, "respec");
  assert.equal(transition.budget.attempts, 0);
  assert.equal(transition.budget.specDefects, 1);
});

test("a second spec_defect on the same task flags a human", () => {
  // REQ-FAIL-002 AC1.
  let b = budget.startTask("T1", "low");
  b = budget.recordFailure(b, "spec_defect").budget;
  const second = budget.recordFailure(b, "spec_defect");
  assert.equal(second.result.outcome, "flag_human");
  assert.match(second.result.reason, /second spec_defect/);
});

test("recordFailure returns a new budget rather than mutating", () => {
  // A half-applied transition would leave the counter disagreeing with what the human was
  // told, which is precisely what REQ-ORC-005 AC1 forbids.
  const b = budget.startTask("T1", "low");
  budget.recordFailure(b, "test");
  assert.equal(b.attempts, 0);
});

test("the Evaluator loop caps are Architect 2, Planner 3", () => {
  // REQ-EVL-003.
  let loops = budget.startLoops();
  for (let i = 0; i < budget.ARCHITECT_LOOP_CAP; i += 1) {
    const step = budget.recordEvaluatorRoute(loops, "architect");
    assert.equal(step.result.outcome, "reloop");
    loops = step.loops;
  }
  const over = budget.recordEvaluatorRoute(loops, "architect");
  assert.equal(over.result.outcome, "flag_human");

  let planner = budget.startLoops();
  for (let i = 0; i < budget.PLANNER_LOOP_CAP; i += 1) {
    planner = budget.recordEvaluatorRoute(planner, "planner").loops;
  }
  assert.equal(budget.recordEvaluatorRoute(planner, "planner").result.outcome, "flag_human");
});

test("the budget describes itself for the widget", () => {
  const b = budget.startTask("T7", "low");
  assert.equal(budget.describeBudget(b), "T7: attempt 1/3 on Tier A");
  const after = budget.recordFailure(budget.recordFailure(b, "test").budget, "test").budget;
  assert.equal(budget.describeBudget(after), "T7: attempt 3/3 on Tier B");
});

// ------------------------------------------------------------------- state replay

test("replay reconstructs the latest entry of each type", () => {
  // REQ-EXT-006 AC1. Every mutation appends, so the last entry of a type is current and
  // the earlier ones are the trail.
  const entries = [
    { type: "custom", customType: state.ENTRY_SPRINT, data: sprint({ phase: "phase1" }) },
    { type: "message", data: { ignored: true } },
    { type: "custom", customType: state.ENTRY_SPRINT, data: sprint({ phase: "phase2" }) },
    {
      type: "custom",
      customType: state.ENTRY_BUDGET,
      data: { taskId: "T1", attempts: 2, specDefects: 0, startedOnTierB: false },
    },
  ];

  const replayed = state.replayState(entries);
  assert.equal(replayed.sprint.phase, "phase2");
  assert.equal(replayed.budget.attempts, 2);
  assert.equal(replayed.task, null);
});

test("a malformed entry is skipped rather than throwing", () => {
  // A session that cannot be replayed at all is worse than one that resumes from the last
  // entry that parsed — and the caller can see which by comparing against what it expected.
  const replayed = state.replayState([
    { type: "custom", customType: state.ENTRY_SPRINT, data: { nonsense: true } },
    { type: "custom", customType: state.ENTRY_SPRINT, data: null },
    { type: "custom", customType: state.ENTRY_BUDGET, data: { taskId: "T1" } },
  ]);
  assert.equal(replayed.sprint, null);
  assert.equal(replayed.budget, null);
});

test("a sprint fires Phase 1 exactly once", () => {
  // REQ-ORC-001 AC2. This is the cost model, and it lives in code the model cannot argue
  // with rather than in a prompt it can.
  assert.equal(state.canFirePhase1(null).ok, true);
  assert.equal(state.canFirePhase1(sprint({ phase1Fired: false })).ok, true);

  const refusal = state.canFirePhase1(sprint({ phase1Fired: true }));
  assert.equal(refusal.ok, false);
  assert.match(refusal.reason, /already fired Phase 1/);
});

test("nextTaskId walks the order and skips what is done", () => {
  const s = sprint({ taskOrder: ["T1", "T2", "T3"], completed: ["T1"] });
  assert.equal(state.nextTaskId(s), "T2");
  assert.equal(state.nextTaskId(sprint({ taskOrder: ["T1"], completed: ["T1"] })), undefined);
});

function sprint(overrides) {
  return {
    sprintId: "s1",
    goal: "goal",
    phase: "idle",
    phase1Fired: false,
    online: true,
    project: "fresh",
    loops: { architect: 0, planner: 0 },
    taskOrder: [],
    completed: [],
    ...overrides,
  };
}
