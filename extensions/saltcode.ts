/**
 * Saltcode — the bridge between Pi and the Python backend (Task 13).
 *
 * Placement rule (`.claude/rules/project-architecture.md`): orchestration, routing, access
 * control, session state and UI belong here; computation, validation, code execution and
 * vector storage belong to the backend. Every handler below is wiring — the decisions live
 * in `saltcode/*.ts` as pure functions so they can be tested without a running Pi, a
 * model, or a container.
 *
 *   13.1  session_start / session_shutdown lifecycle + state replay
 *   13.2  registerTool for each saltcode_* backend capability      → saltcode/tools.ts
 *   13.3  on("tool_call") access control + contained built-ins     → saltcode/access.ts
 *   13.4  setModel / setThinkingLevel routing per design §6        → saltcode/routing.ts
 *   13.5  appendEntry state: sprint, budget, per-task counters     → saltcode/state.ts
 *   13.6  session_before_compact — preserve ## HARD CONSTRAINTS    → saltcode/constraints.ts
 *   13.7  ctx.ui widgets for phase, task deck, gate pipeline, cost → saltcode/ui.ts
 *   13.8  registerCommand: /sprint /review /status /cost           → saltcode/sprint.ts
 *   13.9  registerFlag: dry-run, builder-escalation
 *   13.10 the Phase-2 loop                                          → saltcode/phase2.ts
 *
 * Note on layout: helper modules live in `extensions/saltcode/`, which Pi's loader does
 * **not** treat as a second extension because it has no `index.ts` and no `package.json`
 * (`discoverExtensionsInDir`). Adding either would double-register this extension.
 */

import {
  type ExtensionAPI,
  type ExtensionContext,
  generateSummary,
  type SessionBeforeCompactEvent,
  type SessionStartEvent,
  type ToolCallEvent,
} from "@earendil-works/pi-coding-agent";
import { decideAccess } from "./saltcode/access.ts";
import { type AgentName, selectBuilderProfile } from "./saltcode/agents.ts";
import { BackendRunner, DRY_RUN_LOG, EXIT_POSITIVE } from "./saltcode/backend.ts";
import { describeBudget, type TaskBudget } from "./saltcode/budget.ts";
import { registerContainedBuiltins } from "./saltcode/builtins.ts";
import {
  configFromToml,
  DEFAULT_CONFIG,
  parseTomlSubset,
  type SaltcodeConfig,
} from "./saltcode/config.ts";
import {
  extractConstraints,
  missingConstraints,
  retainsAllConstraints,
  spliceConstraints,
} from "./saltcode/constraints.ts";
import { createContainedExec } from "./saltcode/contained.ts";
import { createGates } from "./saltcode/gates.ts";
import { runTask, type TaskSpec } from "./saltcode/phase2.ts";
import { ensureProfile, reconcileLocalTurn, registerProviders } from "./saltcode/providers.ts";
import { applyRoute, describeRoute } from "./saltcode/routing.ts";
import { openSprint, readTaskOrder, runPhase1 } from "./saltcode/sprint.ts";
import {
  ENTRY_BUDGET,
  ENTRY_SPRINT,
  ENTRY_TASK,
  replayState,
  type SaltcodeState,
} from "./saltcode/state.ts";
import { registerBackendTools } from "./saltcode/tools.ts";
import { type GateBoard, type GateName, type GateStatus, SaltcodeUI } from "./saltcode/ui.ts";
import { Workspace } from "./saltcode/workspace.ts";

/** Everything that is per-session. Rebuilt on `session_start`, torn down on shutdown. */
interface Session {
  workspace: Workspace;
  runner: BackendRunner;
  config: SaltcodeConfig;
  state: SaltcodeState;
  ui: SaltcodeUI;
  board: GateBoard;
  online: boolean;
  trusted: boolean;
  flag?: string;
}

