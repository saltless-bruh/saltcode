/**
 * The cache ladder that runs *before* Phase 1 (Task 8.1 · REQ-CACHE-001, REQ-CACHE-003,
 * design §11.2).
 *
 * ```
 * scope  → --scope, else saltcode_scope_probe, else empty (goal-only)
 * tier 1 → exact spec cache      hit → reuse tasks.json, ZERO API, stop
 * tier 2 → semantic cache        candidate → Architect confirmation (PCD-adaptive)
 * miss   → fire Phase 1
 * ```
 *
 * **The backend decides the bar; this file runs it.** `saltcode_cache_lookup` returns a
 * candidate plus a `confirmation` level — the backend measures PCD but cannot spawn an
 * Architect, because agents belong to the extension. So the split is: density is computed
 * where the vectors are, and the confirmation is *run* where the agents are. Neither half
 * decides the other's business.
 *
 * **A `semantic_candidate` is never authorisation to reuse.** REQ-CACHE-003 AC1 requires
 * the confirmation first, and `decideLadder` has no path from `semantic_candidate` to
 * `reuse` that does not pass through it. The one exception is `confirmation: "skip"`, which
 * REQ-CACHE-003 AC2 makes **opt-in configuration** — the backend only ever returns it when
 * the project asked for it, so honouring it here is following the config, not lowering the
 * bar unasked.
 *
 * **A failed lookup falls through to Phase 1 rather than stopping the sprint.** The cache
 * is an optimisation; a broken LanceDB, a missing embedding endpoint or an unparseable
 * payload should cost the API call the cache would have saved, not the sprint. What it must
 * never do is silently *hit* — every fall-through says why.
 */

import type { BackendResult, BackendRunner } from "./backend.ts";
import { EXIT_POSITIVE } from "./backend.ts";

/** REQ-CACHE-003's four bars, as `semantic_cache.py::Confirmation` defines them. */
export type Confirmation = "skip" | "cheap" | "full" | "fall_through";

/** What `saltcode_cache_lookup` reports (docs/entrypoints.md, task 5.7). */
export interface CacheLookup {
  verdict: "exact_hit" | "semantic_candidate" | "miss" | "unreadable";
  key: string;
  scopeFingerprint: string[];
  scopeSource: string;
  confirmation?: Confirmation | undefined;
  similarity?: number | undefined;
  pcd?: number | undefined;
  /** The cached `tasks.json`, present on a hit or a candidate. */
  tasks?: unknown;
  detail: string;
  /** True when any threshold driving this answer is still provisional (REQ-CAL-001). */
  uncalibrated: boolean;
}

export type LadderAction =
  | { action: "reuse"; lookup: CacheLookup; why: string }
  | { action: "confirm"; lookup: CacheLookup; bar: "cheap" | "full"; why: string }
  | { action: "fire"; lookup: CacheLookup | null; why: string };

// ------------------------------------------------------------------- parsing

/** Read a `cache_lookup` result without trusting any field's type. */
export function parseCacheLookup(result: BackendResult): CacheLookup {
  const payload = result.payload as Record<string, unknown>;
  const verdict = payload.verdict;
  const known = verdict === "exact_hit" || verdict === "semantic_candidate" || verdict === "miss";

  return {
    verdict: known ? verdict : "unreadable",
    key: typeof payload.key === "string" ? payload.key : "",
    scopeFingerprint: Array.isArray(payload.scope_fingerprint)
      ? payload.scope_fingerprint.filter((entry): entry is string => typeof entry === "string")
      : [],
    scopeSource: typeof payload.scope_source === "string" ? payload.scope_source : "unknown",
    confirmation: isConfirmation(payload.confirmation) ? payload.confirmation : undefined,
    similarity: typeof payload.similarity === "number" ? payload.similarity : undefined,
    pcd: typeof payload.pcd === "number" ? payload.pcd : undefined,
    tasks: payload.tasks,
    detail:
      typeof payload.detail === "string"
        ? payload.detail
        : known
          ? ""
          : `cache_lookup returned an unrecognised verdict ${JSON.stringify(verdict)}`,
    uncalibrated: readUncalibrated(payload.thresholds),
  };
}

function isConfirmation(value: unknown): value is Confirmation {
  return value === "skip" || value === "cheap" || value === "full" || value === "fall_through";
}

/**
 * Whether any threshold behind this answer is still provisional.
 *
 * Surfaced rather than swallowed: REQ-CAL-001 AC2 wants the human told, and a semantic hit
 * decided by a guessed cosine threshold is precisely the answer somebody should be able to
 * distrust on sight.
 */
function readUncalibrated(thresholds: unknown): boolean {
  if (thresholds === null || typeof thresholds !== "object") return false;
  return Object.values(thresholds as Record<string, unknown>).some(
    (entry) =>
      entry !== null &&
      typeof entry === "object" &&
      (entry as { calibrated?: unknown }).calibrated === false,
  );
}

// ------------------------------------------------------------------ the ladder

/**
 * Map a lookup to the next move, strictly top-down and stopping at the first hit.
 *
 * Pure, so REQ-CACHE-001's ordering is provable without a backend, a cache, or a model.
 */
