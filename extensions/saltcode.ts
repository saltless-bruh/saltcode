/**
 * Saltcode — the bridge between Pi and the Python backend.
 *
 * Scaffold only (Task 0.2). This file establishes the extension's shape and
 * proves the package loads; the behaviour lands in Task 13:
 *
 *   13.1  session_start / session_shutdown lifecycle + state replay
 *   13.2  registerTool for each saltcode_* backend capability
 *   13.3  on("tool_call") access control + built-in write/edit/bash override
 *   13.4  setModel / setThinkingLevel routing per design §6
 *   13.5  appendEntry state: sprint, budget, per-task counters
 *   13.6  session_before_compact — preserve ## HARD CONSTRAINTS
 *   13.7  ctx.ui widgets for phase, task deck, gate pipeline, cost
 *   13.8  registerCommand: /sprint /review /status /cost
 *   13.9  registerFlag: dry-run, builder-escalation
 *   13.10 the Phase-2 loop
 *
 * Placement rule: orchestration, routing, access control, session state and UI
 * belong here. Computation, validation, code execution and vector storage belong
 * to the Python backend (../saltcode_backend). See design §5 and
 * .claude/rules/project-architecture.md.
 */

import type {
  ExtensionAPI,
  ExtensionContext,
  SessionStartEvent,
} from "@earendil-works/pi-coding-agent";

/** Key used for this extension's footer status line. */
const STATUS_KEY = "saltcode";

export default function saltcode(pi: ExtensionAPI): void {
  pi.on("session_start", (_event: SessionStartEvent, ctx: ExtensionContext): void => {
    // Task 13.1 replaces this with: replay ctx.sessionManager.getEntries() to
    // rebuild sprint/budget/task state, probe connectivity, register the
    // Saltnitor provider, and start the backend daemon.
    ctx.ui.setStatus(STATUS_KEY, "saltcode: scaffold (Task 0)");
  });

  pi.on("session_shutdown", (_event, ctx: ExtensionContext): void => {
    // Task 13.1 / 19.2: stop the backend daemon and flush session-scoped state.
    ctx.ui.setStatus(STATUS_KEY, undefined);
  });
}
