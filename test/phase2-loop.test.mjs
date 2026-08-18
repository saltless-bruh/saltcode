/**
 * Task 13.10 — the Phase-2 loop's routing (REQ-AUD-002, REQ-AUD-003, REQ-FAIL-001..003,
 * design §10).
 *
 * The loop is where a wrong decision is most expensive and least visible: a failure
 * charged to the wrong counter still produces a plausible-looking run, just one that
 * spends its budget on the wrong question or never stops. So each test drives the whole
 * sequence with a scripted {@link Gates} and asserts on what the loop *did* — which gates
 * ran, what it charged, where it stopped — rather than on its return value alone.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let phase2;

before(async () => {
  phase2 = await load("saltcode/phase2.ts");
});

const TASK = {
  id: "T1",
  description: "implement the thing",
  filesAffected: ["src/thing.py"],
  complexity: "low",
};

/** A Gates double whose every method passes unless overridden. */
function gates(overrides = {}) {
  const calls = [];
  const record = (name, value) => {
    calls.push(name);
    return value;
  };
  const base = {
    calls,
    build: async () =>
      record("build", { ok: true, diff: "--- a/src/thing.py\n+++ b/src/thing.py\n" }),
    diffCheck: async () => record("diffCheck", { kind: "pass" }),
    sandboxApply: async () => record("sandboxApply", { ok: true, sandbox: "/tmp/sandbox" }),
    staticGate: async () => record("staticGate", { kind: "pass" }),
    testRun: async () => record("testRun", { kind: "pass" }),
    audit: async () =>
      record("audit", { verdict: "pass", stability: 1, detail: "faithful", escalation: "none" }),
    rejudgeOnFlash: async () =>
      record("rejudgeOnFlash", {
        verdict: "pass",
        stability: 1,
        detail: "flash",
        escalation: "none",
      }),
    applyLive: async () => record("applyLive", { kind: "pass" }),
    respec: async () => record("respec", { ok: true, detail: "respecced" }),
  };
  return { ...base, ...overrides, calls };
}

function deps(g, overrides = {}) {
  const flags = [];
  const budgets = [];
  return {
    flags,
    budgets,
    value: {
      gates: g,
      online: true,
      auditorStabilityThreshold: 0.5,
      onGate: () => {},
      onBudget: (b) => budgets.push(b),
      onFlagHuman: (reason) => flags.push(reason),
      ...overrides,
    },
  };
}

test("a clean task runs every gate in order and applies", async () => {
  const g = gates();
  const d = deps(g);
  const outcome = await phase2.runTask(TASK, d.value);

  assert.equal(outcome.outcome, "passed");
  assert.deepEqual(g.calls, [
    "build",
    "diffCheck",
    "sandboxApply",
    "staticGate",
    "testRun",
    "audit",
    "applyLive",
  ]);
  assert.equal(outcome.budget.attempts, 0, "a clean pass spends nothing");
});

test("a failing gate short-circuits — the Auditor never sees a dirty diff", async () => {
  // Design §10: every gate short-circuits on failure, and the Auditor is never called on
  // a malformed, dirty or failing diff. Calling it anyway would ask it to judge
  // faithfulness against a build that does not compile.
  let attempts = 0;
  const g = gates({
    staticGate: async () => {
      attempts += 1;
      return attempts === 1 ? { kind: "fail", detail: "ruff: F401" } : { kind: "pass" };
    },
  });
  const d = deps(g);
  const outcome = await phase2.runTask(TASK, d.value);

  assert.equal(outcome.outcome, "passed");
  assert.equal(g.calls.filter((c) => c === "audit").length, 1, "audited once, after the retry");
  assert.equal(
    g.calls.indexOf("testRun") > g.calls.indexOf("staticGate"),
    true,
    "tests run only after static is clean",
  );
  assert.equal(outcome.budget.attempts, 1, "the dirty gate cost one retry");
});

test("the Builder is told exactly what failed", async () => {
  const seen = [];
  let first = true;
  const g = gates({
    build: async (request) => {
      seen.push(request);
      return { ok: true, diff: "diff" };
    },
    testRun: async () => {
      if (first) {
        first = false;
        return { kind: "fail", detail: "assert 3 == 4" };
      }
      return { kind: "pass" };
    },
  });
  await phase2.runTask(TASK, deps(g).value);

  assert.equal(seen.length, 2);
  assert.equal(seen[0].feedback, undefined, "the first attempt has nothing to fix");
  assert.match(seen[1].feedback, /assert 3 == 4/);
  assert.equal(seen[1].attempt, 2);
});

test("three failures flag a human, and the third attempt is on Tier B", async () => {
  // REQ-FAIL-001 AC1/AC2 and REQ-FAIL-003: no silent spinning, ever.
  const tiers = [];
  const g = gates({
    build: async (request) => {
      tiers.push(request.tier);
      return { ok: true, diff: "diff" };
    },
    testRun: async () => ({ kind: "fail", detail: "still red" }),
  });
  const d = deps(g);
  const outcome = await phase2.runTask(TASK, d.value);

  assert.equal(outcome.outcome, "flagged");
  assert.deepEqual(tiers, ["A", "A", "B"]);
  assert.equal(d.flags.length, 1);
  assert.match(d.flags[0], /budget of 3 is spent/);
});

