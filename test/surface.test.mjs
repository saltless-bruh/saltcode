/**
 * Tasks 13.2 and 13.7 — the registered surface and what a human sees
 * (REQ-EXT-004 AC2, REQ-EXT-008, REQ-FAIL-003, REQ-FAIL-004, REQ-SEC-004 AC2).
 *
 * Two kinds of assertion here. The first pins the *set* of registered tools against the
 * requirement's own list, in both directions, so a thirteenth tool or a missing one is a
 * failure rather than a discovery. The second checks the lines a human actually reads
 * during an autonomous run — a paused sprint that looks like a slow one is a real failure
 * mode, and it is invisible to every other test in this suite.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let tools;
let ui;
let workspace;

before(async () => {
  tools = await load("saltcode/tools.ts");
  ui = await load("saltcode/ui.ts");
  workspace = await load("saltcode/workspace.ts");
});

/** REQ-EXT-004 AC2, verbatim: the twelve capabilities the bridge SHALL cover. */
const REQUIRED = [
  "validate_contract",
  "scope_probe",
  "cache_lookup",
  "diff_check",
  "sandbox_apply",
  "static_gate",
  "test_run",
  "stability",
  "apply_live",
  "compact_spec",
  "calibrate",
  "read_scoped",
].map((name) => `saltcode_${name}`);

test("the registered tool set is exactly REQ-EXT-004 AC2's twelve", () => {
  assert.deepEqual([...tools.SALTCODE_TOOL_NAMES].sort(), [...REQUIRED].sort());
});

test("connectivity is deliberately not a registered tool", () => {
  // It is the session-open probe (REQ-GATE-001). Exposing it to the model would invite a
  // mid-sprint re-probe that silently changes the routing tier under a running plan.
  assert.equal(tools.SALTCODE_TOOL_NAMES.includes("saltcode_connectivity"), false);
});

// ------------------------------------------------------------------- the widget

const SPRINT = {
  sprintId: "s1",
  goal: "add rate limiting",
  phase: "phase2",
  phase1Fired: true,
  online: true,
  project: "fresh",
  loops: { architect: 0, planner: 0 },
  taskOrder: ["T1", "T2", "T3"],
  completed: ["T1"],
};

const BUDGET = { taskId: "T2", attempts: 1, specDefects: 0, startedOnTierB: false };

test("the gate line shows the sequence in order, and where it stopped", () => {
  const line = ui.renderGateLine({
    gates: { diff: "pass", sandbox: "pass", static: "fail" },
  });
  assert.equal(line, "diff ✓  sandbox ✓  static ✗  tests ·  auditor ·  apply ·");
});

test("a FLAG HUMAN line comes first and pushes everything else down", () => {
  // REQ-FAIL-003. A stopped run waiting for a human is the only fact that matters at that
  // moment; under a progress board it reads as a slow one.
  const lines = ui.renderWidget({
    sprint: SPRINT,
    budget: BUDGET,
    board: { gates: {} },
    flag: "budget exhausted on T2",
  });
  assert.match(lines[0], /^⚑ FLAG HUMAN — budget exhausted on T2$/);
  assert.ok(lines.some((line) => line.includes("phase phase2")));
});

test("the widget reports progress, the budget and the tier", () => {
  const lines = ui.renderWidget({ sprint: SPRINT, budget: BUDGET, board: { gates: {} } });
  assert.ok(lines.some((line) => line.includes("tasks 1/3")));
  assert.ok(lines.some((line) => line.includes("next T2")));
  assert.ok(lines.some((line) => line.includes("T2: attempt 2/3 on Tier A")));
});

test("with no sprint the widget says how to start one", () => {
  const lines = ui.renderWidget({ sprint: null, budget: null, board: { gates: {} } });
  assert.match(lines[0], /no sprint/);
});

test("the integration notice states what is NOT verified", () => {
  // REQ-FAIL-004 AC1 and Trade B. This is the permanent non-goal; the wording exists so a
  // human reads it as a limit rather than as boilerplate.
  assert.match(ui.INTEGRATION_NOTICE, /cross-task integration is YOUR check/);
  assert.match(ui.INTEGRATION_NOTICE, /never will be claimed as verified/);
});

test("headless modes answer the four decisions with 'do not proceed'", async () => {
  // In `-p` or JSON mode there is nobody to ask, and all four gate an action. Defaulting
  // to false is the only answer that cannot silently ship something unreviewed.
  const surface = new ui.SaltcodeUI({
    hasUI: false,
    ui: { confirm: async () => assert.fail("must not prompt when there is no UI") },
  });
  for (const decision of [1, 2, 3, 4]) {
    assert.equal(await surface.decide(decision, "body"), false);
  }
});

test("Decision 3 carries the integration notice, and the others do not", async () => {
  const seen = [];
  const surface = new ui.SaltcodeUI({
    hasUI: true,
    ui: {
      confirm: async (title, message) => {
        seen.push({ title, message });
        return true;
      },
    },
  });

  await surface.decide(1, "plan body");
  await surface.decide(3, "diff body");

  assert.equal(seen[0].message.includes(ui.INTEGRATION_NOTICE), false);
  assert.equal(seen[1].message.includes(ui.INTEGRATION_NOTICE), true);
  assert.match(seen[1].title, /Decision 3/);
});

// ------------------------------------------------------------------- the workspace

test("workspace paths cannot escape .saltcode/", () => {
  // REQ-SEC-004 AC2 makes "nothing outside .saltcode/" a hard property of dry-run mode;
  // holding it in every mode means dry-run needs no second code path to be honest about.
  const w = new workspace.Workspace("/repo");
  assert.equal(w.path("scratch", "diff.txt"), "/repo/.saltcode/scratch/diff.txt");
  assert.throws(() => w.path("../escape"), /refusing to build/);
  assert.throws(() => w.path("/etc/passwd"), /refusing to build/);
  assert.throws(() => w.path("scratch", "a/../../b"), /refusing to build/);
});

test("staged filenames are sanitised and deterministic per task", () => {
  // Deterministic so a retry overwrites its own scratch file instead of leaving one per
  // attempt, and so a human reading .saltcode/scratch/ finds the last real output.
  const w = new workspace.Workspace("/repo");
  assert.equal(
    w.path("scratch", `diff-${"T1/../../etc".replace(/[^A-Za-z0-9._-]/g, "_")}.txt`),
    "/repo/.saltcode/scratch/diff-T1_.._.._etc.txt",
  );
});
