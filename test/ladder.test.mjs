/**
 * Task 8 — the cache ladder and the Evaluator re-loop (REQ-CACHE-001, REQ-CACHE-003,
 * REQ-EVL-002, REQ-EVL-003, REQ-FAIL-003).
 *
 * The asymmetry these tests defend: a wrong *reuse* builds against a plan written for
 * different work, and every later gate — static, tests, Auditor, regression — validates it
 * faithfully, because each of them checks the diff against the plan rather than the plan
 * against reality. A wrong *fall-through* costs one planning pass. So every ambiguous path
 * here must fall through, and the tests assert that rather than assuming it.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let ladder;
let evaluator;
let budget;

before(async () => {
  ladder = await load("saltcode/ladder.ts");
  evaluator = await load("saltcode/evaluator.ts");
  budget = await load("saltcode/budget.ts");
});

function lookup(over = {}) {
  return {
    verdict: "miss",
    key: "abcdef0123456789",
    scopeFingerprint: [],
    scopeSource: "probed",
    detail: "",
    uncalibrated: false,
    ...over,
  };
}

// ------------------------------------------------------- REQ-CACHE-001 order

test("an exact hit reuses and stops the ladder with zero API", () => {
  const action = ladder.decideLadder(lookup({ verdict: "exact_hit", tasks: { tasks: [] } }));
  assert.equal(action.action, "reuse");
  assert.match(action.why, /zero API/);
});

test("a miss fires Phase 1", () => {
  assert.equal(ladder.decideLadder(lookup({ verdict: "miss" })).action, "fire");
});

test("an unreadable verdict fires and names the fault rather than pretending to miss", () => {
  // A ladder that silently degrades to "always fire" is a cache nobody notices is dead.
  const action = ladder.decideLadder(lookup({ verdict: "unreadable", detail: "boom" }));
  assert.equal(action.action, "fire");
  assert.match(action.why, /could not be read/);
});

// ------------------------------------------------- REQ-CACHE-003 confirmation

test("a semantic candidate never reaches reuse without a confirmation", () => {
  for (const confirmation of ["cheap", "full", undefined]) {
    const action = ladder.decideLadder(
      lookup({ verdict: "semantic_candidate", confirmation, similarity: 0.9, pcd: 0.5 }),
    );
    assert.equal(action.action, "confirm", `confirmation=${confirmation}`);
    assert.ok(action.bar === "cheap" || action.bar === "full");
  }
});

test("an absent confirmation level defaults to the strictest bar", () => {
  const action = ladder.decideLadder(lookup({ verdict: "semantic_candidate" }));
  assert.equal(action.bar, "full");
});

test("skip is honoured only because the backend returns it opt-in", () => {
  // REQ-CACHE-003 AC2 makes skipping configuration, so honouring it is following the
  // project's choice — the extension never chooses to lower the bar on its own.
  const action = ladder.decideLadder(
    lookup({ verdict: "semantic_candidate", confirmation: "skip", pcd: 0.9 }),
  );
  assert.equal(action.action, "reuse");
  assert.match(action.why, /opted in/);
});

test("fall_through fires without spending an Architect turn", () => {
  const action = ladder.decideLadder(
    lookup({ verdict: "semantic_candidate", confirmation: "fall_through", pcd: 0.01 }),
  );
  assert.equal(action.action, "fire");
});

test("the confirmation prompt tells the Architect to answer NO when unsure", () => {
  const prompt = ladder.confirmationPrompt("add auth", "full", { tasks: [{ id: "T1" }] });
  assert.match(prompt, /Answer `NO` if you are unsure/);
  assert.match(prompt, /T1/);
  assert.match(ladder.confirmationPrompt("g", "cheap", {}), /CHEAP confirmation/);
});

test("anything that is not an unambiguous YES is a NO", () => {
  assert.equal(ladder.readConfirmation("YES — same work").confirmed, true);
  assert.equal(ladder.readConfirmation("yes, reuse it").confirmed, true);
  assert.equal(ladder.readConfirmation("NO, different repo").confirmed, false);
  // An unparseable answer and a refusal have the same correct handling.
  for (const reply of ["Probably yes?", "I think so", "", "Well, YES"]) {
    assert.equal(ladder.readConfirmation(reply).confirmed, false, reply);
  }
});

// ------------------------------------------------------------------- parsing

test("a cache_lookup payload is read without trusting any field's type", () => {
  const parsed = ladder.parseCacheLookup({
    code: 0,
    payload: {
      verdict: "semantic_candidate",
      key: "k",
      scope_fingerprint: ["a.py", 7, "b.py"],
      scope_source: "provided",
      confirmation: "cheap",
      similarity: 0.91,
      pcd: 0.4,
      tasks: { tasks: [] },
      detail: "d",
      thresholds: { semantic_cosine: { value: 0.85, calibrated: false } },
    },
  });
  assert.equal(parsed.verdict, "semantic_candidate");
  assert.deepEqual(parsed.scopeFingerprint, ["a.py", "b.py"]);
  assert.equal(parsed.uncalibrated, true);
});

test("a garbage payload parses to unreadable, never to a hit", () => {
  const parsed = ladder.parseCacheLookup({ code: 3, payload: { verdict: 42 } });
  assert.equal(parsed.verdict, "unreadable");
  assert.equal(parsed.uncalibrated, false);
});

test("a fully calibrated threshold set does not raise the warning", () => {
  const parsed = ladder.parseCacheLookup({
    code: 0,
    payload: {
      verdict: "miss",
      thresholds: { semantic_cosine: { value: 0.85, calibrated: true } },
    },
  });
  assert.equal(parsed.uncalibrated, false);
});

// ----------------------------------------------------- REQ-EVL-002 routing

const gap = (type, over = {}) => ({ id: "G1", type, detail: "d", ...over });

test("any design_gap sends the loop to the Architect", () => {
  // AC1. One design gap outranks any number of plan gaps: re-planning against a design
  // still missing something produces a different wrong plan, not a right one.
  const routing = evaluator.routeGaps([
    gap("plan_gap"),
    gap("plan_gap"),
    gap("design_gap"),
    gap("constraint_violation"),
  ]);
  assert.equal(routing.target, "architect");
  assert.match(routing.why, /design_gap/);
});

test("plan gaps alone go to the Planner", () => {
  assert.equal(evaluator.routeGaps([gap("plan_gap")]).target, "planner");
});

test("a constraint_violation goes to the Planner unless the Evaluator escalated it", () => {
  assert.equal(evaluator.routeGaps([gap("constraint_violation")]).target, "planner");
  assert.equal(
    evaluator.routeGaps([gap("constraint_violation", { target: "architect" })]).target,
    "architect",
  );
});

test("no gaps means no routing", () => {
  assert.equal(evaluator.routeGaps([]), null);
});

test("an unreadable report is never pass", () => {
  // `pass` unlocks the Phase Gate and starts spending the Builder's budget, so it is the
  // one value that must never be reached by a parse falling back to a default.
  for (const raw of [undefined, "", "{not json", "[]", '{"status": "ok"}', "null"]) {
    assert.equal(evaluator.parseEvaluatorReport(raw).status, "unreadable", String(raw));
  }
  assert.equal(evaluator.parseEvaluatorReport('{"status":"pass"}').status, "pass");
});

test("malformed gap entries are dropped rather than routed on a guess", () => {
  const report = evaluator.parseEvaluatorReport(
    JSON.stringify({
      status: "gaps",
      gaps: [{ type: "design_gap", id: "G1", detail: "d" }, { type: "nonsense" }, null, "x"],
    }),
  );
  assert.equal(report.gaps.length, 1);
  assert.equal(report.gaps[0].type, "design_gap");
});

// ------------------------------------------------------ REQ-EVL-003 caps

test("the caps are Architect 2 and Planner 3, then a human flag", () => {
  let loops = budget.startLoops();
  for (let i = 0; i < 2; i += 1) {
    const t = budget.recordEvaluatorRoute(loops, "architect");
    loops = t.loops;
    assert.equal(t.result.outcome, "reloop");
  }
  assert.equal(budget.recordEvaluatorRoute(loops, "architect").result.outcome, "flag_human");

  let planner = budget.startLoops();
  for (let i = 0; i < 3; i += 1) {
    const t = budget.recordEvaluatorRoute(planner, "planner");
    planner = t.loops;
    assert.equal(t.result.outcome, "reloop");
  }
  assert.equal(budget.recordEvaluatorRoute(planner, "planner").result.outcome, "flag_human");
});

test("the caps are counted per target, not shared", () => {
  const afterArchitect = budget.recordEvaluatorRoute(budget.startLoops(), "architect").loops;
  assert.equal(afterArchitect.planner, 0);
});

test("the gap report a human sees names every open gap", () => {
  const report = evaluator.parseEvaluatorReport(
    JSON.stringify({
      status: "gaps",
      routing_summary: "2 gaps",
      gaps: [gap("design_gap", { id: "G1" }), gap("plan_gap", { id: "G2" })],
    }),
  );
  const text = evaluator.describeGapReport(report, "cap breached");
  assert.match(text, /cap breached/);
  assert.match(text, /G1/);
  assert.match(text, /G2/);
  assert.match(text, /evaluator_report\.json/);
});

test("a rejection with no routable gap is itself reported as the problem", () => {
  const report = evaluator.parseEvaluatorReport('{"status":"gaps","gaps":[]}');
  assert.match(evaluator.describeGapReport(report, "x"), /without saying what is wrong/);
});

test("the re-loop prompt carries the gaps and targets the right artifact", () => {
  const routing = evaluator.routeGaps([gap("design_gap", { detail: "no error path" })]);
  const prompt = evaluator.reloopPrompt("add auth", routing);
  assert.match(prompt, /no error path/);
  assert.match(prompt, /design\.md/);
  assert.match(prompt, /HARD CONSTRAINTS/);

  const plannerPrompt = evaluator.reloopPrompt("add auth", evaluator.routeGaps([gap("plan_gap")]));
  assert.match(plannerPrompt, /tasks\.json/);
});
