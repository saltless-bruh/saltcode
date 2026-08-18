/**
 * The extension's side of the contained-execution channel (13.3 / REQ-SEC-007, G-029).
 *
 * `saltcode.tools.contained_exec` is the backend entrypoint that runs one allowlisted
 * command — or lands one file — inside the security container. This module is the client:
 * it turns a `bash` command string into an argv the entrypoint accepts, and a `write` into
 * a staged file plus a destination.
 *
 * **The argv is passed as JSON, not as repeated flags.** `--arg -rf` parses as an option,
 * not as a value, and the calls that matter here are exactly that shape — `pytest -q`,
 * `rm -rf /`. Getting it wrong is worse than it sounds: the command is not refused, it is
 * *not understood*, and a usage error reads nothing like "that is not allowed".
 */

import type { BackendResult, BackendRunner } from "./backend.ts";
import type { Workspace } from "./workspace.ts";

export interface ContainedRun {
  code: number;
  stdout: string;
  stderr: string;
  /** True when the allowlist or the write-path check declined — not a run that failed. */
  refused: boolean;
  detail: string;
}

export interface ContainedExec {
  /** Run one allowlisted argv inside the container. */
  exec(
    argv: readonly string[],
    options?: { signal?: AbortSignal | undefined; timeoutSeconds?: number | undefined },
  ): Promise<ContainedRun>;
  /** Land `content` at `relativePath` inside the container's writable root. */
  writeFile(
    relativePath: string,
    content: string,
    options?: { signal?: AbortSignal | undefined },
  ): Promise<ContainedRun>;
}

function toRun(result: BackendResult): ContainedRun {
  const payload = result.payload;
  return {
    code: typeof payload.exit_code === "number" ? payload.exit_code : result.code,
    stdout: typeof payload.stdout === "string" ? payload.stdout : "",
    stderr: typeof payload.stderr === "string" ? payload.stderr : "",
    refused: payload.refused === true,
    detail: typeof payload.detail === "string" ? payload.detail : "",
  };
}

/**
 * Build the channel over a {@link BackendRunner}.
 *
 * `writableRoot` is what the entrypoint calls `--sandbox`. In interactive mode that is the
 * project directory — a `write` the human asked for has to land in the project — and in
 * Phase 2 it is the disposable worktree. Every other container guarantee is the same
 * either way; what interactive mode gives up is the read-only *project*, not containment.
 */
export function createContainedExec(deps: {
  runner: () => BackendRunner;
  workspace: Workspace;
  writableRoot: () => string;
}): ContainedExec {
  return {
    async exec(argv, options = {}) {
      const args = [
        "--sandbox",
        deps.writableRoot(),
        "--repo",
        deps.workspace.root,
        "--argv-json",
        JSON.stringify(argv),
      ];
      if (options.timeoutSeconds !== undefined) {
        args.push("--timeout", String(options.timeoutSeconds));
      }
      return toRun(await deps.runner().call("contained_exec", args, { signal: options.signal }));
    },

    async writeFile(relativePath, content, options = {}) {
      // Staged under `.saltcode/scratch/` rather than passed on the command line: a file
      // of any size blows the argument limit, and every quoting scheme that survives argv
      // is one bug away from injection.
      const staged = deps.workspace.stage("write", relativePath, content);
      return toRun(
        await deps
          .runner()
          .call(
            "contained_exec",
            [
              "--sandbox",
              deps.writableRoot(),
              "--repo",
              deps.workspace.root,
              "--write-path",
              relativePath,
              "--content-file",
              staged,
            ],
            { signal: options.signal },
          ),
      );
    },
  };
}
