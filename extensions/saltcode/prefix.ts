/**
 * The front-loaded stable prefix (Task 6 · REQ-PFX-001, REQ-GLB-004, design §15).
 *
 * Every Phase-1 turn is assembled as
 *
 * ```
 * 1. system prompt (active agent)   fixed per agent    → cached
 * 2. design.md                      session            → cached, may change on a re-loop
 * 3. atomic notes (≤5, FROZEN)      session            → cached
 * 4. per-call delta / new input     per call           → billed
 * ```
 *
 * Segments 1–3 are the cacheable prefix and go into Pi's `systemPrompt`; segment 4 is the
 * turn's own input and is never touched here.
 *
 * **Why this freezes bytes instead of re-deriving them.** REQ-GLB-004 AC1 asks for an
 * *empty byte-diff* of segments 1–3 across two calls in a session — a stronger claim than
 * "we read the same files". `event.systemPrompt` is assembled by Pi, not by us, and
 * design §15 is explicit that the extension cannot see the final serialized payload; the
 * notes file can be rewritten mid-session by a backend call. So the first turn captures
 * the three segments and every later turn re-emits **those exact bytes**, which is the
 * only construction that makes the diff empty by property rather than by luck.
 *
 * **Why the freeze is keyed on `systemPromptOptions` rather than held forever.** Freezing
 * segment 1 unconditionally would eventually send a prompt describing a toolset that is no
 * longer registered — trading a correctness hazard for a cost saving, which is the wrong
 * way round. `fingerprintPromptOptions` hashes what Pi says it built the prompt *from*
 * (6.1's "read `event.systemPromptOptions` to respect user config"): same inputs → re-emit
 * the frozen bytes; different inputs → rotate, and say so. Nothing here ever silently
 * serves a stale prompt.
 *
 * **Segment 2's lifetime is one Evaluator pass, not one session.** An Evaluator→Architect
 * re-loop re-emits `design.md`, so REQ-GLB-004 AC2 makes a change there *expected* — and
 * segments 1 and 3 must survive it untouched. `decidePrefix` therefore rebuilds segment 2
 * alone on a pass change, reusing the frozen bytes of the other two. A Planner re-loop is
 * deliberately not a pass change: it does not re-emit design.md.
 *
 * On a fresh project's first sprint the Scout call has no segment 2 at all (AC3). That is
 * an empty string, not a placeholder — a placeholder would be bytes nobody asked for
 * sitting in the cached prefix of every later turn.
 */

import { createHash } from "node:crypto";

/** REQ-MEM-001: the Lightweight Brain is at most five notes. */
export const MAX_NOTES = 5;

/** Where `atomic_notes.py` writes the session's frozen set (Task 5.4). */
export const FROZEN_NOTES_PATH = ["cache", "frozen_notes.json"] as const;

/** `atomic_notes.DEFAULT_SESSION_ID` — a single-session workspace, the interactive case. */
export const DEFAULT_NOTES_SESSION = "default";

/** Headers are fixed strings so segment boundaries cannot drift between turns. */
export const DESIGN_HEADER = "# Sprint design (design.md)";
export const NOTES_HEADER = "# Atomic notes (frozen for this session)";

/** The three cacheable segments, kept apart so a diff can name which one moved. */
export interface PrefixSegments {
  /** Segment 1 — Pi's assembled system prompt for the active agent. */
  system: string;
  /** Segment 2 — `design.md`. Empty on a fresh project's first sprint (AC3). */
  design: string;
  /** Segment 3 — the frozen atomic notes, ≤5. Empty when the brain is cold. */
  notes: string;
}

export interface PrefixSources {
  systemPrompt: string;
  /** Raw `design.md`, or `undefined` when Phase 1 has not written one yet. */
  design?: string | undefined;
  /** The frozen note bodies, in the order the backend froze them. */
  notes?: readonly string[] | undefined;
}

/** What the extension froze, and the two things that can invalidate it. */
export interface FrozenPrefix {
  fingerprint: string;
  /** The Architect re-loop count — an Evaluator pass boundary for segment 2. */
  evaluatorPass: number;
  segments: PrefixSegments;
}

export interface PrefixDecision {
  segments: PrefixSegments;
  /** The bytes to hand back to Pi as `systemPrompt`. */
  prefix: string;
  /** True when every segment came back byte-identical to the frozen copy. */
  reused: boolean;
  /** Why the prefix rotated. Empty exactly when `reused` is true. */
  rotated: string[];
  /** The frozen record to carry into the next turn. */
  frozen: FrozenPrefix;
}

// ------------------------------------------------------------------- rendering

/**
 * Render the notes block. Deterministic by construction: a fixed header, a fixed bullet,
 * and the backend's order preserved.
 *
 * The ≤5 cap is enforced here as well as in the backend. Not redundancy for its own sake —
 * this is the only place that knows what actually reaches the model, and a sixth note that
 * slipped past the backend would otherwise be paid for on every cached turn.
 */
