/**
 * The real {@link Gates} — the Phase-2 loop's interface wired to the backend and the
 * Builder sub-agent (13.10 / REQ-EXT-004, REQ-ORC-006, REQ-AUD-002).
 *
 * Everything here is translation: a gate's typed result out of one entrypoint's envelope,
 * and a complete zero-context prompt into one Builder spawn. The decisions live in
 * `phase2.ts`; the container, the judgment and the vectors live in the backend. If a
 * policy question ever needs answering in this file, it is in the wrong file.
 */

import type { EventBus } from "@earendil-works/pi-coding-agent";
import { type BackendResult, type BackendRunner, EXIT_POSITIVE, isToolFailure } from "./backend.ts";
import type { AuditOutcome, GateResult, Gates, TaskSpec } from "./phase2.ts";
import { spawnAgent } from "./subagents.ts";
import type { Workspace } from "./workspace.ts";

export interface GatesDeps {
  runner: BackendRunner;
  workspace: Workspace;
  events: EventBus;
  online: boolean;
  /** N for the Auditor's stability measurement. REQ-AUD-002's default is 3. */
  auditPasses?: number;
  signal?: () => AbortSignal | undefined;
}

/** Read a string field from an envelope without pretending unknown shapes are typed. */
function str(result: BackendResult, key: string): string | undefined {
  const value = result.payload[key];
  return typeof value === "string" ? value : undefined;
}

function detailOf(result: BackendResult, fallback: string): string {
  return str(result, "detail") ?? str(result, "verdict") ?? fallback;
}

/** Map an envelope onto a gate result, keeping "could not answer" distinct from "failed". */
function gateResult(
  result: BackendResult,
  options: { skippedVerdicts?: string[] } = {},
): GateResult {
  if (isToolFailure(result)) {
    return {
      kind: "tool_failure",
      detail: detailOf(result, `${result.entrypoint} exited ${result.code}`),
    };
  }
  const verdict = str(result, "verdict") ?? str(result, "outcome");
  if (verdict !== undefined && options.skippedVerdicts?.includes(verdict) === true) {
    return { kind: "skipped", detail: detailOf(result, verdict) };
  }
  // `unavailable` is the static gate's "my own toolchain is broken" — exit 1, but it must
  // not route to the Builder. It is a tool failure in everything but the exit code.
  if (verdict === "unavailable") {
    return { kind: "tool_failure", detail: detailOf(result, "a static runner was unavailable") };
  }
  if (result.code === EXIT_POSITIVE) {
    return { kind: "pass", detail: detailOf(result, "clean") };
  }
  return {
    kind: "fail",
    detail: detailOf(result, `${result.entrypoint} reported a negative verdict`),
  };
}

/**
 * The Builder's prompt.
 *
 * It has to be complete on its own: the child sees this and its definition, and nothing
 * else (REQ-EXT-012 AC1). The file *bodies* are deliberately absent — the Builder fetches
 * them itself through `saltcode_read_scoped`, which is the only path that enforces the
 * scope, and pasting them here would hand it content the broker never authorised.
 */
export function buildBuilderPrompt(request: {
  task: TaskSpec;
  attempt: number;
  tier: "A" | "B";
  feedback?: string | undefined;
  specPath: string;
}): string {
  const { task } = request;
  const lines = [
    `# Task ${task.id}`,
    "",
    task.description,
    "",
    "## Files you may touch",
    "",
    ...task.filesAffected.map((file) => `- ${file}`),
    "",
    "Read any of them with `saltcode_read_scoped`. Any other path is refused — that is the",
    "boundary, not a hint. Use the LSP/AST tools for anything outside this list.",
    "",
    "## Acceptance criteria",
    "",
    ...(task.acceptanceCriteria ?? ["(none recorded — implement the description exactly)"]).map(
      (criterion) => `- ${criterion}`,
    ),
    "",
    `The acceptance spec is at ${request.specPath}. It is the definition of done and you`,
    "cannot change it: writes under `tests/**` are refused.",
    "",
    "## Output",
    "",
    "Emit a unified diff and nothing else. No prose around it, no explanation after it.",
    `This is attempt ${request.attempt} on Tier ${request.tier}.`,
  ];

  if (request.feedback !== undefined) {
    lines.push(
      "",
      "## What failed last time",
      "",
      "```",
      request.feedback.trim(),
      "```",
      "",
      "Fix that. Do not rewrite the parts that were not implicated.",
    );
  }

  return lines.join("\n");
}

