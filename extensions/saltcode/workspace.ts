/**
 * Everything the extension touches on disk, in one place.
 *
 * Two rules hold across this file and are the reason it exists rather than `fs` calls
 * scattered through the handlers:
 *
 *  1. **Nothing is written outside `.saltcode/`.** REQ-SEC-004 AC2 makes that a hard
 *     property of dry-run mode; keeping it true in *every* mode means dry-run does not
 *     need a second code path to be honest about.
 *  2. **`pi.exec` has no stdin.** Several entrypoints take `--in PATH | -`; with no stdin
 *     channel the extension writes the payload to a scratch file under `.saltcode/` and
 *     passes the path. That is a consequence of Pi's exec API, not a preference.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/** Everything Saltcode owns lives here, relative to the workspace root. */
export const SALTCODE_DIR = ".saltcode";

/** Contracts written by Phase 1 (design §9). */
export const ARTIFACTS = {
  contextReport: "context_report.json",
  design: "design.md",
  tasks: "tasks.json",
  evaluatorReport: "evaluator_report.json",
  auditResult: "audit_result.json",
} as const;

export class Workspace {
  constructor(readonly root: string) {}

  /** An absolute path under `.saltcode/`. Refuses to escape it. */
  path(...parts: string[]): string {
    for (const part of parts) {
      if (part.startsWith("/") || part.split("/").includes("..")) {
        throw new Error(`refusing to build a .saltcode path from ${JSON.stringify(part)}`);
      }
    }
    return join(this.root, SALTCODE_DIR, ...parts);
  }

  artifact(name: keyof typeof ARTIFACTS): string {
    return this.path(ARTIFACTS[name]);
  }

  exists(...parts: string[]): boolean {
    return existsSync(this.path(...parts));
  }

  read(...parts: string[]): string | undefined {
    const target = this.path(...parts);
    return existsSync(target) ? readFileSync(target, "utf8") : undefined;
  }

  /** Read a file anywhere in the workspace. Reading is not writing; no confinement. */
  readWorkspaceFile(relative: string): string | undefined {
    const target = join(this.root, relative);
    return existsSync(target) ? readFileSync(target, "utf8") : undefined;
  }

  write(content: string, ...parts: string[]): string {
    const target = this.path(...parts);
    mkdirSync(join(target, ".."), { recursive: true });
    writeFileSync(target, content, "utf8");
    return target;
  }

  append(line: string, ...parts: string[]): void {
    const target = this.path(...parts);
    mkdirSync(join(target, ".."), { recursive: true });
    const existing = existsSync(target) ? readFileSync(target, "utf8") : "";
    writeFileSync(target, `${existing}${line}\n`, "utf8");
  }

  /**
   * Stage a payload for an entrypoint's `--in` argument and return its path.
   *
   * Names are caller-supplied and deterministic per (kind, id) so a retry overwrites its
   * own scratch file rather than accumulating one per attempt — and so a human reading
   * `.saltcode/scratch/` after a failure finds the last thing the Builder actually emitted.
   */
  stage(kind: string, id: string, content: string): string {
    const safe = id.replace(/[^A-Za-z0-9._-]/g, "_");
    return this.write(content, "scratch", `${kind}-${safe}.txt`);
  }
}
