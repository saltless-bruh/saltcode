/**
 * Containing Pi's built-in mutating tools (13.3 / REQ-SEC-007, REQ-EXT-005 AC5, DD-13).
 *
 * Pi ships `read`/`write`/`edit`/`bash`. Left live, an agent — or the human in interactive
 * mode — can mutate the tree and run commands *outside* the sandbox, which is the hole
 * DD-13 exists to close. Saltcode therefore registers same-named overrides, the documented
 * override pattern, built on Pi's own `create*ToolDefinition` factories so the rendering,
 * schemas and result shapes stay exactly the built-ins' (an override that reimplements the
 * UI drifts from it).
 *
 * `read` is deliberately **not** overridden: REQ-SEC-007 AC3 keeps it, and it feeds the
 * scoped-read broker, which is enforced at `tool_call` (REQ-EXT-005 AC3).
 *
 * Every mutating call routes through `saltcode.tools.contained_exec` (G-029), which runs
 * it inside the container the backend owns. Two checks run *here* as well as there —
 * REQ-SEC-002's allowlist and REQ-BLD-003's `tests/**` refusal — because a refusal that
 * costs a subprocess is a refusal that gets optimised away later, and because
 * `pi.on("tool_call")` cannot see the resolved absolute path these operations work in.
 *
 * With no channel injected the overrides **refuse** rather than fall back to the host.
 * That is REQ-SEC-005's rule, and it is the behaviour on a machine with no usable
 * containment backend — where Phase 2 must not run either.
 */

