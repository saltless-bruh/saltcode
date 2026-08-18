/**
 * The Phase-2 execution loop (13.10 / REQ-AUD-002, REQ-AUD-003, REQ-FAIL-001..004,
 * design §10).
 *
 * ```
 * Builder → diff check → sandbox apply → static gate → test run → Auditor
 *   → apply live (uncommitted)
 * ```
 *
 * Every gate short-circuits on failure and every failure costs the same shared budget, so
 * the loop's whole job is: run the sequence, charge the right thing, route the verdict,
 * and stop rather than spin.
 *
 * The gates arrive as an injected {@link Gates} interface rather than as direct backend
 * calls. That is not indirection for its own sake — it is what lets the routing rules
 * (which failure costs a retry, when Tier B takes over, when a `spec_defect` skips the
 * budget, when the run flags a human) be tested without a container, a model, or a repo.
 * Those rules are the part that is easy to get quietly wrong and expensive to get wrong.
 */

import {
  type BudgetOutcome,
  type FailureKind,
  recordFailure,
  startTask,
  type TaskBudget,
  tierForNextAttempt,
} from "./budget.ts";
import type { GateName, GateStatus } from "./ui.ts";

export interface TaskSpec {
  id: string;
  description: string;
  filesAffected: string[];
  complexity: "low" | "medium" | "high";
  acceptanceCriteria?: string[];
}

/** What every gate returns: it passed, it failed with a reason, or it could not answer. */
export type GateResult =
  | { kind: "pass"; detail?: string }
  | { kind: "fail"; detail: string }
  | { kind: "skipped"; detail: string }
  | { kind: "tool_failure"; detail: string };

export type AuditVerdict = "pass" | "impl_fail" | "gaming_suspected" | "spec_defect";

export interface AuditOutcome {
  verdict: AuditVerdict;
  /** Measured over N passes, never self-reported (REQ-AUD-002). */
  stability: number;
  detail: string;
  /** What the backend recommends; the extension, not the backend, spends the API call. */
  escalation: "none" | "flash_rejudgment" | "offline_majority";
}

export interface BuildRequest {
  task: TaskSpec;
  attempt: number;
  tier: "A" | "B";
  /** The previous failure, verbatim, so the Builder fixes the actual complaint. */
  feedback?: string;
}

export interface Gates {
  build(request: BuildRequest): Promise<{ ok: true; diff: string } | { ok: false; detail: string }>;
  diffCheck(task: TaskSpec, diff: string): Promise<GateResult>;
  sandboxApply(
    task: TaskSpec,
    diff: string,
  ): Promise<{ ok: true; sandbox: string } | { ok: false; detail: string }>;
  staticGate(task: TaskSpec, sandbox: string): Promise<GateResult>;
  testRun(task: TaskSpec, sandbox: string): Promise<GateResult>;
  audit(task: TaskSpec, diff: string, sandbox: string): Promise<AuditOutcome>;
  /** REQ-AUD-002 AC2's one online re-judgment. Its verdict is final. */
  rejudgeOnFlash(task: TaskSpec, diff: string): Promise<AuditOutcome>;
  applyLive(task: TaskSpec, diff: string): Promise<GateResult>;
  /** REQ-FAIL-002: Test Intent re-runs with the Auditor's detail. Does not cost a retry. */
  respec(task: TaskSpec, detail: string): Promise<{ ok: boolean; detail: string }>;
}

export interface Phase2Deps {
  gates: Gates;
  online: boolean;
  /** Calibrated or provisional; below it, an online run escalates once (REQ-AUD-002 AC2). */
  auditorStabilityThreshold: number;
  onGate: (gate: GateName, status: GateStatus, detail?: string) => void;
  onBudget: (budget: TaskBudget) => void;
  onFlagHuman: (reason: string) => void;
}

export type TaskOutcome =
  | { outcome: "passed"; budget: TaskBudget; diff: string }
  | { outcome: "flagged"; budget: TaskBudget; reason: string };

/**
 * Build one task through the gates until it passes or the budget flags a human.
 *
 * The `while (true)` is bounded by the budget, not by trust: every path out of the body
 * either returns or has charged a failure, and `recordFailure` flags at 3. REQ-FAIL-003's
 * "never spin silently" is the property this shape exists to hold.
 */
