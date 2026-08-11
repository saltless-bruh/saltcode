/**
 * Applying the design §6 route to Pi, with the failover chain
 * (13.4 / REQ-EXT-003, REQ-EXT-010, REQ-ORC-003).
 *
 * `agents.ts` decides *what* the route is; this decides *whether it took*. The split
 * matters because REQ-EXT-003 AC2 is about the failure case: `pi.setModel` returning
 * `false` means no API key or an unreachable provider, and the requirement is that the
 * extension surfaces it and falls back per policy — never that it proceeds silently on
 * whatever model happened to be active.
 *
 * The chain is evaluated **per turn** (REQ-EXT-010 AC4). A provider that was down for the
 * Scout may answer for the Architect ninety seconds later, and pinning a session to the
 * first fallback would quietly downgrade every remaining agent in the sprint.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  type AgentName,
  type Route,
  type RoutingState,
  resolveRoute,
  SALTNITOR_TIER_B,
} from "./agents.ts";

export interface RouteAttempt {
  provider: string;
  model: string;
  outcome: "selected" | "unknown_model" | "no_api_key";
}

export type RouteResolution =
  | {
      ok: true;
      agent: AgentName;
      provider: string;
      model: string;
      thinking: string;
      attempts: RouteAttempt[];
    }
  | { ok: false; agent: AgentName; reason: string; attempts: RouteAttempt[] };

export interface RoutingDeps {
  pi: Pick<ExtensionAPI, "setModel" | "setThinkingLevel">;
  ctx: Pick<ExtensionContext, "modelRegistry">;
  /** Extra providers from `saltcode.toml [providers.fallback]`, tried after the built-ins. */
  configuredFallbacks?: ReadonlyArray<{ provider: string; model: string }>;
  /** REQ-EXT-010 AC2: every fallback event is logged. */
  onFallback: (event: {
    agent: AgentName;
    from: RouteAttempt;
    to: { provider: string; model: string };
  }) => void;
}

/**
 * Set the model and thinking level for one agent's turn.
 *
 * Returns a resolution rather than throwing, because "no provider is reachable" is a
 * FLAG HUMAN (REQ-EXT-010 step 4) that the caller has to surface with the sprint's state
 * attached — an exception here would lose that context on the way up.
 */
export async function applyRoute(
  agent: AgentName,
  state: RoutingState,
  deps: RoutingDeps,
): Promise<RouteResolution> {
  const route: Route = resolveRoute(agent, state);
  const candidates = [
    { provider: route.provider, model: route.model },
    ...route.fallbacks,
    ...(deps.configuredFallbacks ?? []),
  ];

  const attempts: RouteAttempt[] = [];
  for (const candidate of candidates) {
    const model = deps.ctx.modelRegistry.find(candidate.provider, candidate.model);
    if (model === undefined) {
      attempts.push({ ...candidate, outcome: "unknown_model" });
      continue;
    }

    const selected = await deps.pi.setModel(model);
    if (!selected) {
      const attempt: RouteAttempt = { ...candidate, outcome: "no_api_key" };
      attempts.push(attempt);
      const next = candidates[candidates.indexOf(candidate) + 1];
      if (next !== undefined) deps.onFallback({ agent, from: attempt, to: next });
      continue;
    }

    attempts.push({ ...candidate, outcome: "selected" });
    // Pi clamps to model capability, so asking a non-reasoning model for `high` is safe
    // rather than an error — but the policy value is still what we ask for, which is what
    // REQ-EXT-003 AC1 is about.
    deps.pi.setThinkingLevel(route.thinking);
    return {
      ok: true,
      agent,
      provider: candidate.provider,
      model: candidate.model,
      thinking: route.thinking,
      attempts,
    };
  }

  return {
    ok: false,
    agent,
    attempts,
    reason:
      `no provider is available for the ${agent}. Tried ` +
      `${attempts.map((a) => `${a.provider}/${a.model} (${a.outcome})`).join(", ")}. ` +
      `The chain ends at Saltnitor ${SALTNITOR_TIER_B.model}; with that unreachable too there is ` +
      "nothing left to fall back to, so the sprint pauses rather than guessing (REQ-EXT-010).",
  };
}

/** One line for the status footer — what is actually running, not what was requested. */
export function describeRoute(resolution: RouteResolution): string {
  if (!resolution.ok) return `${resolution.agent}: no provider`;
  return `${resolution.agent}: ${resolution.provider}/${resolution.model} · thinking ${resolution.thinking}`;
}
