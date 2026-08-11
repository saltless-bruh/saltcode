/**
 * Keeping `## HARD CONSTRAINTS` alive across conversation compaction
 * (13.6 / REQ-EXT-007, DD-6).
 *
 * This is the *conversation* half of constraint preservation. The other half — the
 * on-disk Spec Compactor — is REQ-CMP-001 and lives in the backend. They are distinct
 * (AC2 says so) and they use the same tactic for the same reason: **splice the original
 * bytes back in and verify**, rather than asking a model to keep them and hoping.
 *
 * A paraphrased constraint is a lost constraint that reads as if it survived. That is the
 * failure mode worth engineering against: not the summary that drops a constraint
 * visibly, but the one that turns "never call the payment API in tests" into "avoid
 * external calls where practical" and looks fine.
 */

/** The heading that opens the block. Matched on a stripped line, any `#` depth ≥ 2. */
const HARD_CONSTRAINTS_HEADING = /^#{2,}\s+HARD\s+CONSTRAINTS\s*$/i;

/** Any Markdown heading — where the block ends. */
const ANY_HEADING = /^#{1,6}\s+/;

export interface ConstraintsBlock {
  /** The heading line plus everything up to the next heading, verbatim. */
  text: string;
  /** The individual constraint lines, for verification. Empty when there is no block. */
  lines: string[];
}

/** No block. Distinct from an empty one: "nothing to preserve" vs "preserve nothing". */
export const NO_CONSTRAINTS: ConstraintsBlock = { text: "", lines: [] };

/**
 * Find the `## HARD CONSTRAINTS` block in a design document.
 *
 * The block runs from its heading to the next heading of any level, or to end of file.
 * A document with no such section yields {@link NO_CONSTRAINTS} — which a caller must
 * read as "this design has no constraints yet", not as an error.
 */
export function extractConstraints(design: string): ConstraintsBlock {
  const lines = design.split("\n");
  let start = -1;
  let end = lines.length;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
    if (start < 0) {
      if (HARD_CONSTRAINTS_HEADING.test(line.trim())) start = i;
    } else if (ANY_HEADING.test(line.trimStart())) {
      end = i;
      break;
    }
  }

  if (start < 0) return NO_CONSTRAINTS;

  const block = lines.slice(start, end);
  const text = block.join("\n").replace(/\s+$/, "");
  return { text, lines: constraintLines(block.slice(1)) };
}

/**
 * The content lines of the block — what has to still be there afterwards.
 *
 * Bullets, numbered items and plain prose all count; blank lines and horizontal rules do
 * not. Leading list markers are stripped so the check is about the *constraint*, not
 * about whether the summary reproduced the same bullet character.
 */
function constraintLines(body: readonly string[]): string[] {
  const out: string[] = [];
  for (const raw of body) {
    const line = raw.trim();
    if (line === "" || /^([-*_])\1{2,}$/.test(line)) continue;
    out.push(line.replace(/^(?:[-*+]|\d+[.)])\s+/, "").trim());
  }
  return out;
}

/** Whether every constraint line survives verbatim in `summary`. */
export function retainsAllConstraints(summary: string, block: ConstraintsBlock): boolean {
  return block.lines.every((line) => summary.includes(line));
}

/** The constraint lines missing from `summary` — the report when preservation fails. */
export function missingConstraints(summary: string, block: ConstraintsBlock): string[] {
  return block.lines.filter((line) => !summary.includes(line));
}

/**
 * Put the block back into a model-produced summary, verbatim and unmissable.
 *
 * Prepending rather than appending is deliberate. A summary is read top-down by the next
 * turn, and these are the lines that constrain everything below them; burying them under
 * a progress report inverts that. The wrapper sentence exists because a bare heading in
 * the middle of a summary reads as a section *about* the constraints rather than as the
 * constraints themselves.
 */
export function spliceConstraints(summary: string, block: ConstraintsBlock): string {
  if (block.text === "") return summary;
  return [
    "The following HARD CONSTRAINTS remain in force for this sprint. They are reproduced",
    "verbatim from the locked design and are not negotiable, superseded, or summarizable.",
    "",
    block.text,
    "",
    "---",
    "",
    summary,
  ].join("\n");
}