export async function runTask(task: TaskSpec, deps: Phase2Deps): Promise<TaskOutcome> {
  let budget = startTask(task.id, task.complexity);
  let feedback: string | undefined;

  for (;;) {
    deps.onBudget(budget);
    resetBoard(deps);

    const charge = (kind: FailureKind, detail: string): BudgetOutcome => {
      const transition = recordFailure(budget, kind);
      budget = transition.budget;
      feedback = detail;
      deps.onBudget(budget);
      return transition.result;
    };

    // ---------------------------------------------------------------- Builder
    deps.onGate("diff", "running");
    const built = await deps.gates.build({
      task,
      attempt: budget.attempts + 1,
      tier: tierForNextAttempt(budget),
      ...(feedback !== undefined ? { feedback } : {}),
    });
    if (!built.ok) {
      deps.onGate("diff", "fail", built.detail);
      const result = charge("impl_fail", built.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    const { diff } = built;

    // ------------------------------------------------------------- diff check
    const format = await deps.gates.diffCheck(task, diff);
    if (format.kind !== "pass") {
      deps.onGate("diff", "fail", format.detail);
      if (format.kind === "tool_failure")
        return flag(deps, budget, toolFailure("diff_check", format.detail));
      const result = charge("diff_format", format.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    deps.onGate("diff", "pass");

    // ----------------------------------------------------------- sandbox apply
    deps.onGate("sandbox", "running");
    const sandbox = await deps.gates.sandboxApply(task, diff);
    if (!sandbox.ok) {
      deps.onGate("sandbox", "fail", sandbox.detail);
      const result = charge("diff_format", sandbox.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    deps.onGate("sandbox", "pass");

    // ------------------------------------------------------------- static gate
    deps.onGate("static", "running");
    const statik = await deps.gates.staticGate(task, sandbox.sandbox);
    if (statik.kind === "tool_failure") {
      // `unavailable` is not `dirty`. A runner that failed on its own configuration must
      // never spend a Builder retry rewriting code that was never wrong (G-017).
      deps.onGate("static", "fail", statik.detail);
      return flag(deps, budget, toolFailure("static_gate", statik.detail));
    }
    if (statik.kind === "fail") {
      deps.onGate("static", "fail", statik.detail);
      const result = charge("static", statik.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    deps.onGate("static", statik.kind === "skipped" ? "skipped" : "pass");

    // --------------------------------------------------------------- test run
    deps.onGate("tests", "running");
    const tests = await deps.gates.testRun(task, sandbox.sandbox);
    if (tests.kind === "tool_failure") {
      deps.onGate("tests", "fail", tests.detail);
      return flag(deps, budget, toolFailure("test_run", tests.detail));
    }
    if (tests.kind === "fail") {
      deps.onGate("tests", "fail", tests.detail);
      const result = charge("test", tests.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    deps.onGate("tests", tests.kind === "skipped" ? "skipped" : "pass");

    // ---------------------------------------------------------------- Auditor
    deps.onGate("auditor", "running");
    let audit = await deps.gates.audit(task, diff, sandbox.sandbox);

    // REQ-AUD-002: an unstable verdict escalates once when online, and that verdict is
    // final. Offline it never escalates (AC1) — the majority local verdict stands, because
    // "the Auditor is unsure" and "the Auditor never ran" are different facts.
    if (deps.online && audit.stability < deps.auditorStabilityThreshold) {
      audit = await deps.gates.rejudgeOnFlash(task, diff);
    }

    if (audit.verdict === "spec_defect") {
      deps.onGate("auditor", "fail", audit.detail);
      const result = charge("spec_defect", audit.detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      const respec = await deps.gates.respec(task, audit.detail);
      if (!respec.ok) {
        return flag(
          deps,
          budget,
          `task ${task.id}: the Auditor called spec_defect and Test Intent could not re-spec: ` +
            `${respec.detail}. The task's definition of done is unresolved, which is a human call.`,
        );
      }
      continue;
    }

    if (audit.verdict !== "pass") {
      deps.onGate("auditor", "fail", audit.detail);
      const kind: FailureKind =
        audit.verdict === "gaming_suspected" ? "gaming_suspected" : "impl_fail";
      const detail =
        audit.verdict === "gaming_suspected"
          ? `${audit.detail}\n\nImplement the behaviour the tests describe. Do not special-case ` +
            "test inputs, return fixture literals, or branch on test names — the spec is the " +
            "target, not the assertion."
          : audit.detail;
      const result = charge(kind, detail);
      if (result.outcome === "flag_human") return flag(deps, budget, result.reason);
      continue;
    }
    deps.onGate("auditor", "pass", `stability ${audit.stability.toFixed(2)}`);

    // -------------------------------------------------------------- apply live
    deps.onGate("apply", "running");
    const applied = await deps.gates.applyLive(task, diff);
    if (applied.kind !== "pass") {
      deps.onGate("apply", "fail", applied.detail);
      // A refused apply after a passing Auditor is not a code problem — the diff was
      // judged good and the tree rejected it. Retrying the Builder would burn budget on
      // the wrong question.
      return flag(
        deps,
        budget,
        `task ${task.id}: the Auditor passed but the live apply did not land: ${applied.detail}. ` +
          "The working tree is not where the sandbox thought it was; reconcile before continuing.",
      );
    }
    deps.onGate("apply", "pass");

    return { outcome: "passed", budget, diff };
  }
}

function resetBoard(deps: Phase2Deps): void {
  for (const gate of ["diff", "sandbox", "static", "tests", "auditor", "apply"] as const) {
    deps.onGate(gate, "pending");
  }
}

function toolFailure(tool: string, detail: string): string {
  return (
    `${tool} could not answer: ${detail}. This is a tool failure, not a verdict — it is not ` +
    "charged to the Builder's budget, because spending a retry rewriting correct code would " +
    "end in FLAG HUMAN with the wrong reason (REQ-FAIL-001)."
  );
}

function flag(deps: Phase2Deps, budget: TaskBudget, reason: string): TaskOutcome {
  deps.onFlagHuman(reason);
  return { outcome: "flagged", budget, reason };
}
