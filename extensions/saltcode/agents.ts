/**
 * The roster and the design §6 routing table (REQ-EXT-003, REQ-ORC-003, REQ-GATE-002).
 *
 * The table lives here as data rather than as branches inside the handlers, for one
 * reason: REQ-EXT-003 AC1 says the active model and thinking level SHALL *equal* the
 * policy value, which is a claim a test can check only if the policy is a value.
 *
 * The Auditor appears here but is **not** a spawnable agent — design §5.6a puts its N-pass
 * judgment in the backend (`saltcode_stability`). It has a row because the extension still
 * routes the one online call it can make: REQ-AUD-002 AC2's Flash re-judgment.
 */

/** Pi's thinking scale (REQ-EXT-003 AC3). `max` exists in Pi and is unused by policy. */
export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

/** The six agents that run as isolated sub-agents, plus the two that do not. */
export type AgentName =
  | "scout"
  | "architect"
  | "planner"
  | "test-intent"
  | "evaluator"
  | "builder"
  | "auditor"
  | "compactor";

/** The six with a definition in `agents/` (REQ-EXT-012). */
export const SPAWNED_AGENTS = [
  "scout",
  "architect",
  "planner",
  "test-intent",
  "evaluator",
  "builder",
] as const satisfies readonly AgentName[];

/** Phase-1's dependency order. The handler owns this, not the LLM (REQ-EXT-012 AC2). */
export const PHASE1_CHAIN = [
  "scout",
  "architect",
  "planner",
  "test-intent",
  "evaluator",
] as const satisfies readonly AgentName[];

/** Saltnitor router sections (design §6). */
export type LocalProfile = "A_STD" | "A_FOCUS" | "B";

/** What the sub-agent definitions in `agents/` are named. */
export function definitionName(agent: AgentName): string {
  return `saltcode-${agent}`;
}

/** Everything the routing decision depends on. Anything else is not an input. */
export interface RoutingState {
  /** REQ-GATE-001: offline overrides every Phase-1 agent to Tier B. */
  online: boolean;
  /** REQ-GATE-002 AC1: fresh → Architect/Evaluator on Pro; amend/rerun → Flash. */
  project: "fresh" | "amend";
  /** REQ-ORC-003: the Evaluator raises thinking only when re-invoked after a fail. */
  evaluatorReinvoked?: boolean;
  /** REQ-ORC-003: the Auditor raises thinking only on retry / `gaming_suspected`. */
  auditorRetry?: boolean;
  /** The Builder's profile for this task (the VRAM triangle, design §6). */
  builderProfile?: LocalProfile;
}

/** A resolved route: what `pi.setModel` and `pi.setThinkingLevel` are asked for. */
export interface Route {
  provider: string;
  model: string;
  thinking: ThinkingLevel;
  /** Tried in order when `pi.setModel` returns false (REQ-EXT-010 step 1→2). */
  fallbacks: ReadonlyArray<{ provider: string; model: string }>;
}

const DEEPSEEK_FLASH = { provider: "deepseek", model: "v4-flash" } as const;
const DEEPSEEK_PRO = { provider: "deepseek", model: "v4-pro" } as const;

/** Offline Tier B. Also the last cloud fallback before FLAG HUMAN (REQ-EXT-010 step 3). */
export const SALTNITOR_TIER_B = { provider: "saltnitor", model: "B" } as const;

/**
 * The design §6 table, resolved for one agent in one session state.
 *
 * Offline sends every *API-routed* agent to Tier B. The Builder and Auditor are already
 * local, so "offline" changes nothing for them except that the Auditor loses its one
 * escalation (REQ-AUD-002 AC1) — which is not a routing decision, so it is not made here.
 */
export function resolveRoute(agent: AgentName, state: RoutingState): Route {
  const thinking = resolveThinking(agent, state);

  if (agent === "builder") {
    const profile = state.builderProfile ?? "A_STD";
    return {
      provider: "saltnitor",
      model: profile,
      // The Tier-A sub-cap escalates to B (REQ-FAIL-001 AC2); so does an unreachable A.
      thinking: profile === "A_FOCUS" ? "off" : thinking,
      fallbacks: profile === "B" ? [] : [SALTNITOR_TIER_B],
    };
  }

  if (agent === "auditor") {
    // Local judgment. Its one online move is the Flash re-judgment, which the Phase-2
    // loop asks for explicitly rather than inheriting from the routing table.
    return { provider: "saltnitor", model: "A_STD", thinking, fallbacks: [SALTNITOR_TIER_B] };
  }

  if (!state.online) {
    return { ...SALTNITOR_TIER_B, thinking, fallbacks: [] };
  }

  const base = baseCloudModel(agent, state);
  return {
    ...base,
    thinking,
    fallbacks: [base === DEEPSEEK_PRO ? DEEPSEEK_FLASH : DEEPSEEK_PRO, SALTNITOR_TIER_B],
  };
}

function baseCloudModel(
  agent: AgentName,
  state: RoutingState,
): { provider: string; model: string } {
  switch (agent) {
    case "architect":
      // Base Pro; amend/rerun downgrades to Flash (REQ-GATE-002 AC1).
      return state.project === "fresh" ? DEEPSEEK_PRO : DEEPSEEK_FLASH;
    case "evaluator":
      // Base Flash; a fresh project raises it to Pro (design §6 "session modifier").
      return state.project === "fresh" ? DEEPSEEK_PRO : DEEPSEEK_FLASH;
    default:
      return DEEPSEEK_FLASH;
  }
}

/**
 * REQ-ORC-003 in full: Architect `high`; Evaluator raised only when re-invoked after a
 * fail; Auditor raised only on retry/`gaming_suspected`; all others `off`.
 */
function resolveThinking(agent: AgentName, state: RoutingState): ThinkingLevel {
  switch (agent) {
    case "architect":
      return "high";
    case "evaluator":
      return state.evaluatorReinvoked === true ? "medium" : "off";
    case "auditor":
      return state.auditorRetry === true ? "medium" : "off";
    default:
      // Scout, Planner, Test Intent and the Compactor emit structured JSON; the Builder's
      // level is chosen per task by the VRAM triangle and defaults to off.
      return "off";
  }
}

/**
 * The Builder's profile for a task (design §6, "A_FOCUS selection rule").
 *
 * `A_FOCUS` buys 256K of context by giving up thinking, so it is selected on size, not on
 * difficulty. `B` wins over both: it is the escalation, and `complexity == high` starts
 * there (REQ-FAIL-001).
 */
export function selectBuilderProfile(options: {
  complexity: "low" | "medium" | "high";
  escalated: boolean;
  estimatedInputTokens: number;
  aFocusThreshold: number;
}): LocalProfile {
  if (options.complexity === "high" || options.escalated) return "B";
  return options.estimatedInputTokens > options.aFocusThreshold ? "A_FOCUS" : "A_STD";
}