export function createGates(deps: GatesDeps): Gates {
  const signal = (): AbortSignal | undefined => deps.signal?.();
  const call = (entrypoint: Parameters<BackendRunner["call"]>[0], args: string[]) =>
    deps.runner.call(entrypoint, args, { signal: signal() });

  const specPath = (task: TaskSpec): string => `tests/task_${task.id}_spec.*`;

  return {
    async build(request) {
      const result = await spawnAgent(deps.events, "builder", {
        task: buildBuilderPrompt({ ...request, specPath: specPath(request.task) }),
        cwd: deps.workspace.root,
        ...(signal() !== undefined ? { signal: signal() } : {}),
      });
      if (!result.ok) {
        return {
          ok: false,
          detail: `the Builder did not finish: ${result.status}${result.error !== undefined ? ` — ${result.error}` : ""}`,
        };
      }
      const diff = result.output ?? "";
      if (diff.trim() === "") {
        return { ok: false, detail: "the Builder returned no diff at all" };
      }
      return { ok: true, diff };
    },

    async diffCheck(task, diff) {
      const path = deps.workspace.stage("diff", task.id, diff);
      return gateResult(await call("diff_check", ["--in", path, "--repo", deps.workspace.root]));
    },

    async sandboxApply(task, diff) {
      const path = deps.workspace.stage("diff", task.id, diff);
      const result = await call("sandbox_apply", ["--repo", deps.workspace.root, "--in", path]);
      const sandbox = str(result, "sandbox");
      if (result.code !== EXIT_POSITIVE || sandbox === undefined) {
        return {
          ok: false,
          detail: detailOf(result, "the sandbox apply did not produce a worktree"),
        };
      }
      return { ok: true, sandbox };
    },

    async staticGate(_task, sandbox) {
      return gateResult(
        await call("static_gate", ["--sandbox", sandbox, "--repo", deps.workspace.root]),
      );
    },

    async testRun(task, sandbox) {
      return gateResult(
        await call("test_run", [
          "--sandbox",
          sandbox,
          "--task-id",
          task.id,
          "--repo",
          deps.workspace.root,
        ]),
        // A project with no `test_runner_cmd` legitimately skips (REQ-STAT-004 AC3);
        // `no_tests` never does — cargo and go both exit 0 on an empty run.
        { skippedVerdicts: ["skipped"] },
      );
    },

    async audit(task, diff, _sandbox) {
      const path = deps.workspace.stage("diff", task.id, diff);
      const args = [
        "--repo",
        deps.workspace.root,
        "--task-id",
        task.id,
        "--diff",
        path,
        "--tasks",
        deps.workspace.artifact("tasks"),
      ];
      if (deps.auditPasses !== undefined) args.push("--passes", String(deps.auditPasses));
      if (deps.online) args.push("--online");
      return toAudit(await call("compute_stability", args));
    },

    async rejudgeOnFlash(task, diff) {
      // REQ-AUD-002 AC2. The re-judgment is the extension's API call, and its verdict is
      // final — so it is deliberately not re-measured for stability. One escalation, one
      // answer; measuring the escalation would invite a second one.
      const path = deps.workspace.stage("diff", task.id, diff);
      const result = await call("compute_stability", [
        "--repo",
        deps.workspace.root,
        "--task-id",
        task.id,
        "--diff",
        path,
        "--tasks",
        deps.workspace.artifact("tasks"),
        "--passes",
        "1",
        "--online",
      ]);
      const outcome = toAudit(result);
      return { ...outcome, stability: 1, escalation: "none" };
    },

    async applyLive(task, diff) {
      const path = deps.workspace.stage("diff", task.id, diff);
      return gateResult(await call("apply_live", ["--repo", deps.workspace.root, "--in", path]));
    },

    async respec(task, detail) {
      const result = await spawnAgent(deps.events, "test-intent", {
        task: [
          `# Re-spec task ${task.id}`,
          "",
          "The Auditor judged the existing acceptance spec defective. Its finding:",
          "",
          "```",
          detail.trim(),
          "```",
          "",
          `Rewrite \`tests/task_${task.id}_spec.*\` so it tests the task's stated behaviour`,
          "rather than the defect. Do not write implementation code, and do not weaken the",
          "criteria to make the current implementation pass — that inverts what the spec is for.",
          "",
          "## The task",
          "",
          task.description,
          "",
          ...(task.acceptanceCriteria ?? []).map((criterion) => `- ${criterion}`),
        ].join("\n"),
        cwd: deps.workspace.root,
      });
      return {
        ok: result.ok,
        detail: result.ok ? "re-spec complete" : `Test Intent did not finish: ${result.status}`,
      };
    },
  };
}

function toAudit(result: BackendResult): AuditOutcome {
  const verdict = str(result, "verdict");
  const measurement = result.payload.measurement;
  const stability =
    measurement !== null && typeof measurement === "object" && "stability_score" in measurement
      ? Number((measurement as { stability_score: unknown }).stability_score)
      : Number.NaN;
  const escalation = str(result, "escalation");

  return {
    verdict:
      verdict === "pass" ||
      verdict === "impl_fail" ||
      verdict === "gaming_suspected" ||
      verdict === "spec_defect"
        ? verdict
        : "impl_fail",
    // A measurement the backend could not produce must not read as perfect confidence.
    // Treating it as zero sends an online run to the Flash re-judgment, which is the
    // conservative direction (REQ-AUD-002 AC2).
    stability: Number.isFinite(stability) ? stability : 0,
    detail: detailOf(result, "the Auditor returned no detail"),
    escalation:
      escalation === "flash_rejudgment" || escalation === "offline_majority" ? escalation : "none",
  };
}
