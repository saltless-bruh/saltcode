/**
 * The access-control decision (13.3 / REQ-EXT-005, REQ-ORC-004, REQ-ORC-007, REQ-SEC-002).
 *
 * `decideAccess` is a pure function of the request and the current sprint scope, and
 * `pi.on("tool_call")` is a three-line adapter over it. That split is the point: the
 * privacy boundary is the one thing in this extension that must be provable without a
 * running Pi, a model, or a container.
 *
 * **This is a backstop, not the only line.** Layer 1 is the sub-agent tool allowlist —
 * Scout holds no body-reading tool at all, so its capability is *absent* rather than
 * blocked. Layer 3 is the backend, which re-derives `task.files_affected` from the
 * contract instead of trusting a caller's copy. This layer catches what neither can: a
 * tool that is nominally active being pointed somewhere it must not go, including by the
 * interactive human (REQ-SEC-007 AC2).
 */

import { checkCommand } from "./allowlist.ts";
import { blockedDiffPaths, isTestPath, isWithinScope } from "./paths.ts";

/** Tools whose invocation writes to a path named in their arguments. */
const PATH_WRITING_TOOLS = new Set(["write", "edit"]);

/** Tools that carry a unified diff we can inspect before anything applies it. */
const DIFF_CARRYING_TOOLS = new Set(["saltcode_apply_live", "saltcode_sandbox_apply"]);

/** Tools that run a command inside the container. */
const COMMAND_TOOLS = new Set(["bash"]);

/** The scoped read, and the LSP tools that must never return a body. */
const SCOPED_READ_TOOL = "saltcode_read_scoped";

export interface AccessContext {
  /** The agent whose turn this is, or `top-level` for the interactive session. */
  agent: string;
  /**
   * `task.files_affected` for the task in flight, or `null` when no task is active.
   * A scoped read with no active task is refused: the scope is the authorisation.
   */
  scope: readonly string[] | null;
  /** From `[security] allowed_commands`; undefined means REQ-SEC-002's default list. */
  allowedCommands?: ReadonlyArray<string | readonly string[]>;
}

export interface AccessRequest {
  toolName: string;
  input: Readonly<Record<string, unknown>>;
}

export type AccessDecision = { block: false } | { block: true; reason: string };

const ALLOW: AccessDecision = { block: false };

function deny(reason: string): AccessDecision {
  return { block: true, reason };
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * Decide whether a tool call may proceed.
 *
 * Checks run cheapest-first and each is independent, so a call that trips two rules is
 * reported by the first — the reason reaches the model, and one precise sentence beats a
 * list it has to triage.
 */
export function decideAccess(request: AccessRequest, ctx: AccessContext): AccessDecision {
  const { toolName, input } = request;

  // 1. A write whose target is under tests/** (REQ-ORC-004 AC1, REQ-BLD-003).
  if (PATH_WRITING_TOOLS.has(toolName)) {
    const path = asString(input.path);
    if (path !== undefined && isTestPath(path)) {
      return deny(
        `write blocked: ${path} is under tests/**, which no agent may create, edit or delete ` +
          "(REQ-BLD-003). Test specs are written once by Test Intent and are immutable to the Builder.",
      );
    }
  }

  // 2. A diff that touches tests/** — checked on the body, so a *rename into* tests/ is
  //    caught too. The backend refuses the same diff; this refuses it before git is asked.
  if (DIFF_CARRYING_TOOLS.has(toolName)) {
    const diff = asString(input.diff);
    if (diff !== undefined) {
      const blocked = blockedDiffPaths(diff);
      if (blocked.length > 0) {
        return deny(
          `diff blocked: it writes under tests/** (${blocked.join(", ")}). REQ-BLD-003 forbids ` +
            "any agent creating, editing or deleting a test spec. Fix the implementation instead.",
        );
      }
    }
  }

  // 3. A command that is not on the allowlist (REQ-SEC-002 AC1).
  if (COMMAND_TOOLS.has(toolName)) {
    const command = asString(input.command);
    if (command !== undefined) {
      const decision = checkCommand(command, ctx.allowedCommands);
      if (!decision.allowed) return deny(`command blocked: ${decision.reason}`);
    }
  }

  // 4. The scoped read (REQ-EXT-005 AC3, REQ-ORC-007).
  if (toolName === SCOPED_READ_TOOL) {
    // Scout is denied the scoped read entirely — it is API-routed, so a body in its
    // context is a body on its way to a network provider (`privacy-boundary.md`).
    if (ctx.agent === "scout" || ctx.agent === "saltcode-scout") {
      return deny(
        "scoped read blocked: Scout may never read a file body. Use the LSP/AST tools " +
          "(where_is, find_references, outline) — symbols and structure, never content (REQ-SCT-001).",
      );
    }
    if (ctx.scope === null) {
      return deny(
        "scoped read blocked: no task is active, so there is no files_affected to authorise " +
          "against. The scope is the authorisation (REQ-BLD-002).",
      );
    }
    const path = asString(input.path);
    if (path === undefined) {
      return deny("scoped read blocked: no path given.");
    }
    if (!isWithinScope(path, ctx.scope)) {
      return deny(
        `scoped read blocked: ${path} is not in task.files_affected ` +
          `(${ctx.scope.join(", ") || "empty"}). The Builder sees one task's files and no others ` +
          "(REQ-BLD-002, REQ-ORC-006).",
      );
    }
  }

  return ALLOW;
}
