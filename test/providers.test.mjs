/**
 * Task 11 — registered providers, Saltnitor residency, and the VRAM triangle
 * (REQ-EXT-002, REQ-MOD-001b, REQ-MOD-003, REQ-MOD-005, REQ-MOD-006).
 *
 * Three things here are worth more than the rest.
 *
 * `ensure` refusing with OOM has to be a **flag**, not an exception: the oracle declining
 * to load Tier B is a correct answer from a healthy box, and crashing on it would take
 * down a sprint that could have paused.
 *
 * Saltnitor being unreachable at startup has to **degrade**, not fail: the offline route
 * depends on Saltnitor models being registered, so a router that is merely slow to start
 * must not take the registration — and with it the offline path — down with it.
 *
 * And the VRAM triangle has to be reconciled *visibly*. Dropping the caller's thinking
 * level silently is how a Builder turn quietly stops reasoning and nobody knows why.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let providers;

before(async () => {
  providers = await load("saltcode/providers.ts");
});

function piDouble() {
  const registered = new Map();
  return {
    registered,
    pi: { registerProvider: (name, config) => registered.set(name, config) },
  };
}

const OK_MODELS = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ data: [{ id: "A_STD" }, { id: "B" }] }),
});

test("DeepSeek and Saltnitor are both registered (REQ-EXT-002 AC1)", async () => {
  const { pi, registered } = piDouble();
  await providers.registerProviders(pi, { fetchImpl: OK_MODELS });

  assert.deepEqual([...registered.keys()].sort(), ["deepseek", "saltnitor"]);
  assert.equal(registered.get("deepseek").apiKey, "$DEEPSEEK_API_KEY");
  assert.deepEqual(
    registered.get("deepseek").models.map((m) => m.id),
    ["v4-flash", "v4-pro"],
  );
});

test("Saltnitor is addressed on loopback, never a LAN address", () => {
  // `.claude/rules/privacy-boundary.md`: the deployment is one box, so only loopback
  // counts as local. A LAN address would be off-box and would put bodies on a wire.
  assert.equal(providers.SALTNITOR_BASE_URL, "http://127.0.0.1:8765/v1");
});

test("discovery narrows the registered sections to the live ones", async () => {
  const { pi, registered } = piDouble();
  const result = await providers.registerProviders(pi, { fetchImpl: OK_MODELS });

  assert.deepEqual(
    registered.get("saltnitor").models.map((m) => m.id),
    ["A_STD", "B"],
    "A_FOCUS was not reported, so it is not registered",
  );
  assert.equal(result.find((r) => r.provider === "saltnitor").source, "discovered");
});

test("an unreachable Saltnitor degrades instead of failing the registration", async () => {
  // REQ-EXT-002 AC2. The offline path needs these models registered; a router that is slow
  // to start must not remove the only route that works without the network.
  const { pi, registered } = piDouble();
  const result = await providers.registerProviders(pi, {
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
  });

  const saltnitor = result.find((r) => r.provider === "saltnitor");
  assert.equal(saltnitor.source, "degraded");
  assert.match(saltnitor.detail, /unreachable/);
  assert.deepEqual(
    registered.get("saltnitor").models.map((m) => m.id),
    ["A_STD", "A_FOCUS", "B"],
    "all three configured sections stay available",
  );
});

test("an optional Qwen provider registers when configured, and not otherwise", async () => {
  const without = piDouble();
  await providers.registerProviders(without.pi, { fetchImpl: OK_MODELS });
  assert.equal(without.registered.has("qwen"), false);

  const with_ = piDouble();
  await providers.registerProviders(with_.pi, {
    fetchImpl: OK_MODELS,
    qwen: {
      baseUrl: "https://example.invalid/v1",
      models: [
        {
          id: "qwen3.6-plus",
          name: "Qwen",
          reasoning: true,
          input: ["text"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 1000,
          maxTokens: 100,
        },
      ],
    },
  });
  assert.equal(with_.registered.has("qwen"), true);
});

// -------------------------------------------------------------------- ensure

test("ensure reports residency when Saltnitor accepts", async () => {
  const seen = [];
  const outcome = await providers.ensureProfile("B", {
    fetchImpl: async (url, init) => {
      seen.push([url, JSON.parse(init.body)]);
      return { ok: true, status: 200, json: async () => ({ already_resident: false }) };
    },
  });

  assert.equal(outcome.ok, true);
  assert.equal(outcome.alreadyResident, false);
  assert.match(seen[0][0], /\/ensure$/);
  assert.deepEqual(seen[0][1], { profile: "B" });
});

test("an OOM refusal is a human flag, not a crash (REQ-MOD-005 AC1)", async () => {
  for (const response of [
    { ok: false, status: 507, text: async () => "insufficient VRAM for B" },
    { ok: false, status: 400, text: async () => "oracle refused: OOM" },
  ]) {
    const outcome = await providers.ensureProfile("B", { fetchImpl: async () => response });

    assert.equal(outcome.ok, false);
    assert.equal(outcome.flagHuman, true, "an OOM must stop the task for a human");
    assert.match(outcome.reason, /21GB|out of memory|OOM|memory/i);
  }
});

test("an unreachable Saltnitor is a failure but NOT a human flag", async () => {
  // The distinction is the point: "the box cannot fit this" needs a person, "the router is
  // not up" needs a retry or the documented llama.cpp fallback.
  const outcome = await providers.ensureProfile("A_STD", {
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
  });

  assert.equal(outcome.ok, false);
  assert.equal(outcome.flagHuman, false);
  assert.match(outcome.reason, /llama\.cpp|unreachable/);
});

// ------------------------------------------------------------- VRAM triangle

test("A_FOCUS forces thinking off (REQ-MOD-006 AC1)", () => {
  const { turn, adjustments } = providers.reconcileLocalTurn({
    profile: "A_FOCUS",
    thinking: "high",
    mtpEnabled: false,
  });

  assert.equal(turn.thinking, "off");
  assert.equal(adjustments.length, 1);
  assert.match(adjustments[0], /256K/, "the reason names what the thinking was traded for");
});

test("MTP is never enabled on A_FOCUS (REQ-MOD-006 AC3)", () => {
  const { turn, adjustments } = providers.reconcileLocalTurn({
    profile: "A_FOCUS",
    thinking: "off",
    mtpEnabled: true,
  });

  assert.equal(turn.mtp, false);
  assert.match(adjustments.join(" "), /MTP disabled/);
});

test("on A_STD, thinking and MTP cannot both be held", () => {
  const withThinking = providers.reconcileLocalTurn({
    profile: "A_STD",
    thinking: "high",
    mtpEnabled: true,
  });
  assert.equal(withThinking.turn.thinking, "high", "thinking is the one the caller asked for");
  assert.equal(withThinking.turn.mtp, false);

  const throughput = providers.reconcileLocalTurn({
    profile: "A_STD",
    thinking: "off",
    mtpEnabled: true,
  });
  assert.equal(throughput.turn.mtp, true, "with thinking off there is room for MTP");
  assert.deepEqual(throughput.adjustments, []);
});

test("Tier B is not bound by the triangle", () => {
  // Its hybrid offload does not compete for the same 12GB, and its MTP is the stable one.
  const { turn, adjustments } = providers.reconcileLocalTurn({
    profile: "B",
    thinking: "medium",
    mtpEnabled: true,
  });

  assert.equal(turn.thinking, "medium");
  assert.equal(turn.mtp, true);
  assert.deepEqual(adjustments, []);
});

test("a legal combination passes through untouched and says nothing", () => {
  // Silence is the signal that nothing was traded away; an adjustment list that is never
  // empty would train a reader to ignore it.
  const { turn, adjustments } = providers.reconcileLocalTurn({
    profile: "A_STD",
    thinking: "high",
    mtpEnabled: false,
  });

  assert.deepEqual(turn, { profile: "A_STD", thinking: "high", mtp: false });
  assert.deepEqual(adjustments, []);
});
