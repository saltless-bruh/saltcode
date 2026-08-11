/**
 * The twelve registered tools (13.2 / REQ-EXT-004, design §5.3).
 *
 * Each is a thin bridge onto one `saltcode.tools.*` entrypoint, written against the
 * argument names and JSON keys in `docs/entrypoints.md`. Thin is the design: computation,
 * validation, code execution and vector storage belong to the backend
 * (`.claude/rules/project-architecture.md`), so anything here that started to *decide*
 * something would be in the wrong layer.
 *
 * Two shapes recur and both are deliberate:
 *
 *  - **Large text arrives as a parameter, not a path.** A diff comes in as a string, gets
 *    staged under `.saltcode/scratch/`, and the path is passed to `--in`. That is partly
 *    because `pi.exec` has no stdin, and partly because it puts the diff where
 *    `pi.on("tool_call")` can inspect it *before* anything applies it (REQ-ORC-004 AC1).
 *  - **A negative verdict is a result, not an error.** Exit 1 means miss / dirty / fail /
 *    refused, and the loop routes on it. Only a broken contract throws.
 */

import { StringEnum } from "@earendil-works/pi-ai";
import type { AgentToolResult, ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  type BackendResult,
  type BackendRunner,
  type Entrypoint,
  isToolFailure,
} from "./backend.ts";
import type { Workspace } from "./workspace.ts";

/** What every Saltcode tool puts in `details` — enough to render and to audit. */
export interface SaltcodeToolDetails {
  entrypoint: Entrypoint;
  code: number;
  verdict: string;
  dryRun: boolean;
  toolFailure: boolean;
}

const LANGUAGES = ["python", "typescript", "javascript", "rust", "go"] as const;
const CONTRACT_KINDS = [
  "context_report",
  "design",
  "tasks",
  "evaluator_report",
  "audit_result",
] as const;
const LOCAL_PROFILES = ["A_STD", "A_FOCUS", "B"] as const;

function toResult(result: BackendResult): AgentToolResult<SaltcodeToolDetails> {
  const verdict =
    typeof result.payload.verdict === "string" ? result.payload.verdict : String(result.payload.ok);
  return {
    content: [{ type: "text", text: JSON.stringify(result.payload) }],
    details: {
      entrypoint: result.entrypoint,
      code: result.code,
      verdict,
      dryRun: result.dryRun,
      toolFailure: isToolFailure(result),
    },
  };
}

/** Push `--flag value` only when the value is present, so defaults stay the backend's. */
function optional(flag: string, value: string | number | undefined): string[] {
  return value === undefined ? [] : [flag, String(value)];
}

export interface ToolDeps {
  runner: () => BackendRunner;
  workspace: Workspace;
}

/**
 * Register all twelve. `connectivity` — the thirteenth entrypoint — is deliberately not
 * among them: it is the session-open probe (REQ-GATE-001), called by the extension, and
 * exposing it to the model would invite a mid-sprint re-probe that silently changes the
 * routing tier under a running plan.
 */
