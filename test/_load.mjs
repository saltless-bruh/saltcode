/**
 * Load the extension's TypeScript modules from `node --test`.
 *
 * Pi itself loads extensions through jiti, so importing them the same way is not a test
 * convenience — it is the same resolution path the runtime uses, including the `.ts`
 * import specifiers. A test harness that compiled them differently would be checking
 * something Pi never runs.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

let jiti;

/** Import one module under `extensions/`, e.g. `load("saltcode/access.ts")`. */
export async function load(relative) {
  if (jiti === undefined) {
    const { createJiti } = await import(path.join(REPO_ROOT, "node_modules/jiti/lib/jiti.mjs"));
    jiti = createJiti(import.meta.url, { interopDefault: true });
  }
  return jiti.import(path.join(REPO_ROOT, "extensions", relative));
}
