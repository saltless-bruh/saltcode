/**
 * The `/sprint` orchestrator (13.8 / REQ-EXT-009, REQ-EXT-012, REQ-ORC-001, design §5.9).
 *
 * Phase 1 is driven by **spawning each agent as an isolated sub-agent in dependency
 * order**, awaiting and validating each result before the next. That is a deliberate
 * replacement for the older "`sendUserMessage` into one accumulating session", which
 * violated one-task-per-context: in a shared session the Planner can see the Scout's
 * report, and REQ-PLN-001 says it must not.
 *
 * Ordering is owned by this code, not by a model. A chain the LLM could reorder is not a
 * DAG, and the one-fire-per-sprint rule (REQ-ORC-001 AC2) would be a suggestion.
 *
 * **What this file deliberately does not do.** The cache ladder that runs *before* Phase 1
 * (REQ-CACHE-001), the automatic Phase-Gate persistence, and the Evaluator's re-loop
 * routing are Task 8; the regression gate, the checkpoint and the auto-advance run modes
 * are Tasks 17 and 18. The seams are named where they attach. A non-`pass` Evaluator
 * therefore stops and reports here rather than looping — stopping is honest, and inventing
 * Task 8's routing would bury it.
 */

import type { EventBus } from "@earendil-works/pi-coding-agent";
import { type AgentName, PHASE1_CHAIN } from "./agents.ts";
import type { BackendRunner } from "./backend.ts";
import { EXIT_POSITIVE } from "./backend.ts";
import { canFirePhase1, newSprint, type SprintState } from "./state.ts";
import { describeSpawn, type SpawnResult, spawnAgent } from "./subagents.ts";
import type { SaltcodeUI } from "./ui.ts";
import type { Workspace } from "./workspace.ts";

/** The artifact each Phase-1 agent must leave behind, and the contract it validates as. */
const PHASE1_ARTIFACTS: Record<
  (typeof PHASE1_CHAIN)[number],
  { path: string; kind: string } | undefined
> = {
  scout: { path: ".saltcode/context_report.json", kind: "context_report" },
  architect: { path: ".saltcode/design.md", kind: "design" },
  planner: { path: ".saltcode/tasks.json", kind: "tasks" },
  // Test Intent writes one spec per task under `tests/`, not a single typed contract.
  "test-intent": undefined,
  evaluator: { path: ".saltcode/evaluator_report.json", kind: "evaluator_report" },
};

export interface Phase1Deps {
  events: EventBus;
  runner: BackendRunner;
  workspace: Workspace;
  ui: SaltcodeUI;
  /** Applies the design §6 route for this agent before its turn (REQ-EXT-003). */
  route: (agent: AgentName) => Promise<{ ok: boolean; reason?: string }>;
  onProgress: (agent: AgentName, phase: "start" | "done", detail?: string) => void;
  signal?: AbortSignal | undefined;
}

export type Phase1Outcome =
  | { outcome: "planned"; evaluator: "pass" }
  | { outcome: "stopped"; agent: AgentName; reason: string };

/**
 * Run Scout → Architect → Planner → Test Intent → Evaluator, serially.
 *
 * Each step is: route the model, spawn, then **validate the artifact through the backend**
 * before moving on. Validating between steps rather than at the end is what keeps a
 * malformed `design.md` from being handed to the Planner, which would produce a plausible
 * `tasks.json` built on nothing.
 */
