/**
 * Project configuration, read only for a trusted project (REQ-SEC-006 AC1).
 *
 * `saltcode.toml` is authored by the human and read by both halves of the system. The
 * backend parses it with `tomllib`; this side needs a handful of scalars and arrays, so it
 * carries a **deliberately small TOML subset** rather than a dependency.
 *
 * What the subset covers: `[section]` and `[a.b]` headers, `key = value` for strings,
 * integers, floats, booleans, and single-line arrays of those. What it does not: inline
 * tables, multi-line arrays, dotted keys, arrays of tables, multi-line strings, dates.
 * Anything it does not understand is an **error**, not a silent skip — a config key that
 * quietly fails to apply is the failure mode this whole file exists to avoid, and the
 * caller answers it by falling back to documented defaults and saying so out loud.
 *
 * Recorded as a known limitation rather than presented as a TOML parser.
 */

export interface SaltcodeConfig {
  /** REQ-SEC-002 AC2. `undefined` means the requirement's default list. */
  allowedCommands?: ReadonlyArray<string>;
  /** Interpreter that can `import saltcode`. */
  python?: string;
  /** REQ-STAT-004 / design §14: how the project runs one task spec. */
  testRunnerCmd?: string;
  /**
   * The full suite for the regression gate (Task 17). Read from `[checkpoint]`, which is
   * where design §10.1's config block puts it.
   */
  regressionCmd?: string;
  language?: string;
  /** design §6: above this many estimated input tokens the Builder uses A_FOCUS. */
  aFocusThreshold?: number;
  mtpEnabled?: boolean;
  /** design §10.1 run modes. Consumed by Task 18; read here so `/status` can show it. */
  autoMode?: "off" | "hybrid" | "full";
  autoPush?: boolean;
  /**
   * REQ-EXT-010 AC3: `[providers] fallback = ["qwen/qwen3.6-plus", …]`, tried after an
   * agent's own fallbacks and before Saltnitor Tier B. Entries are `provider/model`.
   */
  providerFallbacks?: ReadonlyArray<{ provider: string; model: string }>;
  /** Where Saltnitor's router answers. Loopback only — a LAN address is off-box. */
  saltnitorBaseUrl?: string;
}

/** Defaults that hold when there is no config, or when the project is untrusted. */
export const DEFAULT_CONFIG: Readonly<SaltcodeConfig> = Object.freeze({
  aFocusThreshold: 32768,
  mtpEnabled: false,
  autoMode: "off",
  autoPush: false,
});

export type TomlValue = string | number | boolean | Array<string | number | boolean>;
export type TomlTable = Record<string, TomlValue | Record<string, TomlValue>>;

export class TomlSubsetError extends Error {
  constructor(line: number, text: string, detail: string) {
    super(`saltcode.toml line ${line}: ${detail} — ${JSON.stringify(text)}`);
    this.name = "TomlSubsetError";
  }
}

/** Parse the subset described above. Throws on anything outside it. */
export function parseTomlSubset(source: string): TomlTable {
  const root: TomlTable = {};
  let table: Record<string, TomlValue> = root as Record<string, TomlValue>;

  const lines = source.split("\n");
  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i] ?? "";
    const line = stripComment(raw).trim();
    if (line === "") continue;

    const header = /^\[([^\]]+)\]$/.exec(line);
    if (header) {
      const path = (header[1] ?? "").split(".").map((part) => part.trim());
      if (path.some((part) => part === "")) {
        throw new TomlSubsetError(i + 1, raw, "empty table name segment");
      }
      let cursor = root as Record<string, unknown>;
      for (const part of path) {
        const existing = cursor[part];
        if (existing === undefined) cursor[part] = {};
        else if (typeof existing !== "object" || existing === null || Array.isArray(existing)) {
          throw new TomlSubsetError(i + 1, raw, `"${part}" is already a value, not a table`);
        }
        cursor = cursor[part] as Record<string, unknown>;
      }
      table = cursor as Record<string, TomlValue>;
      continue;
    }

    const eq = line.indexOf("=");
    if (eq <= 0) throw new TomlSubsetError(i + 1, raw, "not a table header or key = value");
    const key = line.slice(0, eq).trim();
    if (!/^[A-Za-z0-9_-]+$/.test(key)) {
      throw new TomlSubsetError(i + 1, raw, "unsupported key (dotted keys are outside the subset)");
    }
    table[key] = parseValue(line.slice(eq + 1).trim(), i + 1, raw);
  }

  return root;
}

/** Strip a `#` comment, respecting `#` inside a double-quoted string. */
function stripComment(line: string): string {
  let inString = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"' && line[i - 1] !== "\\") inString = !inString;
    else if (ch === "#" && !inString) return line.slice(0, i);
  }
  return line;
}

