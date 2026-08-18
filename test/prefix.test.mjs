/**
 * Task 6 — the front-loaded stable prefix (REQ-PFX-001, REQ-GLB-004, design §15).
 *
 * The Done-when is a *byte-diff*, so these tests assert byte equality rather than "the
 * same file was read". That distinction is the whole point: `event.systemPrompt` is
 * assembled by Pi and the notes file can be rewritten under us mid-session, so a prefix
 * that merely re-derives its inputs can differ byte-for-byte while every input "looks"
 * unchanged. `decidePrefix` returns the previously frozen strings, and these tests hold it
 * to that by mutating the sources between calls and demanding the output not move.
 */

import { strict as assert } from "node:assert";
import { before, test } from "node:test";
import { load } from "./_load.mjs";

let prefix;

before(async () => {
  prefix = await load("saltcode/prefix.ts");
});

const OPTIONS = {
  selectedTools: ["read", "bash"],
  cwd: "/repo",
  contextFiles: [{ path: "CLAUDE.md", content: "rules" }],
  skills: [{ name: "saltcode-scout" }],
};

function sources(overrides = {}) {
  return {
    systemPrompt: "You are the Scout.",
    design: "## HARD CONSTRAINTS\n\n- no raw source leaves the box",
    notes: ["auth lives in src/auth.py", "tests use pytest"],
    ...overrides,
  };
}

function turn(over = {}) {
  return {
    fingerprint: prefix.fingerprintPromptOptions(OPTIONS),
    evaluatorPass: 0,
    sources: sources(),
    ...over,
  };
}

// ------------------------------------------------------------------- assembly

test("segments are emitted in the order REQ-PFX-001 fixes", () => {
  const decision = prefix.decidePrefix(null, turn());
  const text = decision.prefix;
  assert.ok(text.indexOf("You are the Scout.") < text.indexOf(prefix.DESIGN_HEADER));
  assert.ok(text.indexOf(prefix.DESIGN_HEADER) < text.indexOf(prefix.NOTES_HEADER));
});

test("a fresh project's first Scout call has no segment 2", () => {
  // REQ-GLB-004 AC3. An empty string, not a placeholder — a placeholder would be bytes
  // nobody asked for sitting in the cached prefix of every later turn.
  const decision = prefix.decidePrefix(null, turn({ sources: sources({ design: undefined }) }));
  assert.equal(decision.segments.design, "");
  assert.ok(!decision.prefix.includes(prefix.DESIGN_HEADER));
});

test("the notes cap is enforced here as well as in the backend", () => {
  const seven = ["a", "b", "c", "d", "e", "f", "g"];
  const rendered = prefix.renderNotes(seven);
  assert.equal(rendered.split("\n").length, prefix.MAX_NOTES + 1); // header + 5
  assert.ok(!rendered.includes("- f"));
});

test("blank notes and a blank design collapse to empty segments", () => {
  assert.equal(prefix.renderNotes(["", "   "]), "");
  assert.equal(prefix.renderNotes(undefined), "");
  assert.equal(prefix.renderDesign("   \n  "), "");
  assert.equal(prefix.renderDesign(undefined), "");
});

// -------------------------------------------------------- REQ-GLB-004 AC1

test("segments 1-3 are byte-identical across two calls in one session", () => {
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(first.frozen, turn());

  assert.equal(second.prefix, first.prefix);
  assert.deepEqual(second.segments, first.segments);
  assert.equal(second.reused, true);
  assert.deepEqual(second.rotated, []);
});

test("a wobbling system prompt or notes file cannot move the frozen bytes", () => {
  // The failure this design exists to prevent. Pi re-renders its prompt, the backend
  // rewrites the freeze — and the emitted prefix must still be the same bytes, because
  // nothing that identifies the *configuration* changed.
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(
    first.frozen,
    turn({
      sources: sources({
        systemPrompt: "You are the Scout.  ",
        notes: ["something else entirely"],
        design: "## HARD CONSTRAINTS\n\n- rewritten",
      }),
    }),
  );

  assert.equal(second.prefix, first.prefix);
  assert.equal(second.reused, true);
});

// -------------------------------------------------------- REQ-GLB-004 AC2

test("an Architect re-loop may move segment 2 and must not move 1 or 3", () => {
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(
    first.frozen,
    turn({
      evaluatorPass: 1,
      sources: sources({ design: "## HARD CONSTRAINTS\n\n- revised by the Architect" }),
    }),
  );

  assert.equal(second.segments.system, first.segments.system);
  assert.equal(second.segments.notes, first.segments.notes);
  assert.notEqual(second.segments.design, first.segments.design);
  assert.equal(second.reused, false);
  assert.match(second.rotated.join(" "), /Evaluator pass 0 → 1/);
  assert.match(second.rotated.join(" "), /it changed/);
});

test("a re-loop that did not actually change design.md says so", () => {
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(first.frozen, turn({ evaluatorPass: 1 }));
  assert.equal(second.segments.design, first.segments.design);
  assert.match(second.rotated.join(" "), /it was unchanged/);
});

// --------------------------------------------------------------- fingerprint

