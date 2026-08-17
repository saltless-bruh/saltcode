/**
 * Registered providers and local-tier residency (Task 11 / REQ-EXT-002, REQ-EXT-010,
 * REQ-MOD-001..006, REQ-GATE-001).
 *
 * Two halves that are easy to conflate and must not be:
 *
 * * **Registration** tells Pi which models exist and how to reach them. It happens once,
 *   at session open, and it is the thing REQ-GLB-001 AC2 protects — swapping the Phase-1
 *   provider must be a config edit here and nothing else, no backend or gate change.
 * * **Residency** is a Saltnitor concept with no Pi equivalent: one local model fits in
 *   12GB, so a Tier-B turn has to *evict* Tier A first (`POST /v1/ensure`). Pi's
 *   `setModel` does not know that, which is why REQ-MOD-005 exists and why an OOM refusal
 *   from the oracle is a FLAG HUMAN rather than an exception — the box is fine, the plan
 *   is what has to change.
 *
 * The VRAM triangle is a pure function at the bottom of the file. Thinking, 256K context
 * and MTP all draw on the same 12GB: hold any two. Getting that wrong does not produce a
 * wrong answer, it produces an OOM three minutes into a Builder turn, which is worse.
 */

import type { ExtensionAPI, ProviderModelConfig } from "@earendil-works/pi-coding-agent";
import type { LocalProfile, ThinkingLevel } from "./agents.ts";

/** Saltnitor's router, on loopback. Only loopback counts as local (`privacy-boundary.md`). */
export const SALTNITOR_BASE_URL = "http://127.0.0.1:8765/v1";

/** Cost is zero on purpose — Phase 2 runs at $0 API, and the widget shows that. */
const FREE = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } as const;

/** design §6's three router sections. Context windows follow the run flags there. */
export const SALTNITOR_MODELS: ProviderModelConfig[] = [
  {
    id: "A_STD",
    name: "Saltnitor Tier A (Qwen3.5-9B, 64K)",
    reasoning: true,
    input: ["text"],
    cost: FREE,
    contextWindow: 65_536,
    maxTokens: 8_192,
  },
  {
    id: "A_FOCUS",
    name: "Saltnitor Tier A focus (Qwen3.5-9B, 256K, thinking off)",
    // REQ-MOD-006 AC1: the 256K window is bought by giving up thinking, so the model is
    // declared non-reasoning and Pi clamps any level to `off` for us.
    reasoning: false,
    input: ["text"],
    cost: FREE,
    contextWindow: 262_144,
    maxTokens: 8_192,
  },
  {
    id: "B",
    name: "Saltnitor Tier B (Qwen3.6-35B-A3B, hybrid offload)",
    reasoning: true,
    input: ["text"],
    cost: FREE,
    contextWindow: 131_072,
    maxTokens: 16_384,
  },
];

/** DeepSeek V4, the online Phase-1 provider (design §5.4). */
export const DEEPSEEK_MODELS: ProviderModelConfig[] = [
  {
    id: "v4-flash",
    name: "DeepSeek V4 Flash",
    reasoning: true,
    input: ["text"],
    cost: { input: 0.27, output: 1.1, cacheRead: 0.027, cacheWrite: 0.27 },
    contextWindow: 128_000,
    maxTokens: 8_192,
  },
  {
    id: "v4-pro",
    name: "DeepSeek V4 Pro",
    reasoning: true,
    input: ["text"],
    cost: { input: 1.1, output: 4.4, cacheRead: 0.11, cacheWrite: 1.1 },
    contextWindow: 128_000,
    maxTokens: 16_384,
  },
];

export interface ProviderRegistration {
  provider: string;
  models: number;
  /** How the model list was obtained — `discovered` means Saltnitor answered. */
  source: "static" | "discovered" | "degraded";
  detail: string;
}

export interface RegisterProvidersOptions {
  /** Injected so a test can drive discovery without a Saltnitor. */
  fetchImpl?: typeof fetch;
  saltnitorBaseUrl?: string;
  /** Optional Qwen provider (design §5.4, "mixed fleet"). */
  qwen?: { baseUrl: string; models: ProviderModelConfig[] } | undefined;
}

