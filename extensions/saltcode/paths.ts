/**
 * Path and diff-path predicates for the write-scope allowlist (REQ-ORC-004, REQ-BLD-003).
 *
 * These deliberately mirror `saltcode_backend/saltcode/diffs/apply.py` rather than
 * inventing a second definition. The privacy/write boundary is enforced in three places
 * (`.claude/rules/privacy-boundary.md`); defence in depth only works when the layers agree
 * on what they are defending. Where the two differ, the backend is the one that touches
 * the tree and this one is the one that answers first — so a disagreement means a diff is
 * blocked here and would have been refused there anyway, never the reverse.
 */

/**
 * Paths no agent may write (REQ-BLD-003).
 *
 * `(?:^|/)tests/` covers all three shapes a suite takes: a top-level `tests/`,
 * `.saltcode/tests/`, and a nested one (`src/pkg/tests/…`). Case-insensitive because the
 * live tree may sit on APFS or NTFS, where `Tests/task_T1_spec.py` clears a
 * case-sensitive check and then overwrites the existing `tests/task_T1_spec.py`.
 */
const FORBIDDEN_PATH_RE = /(?:^|\/)tests\//i;

/**
 * Every way a unified diff names a file.
 *
 * Wider than `--- a/` / `+++ b/` on purpose: `git diff --no-prefix` emits bare paths and
 * `git format-patch` emits `rename from`/`rename to` for a move — so a check that only
 * understood the prefixed form would pass a *rename into* `tests/`, whose body carries no
 * `+++ b/tests/...` line at all.
 */
const DIFF_PATH_RE =
  /^(?:diff --git "?[ab]\/(?<gitA>[^"\t]+?)"? "?[ab]\/(?<gitB>[^"\t]+?)"?|--- "?(?:[ab]\/)?(?<minus>[^"\t]+?)"?|\+\+\+ "?(?:[ab]\/)?(?<plus>[^"\t]+?)"?|rename (?:from|to) "?(?<rename>[^"\t]+?)"?)\s*$/;

/** `/dev/null` is how a unified diff spells "this side does not exist". Not a path. */
const DEV_NULL = "/dev/null";

/** Collapse `./`, `a//b` and a trailing slash so two spellings of one path compare equal. */
export function normalizePath(raw: string): string {
  const parts: string[] = [];
  for (const segment of raw.split("/")) {
    if (segment === "" || segment === ".") continue;
    if (segment === ".." && parts.length > 0 && parts[parts.length - 1] !== "..") {
      parts.pop();
      continue;
    }
    parts.push(segment);
  }
  return parts.join("/");
}

/** Whether a repo-relative path falls under a `tests/` directory at any depth. */
export function isTestPath(path: string): boolean {
  return FORBIDDEN_PATH_RE.test(normalizePath(path));
}

/** Every path a unified diff names, in first-seen order, `/dev/null` excluded. */
export function diffPaths(diff: string): string[] {
  const seen = new Set<string>();
  for (const line of diff.split("\n")) {
    const match = DIFF_PATH_RE.exec(line);
    if (!match?.groups) continue;
    for (const value of Object.values(match.groups)) {
      if (value === undefined || value === DEV_NULL) continue;
      const path = normalizePath(value);
      if (path !== "") seen.add(path);
    }
  }
  return [...seen];
}

/** The paths in a diff that the write-scope allowlist forbids. Empty means clean. */
export function blockedDiffPaths(diff: string): string[] {
  return diffPaths(diff).filter(isTestPath);
}

/**
 * Whether `path` is inside `scope` — the Builder's `task.files_affected` (REQ-BLD-002).
 *
 * A scope entry naming a directory admits everything under it; naming a file admits only
 * that file. Matching is on normalized repo-relative paths, so `./src/a.py` and `src/a.py`
 * are the same request.
 */
export function isWithinScope(path: string, scope: readonly string[]): boolean {
  const target = normalizePath(path);
  if (target === "") return false;
  return scope.some((entry) => {
    const allowed = normalizePath(entry);
    if (allowed === "") return false;
    return target === allowed || target.startsWith(`${allowed}/`);
  });
}