export function renderNotes(notes: readonly string[] | undefined): string {
  const kept = (notes ?? [])
    .map((note) => note.trim())
    .filter((note) => note.length > 0)
    .slice(0, MAX_NOTES);
  if (kept.length === 0) return "";
  return [NOTES_HEADER, ...kept.map((note) => `- ${note}`)].join("\n");
}

/** Render segment 2. An absent or blank `design.md` is an empty segment, not a stub. */
export function renderDesign(design: string | undefined): string {
  const body = (design ?? "").trim();
  if (body === "") return "";
  return `${DESIGN_HEADER}\n\n${body}`;
}

/** Join the non-empty segments in the order REQ-PFX-001 fixes. */
export function joinSegments(segments: PrefixSegments): string {
  return [segments.system, segments.design, segments.notes]
    .map((segment) => segment.trim())
    .filter((segment) => segment !== "")
    .join("\n\n");
}

export function assembleSegments(sources: PrefixSources): PrefixSegments {
  return {
    system: sources.systemPrompt,
    design: renderDesign(sources.design),
    notes: renderNotes(sources.notes),
  };
}

// ---------------------------------------------------------------- fingerprint

/**
 * What Pi says it built the system prompt from, as one stable hash.
 *
 * Structural, not textual: it hashes the *inputs* Pi reports rather than the prompt it
 * produced, so a re-render that happens to reorder something does not read as a config
 * change — while a genuine change (a tool registered, a skill loaded, `--append-system-prompt`
 * edited) does. `contextFiles` and `skills` contribute their identities and content
 * lengths rather than their bodies: a changed body is a changed prompt, and hashing whole
 * files on every turn to learn that would cost more than it saves.
 */
export function fingerprintPromptOptions(options: PromptOptionsLike | undefined): string {
  const material = {
    customPrompt: options?.customPrompt ?? null,
    selectedTools: options?.selectedTools ?? null,
    toolSnippets: options?.toolSnippets ? Object.keys(options.toolSnippets).sort() : null,
    promptGuidelines: options?.promptGuidelines ?? null,
    appendSystemPrompt: options?.appendSystemPrompt ?? null,
    cwd: options?.cwd ?? null,
    contextFiles: (options?.contextFiles ?? []).map((file) => [file.path, file.content.length]),
    skills: (options?.skills ?? []).map((skill) => skill.name ?? ""),
  };
  return createHash("sha256").update(JSON.stringify(material)).digest("hex").slice(0, 16);
}

/** The subset of Pi's `BuildSystemPromptOptions` the fingerprint reads. */
export interface PromptOptionsLike {
  customPrompt?: string | undefined;
  selectedTools?: string[] | undefined;
  toolSnippets?: Record<string, string> | undefined;
  promptGuidelines?: string[] | undefined;
  appendSystemPrompt?: string | undefined;
  cwd?: string | undefined;
  contextFiles?: Array<{ path: string; content: string }> | undefined;
  skills?: Array<{ name?: string | undefined }> | undefined;
}

// -------------------------------------------------------------------- decision

/**
 * Decide this turn's prefix against what was frozen last turn.
 *
 * Pure, and the whole of REQ-GLB-004's guarantee: given the same fingerprint and the same
 * Evaluator pass it returns the *previous object's* segment strings, so the byte-diff is
 * empty because they are the same bytes, not because two reads agreed.
 */
export function decidePrefix(
  previous: FrozenPrefix | null,
  current: { fingerprint: string; evaluatorPass: number; sources: PrefixSources },
): PrefixDecision {
  const { fingerprint, evaluatorPass, sources } = current;

  if (previous === null) {
    return settle(assembleSegments(sources), fingerprint, evaluatorPass, [
      "first turn of the session — the prefix is frozen from here",
    ]);
  }

  if (previous.fingerprint !== fingerprint) {
    // Pi is building the prompt from different inputs, so segment 1 is genuinely a
    // different prompt. Re-freeze everything rather than serve a description of a
    // toolset that is no longer registered.
    return settle(assembleSegments(sources), fingerprint, evaluatorPass, [
      `system prompt options changed (${previous.fingerprint} → ${fingerprint}); the whole prefix re-froze`,
    ]);
  }

  if (previous.evaluatorPass !== evaluatorPass) {
    // AC2. Only segment 2 may move; 1 and 3 keep their frozen bytes so their diff across
    // the whole session stays empty even through a re-loop.
    const segments: PrefixSegments = {
      system: previous.segments.system,
      design: renderDesign(sources.design),
      notes: previous.segments.notes,
    };
    const changed = segments.design !== previous.segments.design;
    return settle(segments, fingerprint, evaluatorPass, [
      `Evaluator pass ${previous.evaluatorPass} → ${evaluatorPass}: design.md re-read and ` +
        (changed ? "it changed (expected on an Architect re-loop)" : "it was unchanged"),
    ]);
  }

  return {
    segments: previous.segments,
    prefix: joinSegments(previous.segments),
    reused: true,
    rotated: [],
    frozen: previous,
  };
}