/**
 * Register DeepSeek, optionally Qwen, and Saltnitor (REQ-EXT-002).
 *
 * Saltnitor's list is fetched from `/v1/models` when it answers and falls back to the
 * three static sections when it does not (AC2's "degrade gracefully"). Degrading rather
 * than failing matters because the offline path *depends* on Saltnitor models being
 * registered: if a Saltnitor that is merely slow to start took the whole registration down
 * with it, the offline route would have nowhere to go.
 */
export async function registerProviders(
  pi: Pick<ExtensionAPI, "registerProvider">,
  options: RegisterProvidersOptions = {},
): Promise<ProviderRegistration[]> {
  const registrations: ProviderRegistration[] = [];

  pi.registerProvider("deepseek", {
    name: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    apiKey: "$DEEPSEEK_API_KEY",
    api: "openai-completions",
    models: DEEPSEEK_MODELS,
  });
  registrations.push({
    provider: "deepseek",
    models: DEEPSEEK_MODELS.length,
    source: "static",
    detail: "Phase-1 online provider",
  });

  if (options.qwen !== undefined) {
    pi.registerProvider("qwen", {
      name: "Qwen",
      baseUrl: options.qwen.baseUrl,
      apiKey: "$QWEN_API_KEY",
      api: "openai-completions",
      models: options.qwen.models,
    });
    registrations.push({
      provider: "qwen",
      models: options.qwen.models.length,
      source: "static",
      detail: "optional Phase-1 reasoning provider",
    });
  }

  const baseUrl = options.saltnitorBaseUrl ?? SALTNITOR_BASE_URL;
  const discovered = await discoverSaltnitorModels(baseUrl, options.fetchImpl);

  pi.registerProvider("saltnitor", {
    name: "Saltnitor",
    baseUrl,
    // Saltnitor is on loopback and unauthenticated; the literal keeps Pi's auth path
    // satisfied without inventing an environment variable nobody sets.
    apiKey: "local",
    api: "openai-completions",
    models: discovered.models,
  });
  registrations.push({
    provider: "saltnitor",
    models: discovered.models.length,
    source: discovered.source,
    detail: discovered.detail,
  });

  return registrations;
}

