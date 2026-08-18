/**
 * Spawning one agent in an isolated context (13.8 / REQ-EXT-012, REQ-ORC-006, DD-11).
 *
 * Pi has no built-in sub-agents, so this talks to the adopted sub-agent extension over
 * `pi.events` using its published delegation contract — the surface
 * `docs/subagent_contract.md` §5 pins, and the one that keeps REQ-EXT-015 AC1's
 * substitutability real: any extension answering these events can replace `pi-subagents`.
 *
 * **Why not the `subagent` tool.** That surface is the one an LLM calls, and design §5.6a
 * requires the *command handler* to own ordering and the one-fire-per-sprint rule. Letting
 * the model choose which agent runs next would put the DAG inside the thing the DAG exists
 * to constrain.
 *
 * Isolation is what makes REQ-ORC-006 true by construction rather than by pruning a shared
 * session: `context: "fresh"` means the child sees its task prompt and its definition, and
 * nothing of its siblings. Which is also why every prompt built here must be complete on
 * its own — there is no history to fall back on (REQ-EXT-012 AC1).
 */

import type { EventBus } from "@earendil-works/pi-coding-agent";
import { type AgentName, definitionName } from "./agents.ts";

/**
 * The v1 delegation channel. v2 rides the same two event names, distinguished by
 * `version`, and adds `thinking` plus structured output at the cost of run-tree identity
 * fields (`ownerRunId`, `nodeId`). Saltcode uses v1: model and thinking already come from
 * the definition's frontmatter (REQ-EXT-012 AC3), so v2's extra fields would duplicate the
 * routing table in a second place — which is the failure mode DD-16 already cost us once.
 */
const REQUEST_EVENT = "prompt-template:subagent:request";
const RESPONSE_EVENT = "prompt-template:subagent:response";
const CANCEL_EVENT = "prompt-template:subagent:cancel";
const PROTOCOL_VERSION = 1;

/** Terminal statuses the contract can report. Only `completed` is a success. */
export type SpawnStatus =
  | "completed"
  | "failed"
  | "timed_out"
  | "cancelled"
  | "interrupted"
  | "turn_budget_exhausted"
  | "tool_budget_exhausted"
  | "structured_output_failed"
  | "acceptance_failed"
  | "invalid_request"
  | "unavailable_context";

export interface SpawnResult {
  status: SpawnStatus;
  ok: boolean;
  output?: string;
  outputPath?: string;
  error?: string;
  agent?: string;
  model?: string;
  durationMs?: number;
  warnings?: string[];
}

export interface SpawnOptions {
  /** The complete, zero-context task prompt. */
  task: string;
  cwd: string;
  /** Where the agent's artifact should land, relative to cwd. */
  output?: string | undefined;
  timeoutMs?: number | undefined;
  signal?: AbortSignal | undefined;
}

/** Raised when no sub-agent extension answers — a missing dependency, not a failed agent. */
export class NoSubagentExtensionError extends Error {
  constructor(agent: string, waitedMs: number) {
    super(
      `no sub-agent extension answered the delegation request for ${agent} within ${waitedMs}ms. ` +
        "Saltcode depends on one (REQ-EXT-012, REQ-EXT-015): install pi-subagents, or any " +
        "extension implementing the same isolated-context spawn contract. Phase 1 cannot run " +
        "in one shared session — that would defeat the isolation the contracts are built on.",
    );
    this.name = "NoSubagentExtensionError";
  }
}

/** How long to wait for *any* answer before concluding nothing is listening. */
const HANDSHAKE_TIMEOUT_MS = 30_000;

let counter = 0;

function nextRequestId(agent: string): string {
  counter += 1;
  return `saltcode-${agent}-${Date.now().toString(36)}-${counter}`;
}

/**
 * Spawn one agent and wait for its terminal response.
 *
 * Serial by construction: this resolves before the caller starts the next agent, which is
 * how `/sprint` enforces the Scout → Architect → Planner → Test Intent → Evaluator order
 * (REQ-EXT-012 AC2) without asking anything to cooperate.
 */
export function spawnAgent(
  events: EventBus,
  agent: AgentName,
  options: SpawnOptions,
): Promise<SpawnResult> {
  const name = definitionName(agent);
  const requestId = nextRequestId(agent);

  return new Promise<SpawnResult>((resolve, reject) => {
    let settled = false;
    const finish = (fn: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(handshakeTimer);
      unsubscribe();
      options.signal?.removeEventListener("abort", onAbort);
      fn();
    };

    const unsubscribe = events.on(RESPONSE_EVENT, (data) => {
      const response = asResponse(data);
      if (response === undefined || response.requestId !== requestId) return;
      finish(() =>
        resolve({
          status: response.status,
          ok: response.status === "completed",
          ...(response.output !== undefined ? { output: response.output } : {}),
          ...(response.outputPath !== undefined ? { outputPath: response.outputPath } : {}),
          ...(response.error !== undefined ? { error: response.error } : {}),
          ...(response.agent !== undefined ? { agent: response.agent } : {}),
          ...(response.model !== undefined ? { model: response.model } : {}),
          ...(response.durationMs !== undefined ? { durationMs: response.durationMs } : {}),
          ...(response.warnings !== undefined ? { warnings: response.warnings } : {}),
        }),
      );
    });

    const onAbort = (): void => {
      events.emit(CANCEL_EVENT, { version: PROTOCOL_VERSION, requestId });
      finish(() => resolve({ status: "cancelled", ok: false, error: "aborted by the caller" }));
    };
    options.signal?.addEventListener("abort", onAbort, { once: true });

    // A missing dependency and a slow agent look identical for the first instant, so the
    // handshake timer is separate from (and much shorter than) the agent's own timeout.
    const handshakeTimer = setTimeout(
      () => {
        finish(() => reject(new NoSubagentExtensionError(name, HANDSHAKE_TIMEOUT_MS)));
      },
      Math.min(HANDSHAKE_TIMEOUT_MS, options.timeoutMs ?? HANDSHAKE_TIMEOUT_MS),
    );

    events.emit(REQUEST_EVENT, {
      version: PROTOCOL_VERSION,
      requestId,
      agent: name,
      task: options.task,
      // Fresh, always. A forked context would carry the parent's history into an agent
      // whose entire contract is that it has none (REQ-EXT-012 AC1).
      context: "fresh",
      cwd: options.cwd,
      ...(options.output !== undefined ? { output: options.output } : {}),
      ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
      artifacts: true,
    });
  });
}

interface DelegationResponse {
  requestId: string;
  status: SpawnStatus;
  output?: string;
  outputPath?: string;
  error?: string;
  agent?: string;
  model?: string;
  durationMs?: number;
  warnings?: string[];
}

function asResponse(data: unknown): DelegationResponse | undefined {
  if (data === null || typeof data !== "object" || Array.isArray(data)) return undefined;
  const value = data as Record<string, unknown>;
  if (typeof value.requestId !== "string" || typeof value.status !== "string") return undefined;
  return value as unknown as DelegationResponse;
}

/**
 * Turn a terminal status into the sentence a human should read.
 *
 * `structured_output_failed` gets its own line because it is the one that looks like an
 * agent failure and is not: the agent ran and produced something the contract rejected,
 * which routes to validation, not to a retry of the agent.
 */
export function describeSpawn(agent: AgentName, result: SpawnResult): string {
  if (result.ok) return `${agent}: completed`;
  if (result.status === "structured_output_failed") {
    return `${agent}: produced output that failed its typed contract — validate and re-spec, not re-run`;
  }
  return `${agent}: ${result.status}${result.error !== undefined ? ` — ${result.error}` : ""}`;
}