test("a changed toolset rotates the whole prefix rather than serving a stale one", () => {
  // Freezing segment 1 forever would eventually describe tools that are no longer
  // registered — a correctness hazard traded for a cost saving, which is the wrong way
  // round. The fingerprint is what keeps the freeze honest.
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(
    first.frozen,
    turn({
      fingerprint: prefix.fingerprintPromptOptions({
        ...OPTIONS,
        selectedTools: ["read", "bash", "saltcode_read_scoped"],
      }),
      sources: sources({ systemPrompt: "You are the Builder." }),
    }),
  );

  assert.equal(second.reused, false);
  assert.equal(second.segments.system, "You are the Builder.");
  assert.match(second.rotated.join(" "), /system prompt options changed/);
});

test("the fingerprint is stable for identical options and moves for each field", () => {
  assert.equal(
    prefix.fingerprintPromptOptions(OPTIONS),
    prefix.fingerprintPromptOptions({ ...OPTIONS }),
  );
  const base = prefix.fingerprintPromptOptions(OPTIONS);
  for (const change of [
    { customPrompt: "x" },
    { appendSystemPrompt: "x" },
    { cwd: "/elsewhere" },
    { promptGuidelines: ["x"] },
    { toolSnippets: { read: "x" } },
    { skills: [{ name: "saltcode-builder" }] },
    { contextFiles: [{ path: "CLAUDE.md", content: "rules and more" }] },
  ]) {
    assert.notEqual(prefix.fingerprintPromptOptions({ ...OPTIONS, ...change }), base);
  }
});

test("an absent options object still produces a usable fingerprint", () => {
  assert.equal(typeof prefix.fingerprintPromptOptions(undefined), "string");
});

// -------------------------------------------------------------- notes on disk

test("the frozen set is read from disk and capped", () => {
  const raw = JSON.stringify({
    session_id: "default",
    goal: "g",
    notes: ["a", "b", "c", "d", "e", "f"],
  });
  assert.deepEqual(prefix.parseFrozenNotes(raw, "default"), ["a", "b", "c", "d", "e"]);
});

test("a freeze belonging to another session is ignored, not adapted", () => {
  const raw = JSON.stringify({ session_id: "other", notes: ["a"] });
  assert.deepEqual(prefix.parseFrozenNotes(raw, "default"), []);
});

test("a corrupt or missing freeze is a cold brain, not a failed turn", () => {
  // The notes are an optimisation; failing the turn over them would trade a whole sprint
  // for a cache segment.
  assert.deepEqual(prefix.parseFrozenNotes("{not json", "default"), []);
  assert.deepEqual(prefix.parseFrozenNotes(undefined, "default"), []);
  assert.deepEqual(prefix.parseFrozenNotes("[]", "default"), []);
  assert.deepEqual(prefix.parseFrozenNotes('{"notes": [1, "a", null]}', "default"), ["a"]);
});

test("the notes path matches what atomic_notes.py writes", () => {
  assert.deepEqual([...prefix.FROZEN_NOTES_PATH], ["cache", "frozen_notes.json"]);
  assert.equal(prefix.DEFAULT_NOTES_SESSION, "default");
});

// ------------------------------------------------------------------ reporting

test("describePrefix distinguishes a reuse from a rotation", () => {
  const first = prefix.decidePrefix(null, turn());
  const second = prefix.decidePrefix(first.frozen, turn());
  assert.match(prefix.describePrefix(first), /rotated/);
  assert.match(prefix.describePrefix(second), /reused byte-identically/);
});

// ------------------------------------------------ 6.3 the debug instrument

test("the payload observer hashes the cacheable head, not the whole request", () => {
  // A growing conversation must not read as a moved prefix on every turn — that would
  // make the instrument report the failure it exists to detect, every single time.
  const first = prefix.observePayloadPrefix({ system: "S", messages: [{ role: "user" }] }, null);
  const grown = prefix.observePayloadPrefix(
    { system: "S", messages: [{ role: "user" }, { role: "assistant" }] },
    first.hash,
  );
  assert.equal(grown.source, "system");
  assert.equal(grown.changed, false);
  assert.equal(grown.hash, first.hash);
});

test("a moved system prefix is reported as changed", () => {
  const first = prefix.observePayloadPrefix({ system: "S" }, null);
  const moved = prefix.observePayloadPrefix({ system: "S plus a tool schema" }, first.hash);
  assert.equal(moved.changed, true);
});

test("the observer handles both provider payload shapes and an unknown one", () => {
  assert.equal(prefix.observePayloadPrefix({ system: "S" }, null).source, "system");
  assert.equal(
    prefix.observePayloadPrefix({ messages: [{ role: "system", content: "S" }] }, null).source,
    "messages-head",
  );
  assert.equal(prefix.observePayloadPrefix("something else", null).source, "payload-head");
  assert.equal(prefix.observePayloadPrefix(null, null).source, "payload-head");
});

test("the first observation is never reported as changed", () => {
  assert.equal(prefix.observePayloadPrefix({ system: "S" }, null).changed, false);
});

test("an unrecognised payload is hashed only up to the bounded head", () => {
  const long = "x".repeat(prefix.PAYLOAD_HEAD_CHARS * 2);
  const observation = prefix.observePayloadPrefix(long, null);
  assert.equal(observation.bytes, prefix.PAYLOAD_HEAD_CHARS);
});