export default function saltcode(pi: ExtensionAPI): void {
  let session: Session | undefined;

  // ------------------------------------------------------------------ 13.9 flags
  pi.registerFlag("dry-run", {
    description:
      "Walk the whole pipeline without executing anything: every backend command is logged " +
      `to ${DRY_RUN_LOG} instead of run, and nothing outside .saltcode/ is written.`,
    type: "boolean",
    default: false,
  });
  pi.registerFlag("builder-escalation", {
    description:
      "Online only, default off (Task 16): allow one DeepSeek Flash rebuild of a task that " +
      "has failed all three local attempts, before flagging a human.",
    type: "boolean",
    default: false,
  });

  // ------------------------------------------------------ 13.2 the backend bridge
  // Registered at factory time so the tools exist before the first turn; the runner is
  // resolved lazily because it needs the session's cwd, config and dry-run flag.
  registerBackendTools(pi, {
    runner: () => requireSession().runner,
    get workspace() {
      return requireSession().workspace;
    },
  });

  function requireSession(): Session {
    if (session === undefined) {
      throw new Error(
        "Saltcode has no session yet. Backend tools are available from session_start onward; " +
          "if you are seeing this, the extension loaded but the session never started.",
      );
    }
    return session;
  }

  // ------------------------------------------------------------- 13.1 lifecycle
  pi.on("session_start", async (_event: SessionStartEvent, ctx: ExtensionContext) => {
    const workspace = new Workspace(ctx.cwd);
    const trusted = ctx.isProjectTrusted();
    const ui = new SaltcodeUI(ctx);

    // REQ-SEC-006 AC1: project-local config is honoured only for a trusted project.
    // Untrusted means documented defaults, said out loud — not a silent half-configuration.
    const config = trusted ? loadConfig(workspace, ui) : { ...DEFAULT_CONFIG };
    if (!trusted) {
      ui.notify(
        "Saltcode: this project is not trusted, so saltcode.toml is ignored and the built-in " +
          "defaults apply. Phase 2 stays available but runs on the default command allowlist.",
        "warning",
      );
    }

    const dryRun = pi.getFlag("dry-run") === true;
    const runner = new BackendRunner({
      // `exactOptionalPropertyTypes` makes `{signal: undefined}` and `{}` different types,
      // and Pi's ExecOptions takes the latter — so drop the absent keys rather than
      // widening its signature from this side.
      exec: (command, args, options) =>
        pi.exec(command, args, {
          ...(options?.signal !== undefined ? { signal: options.signal } : {}),
          ...(options?.timeout !== undefined ? { timeout: options.timeout } : {}),
          ...(options?.cwd !== undefined ? { cwd: options.cwd } : {}),
        }),
      python: config.python ?? "python3",
      cwd: ctx.cwd,
      dryRun,
      logDryRun: (line) => workspace.append(line, "dry_run_log.txt"),
    });

    const state = replayState(ctx.sessionManager.getEntries());

    session = {
      workspace,
      runner,
      config,
      state,
      ui,
      board: { gates: {} },
      online: false,
      trusted,
    };

    // 13.3 — the contained built-ins. Registered here rather than at factory time because
    // they need ctx.cwd, and re-registering on reload is how they survive `/reload`.
    //
    // The writable root is the project: in interactive mode a `write` the human asked for
    // has to land in the project, and everything else the container guarantees — no
    // network, no $HOME, no credentials, PID namespace, limits — is unchanged. Phase 2's
    // own writes never come through here; they arrive as a Builder diff through
    // saltcode_sandbox_apply, whose writable root is the disposable worktree.
    registerContainedBuiltins(pi, {
      cwd: ctx.cwd,
      containedExec: createContainedExec({
        runner: () => requireSession().runner,
        workspace,
        writableRoot: () => ctx.cwd,
      }),
      allowedCommands: config.allowedCommands,
      onRefusal: (tool, reason) => {
        workspace.append(
          JSON.stringify({ at: new Date().toISOString(), tool, refused: reason }),
          "refusals.jsonl",
        );
        ui.notify(`${tool} refused: ${reason}`, "warning");
      },
    });

    // Task 11.1/11.2 (G-030 gave 11.2 ownership): register the providers before anything
    // tries to route. Saltnitor degrades to its three configured sections when the router
    // is not up, because the *offline* path depends on those models existing — a slow
    // router must not remove the only route that works without the network.
    const registrations = await registerProviders(pi, {
      ...(config.saltnitorBaseUrl !== undefined
        ? { saltnitorBaseUrl: config.saltnitorBaseUrl }
        : {}),
    });
    for (const registration of registrations) {
      if (registration.source === "degraded")
        ui.notify(`saltcode: ${registration.detail}`, "warning");
    }

    // REQ-GATE-001: probe connectivity once, at session open, and route from the answer.
    session.online = await probeConnectivity(runner, ui);

    render();
    ui.status(
      `saltcode: ${session.online ? "online" : "offline"}${dryRun ? " · dry-run" : ""}` +
        `${state.sprint !== null ? ` · ${state.sprint.phase}` : ""}`,
    );
  });

  pi.on("session_shutdown", (_event, ctx: ExtensionContext) => {
    // Task 19 stops the daemon here. Nothing else is long-lived: `pi.exec` calls are
    // per-invocation by construction, which is the point of the fallback path.
    if (ctx.hasUI) {
      ctx.ui.setStatus("saltcode", undefined);
      ctx.ui.setWidget("saltcode", undefined);
    }
    session = undefined;
  });

  pi.on("resources_discover", (_event, _ctx) => ({
    // Only reached when Saltcode is loaded as a bare extension rather than as an installed
    // package; when it is installed, package.json's `pi` manifest already declares these
    // and this returns paths Pi has resolved anyway.
    skillPaths: ["./skills"],
    promptPaths: ["./prompts"],
  }));

  // -------------------------------------------------- 13.3 access control backstop
  pi.on("tool_call", (event: ToolCallEvent, _ctx: ExtensionContext) => {
    const current = session;
    const decision = decideAccess(
      { toolName: event.toolName, input: (event.input ?? {}) as Record<string, unknown> },
      {
        agent: "top-level",
        scope: current?.state.task?.filesAffected ?? null,
        ...(current?.config.allowedCommands !== undefined
          ? { allowedCommands: current.config.allowedCommands }
          : {}),
      },
    );
    if (decision.block) {
      current?.workspace.append(
        JSON.stringify({
          at: new Date().toISOString(),
          tool: event.toolName,
          blocked: decision.reason,
        }),
        "refusals.jsonl",
      );
      return { block: true, reason: decision.reason };
    }
    return undefined;
  });

  // ------------------------------------------- 13.6 HARD CONSTRAINTS vs compaction
  pi.on(
    "session_before_compact",
    async (event: SessionBeforeCompactEvent, ctx: ExtensionContext) => {
      const current = session;
      if (current === undefined) return undefined;

      const design = current.workspace.read("design.md");
      const block = design === undefined ? undefined : extractConstraints(design);
      if (block === undefined || block.text === "") {
        // No active constraints: nothing to preserve, so Pi compacts as it normally would.
        return undefined;
      }

      const model = ctx.model;
      if (model === undefined) {
        return cancelCompaction(current, "there is no active model to summarize with");
      }

      try {
        const apiKey = await ctx.modelRegistry.getApiKeyForProvider(model.provider);
        const summary = await generateSummary(
          event.preparation.messagesToSummarize,
          model,
          event.preparation.settings.reserveTokens,
          apiKey,
          undefined,
          event.signal,
          event.customInstructions,
          event.preparation.previousSummary,
        );

        // Splice the original bytes back in rather than trusting the model to have kept
        // them. A paraphrased constraint is a lost constraint that reads as if it survived —
        // the same reason the on-disk Spec Compactor re-splices instead of diffing prose.
        const preserved = spliceConstraints(summary, block);
        if (!retainsAllConstraints(preserved, block)) {
          return cancelCompaction(
            current,
            `the summary would have dropped ${missingConstraints(preserved, block).length} constraint(s)`,
          );
        }

        return {
          compaction: {
            summary: preserved,
            firstKeptEntryId: event.preparation.firstKeptEntryId,
            tokensBefore: event.preparation.tokensBefore,
          },
        };
      } catch (error) {
        return cancelCompaction(current, (error as Error).message);
      }
    },
  );

  function cancelCompaction(current: Session, why: string): { cancel: true } {
    current.ui.notify(
      `Compaction cancelled: ${why}. The active HARD CONSTRAINTS could not be guaranteed to ` +
        "survive it (REQ-EXT-007), and losing one silently is worse than a full context.",
      "error",
    );
    return { cancel: true };
  }

  // ------------------------------------------------------- 13.4 routing feedback
  pi.on("model_select", (_event, _ctx) => {
    updateStatus();
  });
  pi.on("thinking_level_select", (_event, _ctx) => {
    updateStatus();
  });

  // ------------------------------------------------------------- 13.8 commands
  pi.registerCommand("sprint", {
    description: "Run one sprint: Phase 1 planning, then the Phase-2 execution loop.",
    handler: async (args, ctx) => {
      const current = requireSession();
      const goal = args.trim();
      if (goal === "") {
        current.ui.notify('Usage: /sprint "<goal>"', "error");
        return;
      }

      const opened = openSprint(current.state.sprint, {
        goal,
        online: current.online,
        project: current.state.sprint === null ? "fresh" : "amend",
      });
      if (!opened.ok) {
        current.ui.notify(opened.reason, "error");
        return;
      }

      current.state.sprint = opened.sprint;
      current.state.sprint.phase = "phase1";
      current.state.sprint.phase1Fired = true;
      persistSprint();
      render();

      const phase1 = await runPhase1(goal, {
        events: pi.events,
        runner: current.runner,
        workspace: current.workspace,
        ui: current.ui,
        route: async (agent) => {
          const resolution = await applyRoute(
            agent,
            {
              online: current.online,
              project: current.state.sprint?.project ?? "fresh",
            },
            {
              pi,
              ctx,
              ...(current.config.providerFallbacks !== undefined
                ? { configuredFallbacks: current.config.providerFallbacks }
                : {}),
              onFallback: ({ agent: who, from, to }) =>
                current.workspace.append(
                  JSON.stringify({
                    at: new Date().toISOString(),
                    agent: who,
                    fallback_from: from,
                    fallback_to: to,
                  }),
                  "audit_log.jsonl",
                ),
            },
          );
          current.ui.status(describeRoute(resolution));
          return resolution.ok ? { ok: true } : { ok: false, reason: resolution.reason };
        },
        onProgress: (agent, phase, detail) => {
          current.ui.status(
            `saltcode: ${agent} ${phase}${detail !== undefined ? ` — ${detail}` : ""}`,
          );
        },
        ...(ctx.signal !== undefined ? { signal: ctx.signal } : {}),
      });

      if (phase1.outcome === "stopped") {
        flagHuman(`Phase 1 stopped at the ${phase1.agent}: ${phase1.reason}`);
        return;
      }

      // REQ-ORC-002: the Phase-Gate is automatic on an Evaluator pass. Task 8.2 adds the
      // spec lock and the stored spec hash; the advance itself belongs here.
      current.state.sprint.phase = "phase_gate";
      const tasksJson = current.workspace.read("tasks.json");
      current.state.sprint.taskOrder = tasksJson === undefined ? [] : readTaskOrder(tasksJson);
      persistSprint();
      render();

      // ---------------------------------------------------------- Decision 1
      const approved = await current.ui.decide(
        1,
        `Phase 1 produced ${current.state.sprint.taskOrder.length} task(s) for: ${goal}\n\n` +
          "Review .saltcode/design.md and .saltcode/tasks.json. Approving starts the Phase-2 " +
          "loop, which builds each task locally behind the static, test, faithfulness and " +
          "regression gates.",
      );
      if (!approved) {
        current.ui.notify(
          "Sprint paused at Decision 1. The plan is on disk under .saltcode/.",
          "info",
        );
        return;
      }

      current.state.sprint.phase = "phase2";
      persistSprint();
      await runPhase2(ctx);
    },
  });

  pi.registerCommand("status", {
    description: "Show the current sprint, task, budget and gate state.",
    handler: async (_args, _ctx) => {
      const current = requireSession();
      const { sprint, budget } = current.state;
      const lines = [
        sprint === null
          ? "no sprint"
          : `sprint ${sprint.sprintId} · phase ${sprint.phase} · ${sprint.goal}`,
        sprint === null ? "" : `tasks ${sprint.completed.length}/${sprint.taskOrder.length}`,
        budget === null ? "no task in flight" : describeBudget(budget),
        `connectivity: ${current.online ? "online" : "offline"}`,
        `auto_mode: ${current.config.autoMode ?? "off"} · auto_push: ${current.config.autoPush === true}`,
        current.runner.dryRun ? "dry-run: nothing executes" : "",
        current.flag !== undefined ? `FLAG HUMAN: ${current.flag}` : "",
      ].filter((line) => line !== "");
      current.ui.notify(lines.join("\n"), "info");
    },
  });

  pi.registerCommand("cost", {
    description: "Show what this sprint has cost in API calls.",
    handler: async (_args, _ctx) => {
      const current = requireSession();
      current.ui.notify(
        "Phase 2 runs entirely on local models at $0 API cost. The billable surface is the " +
          "single Phase-1 fire plus any Auditor Flash re-judgment. Per-turn accounting comes " +
          "from Pi's own usage totals (/session); Saltcode does not double-count them here.",
        "info",
      );
    },
  });

  pi.registerCommand("review", {
    description: "Surface the cumulative diff for human review, with the integration notice.",
    handler: async (_args, _ctx) => {
      const current = requireSession();
      const diff = await pi.exec("git", ["diff", "HEAD"], {});
      const body =
        diff.stdout.trim() === ""
          ? "The working tree matches HEAD — nothing to review."
          : `${diff.stdout.slice(0, 8000)}${diff.stdout.length > 8000 ? "\n… (truncated; run `git diff` for the rest)" : ""}`;
      await current.ui.decide(3, body);
    },
  });

  // --------------------------------------------------------- 13.10 the Phase-2 loop
  async function runPhase2(ctx: ExtensionContext): Promise<void> {
    const current = requireSession();
    const sprint = current.state.sprint;
    if (sprint === null) return;

    const tasksJson = current.workspace.read("tasks.json");
    const specs = tasksJson === undefined ? [] : readTaskSpecs(tasksJson);
    const gates = createGates({
      runner: current.runner,
      workspace: current.workspace,
      events: pi.events,
      online: current.online,
      signal: () => ctx.signal,
    });

    for (const spec of specs) {
      if (sprint.completed.includes(spec.id)) continue;

      current.state.task = {
        taskId: spec.id,
        status: "building",
        filesAffected: spec.filesAffected,
        complexity: spec.complexity,
      };
      pi.appendEntry(ENTRY_TASK, current.state.task);

      // The Builder's profile, per the VRAM triangle. The token estimate is deliberately
      // coarse — it selects a context window, not a model.
      const profile = selectBuilderProfile({
        complexity: spec.complexity,
        escalated: false,
        estimatedInputTokens: estimateTaskTokens(spec),
        aFocusThreshold: current.config.aFocusThreshold ?? 32768,
      });
      // REQ-MOD-004/005: one local model is resident at a time, so the tier has to be
      // made resident before inference rather than discovered mid-turn. An OOM refusal
      // stops the task for a human — the box is healthy and the plan is what must change.
      const resident = await ensureProfile(profile, {
        ...(current.config.saltnitorBaseUrl !== undefined
          ? { baseUrl: current.config.saltnitorBaseUrl }
          : {}),
        ...(ctx.signal !== undefined ? { signal: ctx.signal } : {}),
      });
      if (!resident.ok && resident.flagHuman) {
        flagHuman(resident.reason);
        current.state.task.status = "flagged";
        pi.appendEntry(ENTRY_TASK, current.state.task);
        return;
      }
      if (!resident.ok) {
        current.ui.notify(`saltcode: ${resident.reason}`, "warning");
      }

      await routeAgent("builder", { builderProfile: profile }, ctx);

      // REQ-MOD-006: reconcile the turn against the VRAM triangle *after* routing, so the
      // level Pi actually holds is the one that fits. `mtp_enabled` is opt-in and off by
      // default (design §6 — Tier A's MTP is finicky until benchmarked on this build).
      const { adjustments } = reconcileLocalTurn({
        profile,
        thinking: pi.getThinkingLevel(),
        mtpEnabled: current.config.mtpEnabled === true,
      });
      for (const adjustment of adjustments) {
        // Named, never silent: a Builder turn that quietly stopped reasoning is the kind
        // of degradation nobody attributes to the right cause.
        current.ui.notify(`saltcode: ${spec.id} — ${adjustment}`, "info");
      }

      const outcome = await runTask(spec, {
        gates,
        online: current.online,
        // Provisional until Task 14b measures it; REQ-AUD-005 AC1's conservative default.
        auditorStabilityThreshold: 0.5,
        onGate: (gate, status) => {
          setGate(gate, status);
        },
        onBudget: (budget) => {
          persistBudget(budget);
        },
        onFlagHuman: (reason) => flagHuman(reason),
      });

      if (outcome.outcome === "flagged") {
        current.state.task.status = "flagged";
        pi.appendEntry(ENTRY_TASK, current.state.task);
        return;
      }

      // Task 17 + 18 attach here: regression gate, then commit + checkpoint. Until they
      // land, the diff is applied and UNCOMMITTED, which is design §10.1's intended state
      // between apply_live and the checkpoint — not an oversight.
      current.state.task.status = "passed";
      sprint.completed.push(spec.id);
      pi.appendEntry(ENTRY_TASK, current.state.task);
      persistSprint();
      render();
    }

    sprint.phase = "complete";
    persistSprint();
    render();

    // ------------------------------------------------------------ Decisions 3 and 4
    const reviewed = await current.ui.decide(
      3,
      `All ${sprint.completed.length} task(s) passed their gates and are applied to the working ` +
        "tree, uncommitted. Review `git diff` before shipping.",
    );
    if (!reviewed) {
      current.ui.notify(
        "Sprint held at Decision 3. The changes are in the tree, uncommitted.",
        "info",
      );
      return;
    }
    await current.ui.decide(
      4,
      "Ship this sprint? Saltcode does not push. `auto_push` is off unless you set it, in " +
        "every run mode.",
    );
  }

  async function routeAgent(
    agent: AgentName,
    extra: { builderProfile?: "A_STD" | "A_FOCUS" | "B" },
    ctx: ExtensionContext,
  ): Promise<void> {
    const current = requireSession();
    const resolution = await applyRoute(
      agent,
      {
        online: current.online,
        project: current.state.sprint?.project ?? "fresh",
        ...(extra.builderProfile !== undefined ? { builderProfile: extra.builderProfile } : {}),
      },
      {
        pi,
        ctx,
        ...(current.config.providerFallbacks !== undefined
          ? { configuredFallbacks: current.config.providerFallbacks }
          : {}),
        onFallback: ({ agent: who, from, to }) =>
          current.workspace.append(
            JSON.stringify({
              at: new Date().toISOString(),
              agent: who,
              fallback_from: from,
              fallback_to: to,
            }),
            "audit_log.jsonl",
          ),
      },
    );
    if (!resolution.ok) flagHuman(resolution.reason);
    else current.ui.status(describeRoute(resolution));
  }

  // ------------------------------------------------------------------- helpers
  function setGate(gate: GateName, status: GateStatus): void {
    const current = session;
    if (current === undefined) return;
    current.board.gates[gate] = status;
    render();
  }

  function persistSprint(): void {
    const current = session;
    if (current?.state.sprint == null) return;
    pi.appendEntry(ENTRY_SPRINT, current.state.sprint);
  }

  function persistBudget(budget: TaskBudget): void {
    const current = session;
    if (current === undefined) return;
    current.state.budget = budget;
    // REQ-ORC-005 AC1: every counter change is appended, so the trail shows what was spent
    // and when. Nothing here ever writes a lower number than it read.
    pi.appendEntry(ENTRY_BUDGET, budget);
    render();
  }

  function flagHuman(reason: string): void {
    const current = session;
    if (current === undefined) return;
    current.flag = reason;
    if (current.state.sprint !== null) {
      current.state.sprint.phase = "flagged";
      persistSprint();
    }
    current.ui.flagHuman(reason);
    current.workspace.append(
      JSON.stringify({ at: new Date().toISOString(), flag_human: reason }),
      "audit_log.jsonl",
    );
    render();
  }

  function render(): void {
    const current = session;
    if (current === undefined) return;
    current.ui.widget({
      sprint: current.state.sprint,
      budget: current.state.budget,
      board: current.board,
      ...(current.flag !== undefined ? { flag: current.flag } : {}),
    });
  }

  function updateStatus(): void {
    const current = session;
    if (current === undefined) return;
    const sprint = current.state.sprint;
    current.ui.status(
      `saltcode: ${current.online ? "online" : "offline"}${sprint !== null ? ` · ${sprint.phase}` : ""}`,
    );
  }
}

