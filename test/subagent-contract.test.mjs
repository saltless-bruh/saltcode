/**
 * Task 7.3 / 7.3b — the sub-agent definitions, resolved through `pi-subagents`' own loader.
 *
 * Everything else asserting these definitions reads the Markdown. This file is the only
 * check that asks the *extension that will actually consume them* what it sees, which is
 * the difference between "the file says Scout has no read tool" and "Scout has no read
 * tool". Three things can only be proven here:
 *
 *   1. The `pi.subagents.agents` key in package.json is read at all (G-024). A repo-root
 *      `agents/` is on no default discovery path; if that key were wrong the definitions
 *      would be silently invisible and every Markdown-level test would still pass.
 *   2. The skill really reaches the child's system prompt (DD-16 / G-023). The skill is
 *      inlined into the body because `pi-subagents` has no preload feature; whether that
 *      lands is a property of the loader, not of the file.
 *   3. The tool allowlist a spawn would actually receive (7.3b). A definition that merely
 *      *says* "no read tool" is a finding, not a pass.
 *
 * Run: `npm run test:agents`
 */

import { strict as assert } from "node:assert";
import { createRequire } from "node:module";
import path from "node:path";
import { before, test } from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

let agents = new Map();

before(async () => {
  const { createJiti } = await import(path.join(REPO_ROOT, "node_modules/jiti/lib/jiti.mjs"));
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const mod = await jiti.import(
    path.join(REPO_ROOT, "node_modules/pi-subagents/src/agents/agents.ts"),
  );
  process.chdir(REPO_ROOT);
  const discovered = await mod.discoverAgents(REPO_ROOT);
  const list = Array.isArray(discovered) ? discovered : (discovered.agents ?? []);
  agents = new Map(
    list.filter((a) => (a.name ?? "").startsWith("saltcode-")).map((a) => [a.name, a]),
  );
});

const SPAWNED = [
  "saltcode-scout",
  "saltcode-architect",
  "saltcode-planner",
  "saltcode-test-intent",
  "saltcode-evaluator",
  "saltcode-builder",
];

test("the pinned version is the one that was trust-reviewed", () => {
  const pkg = require(path.join(REPO_ROOT, "node_modules/pi-subagents/package.json"));
  assert.equal(
    pkg.version,
    "0.40.0",
    "docs/subagent_contract.md records the schema for 0.40.0; a different version " +
      "may read different frontmatter keys, and unknown keys are ignored silently",
  );
});

test("all six spawned agents are discovered, from the package path", () => {
  assert.deepEqual([...agents.keys()].sort(), [...SPAWNED].sort());
  for (const name of SPAWNED) {
    // `source: "package"` is the G-024 fix working: it means the loader resolved
    // `pi.subagents.agents` from package.json rather than finding these by accident
    // under `.agents/` or `.pi/agents/`.
    assert.equal(agents.get(name).source, "package", `${name} came from the wrong path`);
  }
});

test("the Auditor is not a spawned sub-agent", () => {
  // Design §5.6a: its N-pass judgment is the backend `compute_stability` tool. Spawning
  // one would run the judgment in a context that cannot measure its own stability.
  assert.equal(agents.has("saltcode-auditor"), false);
});

test("every agent runs in an isolated context", () => {
  for (const name of SPAWNED) {
    const a = agents.get(name);
    assert.equal(a.systemPromptMode, "replace", name);
    assert.equal(a.inheritSkills, false, name);
    assert.equal(a.inheritProjectContext, false, name);
  }
});

test("every agent's skill is present in its resolved system prompt", () => {
  // DD-16 / G-023. This is the assertion the whole inline-and-sync mechanism exists to
  // make true, and the only place it can be checked end to end.
  for (const name of SPAWNED) {
    const prompt = agents.get(name).systemPrompt ?? "";
    assert.ok(
      /# Saltcode .*Skill|# Using the LSP\/AST tools/.test(prompt),
      `${name}: no skill content in the prompt the child would receive`,
    );
  }
});

test("lsp-usage is preloaded for Scout and Builder, and only those two", () => {
  // REQ-EXT-016 AC2 names exactly these two.
  const marker = "# Using the LSP/AST tools";
  for (const name of SPAWNED) {
    const has = (agents.get(name).systemPrompt ?? "").includes(marker);
    assert.equal(has, name === "saltcode-scout" || name === "saltcode-builder", name);
  }
});

// ------------------------------------------------------------------ 7.3b: adversarial

test("Scout cannot obtain a file body — the capability is absent, not blocked", () => {
  // REQ-SCT-001 and .claude/rules/privacy-boundary.md. Scout is the one Phase-1 agent
  // that touches the repository and it is API-routed, so a body-reading tool in its
  // allowlist is a path from raw source to a network provider.
  const tools = agents.get("saltcode-scout").tools ?? [];
  assert.ok(!tools.includes("read"), `scout holds a read tool: ${tools}`);
  assert.ok(!tools.includes("saltcode_read_scoped"), `scout holds scoped read: ${tools}`);
  assert.ok(!tools.some((t) => /bash|shell|exec/.test(t)), `scout holds a shell: ${tools}`);
});

test("only the Builder may read file bodies", () => {
  // It runs on a local Saltnitor model, which is the entire reason it is permitted to.
  assert.ok((agents.get("saltcode-builder").tools ?? []).includes("saltcode_read_scoped"));
  for (const name of SPAWNED.filter((n) => n !== "saltcode-builder")) {
    assert.ok(
      !(agents.get(name).tools ?? []).includes("saltcode_read_scoped"),
      `${name} should not hold scoped read`,
    );
  }
});

test("no agent holds a shell", () => {
  // `bash` would route around every tool-level boundary at once.
  for (const name of SPAWNED) {
    const tools = agents.get(name).tools ?? [];
    assert.ok(!tools.some((t) => /^(bash|sh|shell|exec)$/.test(t)), `${name}: ${tools}`);
  }
});

test("the Builder cannot write files directly", () => {
  // Its output is a unified diff the pipeline validates and applies; a write tool would
  // let it bypass diff-check, the sandbox, the static gate and the Auditor entirely.
  assert.ok(!(agents.get("saltcode-builder").tools ?? []).includes("write"));
});

// --------------------------------------------------------------------- design §6 routing

test("model and thinking level match design §6", () => {
  const expected = {
    "saltcode-scout": ["deepseek/v4-flash", "off"],
    "saltcode-architect": ["deepseek/v4-pro", "high"],
    "saltcode-planner": ["deepseek/v4-flash", "off"],
    "saltcode-test-intent": ["deepseek/v4-flash", "off"],
    "saltcode-evaluator": ["deepseek/v4-flash", "off"],
    // The Builder's thinking is chosen per task (the VRAM triangle), so it pins none.
    "saltcode-builder": ["saltnitor/A_STD", undefined],
  };
  for (const [name, [model, thinking]] of Object.entries(expected)) {
    const a = agents.get(name);
    assert.equal(a.model, model, `${name} model`);
    assert.equal(a.thinking, thinking, `${name} thinking`);
  }
});