function settle(
  segments: PrefixSegments,
  fingerprint: string,
  evaluatorPass: number,
  rotated: string[],
): PrefixDecision {
  const frozen: FrozenPrefix = { fingerprint, evaluatorPass, segments };
  return { segments, prefix: joinSegments(segments), reused: false, rotated, frozen };
}

// ------------------------------------------------------------------- notes I/O

/**
 * Read the backend's frozen set from `.saltcode/cache/frozen_notes.json` (Task 5.4).
 *
 * There is no notes entrypoint on the Task 7b roster, and that is not an oversight —
 * the freeze is written to disk precisely so a reader does not have to spawn a process to
 * see it. A record tagged with a different `session_id` is **ignored**, not adapted: notes
 * frozen for another session are somebody else's context, and REQ-GLB-004's guarantee is
 * per session.
 */
export function parseFrozenNotes(raw: string | undefined, sessionId: string): string[] {
  if (raw === undefined || raw.trim() === "") return [];
  let record: unknown;
  try {
    record = JSON.parse(raw);
  } catch {
    // A corrupt freeze is a cold brain, not a crashed turn. The notes are an optimisation;
    // failing the turn over them would trade a whole sprint for a cache segment.
    return [];
  }
  if (record === null || typeof record !== "object") return [];
  const data = record as { session_id?: unknown; notes?: unknown };
  if (typeof data.session_id === "string" && data.session_id !== sessionId) return [];
  if (!Array.isArray(data.notes)) return [];
  return data.notes.filter((note): note is string => typeof note === "string").slice(0, MAX_NOTES);
}

/** A one-line summary for the turn's `message`, so the delta is visible in the TUI. */
export function describePrefix(decision: PrefixDecision): string {
  const sizes =
    `system ${decision.segments.system.length}B · design ${decision.segments.design.length}B · ` +
    `notes ${decision.segments.notes.length}B`;
  if (decision.reused) return `Prefix reused byte-identically (${sizes}).`;
  return `Prefix rotated — ${decision.rotated.join("; ")} (${sizes}).`;
}

// ------------------------------------------------------- 6.3 payload observation

/**
 * What the provider actually received, as one hash (6.3, debug-only).
 *
 * design §15 is blunt that its 74%-discount figure is *modeled, not measured*: prefix
 * caching is provider-side and keyed on the **serialized request prefix**, which Pi
 * assembles — it injects tool schemas around our content, and `getSystemPrompt()` does not
 * reflect the final payload. So a byte-stable `systemPrompt` from this extension is
 * necessary for a cache hit and nowhere near sufficient.
 *
 * This is the only instrument that can settle it. It hashes the cacheable head of the real
 * payload so two consecutive requests can be compared: a `changed: true` between turns that
 * `decidePrefix` reported as `reused` means Pi moved something ahead of our segments, and
 * the cost model is wrong in a way no amount of care on this side would fix.
 *
 * Off by default and behind a flag — it inspects every provider request, and an instrument
 * left running is a cost of its own.
 */
export interface PayloadObservation {
  hash: string;
  /** Which part of the payload was hashed, so the number is interpretable. */
  source: "system" | "messages-head" | "payload-head";
  bytes: number;
  changed: boolean;
}

/** How much of an unrecognised payload counts as "the prefix". */
export const PAYLOAD_HEAD_CHARS = 4096;

export function observePayloadPrefix(
  payload: unknown,
  previousHash: string | null,
): PayloadObservation {
  const { text, source } = extractCacheableHead(payload);
  const hash = createHash("sha256").update(text).digest("hex").slice(0, 16);
  return {
    hash,
    source,
    bytes: text.length,
    changed: previousHash !== null && previousHash !== hash,
  };
}

function extractCacheableHead(payload: unknown): {
  text: string;
  source: PayloadObservation["source"];
} {
  if (payload !== null && typeof payload === "object") {
    const body = payload as { system?: unknown; messages?: unknown };
    // Anthropic-shaped: `system` is the cached prefix and is worth hashing alone.
    if (body.system !== undefined) {
      return { text: JSON.stringify(body.system), source: "system" };
    }
    // OpenAI-shaped: the system turn is the first message.
    if (Array.isArray(body.messages) && body.messages.length > 0) {
      return { text: JSON.stringify(body.messages[0]), source: "messages-head" };
    }
  }
  // Unrecognised: hash a bounded head rather than the whole request, so a growing
  // conversation does not report a moved prefix on every single turn.
  return {
    text: JSON.stringify(payload ?? null).slice(0, PAYLOAD_HEAD_CHARS),
    source: "payload-head",
  };
}