function parseValue(text: string, line: number, raw: string): TomlValue {
  if (text.startsWith("[")) {
    if (!text.endsWith("]")) {
      throw new TomlSubsetError(line, raw, "multi-line arrays are outside the subset");
    }
    const body = text.slice(1, -1).trim();
    if (body === "") return [];
    return splitArray(body, line, raw).map((item) => {
      const value = parseScalar(item, line, raw);
      if (Array.isArray(value)) throw new TomlSubsetError(line, raw, "nested arrays");
      return value;
    });
  }
  return parseScalar(text, line, raw);
}

/** Split on top-level commas only, so `["a, b", "c"]` stays two elements. */
function splitArray(body: string, line: number, raw: string): string[] {
  const items: string[] = [];
  let current = "";
  let inString = false;
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === '"' && body[i - 1] !== "\\") inString = !inString;
    if (ch === "," && !inString) {
      items.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  if (inString) throw new TomlSubsetError(line, raw, "unterminated string in array");
  if (current.trim() !== "") items.push(current.trim());
  return items;
}

function parseScalar(text: string, line: number, raw: string): string | number | boolean {
  if (text === "true") return true;
  if (text === "false") return false;
  if (text.startsWith('"') && text.endsWith('"') && text.length >= 2) {
    return text.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
  if (text.startsWith("'") && text.endsWith("'") && text.length >= 2) {
    return text.slice(1, -1);
  }
  if (/^[+-]?\d[\d_]*$/.test(text)) return Number.parseInt(text.replace(/_/g, ""), 10);
  if (/^[+-]?\d[\d_]*\.\d[\d_]*$/.test(text)) return Number.parseFloat(text.replace(/_/g, ""));
  throw new TomlSubsetError(
    line,
    raw,
    "unsupported value (only strings, numbers, booleans, arrays)",
  );
}

/** Project the parsed table onto the fields the extension actually uses. */
export function configFromToml(table: TomlTable): SaltcodeConfig {
  const config: SaltcodeConfig = { ...DEFAULT_CONFIG };

  const project = section(table, "project");
  assignString(project, "language", (v) => (config.language = v));
  assignString(project, "test_runner_cmd", (v) => (config.testRunnerCmd = v));
  assignString(project, "python", (v) => (config.python = v));

  const security = section(table, "security");
  const allowed = security?.allowed_commands;
  if (Array.isArray(allowed)) {
    config.allowedCommands = allowed.filter((item): item is string => typeof item === "string");
  }

  const local = section(table, "local");
  if (typeof local?.a_focus_threshold === "number")
    config.aFocusThreshold = local.a_focus_threshold;
  if (typeof local?.mtp_enabled === "boolean") config.mtpEnabled = local.mtp_enabled;

  const providers = section(table, "providers");
  const fallback = providers?.fallback;
  if (Array.isArray(fallback)) {
    config.providerFallbacks = fallback
      .filter((entry): entry is string => typeof entry === "string")
      .map((entry) => entry.split("/"))
      // A malformed entry is dropped rather than guessed at: "qwen" with no model is not
      // a fallback, and inventing one would route a turn somewhere nobody chose.
      .filter((parts): parts is [string, string] => parts.length === 2 && parts.every(Boolean))
      .map(([provider, model]) => ({ provider, model }));
  }
  assignString(providers, "saltnitor_base_url", (v) => (config.saltnitorBaseUrl = v));

  const checkpoint = section(table, "checkpoint");
  // design §10.1's config block: `[checkpoint] regression_cmd`. This used to read
  // `[project]`, which meant the backend's `saltcode.tools.regression` and the extension
  // disagreed about where the setting lives — the extension would have shown "no full
  // suite" for a project that had configured one, and vice versa.
  assignString(checkpoint, "regression_cmd", (v) => (config.regressionCmd = v));
  const mode = checkpoint?.auto_mode;
  if (mode === "off" || mode === "hybrid" || mode === "full") config.autoMode = mode;
  if (typeof checkpoint?.auto_push === "boolean") config.autoPush = checkpoint.auto_push;

  return config;
}

function section(table: TomlTable, name: string): Record<string, TomlValue> | undefined {
  const value = table[name];
  if (value === undefined || Array.isArray(value) || typeof value !== "object") return undefined;
  return value as Record<string, TomlValue>;
}

function assignString(
  table: Record<string, TomlValue> | undefined,
  key: string,
  set: (value: string) => void,
): void {
  const value = table?.[key];
  if (typeof value === "string") set(value);
}