function loadConfig(workspace: Workspace, ui: SaltcodeUI): SaltcodeConfig {
  const source =
    workspace.readWorkspaceFile("saltcode.toml") ?? workspace.read("config.toml") ?? undefined;
  if (source === undefined) return { ...DEFAULT_CONFIG };
  try {
    return configFromToml(parseTomlSubset(source));
  } catch (error) {
    // Fail loudly to defaults. A config key that quietly fails to apply is worse than no
    // config: the allowlist, the tier threshold and the run mode all look configured.
    ui.notify(
      `saltcode.toml could not be read (${(error as Error).message}). Falling back to built-in ` +
        "defaults — the command allowlist, thresholds and run mode are NOT what the file says.",
      "error",
    );
    return { ...DEFAULT_CONFIG };
  }
}

async function probeConnectivity(runner: BackendRunner, ui: SaltcodeUI): Promise<boolean> {
  try {
    const result = await runner.call("connectivity", []);
    return result.code === EXIT_POSITIVE && result.payload.online === true;
  } catch (error) {
    // Being unable to probe is not being offline, but routing has to pick one. Offline is
    // the answer that cannot leak: it keeps Phase 1 on Tier B and the Auditor local.
    ui.notify(
      `Connectivity probe failed (${(error as Error).message}); assuming offline. Phase 1 will ` +
        "plan on Saltnitor Tier B and the Auditor stays local.",
      "warning",
    );
    return false;
  }
}

