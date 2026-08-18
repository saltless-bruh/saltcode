/**
 * The command allowlist (REQ-SEC-002), enforced at `tool_call` before the backend sees it.
 *
 * Mirrors `saltcode_backend/saltcode/harness/command_allowlist.py`. The backend is the
 * layer that actually refuses to spawn; this one exists so a refusal costs no subprocess
 * and so an interactive `bash` is refused by the same rule as an agent's (REQ-SEC-007 AC2).
 *
 * Two properties carry the weight and both are inherited deliberately:
 *
 *  - **Entries are phrases, not binaries.** REQ-SEC-002 lists `cargo test`, `cargo check`
 *    and `cargo clippy` separately, so matching on `cargo` alone would admit
 *    `cargo publish`. An entry of n words matches only the first n argv elements.
 *  - **Shell strings are refused outright.** `pytest; rm -rf ~` has argv[0] == "pytest"
 *    under any naive check. A string is accepted only when it is one simple command with
 *    no metacharacter — a convenience, not a parser.
 */

/** Verbatim from REQ-SEC-002. Overridable per project (AC2), never silently extended. */
export const DEFAULT_ALLOWLIST: ReadonlyArray<readonly string[]> = [
  ["pytest"],
  ["jest"],
  ["cargo", "test"],
  ["go", "test"],
  ["pyright"],
  ["ruff"],
  ["tsc"],
  ["eslint"],
  ["cargo", "check"],
  ["cargo", "clippy"],
  ["go", "build"],
  ["go", "vet"],
  ["git", "apply"],
];

/** Any of these in a command string means it is not a single simple command. */
const SHELL_METACHARACTERS = new Set([
  ";",
  "&",
  "|",
  "<",
  ">",
  "`",
  "$",
  "(",
  ")",
  "{",
  "}",
  "[",
  "]",
  "!",
  "*",
  "?",
  "~",
  "\n",
  "\r",
  "\\",
  '"',
  "'",
]);

export interface AllowlistDecision {
  allowed: boolean;
  reason: string;
  /** Populated only when allowed — a parsed form of a refused command invites running it. */
  argv?: readonly string[];
  matched?: readonly string[];
}

/** Accept `["cargo test"]` or `[["cargo","test"]]`; return the argv form. */
export function normalizeAllowlist(
  entries: ReadonlyArray<string | readonly string[]> | undefined,
): ReadonlyArray<readonly string[]> {
  if (entries === undefined) return DEFAULT_ALLOWLIST;
  const normalized: string[][] = [];
  for (const entry of entries) {
    const parts = typeof entry === "string" ? entry.split(/\s+/).filter(Boolean) : [...entry];
    if (parts.length > 0) normalized.push(parts);
  }
  return normalized;
}

/** `/usr/bin/pytest` and `pytest.exe` both match the entry `pytest`. */
function programName(argv0: string): string {
  const base = argv0.split(/[\\/]/).pop() ?? argv0;
  return base.endsWith(".exe") ? base.slice(0, -4) : base;
}

function splitStringCommand(command: string): { parts?: string[]; error?: string } {
  const found = [...new Set([...command])].filter((c) => SHELL_METACHARACTERS.has(c)).sort();
  if (found.length > 0) {
    return {
      error: `command string contains shell metacharacters (${found.join(" ")}); pass argv instead`,
    };
  }
  const parts = command.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return { error: "empty command" };
  return { parts };
}

/** Decide whether `command` may run. Callers log every refusal (REQ-SEC-002 AC1). */
export function checkCommand(
  command: string | readonly string[],
  allowlist?: ReadonlyArray<string | readonly string[]>,
): AllowlistDecision {
  const entries = normalizeAllowlist(allowlist);

  let parts: readonly string[];
  if (typeof command === "string") {
    const split = splitStringCommand(command);
    if (split.parts === undefined)
      return { allowed: false, reason: split.error ?? "empty command" };
    parts = split.parts;
  } else {
    parts = command;
  }

  const head = parts[0];
  if (head === undefined) return { allowed: false, reason: "empty command" };

  // argv[0] may be an absolute path; every later element must match literally.
  const candidate = [programName(head), ...parts.slice(1)];

  // Longest entry first, so `cargo test` wins over a hypothetical bare `cargo`.
  for (const entry of [...entries].sort((a, b) => b.length - a.length)) {
    if (entry.length > candidate.length) continue;
    if (entry.every((word, i) => candidate[i] === word)) {
      return { allowed: true, reason: "", argv: candidate, matched: entry };
    }
  }

  return {
    allowed: false,
    reason:
      `command not on the allowlist: ${candidate.join(" ")}. ` +
      "REQ-SEC-002 permits only pytest, jest, cargo test/check/clippy, go test/build/vet, " +
      "pyright, ruff, tsc, eslint and git apply; extend [security] allowed_commands deliberately.",
  };
}
