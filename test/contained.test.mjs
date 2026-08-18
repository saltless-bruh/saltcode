/**
 * Task 13.3 / G-029 — the extension's side of the contained-execution channel
 * (REQ-SEC-007 AC1/AC2, REQ-SEC-002, REQ-BLD-003).
 *
 * The backend's own suite proves the container holds. What only this side can prove is
 * that the extension *reaches* it correctly: the right entrypoint, the right writable
 * root, and — the one that bit once already — an argv that survives the trip. `--arg -rf`
 * parses as an option rather than a value, which turns "refused" into "not understood";
 * these assert the JSON channel that fixed it.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let contained;
let backend;

before(async () => {
  contained = await load("saltcode/contained.ts");
  backend = await load("saltcode/backend.ts");
});

/** A runner that records the argv it was asked for and replays a scripted envelope. */
function runnerDouble(payload = { tool: "contained_exec", ok: true, exit_code: 0 }) {
  const calls = [];
  const runner = new backend.BackendRunner({
    exec: async (command, args) => {
      calls.push([command, ...args]);
      return { stdout: JSON.stringify(payload), stderr: "", code: payload.exit_code ?? 0 };
    },
    python: "python3",
    cwd: "/repo",
    dryRun: false,
  });
  return { calls, runner };
}

function channel(runner, { root = "/repo" } = {}) {
  return contained.createContainedExec({
    runner: () => runner,
    workspace: { root, stage: (kind, id, _content) => `/repo/.saltcode/scratch/${kind}-${id}.txt` },
    writableRoot: () => root,
  });
}

test("exec targets the contained_exec entrypoint with the writable root", async () => {
  const { calls, runner } = runnerDouble();
  await channel(runner).exec(["pytest", "-q"]);

  const argv = calls[0];
  assert.deepEqual(argv.slice(0, 3), ["python3", "-m", "saltcode.tools.contained_exec"]);
  assert.equal(argv[argv.indexOf("--sandbox") + 1], "/repo");
});

test("the command travels as JSON, so a leading-dash flag survives", async () => {
  // The regression this channel exists for. `rm -rf /` is the exact shape that has to
  // reach the backend intact — if it arrives mangled it is not refused, it is unparsed.
  const { calls, runner } = runnerDouble();
  await channel(runner).exec(["rm", "-rf", "/"]);

  const argv = calls[0];
  const payload = argv[argv.indexOf("--argv-json") + 1];
  assert.deepEqual(JSON.parse(payload), ["rm", "-rf", "/"]);
});

test("a timeout is forwarded in seconds", async () => {
  const { calls, runner } = runnerDouble();
  await channel(runner).exec(["pytest"], { timeoutSeconds: 30 });
  assert.equal(calls[0][calls[0].indexOf("--timeout") + 1], "30");
});

test("a refusal comes back as refused, not as a failed run", async () => {
  // REQ-SEC-002 AC1. "Not permitted" and "ran and failed" must stay distinguishable all
  // the way up, or a blocked command reads to the caller as a plain test failure.
  const { runner } = runnerDouble({
    tool: "contained_exec",
    ok: false,
    exit_code: 126,
    refused: true,
    detail: "command not on the allowlist: rm -rf /",
    stdout: "",
    stderr: "",
  });

  const result = await channel(runner).exec(["rm", "-rf", "/"]);
  assert.equal(result.refused, true);
  assert.match(result.detail, /not on the allowlist/);
});

test("a write stages its content and passes the path, never the bytes", async () => {
  // A file of any size blows the argument limit, and every quoting scheme that survives
  // argv is one bug away from injection.
  const { calls, runner } = runnerDouble();
  await channel(runner).writeFile("src/thing.py", "x".repeat(500_000));

  const argv = calls[0];
  assert.equal(argv[argv.indexOf("--write-path") + 1], "src/thing.py");
  assert.match(argv[argv.indexOf("--content-file") + 1], /^\/repo\/\.saltcode\/scratch\//);
  assert.ok(
    !argv.some((word) => word.length > 10_000),
    "the content must not appear on the command line",
  );
});

test("contained_exec is a known entrypoint, so a typo cannot reach exec", () => {
  // The Entrypoint union is what stops a mistyped module name becoming a runtime
  // ModuleNotFoundError inside a tool call.
  const { calls, runner } = runnerDouble();
  assert.doesNotThrow(() => runner.commandLine("contained_exec", []));
  assert.equal(calls.length, 0);
  assert.match(
    runner.commandLine("contained_exec", ["--sandbox", "/repo"]),
    /python3 -m saltcode\.tools\.contained_exec --sandbox \/repo/,
  );
});

test("dry-run does not execute a contained command either", async () => {
  // REQ-SEC-004 AC1 is about *every* subprocess, and the built-in override is the one
  // most likely to be forgotten because it does not look like a pipeline step.
  const logged = [];
  const runner = new backend.BackendRunner({
    exec: async () => assert.fail("dry-run must not execute a subprocess"),
    python: "python3",
    cwd: "/repo",
    dryRun: true,
    logDryRun: (line) => logged.push(line),
  });

  const result = await channel(runner).exec(["pytest", "-q"]);
  assert.equal(result.refused, false);
  assert.equal(logged.length, 1);
  assert.match(logged[0], /saltcode\.tools\.contained_exec/);
  assert.match(logged[0], /\["pytest","-q"\]/);
});
