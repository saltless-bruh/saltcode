/**
 * The bridge to the Python backend (13.2 / REQ-EXT-004, REQ-SEC-004, REQ-EXT-014).
 *
 * Every backend capability is one CLI entrypoint, and `docs/entrypoints.md` is the
 * contract this module is written against: stdout is exactly one JSON object, stderr is
 * diagnostics only, and the exit code classifies the outcome so the Phase-2 loop can
 * short-circuit without parsing the payload.
 *
 * Two things this module deliberately does *not* do:
 *
 *  - **It does not interpret verdicts.** `0` versus `1` is a routing fact the loop owns.
 *    A cache miss, a dirty gate and a failing test are all exit 1 and all normal.
 *  - **It does not retry.** Retries are budgeted (REQ-FAIL-001); a silent retry here
 *    would spend a Builder attempt nobody counted.
 *
 * The daemon (Task 19) drops in at `BackendRunner`. `pi.exec` is the documented fallback
 * (REQ-EXT-014 AC2), so it stays the reference implementation rather than a stub.
 */

/** The fourteen entrypoints, by module name under `saltcode.tools`. */
export type Entrypoint =
  | "validate_contract"
  | "scope_probe"
  | "cache_lookup"
  | "diff_check"
  | "sandbox_apply"
  | "static_gate"
  | "test_run"
  | "compute_stability"
  | "apply_live"
  | "compact_spec"
  | "calibrate"
  | "read_scoped"
  | "connectivity"
  | "contained_exec";

/** `docs/entrypoints.md` §Exit codes. */
export const EXIT_POSITIVE = 0;
export const EXIT_NEGATIVE = 1;
export const EXIT_USAGE = 2;
export const EXIT_INTERNAL = 3;

/** The common envelope every payload carries. Tools add their own keys on top. */
export interface BackendEnvelope {
  tool: string;
  ok: boolean;
  verdict?: string;
  error?: string;
  detail?: string;
  [key: string]: unknown;
}

export interface BackendResult {
  entrypoint: Entrypoint;
  code: number;
  payload: BackendEnvelope;
  stderr: string;
  /** True when the call was logged instead of run (REQ-SEC-004 AC1). */
  dryRun: boolean;
}

/** Raised when the contract is broken — not when a tool returns a negative verdict. */
export class BackendContractError extends Error {
  constructor(
    readonly entrypoint: Entrypoint,
    readonly code: number,
    readonly stdout: string,
    readonly stderr: string,
    message: string,
  ) {
    super(message);
    this.name = "BackendContractError";
  }
}

/** The slice of `pi.exec` this module needs. Narrow so a test can supply a fake. */
export type ExecFn = (
  command: string,
  args: string[],
  options?: {
    signal?: AbortSignal | undefined;
    timeout?: number | undefined;
    cwd?: string | undefined;
  },
) => Promise<{ stdout: string; stderr: string; code: number; killed?: boolean }>;

/** Where a dry run writes its record (REQ-SEC-004 AC3). Never outside `.saltcode/`. */
export const DRY_RUN_LOG = ".saltcode/dry_run_log.txt";

export interface BackendOptions {
  exec: ExecFn;
  /** Interpreter running the backend. `python3` unless project config overrides it. */
  python: string;
  cwd: string;
  /** REQ-SEC-004: when true, nothing executes and every command is logged instead. */
  dryRun: boolean;
  /** Appends a line to the dry-run log. Injected so the module stays filesystem-free. */
  logDryRun?: (line: string) => void;
}

export interface CallOptions {
  signal?: AbortSignal | undefined;
  timeout?: number | undefined;
}

/**
 * Invokes backend entrypoints.
 *
 * Task 19 will add a daemon-backed sibling; both satisfy this shape, and the extension
 * picks one at `session_start` and falls back to this one whenever the daemon is not
 * answering — correctness preserved, latency lost (REQ-EXT-014 AC2).
 */
export class BackendRunner {
  constructor(private readonly options: BackendOptions) {}

  get dryRun(): boolean {
    return this.options.dryRun;
  }

  /** The exact command line an invocation would run — the dry-run record, and the log. */
  commandLine(entrypoint: Entrypoint, args: readonly string[]): string {
    return [this.options.python, "-m", `saltcode.tools.${entrypoint}`, ...args].join(" ");
  }

  async call(
    entrypoint: Entrypoint,
    args: readonly string[],
    call: CallOptions = {},
  ): Promise<BackendResult> {
    const line = this.commandLine(entrypoint, args);

    if (this.options.dryRun) {
      this.options.logDryRun?.(line);
      return {
        entrypoint,
        code: EXIT_POSITIVE,
        payload: {
          tool: entrypoint,
          ok: true,
          verdict: "dry_run",
          detail: `dry run: would have executed \`${line}\``,
        },
        stderr: "",
        dryRun: true,
      };
    }

    const result = await this.options.exec(
      this.options.python,
      ["-m", `saltcode.tools.${entrypoint}`, ...args],
      { signal: call.signal, timeout: call.timeout, cwd: this.options.cwd },
    );

    return {
      entrypoint,
      code: result.code,
      payload: parseEnvelope(entrypoint, result.code, result.stdout, result.stderr),
      stderr: result.stderr,
      dryRun: false,
    };
  }
}

/**
 * Parse the one JSON object the contract promises on stdout.
 *
 * Empty stdout gets its own error because that was a real defect once: every entrypoint
 * returned exit 2 on an unknown flag with nothing on stdout, and `JSON.parse("")` surfaced
 * a mistyped flag as an unhandled exception rather than a tool result (G-006). The backend
 * emits the envelope now; this stays because the failure was invisible from the other side.
 */
export function parseEnvelope(
  entrypoint: Entrypoint,
  code: number,
  stdout: string,
  stderr: string,
): BackendEnvelope {
  const text = stdout.trim();
  if (text === "") {
    throw new BackendContractError(
      entrypoint,
      code,
      stdout,
      stderr,
      `saltcode.tools.${entrypoint} exited ${code} with empty stdout. The contract ` +
        "(docs/entrypoints.md) requires exactly one JSON object on stdout for every outcome, " +
        `including failures. stderr: ${stderr.trim().slice(0, 500) || "(empty)"}`,
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (cause) {
    throw new BackendContractError(
      entrypoint,
      code,
      stdout,
      stderr,
      `saltcode.tools.${entrypoint} exited ${code} with stdout that is not JSON: ` +
        `${(cause as Error).message}. First 200 bytes: ${text.slice(0, 200)}`,
    );
  }

  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new BackendContractError(
      entrypoint,
      code,
      stdout,
      stderr,
      `saltcode.tools.${entrypoint} exited ${code} with a JSON ${Array.isArray(parsed) ? "array" : typeof parsed}; ` +
        "the contract is one JSON object.",
    );
  }

  return parsed as BackendEnvelope;
}

/**
 * Whether a result is a *tool failure* rather than a verdict.
 *
 * Exit 2 and 3 mean the tool could not answer — a bad argument or an unreachable service.
 * Those must not route to a Builder retry: REQ-FAIL-001's budget exists to fix code, and
 * spending it on a broken `pyrightconfig.json` ends in FLAG HUMAN with the wrong reason.
 */
export function isToolFailure(result: BackendResult): boolean {
  return result.code === EXIT_USAGE || result.code === EXIT_INTERNAL;
}

/** A one-line human summary for the audit log and the widget. */
export function describeResult(result: BackendResult): string {
  const verdict =
    typeof result.payload.verdict === "string" ? result.payload.verdict : String(result.payload.ok);
  return `${result.entrypoint} → ${verdict} (exit ${result.code})`;
}