/** `tasks.json` → the specs the Phase-2 loop builds. Unknown shapes yield nothing. */
export function readTaskSpecs(json: string): TaskSpec[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return [];
  }
  const raw =
    parsed !== null && typeof parsed === "object" && "tasks" in parsed
      ? (parsed as { tasks: unknown }).tasks
      : parsed;
  if (!Array.isArray(raw)) return [];

  const specs: TaskSpec[] = [];
  for (const item of raw) {
    if (item === null || typeof item !== "object") continue;
    const task = item as Record<string, unknown>;
    if (typeof task.id !== "string") continue;
    const complexity = task.complexity;
    specs.push({
      id: task.id,
      description: typeof task.description === "string" ? task.description : "",
      filesAffected: Array.isArray(task.files_affected)
        ? task.files_affected.filter((f): f is string => typeof f === "string")
        : [],
      complexity:
        complexity === "low" || complexity === "medium" || complexity === "high"
          ? complexity
          : "medium",
      ...(Array.isArray(task.acceptance_criteria)
        ? {
            acceptanceCriteria: task.acceptance_criteria.filter(
              (c): c is string => typeof c === "string",
            ),
          }
        : {}),
    });
  }
  return specs;
}

/**
 * A coarse token estimate for the A_STD/A_FOCUS choice (design §6).
 *
 * Deliberately crude — chars/4 over the task object and its file list. It picks a context
 * window, and being wrong costs a larger window than needed, not a wrong answer.
 */
function estimateTaskTokens(spec: TaskSpec): number {
  const text = [spec.description, ...spec.filesAffected, ...(spec.acceptanceCriteria ?? [])].join(
    " ",
  );
  return Math.ceil(text.length / 4);
}