import {
  createBashToolDefinition,
  createEditToolDefinition,
  createWriteToolDefinition,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { checkCommand } from "./allowlist.ts";
import type { ContainedExec } from "./contained.ts";
import { isTestPath } from "./paths.ts";

export interface BuiltinOverrideDeps {
  cwd: string;
  /** The container channel. Undefined only where containment is unavailable. */
  containedExec?: ContainedExec | undefined;
  allowedCommands?: ReadonlyArray<string | readonly string[]> | undefined;
  /** Every refusal is logged (REQ-SEC-002 AC1, REQ-SEC-003). */
  onRefusal: (tool: string, reason: string) => void;
}

const NO_EXEC_CHANNEL =
  "Saltcode overrides the built-in tools so nothing runs uncontained (REQ-SEC-007), and no " +
  "containment backend is usable here, so there is nowhere safe to run this. Install " +
  "bubblewrap, Docker or firejail. Refusing rather than falling back to the host is " +
  "REQ-SEC-005's rule — the same one that stops Phase 2 on this machine.";

/**
 * Register the three overrides.
 *
 * `pi.on("tool_call")` checks the same rules again: these overrides can be swapped or
 * shadowed by a later extension, the handler cannot be, so it stays the backstop rather
 * than the only line.
 */
export function registerContainedBuiltins(pi: ExtensionAPI, deps: BuiltinOverrideDeps): void {
  const refuse = (tool: string, reason: string): never => {
    deps.onRefusal(tool, reason);
    throw new Error(reason);
  };

  // ---------------------------------------------------------------- bash
  const bash = createBashToolDefinition(deps.cwd, {
    operations: {
      exec: async (command, _cwd, options) => {
        const decision = checkCommand(command, deps.allowedCommands);
        if (!decision.allowed) {
          return refuse("bash", `command refused: ${decision.reason}`);
        }
        if (deps.containedExec === undefined) {
          return refuse("bash", NO_EXEC_CHANNEL);
        }
        // `decision.argv` is the parsed form the allowlist actually matched, not the raw
        // string — so what runs is exactly what was authorised, not a re-parse of it.
        const result = await deps.containedExec.exec(decision.argv ?? [], {
          signal: options.signal,
          ...(options.timeout !== undefined
            ? { timeoutSeconds: Math.ceil(options.timeout / 1000) }
            : {}),
        });
        if (result.refused) {
          return refuse("bash", `command refused by the backend: ${result.detail}`);
        }
        if (result.stdout !== "") options.onData(Buffer.from(result.stdout));
        if (result.stderr !== "") options.onData(Buffer.from(result.stderr));
        return { exitCode: result.code };
      },
    },
  });
  pi.registerTool({
    ...bash,
    description:
      `${bash.description}\n\nSaltcode override: runs inside the security container and only ` +
      "the REQ-SEC-002 allowlist executes (pytest, jest, cargo test/check/clippy, go " +
      "test/build/vet, pyright, ruff, tsc, eslint, git apply). Anything else is refused and logged.",
  });

  // --------------------------------------------------------------- write
  const write = createWriteToolDefinition(deps.cwd, {
    operations: {
      writeFile: async (absolutePath, content) => {
        await containedWrite("write", absolutePath, content, deps, refuse);
      },
      // The parent directory is created by the entrypoint inside the writable root, so
      // there is nothing to do here. Doing it on the host would be the one uncontained
      // mutation in an otherwise contained path.
      mkdir: async () => {},
    },
  });
  pi.registerTool({
    ...write,
    description:
      `${write.description}\n\nSaltcode override: the write happens inside the security ` +
      "container, confined to its writable root, and any path under tests/** is refused — " +
      "test specs are written once by Test Intent and are immutable (REQ-BLD-003).",
  });

  // ---------------------------------------------------------------- edit
  const edit = createEditToolDefinition(deps.cwd, {
    operations: {
      // Reading to compute an edit is not a mutation, and REQ-SEC-007 AC3 keeps read
      // available. The scoped-read boundary is enforced at `tool_call`, not here.
      readFile: async (absolutePath) => {
        const { readFile } = await import("node:fs/promises");
        return readFile(absolutePath);
      },
      access: async (absolutePath) => {
        const { access, constants } = await import("node:fs/promises");
        await access(absolutePath, constants.R_OK | constants.W_OK);
      },
      writeFile: async (absolutePath, content) => {
        await containedWrite("edit", absolutePath, content, deps, refuse);
      },
    },
  });
  pi.registerTool({
    ...edit,
    description:
      `${edit.description}\n\nSaltcode override: the write happens inside the security ` +
      "container, and any path under tests/** is refused (REQ-BLD-003).",
  });
}

async function containedWrite(
  tool: string,
  absolutePath: string,
  content: string,
  deps: BuiltinOverrideDeps,
  refuse: (tool: string, reason: string) => never,
): Promise<void> {
  const relative = toRelative(absolutePath, deps.cwd);

  if (isTestPath(relative)) {
    refuse(
      tool,
      `${tool} refused: ${relative} is under tests/**. No agent — and no interactive turn — ` +
        "creates, edits or deletes a test spec (REQ-BLD-003, REQ-ORC-004 AC1).",
    );
  }
  if (relative.startsWith("..") || relative.startsWith("/")) {
    refuse(
      tool,
      `${tool} refused: ${absolutePath} is outside the project. The container's writable ` +
        "root is the boundary (REQ-SEC-001).",
    );
  }
  if (deps.containedExec === undefined) {
    refuse(tool, NO_EXEC_CHANNEL);
    return;
  }

  const result = await deps.containedExec.writeFile(relative, content);
  if (result.refused) {
    refuse(tool, `${tool} refused by the backend: ${result.detail}`);
  }
  if (result.code !== 0) {
    throw new Error(
      `contained ${tool} of ${relative} failed (exit ${result.code}): ${result.stderr || result.detail}`,
    );
  }
}

/** Project-relative form of an absolute path, without pretending `..` is inside. */
function toRelative(absolutePath: string, cwd: string): string {
  const root = cwd.endsWith("/") ? cwd : `${cwd}/`;
  return absolutePath.startsWith(root) ? absolutePath.slice(root.length) : absolutePath;
}
