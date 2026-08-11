/**
 * Session state: what a sprint is, and how it survives a reload (13.5 / REQ-EXT-006,
 * REQ-ORC-001, REQ-ORC-005).
 *
 * State is persisted with `pi.appendEntry` and rebuilt on `session_start` by replaying
 * `ctx.sessionManager.getEntries()`. Custom entries do not participate in LLM context, so
 * this is durable bookkeeping the model never sees and cannot talk its way around — which
 * is the whole reason the one-fire-per-sprint rule lives here rather than in a prompt.
 *
 * `replayState` is a pure fold over entries so REQ-EXT-006 AC1 is testable without a
 * session: hand it a list, get the reconstruction.
 */

import type { LoopCounters, TaskBudget } from "./budget.ts";
import { startLoops } from "./budget.ts";

export const ENTRY_SPRINT = "saltcode:sprint";
export const ENTRY_BUDGET = "saltcode:budget";
export const ENTRY_TASK = "saltcode:task";

/** Where a sprint is. `flagged` is terminal until a human acts (REQ-FAIL-003). */
export type SprintPhase = "idle" | "phase1" | "phase_gate" | "phase2" | "complete" | "flagged";

export interface SprintState {
  sprintId: string;
  goal: string;
  phase: SprintPhase;
  /** REQ-ORC-001 AC2: set the moment Phase 1 starts, so a second fire can be refused. */
  phase1Fired: boolean;
  /** REQ-CACHE-002 / REQ-ORC-002 AC1: stored when the Evaluator passes and the spec locks. */
  specHash?: string;
  /** REQ-GATE-001, resolved by the connectivity probe at session open. */
  online: boolean;
  /** REQ-GATE-002: `fresh` puts Architect and Evaluator on Pro. */
  project: "fresh" | "amend";
  loops: LoopCounters;
  /** Task ids in dependency order, from `tasks.json`. */
  taskOrder: string[];
  /** Task ids that reached a passing Auditor verdict and were applied. */
  completed: string[];
}

export interface TaskState {
  taskId: string;
  status: "pending" | "building" | "gating" | "passed" | "flagged";
  filesAffected: string[];
  complexity: "low" | "medium" | "high";
}

export interface SaltcodeState {
  sprint: SprintState | null;
  budget: TaskBudget | null;
  task: TaskState | null;
}

/** Structural shape of a Pi session entry — enough to replay, nothing more. */
export interface SessionEntryLike {
  type: string;
  customType?: string | undefined;
  data?: unknown;
}

export function emptyState(): SaltcodeState {
  return { sprint: null, budget: null, task: null };
}

export function newSprint(options: {
  sprintId: string;
  goal: string;
  online: boolean;
  project: "fresh" | "amend";
}): SprintState {
  return {
    sprintId: options.sprintId,
    goal: options.goal,
    phase: "idle",
    phase1Fired: false,
    online: options.online,
    project: options.project,
    loops: startLoops(),
    taskOrder: [],
    completed: [],
  };
}

/**
 * Rebuild the latest state from a session's entries.
 *
 * "Latest wins" per `customType`: every mutation appends, so the last entry of a type is
 * the current value and the earlier ones are the audit trail. A malformed entry is
 * skipped rather than throwing — a session that cannot be replayed at all would be worse
 * than one that resumes from the last entry that parsed, and the caller can see which by
 * comparing what came back against what it expected.
 */
export function replayState(entries: readonly SessionEntryLike[]): SaltcodeState {
  const state = emptyState();
  for (const entry of entries) {
    if (entry.type !== "custom") continue;
    const data = entry.data;
    if (data === null || typeof data !== "object") continue;
    switch (entry.customType) {
      case ENTRY_SPRINT:
        if (isSprintState(data)) state.sprint = data;
        break;
      case ENTRY_BUDGET:
        if (isTaskBudget(data)) state.budget = data;
        break;
      case ENTRY_TASK:
        if (isTaskState(data)) state.task = data;
        break;
      default:
        break;
    }
  }
  return state;
}

/**
 * REQ-ORC-001 AC2: exactly one Phase-1 fire per sprint.
 *
 * Evaluator-routed re-loops are *not* a second fire — they re-invoke one agent inside the
 * chain that already fired, and their caps live in `budget.ts`.
 */
export function canFirePhase1(sprint: SprintState | null): { ok: boolean; reason?: string } {
  if (sprint === null) return { ok: true };
  if (sprint.phase1Fired) {
    return {
      ok: false,
      reason:
        `sprint ${sprint.sprintId} has already fired Phase 1. One planning fire per sprint ` +
        "(REQ-ORC-001 AC2) is the cost model, not a rate limit — start a new sprint to re-plan.",
    };
  }
  return { ok: true };
}

/** The next task to build: first in dependency order that is not already completed. */
export function nextTaskId(sprint: SprintState): string | undefined {
  return sprint.taskOrder.find((id) => !sprint.completed.includes(id));
}

function isSprintState(value: object): value is SprintState {
  const v = value as Partial<SprintState>;
  return (
    typeof v.sprintId === "string" &&
    typeof v.goal === "string" &&
    typeof v.phase === "string" &&
    typeof v.phase1Fired === "boolean" &&
    Array.isArray(v.taskOrder) &&
    Array.isArray(v.completed)
  );
}

function isTaskBudget(value: object): value is TaskBudget {
  const v = value as Partial<TaskBudget>;
  return (
    typeof v.taskId === "string" &&
    typeof v.attempts === "number" &&
    typeof v.specDefects === "number" &&
    typeof v.startedOnTierB === "boolean"
  );
}

function isTaskState(value: object): value is TaskState {
  const v = value as Partial<TaskState>;
  return (
    typeof v.taskId === "string" && typeof v.status === "string" && Array.isArray(v.filesAffected)
  );
}
