/**
 * Tasks 13.2, 13.6, 13.9 — the backend bridge, the compaction guard, and dry-run
 * (REQ-EXT-004, REQ-EXT-007, REQ-SEC-004, REQ-SEC-006).
 *
 * The bridge's contract is `docs/entrypoints.md`: one JSON object on stdout, and an exit
 * code that classifies the outcome. Both halves are asserted here, including the empty-
 * stdout case — which is not hypothetical. Every entrypoint once returned exit 2 on an
 * unknown flag with nothing on stdout, and `JSON.parse("")` turned a mistyped flag into an
 * unhandled exception rather than a tool result (G-006).
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let backend;
let constraints;
let config;

before(async () => {
  backend = await load("saltcode/backend.ts");
  constraints = await load("saltcode/constraints.ts");
  config = await load("saltcode/config.ts");
});

function runner(exec, overrides = {}) {
  return new backend.BackendRunner({
    exec,
    python: "python3",
    cwd: "/repo",
    dryRun: false,
    ...overrides,
  });
}

// ------------------------------------------------------------------- the envelope

test("an entrypoint is invoked as python -m saltcode.tools.<name>", async () => {
  const seen = [];
  const r = runner(async (command, args) => {
    seen.push([command, ...args]);
    return { stdout: '{"tool":"static_gate","ok":true}', stderr: "", code: 0 };
  });
  await r.call("static_gate", ["--sandbox", "/tmp/s"]);
  assert.deepEqual(seen[0], ["python3", "-m", "saltcode.tools.static_gate", "--sandbox", "/tmp/s"]);
});

test("a negative verdict is a result, not an error", async () => {
  // Exit 1 is a cache miss, a dirty gate, a failing test, a refused read. Reporting any of
  // them as a crash would make a normal Phase-2 lap look like a broken installation.
  const r = runner(async () => ({
    stdout: '{"tool":"cache_lookup","ok":false,"verdict":"miss"}',
    stderr: "",
    code: 1,
  }));
  const result = await r.call("cache_lookup", ["--goal", "x"]);
  assert.equal(result.code, 1);
  assert.equal(result.payload.verdict, "miss");
  assert.equal(backend.isToolFailure(result), false);
});

test("exit 2 and 3 are tool failures, and must not route to the Builder", async () => {
  for (const code of [2, 3]) {
    const r = runner(async () => ({
      stdout: '{"tool":"static_gate","ok":false,"error":"OSError"}',
      stderr: "",
      code,
    }));
    assert.equal(backend.isToolFailure(await r.call("static_gate", [])), true, `exit ${code}`);
  }
});

test("empty stdout is a contract breach with a diagnosable message", async () => {
  const r = runner(async () => ({
    stdout: "",
    stderr: "usage: saltcode.tools.diff_check",
    code: 2,
  }));
  await assert.rejects(
    () => r.call("diff_check", ["--nope"]),
    (error) => {
      assert.equal(error.name, "BackendContractError");
      assert.match(error.message, /empty stdout/);
      assert.match(error.message, /usage: saltcode.tools.diff_check/, "stderr is carried through");
      return true;
    },
  );
});

test("non-JSON and non-object stdout are both refused", async () => {
  await assert.rejects(() =>
    runner(async () => ({
      stdout: "Traceback (most recent call last):",
      stderr: "",
      code: 3,
    })).call("test_run", []),
  );
  await assert.rejects(() =>
    runner(async () => ({ stdout: "[1,2,3]", stderr: "", code: 0 })).call("test_run", []),
  );
});

// ----------------------------------------------------------------------- dry run

test("dry-run executes nothing and logs the exact command line", async () => {
  // REQ-SEC-004 AC1/AC3.
  const logged = [];
  const r = runner(async () => assert.fail("dry-run must not execute a subprocess"), {
    dryRun: true,
    logDryRun: (line) => logged.push(line),
  });

  const result = await r.call("apply_live", ["--repo", "/repo", "--in", "/repo/.saltcode/d.txt"]);
  assert.equal(result.dryRun, true);
  assert.equal(result.code, 0);
  assert.equal(result.payload.verdict, "dry_run");
  assert.deepEqual(logged, [
    "python3 -m saltcode.tools.apply_live --repo /repo --in /repo/.saltcode/d.txt",
  ]);
});

test("the dry-run log lives inside .saltcode/", () => {
  // REQ-SEC-004 AC2: nothing outside .saltcode/ is modified, and that includes the record.
  assert.equal(backend.DRY_RUN_LOG.startsWith(".saltcode/"), true);
});

// ------------------------------------------------------- HARD CONSTRAINTS (13.6)

const DESIGN = [
  "# Design",
  "",
  "Some prose about the approach.",
  "",
  "## HARD CONSTRAINTS",
  "",
  "- Never call the payment API from a test.",
  "- All money is integer cents.",
  "",
  "## Next section",
  "",
  "More prose.",
].join("\n");

test("the constraints block runs from its heading to the next one", () => {
  const block = constraints.extractConstraints(DESIGN);
  assert.match(block.text, /^## HARD CONSTRAINTS/);
  assert.equal(block.text.includes("More prose"), false);
  assert.deepEqual(block.lines, [
    "Never call the payment API from a test.",
    "All money is integer cents.",
  ]);
});

test("a design with no constraints block is 'nothing to preserve', not an error", () => {
  const block = constraints.extractConstraints("# Design\n\nJust prose.\n");
  assert.equal(block.text, "");
  assert.deepEqual(block.lines, []);
});

test("splicing puts the constraints back verbatim, and verification agrees", () => {
  // The splice is deterministic on purpose. A model asked to "keep the constraints" can
  // paraphrase one, and a paraphrased constraint reads exactly like a preserved one.
  const block = constraints.extractConstraints(DESIGN);
  const summary = "The team refactored the ledger and moved on to reporting.";

  assert.equal(constraints.retainsAllConstraints(summary, block), false);
  assert.deepEqual(constraints.missingConstraints(summary, block), block.lines);

  const spliced = constraints.spliceConstraints(summary, block);
  assert.equal(constraints.retainsAllConstraints(spliced, block), true);
  assert.ok(spliced.includes(summary), "the summary itself survives the splice");
  assert.ok(
    spliced.indexOf("HARD CONSTRAINTS") < spliced.indexOf(summary),
    "constraints come first — they bind everything below them",
  );
});

test("a summary that already restates a constraint still verifies", () => {
  const block = constraints.extractConstraints(DESIGN);
  const summary = [
    "Reminder: All money is integer cents.",
    "Never call the payment API from a test.",
  ].join("\n");
  assert.equal(constraints.retainsAllConstraints(summary, block), true);
});

// ------------------------------------------------------------ project config (TOML)

test("the TOML subset reads the keys the extension actually uses", () => {
  const parsed = config.parseTomlSubset(
    [
      "# a comment",
      "[project]",
      'language = "python"',
      'test_runner_cmd = "pytest -q"   # trailing comment',
      "",
      "[security]",
      'allowed_commands = ["pytest", "python -m pytest"]',
      "",
      "[local]",
      "a_focus_threshold = 65_536",
      "mtp_enabled = true",
      "",
      "[checkpoint]",
      'auto_mode = "hybrid"',
      "auto_push = false",
    ].join("\n"),
  );

  const c = config.configFromToml(parsed);
  assert.equal(c.language, "python");
  assert.equal(c.testRunnerCmd, "pytest -q");
  assert.deepEqual(c.allowedCommands, ["pytest", "python -m pytest"]);
  assert.equal(c.aFocusThreshold, 65536);
  assert.equal(c.mtpEnabled, true);
  assert.equal(c.autoMode, "hybrid");
  assert.equal(c.autoPush, false);
});

test("a `#` inside a string is not a comment", () => {
  const parsed = config.parseTomlSubset('[project]\ntest_runner_cmd = "pytest -k \\"a#b\\""');
  assert.equal(config.configFromToml(parsed).testRunnerCmd, 'pytest -k "a#b"');
});

test("anything outside the subset is an error, never a silent skip", () => {
  // A config key that quietly fails to apply is the failure mode the whole file guards
  // against: the allowlist and the run mode would *look* configured.
  for (const source of [
    "[local]\nvalues = [\n  1,\n  2,\n]",
    "[a]\nkey = 2026-01-01",
    "[project]\nnested.key = 1",
    "not a header or an assignment",
  ]) {
    assert.throws(() => config.parseTomlSubset(source), /saltcode\.toml line/);
  }
});

test("defaults are the documented conservative ones", () => {
  assert.equal(config.DEFAULT_CONFIG.aFocusThreshold, 32768);
  assert.equal(config.DEFAULT_CONFIG.mtpEnabled, false);
  assert.equal(config.DEFAULT_CONFIG.autoMode, "off");
  assert.equal(config.DEFAULT_CONFIG.autoPush, false);
});