export function registerBackendTools(pi: ExtensionAPI, deps: ToolDeps): void {
  const call = async (
    entrypoint: Entrypoint,
    args: string[],
    signal: AbortSignal | undefined,
  ): Promise<AgentToolResult<SaltcodeToolDetails>> =>
    toResult(await deps.runner().call(entrypoint, args, { signal }));

  pi.registerTool({
    name: "saltcode_validate_contract",
    label: "Validate Contract",
    description:
      "Validate a typed Saltcode contract (context_report, design, tasks, evaluator_report, " +
      "audit_result) against its pydantic model and the Output-Length Enforcer. Returns " +
      "{valid, errors[]}; exit 1 means invalid, which is a result, not a crash.",
    parameters: Type.Object({
      kind: StringEnum(CONTRACT_KINDS, { description: "Which contract this is." }),
      content: Type.String({ description: "The contract JSON to validate." }),
    }),
    async execute(_id, params, signal) {
      const path = deps.workspace.stage("contract", params.kind, params.content);
      return call("validate_contract", ["--in", path, "--kind", params.kind], signal);
    },
  });

  pi.registerTool({
    name: "saltcode_scope_probe",
    label: "Scope Probe",
    description:
      "Compute the lookup-time scope fingerprint for the spec-cache key. A filesystem walk — " +
      "not a Phase-1 fire and not an LSP session. Always exits 0; an empty repo yielding an " +
      "empty fingerprint is correct behaviour.",
    parameters: Type.Object({
      scope: Type.Optional(
        Type.Array(Type.String(), {
          description: "Explicit paths. When given they are used directly, with no probe.",
        }),
      ),
      language: Type.Optional(StringEnum(LANGUAGES)),
    }),
    async execute(_id, params, signal) {
      const args = ["--repo", deps.workspace.root];
      for (const path of params.scope ?? []) args.push("--scope", path);
      args.push(...optional("--language", params.language));
      return call("scope_probe", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_cache_lookup",
    label: "Cache Lookup",
    description:
      "Run the cache ladder top-down and stop at the first hit: exact spec cache, then the " +
      "PCD-adaptive semantic tier. Verdict is exact_hit, semantic_candidate or miss. A " +
      "semantic_candidate is NOT authorisation to reuse — the confirmation field says how " +
      "expensive the Architect confirmation must be first.",
    parameters: Type.Object({
      goal: Type.String({ description: "The sprint goal." }),
      scope: Type.Optional(Type.Array(Type.String(), { description: "Skips the probe." })),
      offline: Type.Optional(
        Type.Boolean({ description: "Exact tier only; the semantic tier reports unavailable." }),
      ),
    }),
    async execute(_id, params, signal) {
      const args = ["--repo", deps.workspace.root, "--goal", params.goal];
      for (const path of params.scope ?? []) args.push("--scope", path);
      if (params.offline === true) args.push("--offline");
      return call("cache_lookup", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_diff_check",
    label: "Diff Check",
    description:
      "The first Phase-2 gate: is this a parseable unified diff? Reports whether the single " +
      "bounded repair (unwrapping a code fence) was spent. Exit 1 means malformed.",
    parameters: Type.Object({
      diff: Type.String({ description: "The Builder's raw output." }),
      task_id: Type.String({ description: "The task this diff implements." }),
      check_apply: Type.Optional(
        Type.Boolean({ description: "Also run `git apply --check` against the live tree." }),
      ),
    }),
    async execute(_id, params, signal) {
      const path = deps.workspace.stage("diff", params.task_id, params.diff);
      const args = ["--in", path];
      if (params.check_apply === true) args.push("--repo", deps.workspace.root);
      return call("diff_check", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_sandbox_apply",
    label: "Sandbox Apply",
    description:
      "Apply a diff to a disposable git worktree inside the security container. The live tree " +
      "is never touched here. Returns the sandbox path the later gates run against.",
    parameters: Type.Object({
      diff: Type.String(),
      task_id: Type.String(),
      keep: Type.Optional(Type.Boolean({ description: "Leave the sandbox on disk to inspect." })),
    }),
    async execute(_id, params, signal) {
      const path = deps.workspace.stage("diff", params.task_id, params.diff);
      const args = ["--repo", deps.workspace.root, "--in", path];
      if (params.keep === true) args.push("--keep");
      return call("sandbox_apply", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_static_gate",
    label: "Static Gate",
    description:
      "Run the per-language static gate on the sandbox inside the container. Verdict clean, " +
      "dirty or unavailable — and unavailable is not clean: a runner that failed on its own " +
      "configuration must not spend a Builder retry.",
    parameters: Type.Object({
      sandbox: Type.String({ description: "The worktree from saltcode_sandbox_apply." }),
      language: Type.Optional(StringEnum(LANGUAGES)),
    }),
    async execute(_id, params, signal) {
      const args = ["--sandbox", params.sandbox, "--repo", deps.workspace.root];
      args.push(...optional("--language", params.language));
      return call("static_gate", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_test_run",
    label: "Test Run",
    description:
      "Run this task's acceptance spec (tests/task_{id}_spec.*) in the same container and " +
      "sandbox. Outcome pass, fail, skipped or no_tests — no_tests is a failure, because " +
      "cargo and go both exit 0 on an empty run.",
    parameters: Type.Object({
      sandbox: Type.String(),
      task_id: Type.String(),
    }),
    async execute(_id, params, signal) {
      return call(
        "test_run",
        ["--sandbox", params.sandbox, "--task-id", params.task_id, "--repo", deps.workspace.root],
        signal,
      );
    },
  });

  pi.registerTool({
    name: "saltcode_stability",
    label: "Auditor Stability",
    description:
      "The Auditor: anti-gaming heuristics, then the N-pass judgment, then audit_result.json. " +
      "Confidence is measured over N passes, never self-reported. This makes no network call — " +
      "it reports an escalation recommendation and the extension spends the API call.",
    parameters: Type.Object({
      task_id: Type.String(),
      diff: Type.String(),
      static_report: Type.Optional(
        Type.String({ description: "Path to the clean static report." }),
      ),
      tests_report: Type.Optional(Type.String({ description: "Path to the test-runner output." })),
      spec: Type.Optional(
        Type.String({ description: "Path to the task spec the tests came from." }),
      ),
      passes: Type.Optional(Type.Number({ description: "N, default 3." })),
      model: Type.Optional(StringEnum(LOCAL_PROFILES)),
      online: Type.Optional(Type.Boolean({ description: "Report the online escalation option." })),
    }),
    async execute(_id, params, signal) {
      const diffPath = deps.workspace.stage("diff", params.task_id, params.diff);
      const args = [
        "--repo",
        deps.workspace.root,
        "--task-id",
        params.task_id,
        "--diff",
        diffPath,
        "--tasks",
        deps.workspace.artifact("tasks"),
      ];
      args.push(...optional("--static", params.static_report));
      args.push(...optional("--tests", params.tests_report));
      args.push(...optional("--spec", params.spec));
      args.push(...optional("--passes", params.passes));
      args.push(...optional("--model", params.model));
      if (params.online === true) args.push("--online");
      return call("compute_stability", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_apply_live",
    label: "Apply Live",
    description:
      "Land an Auditor-passed diff on the live working tree, UNCOMMITTED on purpose — the " +
      "commit belongs after the regression gate. A tests/** write is refused before git is " +
      "asked, so it never surfaces as 'the patch did not apply'.",
    parameters: Type.Object({
      diff: Type.String(),
      task_id: Type.String(),
    }),
    async execute(_id, params, signal) {
      const path = deps.workspace.stage("diff", params.task_id, params.diff);
      return call("apply_live", ["--repo", deps.workspace.root, "--in", path], signal);
    },
  });

  pi.registerTool({
    name: "saltcode_compact_spec",
    label: "Compact Spec",
    description:
      "Strip completed and resolved content from design.md, leaving the ## HARD CONSTRAINTS " +
      "block byte-identical. A refusal leaves the file untouched — that is the outcome this " +
      "tool exists to produce when preservation cannot be guaranteed.",
    parameters: Type.Object({
      sprint: Type.Optional(Type.Number({ description: "Fires every 5. Omit to force." })),
      proposed: Type.Optional(
        Type.String({ description: "Path to a model-produced rewrite, treated as untrusted." }),
      ),
      dry_run: Type.Optional(Type.Boolean()),
    }),
    async execute(_id, params, signal) {
      const args = ["--design", deps.workspace.artifact("design")];
      args.push(...optional("--sprint", params.sprint));
      args.push(...optional("--proposed", params.proposed));
      if (params.dry_run === true) args.push("--dry-run");
      return call("compact_spec", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_calibrate",
    label: "Calibrate Thresholds",
    description:
      "Run the measured-then-fixed protocol over a labelled set: max-F1 for the Auditor bar, " +
      "quantiles for the PCD bars. Only thresholds this run actually measured are marked " +
      "calibrated — writing a key for anything else would forge the flag.",
    parameters: Type.Object({
      set: Type.String({ description: "Path to the labelled calibration set." }),
      only: Type.Optional(StringEnum(["auditor", "semantic"] as const)),
      passes: Type.Optional(Type.Number()),
      model: Type.Optional(StringEnum(LOCAL_PROFILES)),
      dry_run: Type.Optional(Type.Boolean()),
    }),
    async execute(_id, params, signal) {
      const args = ["--repo", deps.workspace.root, "--set", params.set];
      args.push(...optional("--only", params.only));
      args.push(...optional("--passes", params.passes));
      args.push(...optional("--model", params.model));
      if (params.dry_run === true) args.push("--dry-run");
      return call("calibrate", args, signal);
    },
  });

  pi.registerTool({
    name: "saltcode_read_scoped",
    label: "Read Scoped",
    description:
      "Read one file body, but only if it is in this task's files_affected. The scope comes " +
      "from the contract, not from the caller — there is deliberately no way to widen it. " +
      "Out of scope is exit 1 and the payload carries no content key at all.",
    parameters: Type.Object({
      task_id: Type.String(),
      path: Type.String({ description: "Repo-relative path to read." }),
    }),
    async execute(_id, params, signal) {
      return call(
        "read_scoped",
        [
          "--repo",
          deps.workspace.root,
          "--tasks",
          deps.workspace.artifact("tasks"),
          "--task-id",
          params.task_id,
          "--path",
          params.path,
        ],
        signal,
      );
    },
  });
}

/** The names registered above, for `pi.setActiveTools` and the per-phase narrowing. */
export const SALTCODE_TOOL_NAMES = [
  "saltcode_validate_contract",
  "saltcode_scope_probe",
  "saltcode_cache_lookup",
  "saltcode_diff_check",
  "saltcode_sandbox_apply",
  "saltcode_static_gate",
  "saltcode_test_run",
  "saltcode_stability",
  "saltcode_apply_live",
  "saltcode_compact_spec",
  "saltcode_calibrate",
  "saltcode_read_scoped",
] as const;
