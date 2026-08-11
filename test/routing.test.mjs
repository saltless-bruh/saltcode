/**
 * Task 13.4 — the design §6 routing table and the failover chain
 * (REQ-EXT-003, REQ-EXT-010, REQ-ORC-003, REQ-GATE-001, REQ-GATE-002).
 *
 * REQ-EXT-003 AC1 says the active model and thinking level SHALL *equal* the policy value,
 * which is only checkable if the policy is data. So the first half of this file reads the
 * §6 table back out of the code, agent by agent, and the second half exercises the one
 * thing that goes wrong in production: `setModel` returning false.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let agents;
let routing;

before(async () => {
  agents = await load("saltcode/agents.ts");
  routing = await load("saltcode/routing.ts");
});

const ONLINE_FRESH = { online: true, project: "fresh" };
const ONLINE_AMEND = { online: true, project: "amend" };

test("design §6, online, fresh project", () => {
  const table = {
    scout: ["deepseek", "v4-flash", "off"],
    architect: ["deepseek", "v4-pro", "high"],
    planner: ["deepseek", "v4-flash", "off"],
    "test-intent": ["deepseek", "v4-flash", "off"],
    // "fresh project → Pro" is the Evaluator's session modifier.
    evaluator: ["deepseek", "v4-pro", "off"],
    compactor: ["deepseek", "v4-flash", "off"],
  };
  for (const [agent, [provider, model, thinking]] of Object.entries(table)) {
    const route = agents.resolveRoute(agent, ONLINE_FRESH);
    assert.equal(route.provider, provider, `${agent} provider`);
    assert.equal(route.model, model, `${agent} model`);
    assert.equal(route.thinking, thinking, `${agent} thinking`);
  }
});

test("amend/rerun downgrades the Architect and the Evaluator to Flash", () => {
  // REQ-GATE-002 AC1. Pro is bought for a fresh design, not for every re-loop.
  assert.equal(agents.resolveRoute("architect", ONLINE_AMEND).model, "v4-flash");
  assert.equal(agents.resolveRoute("evaluator", ONLINE_AMEND).model, "v4-flash");
  // The Architect still reasons hard about a smaller question.
  assert.equal(agents.resolveRoute("architect", ONLINE_AMEND).thinking, "high");
});

test("offline routes every API agent to Saltnitor Tier B", () => {
  // REQ-GATE-001 AC1 / REQ-MOD-003.
  for (const agent of ["scout", "architect", "planner", "test-intent", "evaluator"]) {
    const route = agents.resolveRoute(agent, { online: false, project: "fresh" });
    assert.equal(route.provider, "saltnitor", agent);
    assert.equal(route.model, "B", agent);
  }
});

test("the Evaluator raises thinking only when re-invoked, the Auditor only on retry", () => {
  // REQ-ORC-003, stated exactly.
  assert.equal(agents.resolveRoute("evaluator", ONLINE_FRESH).thinking, "off");
  assert.equal(
    agents.resolveRoute("evaluator", { ...ONLINE_FRESH, evaluatorReinvoked: true }).thinking,
    "medium",
  );
  assert.equal(agents.resolveRoute("auditor", ONLINE_FRESH).thinking, "off");
  assert.equal(
    agents.resolveRoute("auditor", { ...ONLINE_FRESH, auditorRetry: true }).thinking,
    "medium",
  );
});

test("the Builder is local, and A_FOCUS turns thinking off", () => {
  // The VRAM triangle: thinking + 256K context + MTP draw on the same 12GB, so A_FOCUS
  // buys the window by giving up the thinking.
  const std = agents.resolveRoute("builder", { ...ONLINE_FRESH, builderProfile: "A_STD" });
  assert.equal(std.provider, "saltnitor");
  assert.equal(std.model, "A_STD");

  const focus = agents.resolveRoute("builder", { ...ONLINE_FRESH, builderProfile: "A_FOCUS" });
  assert.equal(focus.model, "A_FOCUS");
  assert.equal(focus.thinking, "off");
});

test("the Builder's profile is chosen by size, and escalation beats size", () => {
  const base = { complexity: "low", escalated: false, aFocusThreshold: 32768 };
  assert.equal(agents.selectBuilderProfile({ ...base, estimatedInputTokens: 1000 }), "A_STD");
  assert.equal(agents.selectBuilderProfile({ ...base, estimatedInputTokens: 40000 }), "A_FOCUS");
  assert.equal(
    agents.selectBuilderProfile({ ...base, complexity: "high", estimatedInputTokens: 40000 }),
    "B",
    "complexity high starts on Tier B regardless of size",
  );
  assert.equal(
    agents.selectBuilderProfile({ ...base, escalated: true, estimatedInputTokens: 100 }),
    "B",
  );
});

test("the Auditor is routed but is not a spawnable agent", () => {
  // Design §5.6a: its N-pass judgment is the backend tool. It has a row because the
  // extension still spends the one online call REQ-AUD-002 AC2 allows.
  assert.equal(agents.SPAWNED_AGENTS.includes("auditor"), false);
  assert.equal(agents.resolveRoute("auditor", ONLINE_FRESH).provider, "saltnitor");
});

// --------------------------------------------------------------- the failover chain

function registry(available) {
  return {
    modelRegistry: {
      find: (provider, id) =>
        available.some((m) => m.provider === provider && m.id === id)
          ? { provider, id }
          : undefined,
    },
  };
}

function fakePi(keyedProviders) {
  const calls = { models: [], thinking: [] };
  return {
    calls,
    pi: {
      setModel: async (model) => {
        calls.models.push(`${model.provider}/${model.id}`);
        return keyedProviders.includes(model.provider);
      },
      setThinkingLevel: (level) => calls.thinking.push(level),
    },
  };
}

test("the configured model is used when it resolves", async () => {
  const { pi, calls } = fakePi(["deepseek"]);
  const resolution = await routing.applyRoute("architect", ONLINE_FRESH, {
    pi,
    ctx: registry([{ provider: "deepseek", id: "v4-pro" }]),
    onFallback: () => assert.fail("no fallback should have been needed"),
  });

  assert.equal(resolution.ok, true);
  assert.equal(resolution.model, "v4-pro");
  assert.deepEqual(calls.thinking, ["high"]);
});

test("setModel returning false falls through the chain and logs it", async () => {
  // REQ-EXT-003 AC2 + REQ-EXT-010 AC1/AC2: surface and fall back, never proceed silently
  // on whatever model happened to be active.
  const { pi } = fakePi(["saltnitor"]);
  const fallbacks = [];
  const resolution = await routing.applyRoute("scout", ONLINE_FRESH, {
    pi,
    ctx: registry([
      { provider: "deepseek", id: "v4-flash" },
      { provider: "deepseek", id: "v4-pro" },
      { provider: "saltnitor", id: "B" },
    ]),
    onFallback: (event) => fallbacks.push(event),
  });

  assert.equal(resolution.ok, true);
  assert.equal(resolution.provider, "saltnitor", "the chain ends at Tier B");
  assert.equal(fallbacks.length, 2, "each hop is logged");
  assert.equal(fallbacks[0].from.outcome, "no_api_key");
});

test("nothing reachable is a flag, not a silent downgrade", async () => {
  // REQ-EXT-010 step 4. With no provider at all the sprint pauses; guessing here would
  // produce a plan written by whatever model was loaded for something else.
  const { pi } = fakePi([]);
  const resolution = await routing.applyRoute("planner", ONLINE_FRESH, {
    pi,
    ctx: registry([]),
    onFallback: () => {},
  });

  assert.equal(resolution.ok, false);
  assert.match(resolution.reason, /no provider is available/);
  assert.ok(resolution.attempts.every((a) => a.outcome === "unknown_model"));
});
