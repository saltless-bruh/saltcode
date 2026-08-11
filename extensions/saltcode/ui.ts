/**
 * The TUI surface (13.7 / REQ-EXT-008, REQ-FAIL-003, REQ-FAIL-004, REQ-CAD-003).
 *
 * Pi's native UI *is* the interface — there is no second binary and no separate dashboard
 * (INTERACTION.md). The widget is a window into an autonomous run, so it answers the four
 * questions a human watching one actually has: what phase, which task, how far through the
 * gates, and what it has cost.
 *
 * The line-building is pure and the Pi calls are a thin wrapper, so the thing worth
 * testing — that a flagged run says so, that the gate pipeline shows where it stopped — is
 * testable without a terminal.
 */

import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { describeBudget, type TaskBudget } from "./budget.ts";
import type { SprintState } from "./state.ts";

export const WIDGET_KEY = "saltcode";
export const STATUS_KEY = "saltcode";

/** The Phase-2 gate sequence, in order. Rendering it in order is the point. */
export const GATE_SEQUENCE = ["diff", "sandbox", "static", "tests", "auditor", "apply"] as const;

export type GateName = (typeof GATE_SEQUENCE)[number];
export type GateStatus = "pending" | "running" | "pass" | "fail" | "skipped";

export interface GateBoard {
  gates: Partial<Record<GateName, GateStatus>>;
}

const GATE_GLYPH: Record<GateStatus, string> = {
  pending: "·",
  running: "▸",
  pass: "✓",
  fail: "✗",
  skipped: "–",
};

/** `diff ✓ · sandbox ✓ · static ✗ · tests · · auditor · · apply ·` */
export function renderGateLine(board: GateBoard): string {
  return GATE_SEQUENCE.map((gate) => `${gate} ${GATE_GLYPH[board.gates[gate] ?? "pending"]}`).join(
    "  ",
  );
}

export interface WidgetModel {
  sprint: SprintState | null;
  budget: TaskBudget | null;
  board: GateBoard;
  /** Accumulated API cost for the sprint, in USD. Phase 2 contributes zero by design. */
  costUsd?: number;
  /** Set when the run has stopped for a human (REQ-FAIL-003). */
  flag?: string;
}

/**
 * The widget's lines.
 *
 * A FLAG HUMAN line goes **first** and pushes everything else down. A run that has stopped
 * and is waiting is the only fact that matters at that moment, and burying it under a
 * progress board is how a paused sprint gets mistaken for a slow one.
 */
export function renderWidget(model: WidgetModel): string[] {
  const lines: string[] = [];

  if (model.flag !== undefined) {
    lines.push(`⚑ FLAG HUMAN — ${model.flag}`);
    lines.push("");
  }

  if (model.sprint === null) {
    lines.push('saltcode: no sprint. /sprint "<goal>" starts one.');
    return lines;
  }

  const { sprint } = model;
  const done = sprint.completed.length;
  const total = sprint.taskOrder.length;
  lines.push(
    `phase ${sprint.phase}  ·  ${sprint.online ? "online" : "offline"}  ·  ${sprint.goal}`,
  );

  if (total > 0) {
    lines.push(
      `tasks ${done}/${total}${done < total ? `  ·  next ${sprint.taskOrder[done]}` : ""}`,
    );
  }

  if (model.budget !== null) {
    lines.push(describeBudget(model.budget));
    lines.push(renderGateLine(model.board));
  }

  if (model.costUsd !== undefined) {
    // Phase 2 runs at $0 API by design, so the number is the Phase-1 fire plus any Flash
    // re-judgment. Showing it flat makes a runaway escalation visible immediately.
    lines.push(`cost $${model.costUsd.toFixed(4)} (Phase 1 + escalations; Phase 2 is local)`);
  }

  return lines;
}

/** The four decisions, as design §5.9 numbers them. */
export type Decision = 1 | 2 | 3 | 4;

const DECISION_TITLE: Record<Decision, string> = {
  1: "Decision 1 — approve the plan",
  2: "Decision 2 — intervene",
  3: "Decision 3 — review the cumulative diff",
  4: "Decision 4 — ship",
};

/** REQ-FAIL-004 AC1. Attached to Decision 3, verbatim, every time. */
export const INTEGRATION_NOTICE =
  "Logical cross-task integration is YOUR check. Type safety, constraint legality, " +
  "per-task correctness and the integrated suite are all gated — semantic composition " +
  "across tasks (task 2 returns cents, task 5 passes dollars, both typed number) is not, " +
  "and never will be claimed as verified.";

export class SaltcodeUI {
  constructor(private readonly ctx: ExtensionContext) {}

  widget(model: WidgetModel): void {
    if (!this.ctx.hasUI) return;
    this.ctx.ui.setWidget(WIDGET_KEY, renderWidget(model));
  }

  status(text: string | undefined): void {
    if (!this.ctx.hasUI) return;
    this.ctx.ui.setStatus(STATUS_KEY, text);
  }

  notify(message: string, level: "info" | "warning" | "error" = "info"): void {
    if (!this.ctx.hasUI) return;
    this.ctx.ui.notify(message, level);
  }

  /**
   * Surface one of the four decisions.
   *
   * In a headless mode (`-p`, JSON) there is nobody to ask. Returning `false` there is the
   * safe answer for every one of the four: they all gate an action, and "do not proceed"
   * is the outcome that cannot silently ship something unreviewed.
   */
  async decide(decision: Decision, body: string): Promise<boolean> {
    if (!this.ctx.hasUI) return false;
    const message = decision === 3 ? `${body}\n\n${INTEGRATION_NOTICE}` : body;
    return this.ctx.ui.confirm(DECISION_TITLE[decision], message);
  }

  /** REQ-FAIL-003: every cap breach produces an explicit flag with a report. */
  flagHuman(reason: string): void {
    this.notify(`FLAG HUMAN — ${reason}`, "error");
  }

  /** REQ-CAD-003 AC1: an idle session has lost its cached prefix; say so before it costs. */
  coldCacheWarning(idleMinutes: number): void {
    this.notify(
      `This session has been idle ${idleMinutes} minutes, past the prefix TTL. The next turn ` +
        "pays full input price on the whole prefix rather than the cached rate.",
      "warning",
    );
  }
}