async function discoverSaltnitorModels(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<{ models: ProviderModelConfig[]; source: "discovered" | "degraded"; detail: string }> {
  try {
    const response = await fetchImpl(`${baseUrl}/models`, {
      signal: AbortSignal.timeout(3_000),
    });
    if (!response.ok) {
      return {
        models: SALTNITOR_MODELS,
        source: "degraded",
        detail: `Saltnitor answered ${response.status}; using the three configured sections`,
      };
    }
    const body = (await response.json()) as { data?: Array<{ id?: unknown }> };
    const ids = (body.data ?? [])
      .map((entry) => entry.id)
      .filter((id): id is string => typeof id === "string");

    // Keep the *configured* entry for any section Saltnitor reports, because the context
    // window and cost are ours to state, not the router's. Discovery answers "which
    // sections are live", not "what are their properties".
    const live = SALTNITOR_MODELS.filter((model) => ids.includes(model.id));
    if (live.length === 0) {
      return {
        models: SALTNITOR_MODELS,
        source: "degraded",
        detail: `Saltnitor reported ${ids.length} model(s), none matching A_STD/A_FOCUS/B`,
      };
    }
    return {
      models: live,
      source: "discovered",
      detail: `Saltnitor reports ${live.map((m) => m.id).join(", ")}`,
    };
  } catch (error) {
    return {
      models: SALTNITOR_MODELS,
      source: "degraded",
      detail:
        `Saltnitor is unreachable (${(error as Error).message}); registering the three ` +
        "configured sections anyway so the offline route stays available",
    };
  }
}

// ------------------------------------------------------------------ residency

export type EnsureOutcome =
  | { ok: true; profile: LocalProfile; alreadyResident: boolean }
  | { ok: false; profile: LocalProfile; reason: string; flagHuman: boolean };

/**
 * Ask Saltnitor to make `profile` resident before a local turn (REQ-MOD-005 AC1).
 *
 * An OOM refusal is `flagHuman: true` and not an exception. The oracle declining to load
 * Tier B is a *correct* answer from a healthy box — the machine is fine and the plan is
 * what has to change — and crashing on it would take down a sprint that could have paused.
 */
export async function ensureProfile(
  profile: LocalProfile,
  options: { baseUrl?: string; fetchImpl?: typeof fetch; signal?: AbortSignal | undefined } = {},
): Promise<EnsureOutcome> {
  const baseUrl = options.baseUrl ?? SALTNITOR_BASE_URL;
  const fetchImpl = options.fetchImpl ?? fetch;

  try {
    const response = await fetchImpl(`${baseUrl}/ensure`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile }),
      ...(options.signal !== undefined ? { signal: options.signal } : {}),
    });

    if (response.ok) {
      const body = (await response.json().catch(() => ({}))) as { already_resident?: unknown };
      return { ok: true, profile, alreadyResident: body.already_resident === true };
    }

    const detail = await response.text().catch(() => "");
    const oom = response.status === 507 || /oom|out of memory|insufficient/i.test(detail);
    return {
      ok: false,
      profile,
      flagHuman: oom,
      reason: oom
        ? `Saltnitor refused to make ${profile} resident: ${detail.trim() || "out of memory"}. ` +
          "Tier B needs roughly 21GB across VRAM and RAM; nothing here can free that, so the " +
          "task stops for you rather than thrashing the box (REQ-MOD-005 AC1)."
        : `Saltnitor returned ${response.status} for ensure(${profile}): ${detail.trim()}`,
    };
  } catch (error) {
    return {
      ok: false,
      profile,
      flagHuman: false,
      reason:
        `Saltnitor is unreachable for ensure(${profile}): ${(error as Error).message}. ` +
        "Direct llama.cpp at :8080 is the documented fallback (design §6).",
    };
  }
}

// -------------------------------------------------------------- VRAM triangle

export interface LocalTurn {
  profile: LocalProfile;
  thinking: ThinkingLevel;
  mtp: boolean;
}

/**
 * Reconcile a requested local turn with the VRAM triangle (REQ-MOD-006).
 *
 * Thinking, the 256K window and MTP all draw on the same 12GB — hold any two. This
 * *always* returns a runnable combination rather than refusing, because the caller's
 * request is a preference and the ceiling is physics; what it never does is silently drop
 * the one the caller cared about without saying which (`adjustments` names each).
 */
export function reconcileLocalTurn(requested: {
  profile: LocalProfile;
  thinking: ThinkingLevel;
  mtpEnabled: boolean;
}): { turn: LocalTurn; adjustments: string[] } {
  const adjustments: string[] = [];
  const { profile } = requested;
  let thinking = requested.thinking;
  let mtp = requested.mtpEnabled;

  if (profile === "A_FOCUS") {
    if (thinking !== "off") {
      // AC1. The window is the reason A_FOCUS was chosen, so thinking is what gives way.
      adjustments.push(`thinking ${thinking} → off (A_FOCUS holds 256K context instead)`);
      thinking = "off";
    }
    if (mtp) {
      // AC3. MTP on top of 256K is the third leg of the triangle.
      adjustments.push("MTP disabled (not available on A_FOCUS)");
      mtp = false;
    }
  }

  if (profile === "A_STD" && mtp && thinking !== "off") {
    // Tier A's MTP is finicky even alone (design §6, "MTP regimes"); with thinking on it
    // is the combination most likely to segfault mid-turn.
    adjustments.push(`MTP disabled (thinking ${thinking} takes the second slot on A_STD)`);
    mtp = false;
  }

  if (profile === "B") {
    // Tier B's MTP is stable (~1.4–2.2×) and the hybrid offload does not compete for the
    // same 12GB, so the triangle does not bind here.
    return { turn: { profile, thinking, mtp }, adjustments };
  }

  return { turn: { profile, thinking, mtp }, adjustments };
}