export function decideLadder(lookup: CacheLookup): LadderAction {
  switch (lookup.verdict) {
    case "exact_hit":
      // AC1: zero API. The ladder stops here and no agent is consulted.
      return {
        action: "reuse",
        lookup,
        why: `exact spec-cache hit on ${lookup.key.slice(0, 12)} (scope: ${lookup.scopeSource}) — reused with zero API calls`,
      };

    case "semantic_candidate": {
      const bar = lookup.confirmation ?? "full";
      if (bar === "fall_through") {
        // AC3's configured reading: too sparse a neighbourhood to trust, so do not spend
        // an Architect turn on a confirmation that would probably say no anyway.
        return {
          action: "fire",
          lookup,
          why: `semantic candidate at similarity ${fmt(lookup.similarity)}, but PCD ${fmt(lookup.pcd)} is below the low bar and the project configured fall_through`,
        };
      }
      if (bar === "skip") {
        return {
          action: "reuse",
          lookup,
          why: `semantic hit at similarity ${fmt(lookup.similarity)} in a dense neighbourhood (PCD ${fmt(lookup.pcd)}); the project opted in to skipping confirmation (REQ-CACHE-003 AC2)`,
        };
      }
      return {
        action: "confirm",
        lookup,
        bar,
        why: `semantic candidate at similarity ${fmt(lookup.similarity)}, PCD ${fmt(lookup.pcd)} → ${bar} Architect confirmation`,
      };
    }

    case "miss":
      return { action: "fire", lookup, why: "both cache tiers missed" };

    default:
      // Not a hit, so the only safe reading is a miss — but named as a fault, because a
      // ladder that silently degrades to "always fire" is a cache nobody notices is dead.
      return { action: "fire", lookup, why: `the cache could not be read (${lookup.detail})` };
  }
}

function fmt(value: number | undefined): string {
  return value === undefined ? "n/a" : value.toFixed(3);
}

// ------------------------------------------------------- running the lookup

export interface LadderDeps {
  runner: BackendRunner;
  goal: string;
  /** `--scope` from the command line. Empty means "probe" (REQ-CACHE-002 AC2). */
  scope?: readonly string[] | undefined;
  online: boolean;
  signal?: AbortSignal | undefined;
}

/**
 * Run the ladder and return the next move.
 *
 * `--scope` is passed straight through when given, which is what stops the probe from
 * running (AC2). Offline adds `--offline`, so the exact tier still answers and the semantic
 * tier reports `unavailable` instead of erroring the whole ladder — tier 1 is "zero API"
 * precisely so it keeps working when the embedding side is down.
 */
export async function runLadder(deps: LadderDeps): Promise<LadderAction> {
  const args = ["--goal", deps.goal];
  for (const path of deps.scope ?? []) args.push("--scope", path);
  if (!deps.online) args.push("--offline");

  let result: BackendResult;
  try {
    result = await deps.runner.call("cache_lookup", args, { signal: deps.signal });
  } catch (error) {
    return {
      action: "fire",
      lookup: null,
      why: `the cache lookup could not run (${(error as Error).message}); firing Phase 1 rather than stopping the sprint`,
    };
  }

  const lookup = parseCacheLookup(result);
  // A `miss` is exit 1 by contract, so a non-positive code is not itself an error — the
  // verdict is what routes. `decideLadder` handles the unreadable case.
  if (result.code !== EXIT_POSITIVE && lookup.verdict === "unreadable") {
    return { action: "fire", lookup, why: `the cache lookup failed (${lookup.detail})` };
  }
  return decideLadder(lookup);
}

// ------------------------------------------------- the Architect confirmation

/**
 * The prompt for the confirmation turn (REQ-CACHE-003 AC1).
 *
 * The Architect is asked one closed question about a plan it can see, and told to answer
 * `NO` when unsure. That default is the whole point: a wrong `yes` reuses a plan built for
 * a different repository and every later gate validates it faithfully, while a wrong `no`
 * costs one Phase-1 fire. The two errors are not close in price.
 *
 * `cheap` and `full` differ in how much the Architect is asked to check, not in whether the
 * question is asked — REQ-CACHE-003 lowers the *cost* of confirmation in a dense
 * neighbourhood, never the requirement for one.
 */
export function confirmationPrompt(
  goal: string,
  bar: "cheap" | "full",
  cachedTasks: unknown,
): string {
  const plan = JSON.stringify(cachedTasks ?? {}, null, 2);
  const depth =
    bar === "cheap"
      ? [
          "This is a CHEAP confirmation: the goal is a close match to one already planned in a",
          "well-populated area of the cache. Check that the cached plan is about the same work,",
          "not that every task is optimal.",
        ]
      : [
          "This is a FULL confirmation. Check each task against the goal: that the plan covers",
          "the goal, that no task belongs to a different problem, and that the files it touches",
          "make sense for this repository.",
        ];

  return [
    "# Reuse a cached plan?",
    "",
    "## Goal",
    goal,
    "",
    "## Cached plan",
    "```json",
    plan,
    "```",
    "",
    ...depth,
    "",
    "Answer on the FIRST line with exactly `YES` or `NO`, then one sentence of reasoning.",
    "Answer `NO` if you are unsure: a wrong yes builds against a plan for different work and",
    "every later gate will validate it faithfully; a wrong no costs one planning pass.",
  ].join("\n");
}

/**
 * Read the Architect's answer.
 *
 * Anything that is not an unambiguous yes is a no. Not strictness for its own sake — an
 * unparseable answer and a refusal have the same correct handling (fall through and plan
 * properly), and treating "probably yes" as yes is how a cache starts returning plans for
 * the wrong repository.
 */
export function readConfirmation(reply: string): { confirmed: boolean; why: string } {
  const first = reply.trim().split("\n", 1)[0]?.trim().toUpperCase() ?? "";
  if (/^YES\b/.test(first)) return { confirmed: true, why: reply.trim() };
  if (/^NO\b/.test(first)) return { confirmed: false, why: reply.trim() };
  return {
    confirmed: false,
    why:
      "the Architect's answer did not start with YES or NO, so it is treated as a no and " +
      `Phase 1 fires: ${reply.trim().slice(0, 200)}`,
  };
}