export async function runPhase1(goal: string, deps: Phase1Deps): Promise<Phase1Outcome> {
  for (const agent of PHASE1_CHAIN) {
    deps.onProgress(agent, "start");

    const routed = await deps.route(agent);
    if (!routed.ok) {
      return { outcome: "stopped", agent, reason: routed.reason ?? `no model for the ${agent}` };
    }

    const spawn: SpawnResult = await spawnAgent(deps.events, agent, {
      task: phase1Prompt(agent, goal),
      cwd: deps.workspace.root,
      ...(deps.signal !== undefined ? { signal: deps.signal } : {}),
    });

    if (!spawn.ok) {
      return { outcome: "stopped", agent, reason: describeSpawn(agent, spawn) };
    }

    const artifact = PHASE1_ARTIFACTS[agent];
    if (artifact !== undefined) {
      const validation = await deps.runner.call(
        "validate_contract",
        ["--in", `${deps.workspace.root}/${artifact.path}`, "--kind", artifact.kind],
        { signal: deps.signal },
      );
      if (validation.code !== EXIT_POSITIVE) {
        const detail =
          typeof validation.payload.detail === "string" ? validation.payload.detail : "invalid";
        return {
          outcome: "stopped",
          agent,
          reason: `the ${agent} produced ${artifact.path}, but it does not validate as a ${artifact.kind}: ${detail}`,
        };
      }
    }

    deps.onProgress(agent, "done", describeSpawn(agent, spawn));
  }

  const report = deps.workspace.read("evaluator_report.json");
  const status = report === undefined ? undefined : readEvaluatorStatus(report);
  if (status !== "pass") {
    return {
      outcome: "stopped",
      agent: "evaluator",
      reason:
        `the Evaluator returned ${status ?? "no readable status"}. Its gaps route back to the ` +
        "Architect or the Planner within caps (REQ-EVL-003) — that routing is Task 8.3 and is " +
        "not wired yet, so the sprint stops here with the report on disk rather than advancing " +
        "on an unvalidated plan.",
    };
  }

  return { outcome: "planned", evaluator: "pass" };
}

function readEvaluatorStatus(json: string): string | undefined {
  try {
    const parsed: unknown = JSON.parse(json);
    if (parsed !== null && typeof parsed === "object" && "status" in parsed) {
      const status = (parsed as { status: unknown }).status;
      return typeof status === "string" ? status : undefined;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

/**
 * The zero-context prompt for one Phase-1 agent.
 *
 * Short on purpose: the agent's *contract* is in its definition and its inlined skill,
 * both of which are already in its system prompt. Restating them here would create a
 * second source of truth for behaviour — the exact failure DD-16 produced once already.
 * What belongs here is only what changes per sprint: the goal, and where to read and write.
 */
export function phase1Prompt(agent: AgentName, goal: string): string {
  const header = [`# Sprint goal`, "", goal, ""];
  switch (agent) {
    case "scout":
      return [
        ...header,
        "Map what this repository already is, so the Architect designs against it instead of",
        "guessing. Write `.saltcode/context_report.json`.",
      ].join("\n");
    case "architect":
      return [
        ...header,
        "Read `.saltcode/context_report.json` and decide the shape of the solution. Write",
        "`.saltcode/design.md`, carrying every constraint and anti-pattern through verbatim",
        "into `## HARD CONSTRAINTS`.",
      ].join("\n");
    case "planner":
      return [
        ...header,
        "Read `.saltcode/design.md` — and only that — and write `.saltcode/tasks.json`.",
      ].join("\n");
    case "test-intent":
      return [
        ...header,
        "Read `.saltcode/tasks.json` and the project's test configuration. Write one",
        "`tests/task_{id}_spec.*` per task, before any implementation exists.",
      ].join("\n");
    case "evaluator":
      return [
        ...header,
        "Read `.saltcode/design.md` and `.saltcode/tasks.json`. Run the four checks —",
        "traceability, coverage, preservation, compliance — and write",
        "`.saltcode/evaluator_report.json`, routing each gap to the Architect or the Planner.",
      ].join("\n");
    default:
      return header.join("\n");
  }
}

/** Open a sprint, refusing a second Phase-1 fire in one (REQ-ORC-001 AC2). */
export function openSprint(
  existing: SprintState | null,
  options: { goal: string; online: boolean; project: "fresh" | "amend" },
): { ok: true; sprint: SprintState } | { ok: false; reason: string } {
  const allowed = canFirePhase1(existing);
  if (!allowed.ok) return { ok: false, reason: allowed.reason ?? "Phase 1 has already fired" };
  return {
    ok: true,
    sprint: newSprint({
      sprintId: `sprint-${Date.now().toString(36)}`,
      goal: options.goal,
      online: options.online,
      project: options.project,
    }),
  };
}

/** `tasks.json` → the ordered task list the Phase-2 loop consumes. */
export function readTaskOrder(json: string): string[] {
  try {
    const parsed: unknown = JSON.parse(json);
    const tasks =
      parsed !== null && typeof parsed === "object" && "tasks" in parsed
        ? (parsed as { tasks: unknown }).tasks
        : parsed;
    if (!Array.isArray(tasks)) return [];
    return tasks
      .map((task) =>
        task !== null && typeof task === "object" && "id" in task
          ? String((task as { id: unknown }).id)
          : undefined,
      )
      .filter((id): id is string => id !== undefined);
  } catch {
    return [];
  }
}