test("gaming_suspected retries with the no-hardcoding reason", async () => {
  // REQ-AUD-003. The retry has to carry *why*, or the Builder rewrites the same trick.
  let judged = 0;
  const feedback = [];
  const g = gates({
    build: async (request) => {
      if (request.feedback !== undefined) feedback.push(request.feedback);
      return { ok: true, diff: "diff" };
    },
    audit: async () => {
      judged += 1;
      return judged === 1
        ? {
            verdict: "gaming_suspected",
            stability: 1,
            detail: "returns the fixture literal for the test input",
            escalation: "none",
          }
        : { verdict: "pass", stability: 1, detail: "ok", escalation: "none" };
    },
  });
  const outcome = await phase2.runTask(TASK, deps(g).value);

  assert.equal(outcome.outcome, "passed");
  assert.equal(outcome.budget.attempts, 1);
  assert.match(feedback[0], /fixture literal/);
  assert.match(feedback[0], /Do not special-case test inputs/);
});

test("spec_defect re-specs, and costs no retry", async () => {
  // REQ-AUD-003 AC1 + REQ-FAIL-001 AC3.
  let judged = 0;
  const g = gates({
    audit: async () => {
      judged += 1;
      return judged === 1
        ? {
            verdict: "spec_defect",
            stability: 1,
            detail: "the spec asserts the old signature",
            escalation: "none",
          }
        : { verdict: "pass", stability: 1, detail: "ok", escalation: "none" };
    },
  });
  const outcome = await phase2.runTask(TASK, deps(g).value);

  assert.equal(outcome.outcome, "passed");
  assert.equal(outcome.budget.attempts, 0, "a spec defect is not the Builder's fault");
  assert.equal(outcome.budget.specDefects, 1);
  assert.equal(g.calls.includes("respec"), true);
});

test("an unstable verdict escalates once when online, and that verdict is final", async () => {
  // REQ-AUD-002 AC2.
  let escalations = 0;
  const g = gates({
    audit: async () => ({
      verdict: "impl_fail",
      stability: 0.0,
      detail: "unstable",
      escalation: "flash_rejudgment",
    }),
    rejudgeOnFlash: async () => {
      escalations += 1;
      return { verdict: "pass", stability: 1, detail: "flash says fine", escalation: "none" };
    },
  });
  const outcome = await phase2.runTask(TASK, deps(g).value);

  assert.equal(outcome.outcome, "passed");
  assert.equal(escalations, 1, "exactly one escalation, and its verdict is final");
  assert.equal(outcome.budget.attempts, 0, "the escalated verdict replaced the unstable one");
});

test("offline never escalates — the majority local verdict stands", async () => {
  // REQ-AUD-002 AC1. "The Auditor is unsure" and "the Auditor never ran" are different
  // facts, and offline the second one is not allowed to happen.
  let escalations = 0;
  const g = gates({
    audit: async () => ({
      verdict: "pass",
      stability: 0.0,
      detail: "unstable but passing",
      escalation: "offline_majority",
    }),
    rejudgeOnFlash: async () => {
      escalations += 1;
      return { verdict: "pass", stability: 1, detail: "should never run", escalation: "none" };
    },
  });
  const outcome = await phase2.runTask(TASK, deps(g, { online: false }).value);

  assert.equal(outcome.outcome, "passed");
  assert.equal(escalations, 0, "offline makes no Flash call, whatever the stability");
});

test("a tool that could not answer flags a human instead of charging the Builder", async () => {
  // G-017's lesson: a static runner that failed on its own configuration presents to the
  // Builder as "your code is broken" and ends in FLAG HUMAN with a misleading reason.
  const g = gates({
    staticGate: async () => ({ kind: "tool_failure", detail: "pyrightconfig.json is unreadable" }),
  });
  const d = deps(g);
  const outcome = await phase2.runTask(TASK, d.value);

  assert.equal(outcome.outcome, "flagged");
  assert.equal(outcome.budget.attempts, 0, "a broken toolchain must not spend the budget");
  assert.match(d.flags[0], /tool failure, not a verdict/);
});

test("a skipped test run does not block the Auditor", async () => {
  // REQ-STAT-004 AC3: a project with no test_runner_cmd legitimately skips.
  const g = gates({ testRun: async () => ({ kind: "skipped", detail: "no test_runner_cmd" }) });
  const outcome = await phase2.runTask(TASK, deps(g).value);
  assert.equal(outcome.outcome, "passed");
  assert.equal(g.calls.includes("audit"), true);
});

test("an apply that fails after a passing Auditor flags rather than retrying", async () => {
  // The diff was judged good and the tree rejected it. Retrying the Builder would burn
  // budget on the wrong question.
  const g = gates({ applyLive: async () => ({ kind: "fail", detail: "patch does not apply" }) });
  const d = deps(g);
  const outcome = await phase2.runTask(TASK, d.value);

  assert.equal(outcome.outcome, "flagged");
  assert.equal(outcome.budget.attempts, 0);
  assert.match(d.flags[0], /the Auditor passed but the live apply did not land/);
});

test("budget changes are reported as they happen", async () => {
  // REQ-ORC-005 AC1's "observable": the widget and the ledger see every step, not a total
  // at the end.
  const g = gates({ testRun: async () => ({ kind: "fail", detail: "red" }) });
  const d = deps(g);
  await phase2.runTask(TASK, d.value);
  assert.ok(d.budgets.length >= 4);
  assert.deepEqual(
    d.budgets.map((b) => b.attempts),
    [0, 1, 1, 2, 2, 3],
  );
});
