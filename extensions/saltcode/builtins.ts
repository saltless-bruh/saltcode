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
 * ---
 *
 * **The exec channel is a declared gap, not an omission.** REQ-SEC-007 AC1 says execution
 * SHALL route through the container. The container is spawned by the backend
 * (REQ-SEC-001), and the backend's thirteen CLI entrypoints (`docs/entrypoints.md`) do not
 * include one that runs an arbitrary allowlisted command inside it — `run_in_container()`
 * exists in `harness/sandbox.py` with no CLI surface. So this module takes the channel as
 * an injected {@link ContainedExec} and, when none is supplied, **refuses**.
 *
 * Refusing is the project's own established answer to this shape of problem: REQ-SEC-005
 * says that with no containment backend Phase 2 refuses rather than falling back to
 * uncontained execution. Nothing here silently runs on the host. What refusing does *not*
 * do is satisfy AC1 as written — it removes the capability instead of containing it — so
 * the leg stays unticked and the gap is recorded rather than smoothed over.
 */

import {
  createBashToolDefinition,
  createEditToolDefinition,
  createWriteToolDefinition,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { checkCommand } from "./allowlist.ts";
import { isTestPath } from "./paths.ts";

/**
 * A channel that runs one allowlisted command inside the security container.
 *
 * Task 13 does not implement this; it names it. An implementation must guarantee the
 * REQ-SEC-001 properties (read-only host fs outside the sandbox, no network, no `$HOME`,
 * isolated PID namespace, memory/CPU/time limits, auto-cleanup) — which is why the
 * backend, not the extension, is the only place it can honestly come from.
 */
export interface ContainedExec {
  run(
    command: string,
    options: { cwd: string; signal?: AbortSignal | undefined; timeout?: number | undefined },
  ): Promise<{ stdout: string; stderr: string; code: number }>;
}

export interface BuiltinOverrideDeps {
  cwd: string;
  /** Undefined until a contained-exec channel exists; every command is then refused. */
  containedExec?: ContainedExec | undefined;
  allowedCommands?: ReadonlyArray<string | readonly string[]> | undefined;
  /** Every refusal is logged (REQ-SEC-002 AC1, REQ-SEC-003). */
  onRefusal: (tool: string, reason: string) => void;
}

const NO_EXEC_CHANNEL =
  "Saltcode overrides the built-in bash so nothing runs uncontained (REQ-SEC-007), but no " +
  "contained-exec channel is configured, so there is nowhere safe to run this. Phase-2 " +
  "commands go through saltcode_static_gate / saltcode_test_run, which run inside the " +
  "container with the sandbox they were given. Refusing rather than falling back to the " +
  "host is REQ-SEC-005's rule.";

/**
 * Register the three overrides.
 *
 * Each keeps the built-in's schema and renderers and adds exactly two things: the
 * REQ-SEC-002 / REQ-BLD-003 check, and the container route. `pi.on("tool_call")` checks
 * the same rules again — these overrides can be swapped, the handler cannot be, so it
 * stays the backstop rather than the only line.
 */
export function registerContainedBuiltins(pi: ExtensionAPI, deps: BuiltinOverrideDeps): void {
  const refuse = (tool: string, reason: string): never => {
    deps.onRefusal(tool, reason);
    throw new Error(reason);
  };

  // ---------------------------------------------------------------- bash
  const bash = createBashToolDefinition(deps.cwd, {
    operations: {
      exec: async (command, cwd, options) => {
        const decision = checkCommand(command, deps.allowedCommands);
        if (!decision.allowed) {
          return refuse("bash", `command refused: ${decision.reason}`);
        }
        if (deps.containedExec === undefined) {
          return refuse("bash", NO_EXEC_CHANNEL);
        }
        const result = await deps.containedExec.run(command, {
          cwd,
          signal: options.signal,
          timeout: options.timeout,
        });
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
        guardWritePath("write", absolutePath, deps, refuse);
        if (deps.containedExec === undefined) {
          return refuse("write", containedWriteRefusal("write", absolutePath));
        }
        await writeThroughContainer(deps.containedExec, deps.cwd, absolutePath, content);
      },
      mkdir: async (dir) => {
        guardWritePath("write", dir, deps, refuse);
        if (deps.containedExec === undefined) {
          return refuse("write", containedWriteRefusal("write", dir));
        }
        await deps.containedExec.run(`mkdir -p ${shellQuote(dir)}`, { cwd: deps.cwd });
      },
    },
  });
  pi.registerTool({
    ...write,
    description:
      `${write.description}\n\nSaltcode override: the write is contained, and any path under ` +
      "tests/** is refused — test specs are written once by Test Intent and are immutable (REQ-BLD-003).",
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
        guardWritePath("edit", absolutePath, deps, refuse);
        if (deps.containedExec === undefined) {
          return refuse("edit", containedWriteRefusal("edit", absolutePath));
        }
        await writeThroughContainer(deps.containedExec, deps.cwd, absolutePath, content);
      },
    },
  });
  pi.registerTool({
    ...edit,
    description:
      `${edit.description}\n\nSaltcode override: the write is contained, and any path under ` +
      "tests/** is refused (REQ-BLD-003).",
  });
}

function guardWritePath(
  tool: string,
  absolutePath: string,
  deps: BuiltinOverrideDeps,
  refuse: (tool: string, reason: string) => never,
): void {
  const relative = absolutePath.startsWith(deps.cwd)
    ? absolutePath.slice(deps.cwd.length).replace(/^\/+/, "")
    : absolutePath;
  if (isTestPath(relative)) {
    refuse(
      tool,
      `${tool} refused: ${relative} is under tests/**. No agent — and no interactive turn — ` +
        "creates, edits or deletes a test spec (REQ-BLD-003, REQ-ORC-004 AC1).",
    );
  }
}

function containedWriteRefusal(tool: string, path: string): string {
  return (
    `Saltcode overrides the built-in ${tool} so nothing mutates the tree uncontained ` +
    `(REQ-SEC-007), but no contained-exec channel is configured, so ${path} cannot be written ` +
    "safely. Phase-2 changes reach the tree as a Builder diff through saltcode_sandbox_apply " +
    "and saltcode_apply_live, which are gated and audited."
  );
}

/**
 * Write a file from inside the container.
 *
 * A heredoc keeps the content out of argv — a long file would otherwise blow the argument
 * limit, and any quoting scheme that survives argv is one bug away from injection. The
 * delimiter is quoted so the shell performs no expansion on the body.
 */
async function writeThroughContainer(
  exec: ContainedExec,
  cwd: string,
  absolutePath: string,
  content: string,
): Promise<void> {
  const delimiter = `SALTCODE_EOF_${Math.random().toString(36).slice(2, 10)}`;
  const script = `cat > ${shellQuote(absolutePath)} <<'${delimiter}'\n${content}\n${delimiter}\n`;
  const result = await exec.run(script, { cwd });
  if (result.code !== 0) {
    throw new Error(
      `contained write to ${absolutePath} failed (exit ${result.code}): ${result.stderr}`,
    );
  }
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}
