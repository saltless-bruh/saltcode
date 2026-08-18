/**
 * Evaluator gap routing and the loop caps (Task 8.3 · REQ-EVL-002, REQ-EVL-003,
 * REQ-FAIL-003, design §8).
 *
 * The Evaluator's four checks produce typed gaps; this decides who fixes them and when to
 * stop asking. Two rules, in this order:
 *
 * 1. **Any `design_gap` present ⇒ the Architect loop** (REQ-EVL-002 AC1). Not "the most
 *    common gap type wins", not "route each gap to its own owner" — a plan built on a
 *    design that is missing something cannot be repaired by re-planning, so one design gap
 *    outranks any number of plan gaps.
 * 2. Otherwise `plan_gap` and `constraint_violation` go to the **Planner**, except a
 *    `constraint_violation` that conflicts with `design.md`, which is the Architect's.
 *
 * **The caps are the point, not a safety net.** Architect ≤ 2, Planner ≤ 3 per sprint; the
 * breach halts the sprint with the gap report in front of a human (REQ-EVL-003 AC1,
 * REQ-FAIL-003). A loop that quietly re-ran the Architect a fourth time would be the exact
 * silent spinning the requirement forbids, and it would burn API budget doing it.
 *
 * `recordEvaluatorRoute` in `budget.ts` owns the counters and the breach decision; this
 * file owns *which* target is charged. Kept apart because the counters are session state
 * that outlives any one report.
 */

/** REQ-CON-005's gap shape. */
export type GapType = "design_gap" | "plan_gap" | "constraint_violation";

export interface EvaluatorGap {
  id: string;
  type: GapType;
  detail: string;
  /** The Evaluator's own routing call. Advisory — see `routeGaps`. */
  target?: "architect" | "planner" | undefined;
}

export interface EvaluatorReport {
  status: "pass" | "gaps" | "unreadable";
  gaps: EvaluatorGap[];
  routingSummary: string;
  detail: string;
}

/**
 * Parse `evaluator_report.json` defensively.
 *
 * An unreadable report is `status: "unreadable"`, never `"pass"`. That asymmetry is
 * deliberate: `pass` is what unlocks the Phase Gate and starts spending the Builder's
 * budget, so it is the one value that must never be reached by a parse falling back to a
 * default.
 */
export function parseEvaluatorReport(json: string | undefined): EvaluatorReport {
  if (json === undefined || json.trim() === "") {
    return { status: "unreadable", gaps: [], routingSummary: "", detail: "no report on disk" };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (error) {
    return {
      status: "unreadable",
      gaps: [],
      routingSummary: "",
      detail: `evaluator_report.json is not valid JSON: ${(error as Error).message}`,
    };
  }
  if (parsed === null || typeof parsed !== "object") {
    return {
      status: "unreadable",
      gaps: [],
      routingSummary: "",
      detail: "evaluator_report.json is not an object",
    };
  }

  const body = parsed as { status?: unknown; gaps?: unknown; routing_summary?: unknown };
  const status = body.status === "pass" || body.status === "gaps" ? body.status : "unreadable";
  const gaps = Array.isArray(body.gaps) ? body.gaps.flatMap(readGap) : [];

  return {
    status,
    gaps,
    routingSummary: typeof body.routing_summary === "string" ? body.routing_summary : "",
    detail:
      status === "unreadable"
        ? `evaluator_report.json has no usable status (got ${JSON.stringify(body.status)})`
        : "",
  };
}

function readGap(raw: unknown): EvaluatorGap[] {
  if (raw === null || typeof raw !== "object") return [];
  const gap = raw as { id?: unknown; type?: unknown; detail?: unknown; target?: unknown };
  const type = gap.type;
  if (type !== "design_gap" && type !== "plan_gap" && type !== "constraint_violation") return [];
  return [
    {
      id: typeof gap.id === "string" ? gap.id : "",
      type,
      detail: typeof gap.detail === "string" ? gap.detail : "",
      target: gap.target === "architect" || gap.target === "planner" ? gap.target : undefined,
    },
  ];
}

export interface GapRouting {
  target: "architect" | "planner";
  why: string;
  /** The gaps that drove the decision, for the re-loop prompt and the human flag. */
  gaps: EvaluatorGap[];
}

/**
 * Decide who the next re-loop targets (REQ-EVL-002).
 *
 * The Evaluator states a `target` per gap, and it is honoured for `constraint_violation` —
 * that is the one case the requirement makes conditional on something only the Evaluator
 * saw ("unless it conflicts with design.md"), so second-guessing it here would require
 * re-running its compliance check. Everywhere else the *type* decides, because a
 * `design_gap` routed to the Planner is a contradiction the requirement resolves in the
 * type's favour.
 */
export function routeGaps(gaps: readonly EvaluatorGap[]): GapRouting | null {
  if (gaps.length === 0) return null;

  const designGaps = gaps.filter((gap) => gap.type === "design_gap");
  if (designGaps.length > 0) {
    // AC1. One design gap outranks any number of plan gaps: re-planning against a design
    // that is still missing something produces a different wrong plan, not a right one.
    return {
      target: "architect",
      why: `${designGaps.length} design_gap(s) present, so the loop targets the Architect (REQ-EVL-002 AC1)`,
      gaps: [...gaps],
    };
  }

  const escalated = gaps.filter(
    (gap) => gap.type === "constraint_violation" && gap.target === "architect",
  );
  if (escalated.length > 0) {
    return {
      target: "architect",
      why:
        `${escalated.length} constraint_violation(s) conflict with design.md, which the ` +
        "Evaluator routed to the Architect rather than the Planner",
      gaps: [...gaps],
    };
  }

  return {
    target: "planner",
    why: `${gaps.length} gap(s), none touching the design, so the Planner re-plans`,
    gaps: [...gaps],
  };
}

/** The re-loop prompt: the goal, plus exactly the gaps that target this agent's fix. */
export function reloopPrompt(goal: string, routing: GapRouting): string {
  const lines = [
    "# Sprint goal",
    "",
    goal,
    "",
    "# The Evaluator rejected the current plan",
    "",
    routing.why,
    "",
    "## Gaps to close",
  ];
  for (const gap of routing.gaps) {
    lines.push(`- **${gap.type}** ${gap.id ? `(${gap.id})` : ""}: ${gap.detail}`);
  }
  lines.push(
    "",
    routing.target === "architect"
      ? "Re-emit `.saltcode/design.md` closing these gaps, still carrying every constraint and anti-pattern through verbatim into `## HARD CONSTRAINTS`."
      : "Re-emit `.saltcode/tasks.json` closing these gaps. Read `.saltcode/design.md` and only that.",
  );
  return lines.join("\n");
}

/** The report a human sees when a cap breaks (REQ-EVL-003 AC1, REQ-FAIL-003). */
export function describeGapReport(report: EvaluatorReport, reason: string): string {
  const lines = [reason, ""];
  if (report.routingSummary !== "") lines.push(report.routingSummary, "");
  if (report.gaps.length === 0) {
    lines.push("The report lists no gaps, which is itself the problem — the Evaluator is");
    lines.push("rejecting the plan without saying what is wrong with it.");
  } else {
    lines.push(`${report.gaps.length} gap(s) still open:`);
    for (const gap of report.gaps) {
      lines.push(`  · ${gap.type}${gap.id ? ` (${gap.id})` : ""}: ${gap.detail}`);
    }
  }
  lines.push("", "The report is on disk at `.saltcode/evaluator_report.json`.");
  return lines.join("\n");
}
