/**
 * The retry budget and the loop caps (13.5 / REQ-FAIL-001, REQ-FAIL-002, REQ-EVL-003,
 * REQ-ORC-005).
 *
 * All of it is pure. The extension keeps counters in session state and persists them with
 * `pi.appendEntry` (REQ-EXT-006), but *deciding* what a failure costs is arithmetic, and
 * REQ-ORC-005 AC1's "never silently reset mid-task/mid-sprint" is a property you can only
 * test if the transition is a function rather than a mutation scattered across handlers.
 *
 * The one rule that is easy to get subtly wrong, and is therefore stated twice: a
 * `spec_defect` does **not** consume a retry (REQ-FAIL-001 AC3). It says the *tests* are
 * wrong, so spending a Builder attempt on it would send the Builder to fix code whose
 * ruler is the thing under suspicion.
 */

/** Everything that counts against the shared per-task budget, plus the one that does not. */
export type FailureKind =
  | "diff_format"
  | "static"
  | "test"
  | "impl_fail"
  | "gaming_suspected"
  | "regression_fail"
  | "spec_defect";

/** REQ-FAIL-001: diff-format + static + test + impl_fail + gaming + regression share one. */
export const PER_TASK_BUDGET = 3;

/** REQ-FAIL-001 AC2: after 2 failures on Tier A the 3rd attempt escalates to Tier B. */
export const TIER_A_SUB_CAP = 2;

/** REQ-FAIL-002: at most one Test-Intent re-spec per task. */
export const SPEC_DEFECT_CAP = 1;

/** REQ-EVL-003 loop caps. */
export const ARCHITECT_LOOP_CAP = 2;
export const PLANNER_LOOP_CAP = 3;

export interface TaskBudget {
  taskId: string;
  /** Retries consumed. Never decremented; never reset except by `startTask`. */
  attempts: number;
  /** `spec_defect` routings so far. Capped at 1, and free of the retry budget. */
  specDefects: number;
  /** `complexity == high` starts on Tier B, so all three attempts are on Tier B. */
  startedOnTierB: boolean;
}

export type BudgetOutcome =
  | { outcome: "retry"; tier: "A" | "B"; attemptsLeft: number }
  | { outcome: "respec" }
  | { outcome: "flag_human"; reason: string };

export interface BudgetTransition {
  budget: TaskBudget;
  result: BudgetOutcome;
}

/** Open a budget for a task. The only place a counter legitimately goes back to zero. */
export function startTask(taskId: string, complexity: "low" | "medium" | "high"): TaskBudget {
  return { taskId, attempts: 0, specDefects: 0, startedOnTierB: complexity === "high" };
}

/** Which tier the *next* Builder attempt runs on (REQ-FAIL-001 AC2, REQ-MOD-002). */
export function tierForNextAttempt(budget: TaskBudget): "A" | "B" {
  if (budget.startedOnTierB) return "B";
  return budget.attempts >= TIER_A_SUB_CAP ? "B" : "A";
}

/**
 * Charge a failure to the budget and say what happens next.
 *
 * Returns a *new* budget rather than mutating, so a caller cannot half-apply a transition
 * and leave the counter disagreeing with what it told the human.
 */
export function recordFailure(budget: TaskBudget, kind: FailureKind): BudgetTransition {
  if (kind === "spec_defect") {
    const specDefects = budget.specDefects + 1;
    const next: TaskBudget = { ...budget, specDefects };
    if (specDefects > SPEC_DEFECT_CAP) {
      return {
        budget: next,
        result: {
          outcome: "flag_human",
          reason:
            `task ${budget.taskId}: a second spec_defect on the same task (REQ-FAIL-002 AC1). ` +
            "One re-spec is the cap; a second means the Auditor and Test Intent disagree about " +
            "what the task is, which is a question for you, not another loop.",
        },
      };
    }
    return { budget: next, result: { outcome: "respec" } };
  }

  const attempts = budget.attempts + 1;
  const next: TaskBudget = { ...budget, attempts };

  if (attempts >= PER_TASK_BUDGET) {
    return {
      budget: next,
      result: {
        outcome: "flag_human",
        reason:
          `task ${budget.taskId}: the shared per-task budget of ${PER_TASK_BUDGET} is spent ` +
          `(last failure: ${kind}). REQ-FAIL-001 AC1 flags rather than spinning.`,
      },
    };
  }

  return {
    budget: next,
    result: {
      outcome: "retry",
      tier: tierForNextAttempt(next),
      attemptsLeft: PER_TASK_BUDGET - attempts,
    },
  };
}

/** The Evaluator's re-loop counters, per sprint (REQ-EVL-003). */
export interface LoopCounters {
  architect: number;
  planner: number;
}

export function startLoops(): LoopCounters {
  return { architect: 0, planner: 0 };
}

export interface LoopTransition {
  loops: LoopCounters;
  result:
    | { outcome: "reloop"; target: "architect" | "planner" }
    | { outcome: "flag_human"; reason: string };
}

/** Charge an Evaluator-routed re-loop and enforce the caps (Architect ≤2, Planner ≤3). */
export function recordEvaluatorRoute(
  loops: LoopCounters,
  target: "architect" | "planner",
): LoopTransition {
  const cap = target === "architect" ? ARCHITECT_LOOP_CAP : PLANNER_LOOP_CAP;
  const count = loops[target] + 1;
  const next: LoopCounters = { ...loops, [target]: count };

  if (count > cap) {
    return {
      loops: next,
      result: {
        outcome: "flag_human",
        reason:
          `Evaluator loop cap breached: the ${target} has been re-invoked ${count} times ` +
          `(cap ${cap}, REQ-EVL-003). The plan is not converging; the remaining gaps need a human.`,
      },
    };
  }

  return { loops: next, result: { outcome: "reloop", target } };
}

/** One line for the widget and `/status` — REQ-ORC-005 AC1's "observable". */
export function describeBudget(budget: TaskBudget): string {
  const tier = tierForNextAttempt(budget);
  const spec = budget.specDefects > 0 ? `, re-spec ${budget.specDefects}/${SPEC_DEFECT_CAP}` : "";
  return `${budget.taskId}: attempt ${budget.attempts + 1}/${PER_TASK_BUDGET} on Tier ${tier}${spec}`;
}
