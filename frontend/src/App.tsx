import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  answerInterrupt,
  continueAgent,
  getContextReview,
  generateReport,
  getAttemptGraph,
  getDerivationView,
  getWorkbenchView,
  pauseAgent,
  runWorkbench,
  startAgent,
  stepWorkbench,
  toggleCapability
} from "./api";
import type {
  AttemptGraph,
  AttemptCard,
  ArtifactCard,
  CapabilityCard,
  ExecutionState,
  GraphNode,
  LlmToolSurface,
  RuntimeIssue,
  RuntimeEventCard,
  ToolVisibilityCard,
  WorkbenchNode,
  WorkbenchView
} from "./types";

const typeColors: Record<string, string> = {
  workspace: "#667085",
  analysis_node: "#6d28d9",
  branch: "#2563eb",
  attempt: "#15803d",
  job: "#0369a1",
  artifact: "#b45309",
  observation: "#0f766e",
  outcome: "#15803d",
  trigger: "#b42318",
  finding: "#b42318",
  conclusion: "#ca8a04",
  tool_call: "#667085",
  review_decision: "#0f766e",
  behavior_run: "#6d28d9"
};

function badgeClass(value?: string) {
  const text = String(value ?? "").toLowerCase();
  if (text.includes("fail") || text.includes("block") || text.includes("interrupt")) return "bad";
  if (text.includes("warn") || text.includes("wait") || text.includes("stale")) return "warn";
  if (text.includes("success") || text.includes("complete") || text.includes("ready") || text.includes("actionable")) return "good";
  return "";
}

function truncate(value: string, n = 64) {
  return value.length > n ? `${value.slice(0, n - 1)}...` : value;
}

function briefValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(briefValue).filter(Boolean).join(", ");
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    return briefValue(
      row.summary ?? row.why ?? row.question ?? row.message ?? row.condition_id ?? row.variable_key ??
      row.subject_metric ?? row.type ?? row.tool ?? row.action ?? row.id
    );
  }
  return String(value);
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

type ProcessEvent = {
  id: string;
  kind: string;
  title: string;
  summary: string;
  status?: string;
  timestamp?: string;
  source: "llm" | "kernel" | "runtime" | "artifact" | "issue" | "job";
};

function recordValue(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (value != null && String(value)) return String(value);
  }
  return "";
}

function isVisualArtifact(artifact: ArtifactCard) {
  const kind = String(artifact.kind ?? "").toLowerCase();
  const path = String(artifact.path ?? "").toLowerCase();
  return kind.includes("figure") || /\.(png|jpe?g|gif|svg|webp)$/.test(path);
}

function laneForType(type?: string) {
  const text = String(type ?? "").toLowerCase();
  if (["workspace", "dataset", "metadata", "description", "parameter_set", "analysis_node", "branch"].includes(text)) return "Inputs";
  if (["attempt", "tool_call", "code_cell", "intervention", "diagnosis", "backward_trace", "job"].includes(text)) return "Attempts";
  if (["artifact", "outcome"].includes(text)) return "Artifacts";
  if (["observation", "trigger", "finding", "review_decision", "critic_review", "critic_finding"].includes(text)) return "Observations";
  if (["conclusion", "report"].includes(text)) return "Conclusions";
  return "Observations";
}

function buildFocusSet(edges: AttemptGraph["edges"], focusId: string) {
  const selected = new Set<string>();
  if (!focusId) return selected;
  selected.add(focusId);
  for (let depth = 0; depth < 3; depth += 1) {
    const frontier = new Set(selected);
    for (const edge of edges) {
      if (frontier.has(edge.target_id)) selected.add(edge.source_id);
      if (frontier.has(edge.source_id)) selected.add(edge.target_id);
    }
  }
  return selected;
}

function pickFocusNode(nodes: GraphNode[], selectedNode: string, activeNodeId: string) {
  if (selectedNode) return selectedNode;
  const conclusion = nodes.find((node) => laneForType(node.node_type) === "Conclusions");
  if (conclusion) return conclusion.node_id;
  const observation = [...nodes].reverse().find((node) => laneForType(node.node_type) === "Observations");
  if (observation) return observation.node_id;
  return activeNodeId;
}

function buildProcessEvents(
  view: WorkbenchView | null,
  streamEvents: RuntimeEventCard[] = [],
  streamJobs: Array<Record<string, unknown>> = []
): ProcessEvent[] {
  if (!view) return [];
  const events: ProcessEvent[] = [];
  const workOrder = view.analysis.active_work_order;
  const selectedCapability = workOrder?.selected_capability;
  const runtimeEvents = new Map<string, RuntimeEventCard>();
  for (const item of view.activity.runtime_events ?? []) {
    runtimeEvents.set(item.event_id ?? `${item.event_type}-${runtimeEvents.size}`, item);
  }
  for (const item of streamEvents) {
    runtimeEvents.set(item.event_id ?? `${item.event_type}-${runtimeEvents.size}`, item);
  }
  const jobs = streamJobs.length ? streamJobs : (view.activity.jobs ?? []);
  if (workOrder) {
    events.push({
      id: "llm-work-order",
      kind: "llm_decision",
      source: "llm",
      status: workOrder.mode ?? view.execution_state?.mode,
      title: selectedCapability?.title ?? selectedCapability?.id ?? selectedCapability?.capability_id ?? "Active work order",
      summary: selectedCapability?.next_repair
        ?? workOrder.recommended_actions?.[0]
        ?? "Current bounded decision surface is ready."
    });
  }

  for (const job of jobs) {
    const row = job as Record<string, unknown>;
    events.push({
      id: recordValue(row, ["job_id", "id"]) || `job-${events.length}`,
      kind: recordValue(row, ["job_type", "type"]) || "job",
      source: "job",
      status: recordValue(row, ["status", "state"]),
      title: recordValue(row, ["job_type", "type"]) || "Background job",
      summary: recordValue(row, ["summary", "error", "message", "run_id"])
    });
  }

  for (const item of runtimeEvents.values()) {
    const event = item as RuntimeEventCard;
    events.push({
      id: event.event_id ?? `${event.event_type}-${events.length}`,
      kind: event.card_type ?? event.event_type ?? "runtime_event",
      source: "runtime",
      status: event.card_type,
      title: event.title ?? event.event_type ?? "Runtime event",
      summary: event.summary ?? "",
      timestamp: event.timestamp
    });
  }

  for (const attempt of view.activity.recent_attempts ?? []) {
    events.push({
      id: attempt.attempt_id,
      kind: "code_cell",
      source: "kernel",
      status: attempt.outcome_status || attempt.status,
      title: attempt.title || attempt.attempt_id,
      summary: [
        attempt.capability_ids.join(", "),
        attempt.outcome_summary,
        `${attempt.observations} observations / ${attempt.artifacts} artifacts`
      ].filter(Boolean).join(" | ")
    });
  }

  for (const artifact of view.artifacts.recent ?? []) {
    events.push({
      id: artifact.artifact_id,
      kind: isVisualArtifact(artifact) ? "plot" : artifact.kind || "artifact",
      source: "artifact",
      status: artifact.kind,
      title: artifact.kind || artifact.artifact_id,
      summary: artifact.summary || artifact.path
    });
  }

  for (const issue of view.execution_state?.issues ?? []) {
    events.push({
      id: issue.issue_id,
      kind: issue.kind,
      source: "issue",
      status: issue.severity || issue.status,
      title: issue.kind === "question" ? "Clarification needed" : issue.kind,
      summary: issue.question || issue.summary || issue.suggested_action || ""
    });
  }

  return events.slice(-18);
}

function mergeStreamEvent(prev: RuntimeEventCard[], next: RuntimeEventCard): RuntimeEventCard[] {
  const keyed = new Map<string, RuntimeEventCard>();
  for (const item of [...prev, next]) {
    keyed.set(item.event_id ?? `${item.event_type}-${item.timestamp}-${keyed.size}`, item);
  }
  return Array.from(keyed.values()).slice(-60);
}

export default function App() {
  const [view, setView] = useState<WorkbenchView | null>(null);
  const [graph, setGraph] = useState<AttemptGraph>({ nodes: [], edges: [] });
  const [workspace, setWorkspace] = useState("");
  const [goal, setGoal] = useState("");
  const [answer, setAnswer] = useState("");
  const [selectedNode, setSelectedNode] = useState("");
  const [error, setError] = useState("");
  const [streamEvents, setStreamEvents] = useState<RuntimeEventCard[]>([]);
  const [streamJobs, setStreamJobs] = useState<Array<Record<string, unknown>>>([]);
  const [streamStatus, setStreamStatus] = useState("connecting");

  async function refresh() {
    try {
      const nextView = await getWorkbenchView();
      let nextGraph: AttemptGraph;
      try {
        const nextDerivation = await getDerivationView(selectedNode);
        nextGraph = { nodes: nextDerivation.nodes ?? [], edges: nextDerivation.edges ?? [] };
      } catch {
        nextGraph = await getAttemptGraph();
      }
      setView(nextView);
      setGraph(nextGraph);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!("EventSource" in window)) {
      setStreamStatus("polling");
      return;
    }
    const source = new EventSource("/api/events/stream");
    let refreshTimer = 0;
    const refreshSoon = () => {
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(refresh, 350);
    };
    source.addEventListener("ready", () => {
      setStreamStatus("live");
    });
    source.addEventListener("runtime_event", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as RuntimeEventCard;
        setStreamEvents((prev) => mergeStreamEvent(prev, payload));
        setStreamStatus("live");
        refreshSoon();
      } catch (err) {
        setStreamStatus("polling");
      }
    });
    source.addEventListener("jobs", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as { jobs?: Array<Record<string, unknown>> };
        setStreamJobs(payload.jobs ?? []);
        setStreamStatus("live");
        refreshSoon();
      } catch {
        setStreamStatus("polling");
      }
    });
    source.onerror = () => {
      setStreamStatus("polling");
    };
    return () => {
      window.clearTimeout(refreshTimer);
      source.close();
    };
  }, []);

  async function run(steps: number) {
    await runWorkbench(workspace || "data", goal, steps);
    await refresh();
  }

  async function startAnalysis() {
    await startAgent(workspace || "data", goal);
    await refresh();
  }

  async function continueAnalysis() {
    await continueAgent();
    await refresh();
  }

  async function pauseAnalysis() {
    await pauseAgent();
    await refresh();
  }

  async function sendAnswer() {
    const question = view?.execution_state?.question as RuntimeIssue | undefined;
    const interruptId = question?.kind === "question" && question.issue_id
      ? question.issue_id
      : view?.review.open_interrupts[0]?.interrupt_id;
    if (!interruptId || !answer) return;
    await answerInterrupt(interruptId, answer);
    setAnswer("");
    await refresh();
  }

  const status = view?.status ?? {};
  const executionState = view?.execution_state;
  const openIssueCount = executionState?.issues?.length ?? ((status.triggers_open ?? 0) + (status.interrupts_open ?? 0));

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>Pertura Workbench</h1>
          <div className="subtle">{view?.analysis.domain.name ?? "domain"} / {status.run_id ?? "no run"}</div>
        </div>
        <div className="controls">
          <input value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder="Workspace path" />
          <input value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="Goal" />
          <button className="primary" onClick={startAnalysis}>Start Analysis</button>
          <button className="success" onClick={continueAnalysis}>Continue</button>
          <button onClick={pauseAnalysis}>Pause</button>
          <button onClick={async () => { await generateReport(); await refresh(); }}>Report</button>
          <span className={`chip ${badgeClass(executionState?.mode ?? status.phase ?? status.state)}`}>{executionState?.mode ?? status.phase ?? status.state ?? "idle"}</span>
        </div>
      </header>

      {error && <div className="errorbar">{error}</div>}

      <main className="analysis-layout">
        <aside className="column nav-rail">
          <Panel title="Run">
            <div className="metrics">
              <Metric label="attempts" value={status.attempts ?? 0} />
              <Metric label="observations" value={status.observations ?? 0} />
              <Metric label="artifacts" value={status.artifacts ?? 0} />
              <Metric label="open issues" value={openIssueCount} />
            </div>
            <p className="small strong">{status.goal || "No goal"}</p>
            <p className="small mono">{status.workspace || ""}</p>
            <DeveloperInspector
              run={run}
              step={async () => {
                await stepWorkbench();
                await refresh();
              }}
            />
          </Panel>
          <Panel title="Analysis Graph" fill badge={`${view?.analysis.nodes.length ?? 0} nodes`}>
            <AnalysisNodeList
              nodes={view?.analysis.nodes ?? []}
              activeNodeId={view?.active.node_id ?? ""}
              onSelect={setSelectedNode}
            />
          </Panel>
        </aside>

        <section className="column process-column">
          <LiveProcessStage view={view} streamEvents={streamEvents} streamJobs={streamJobs} streamStatus={streamStatus} />
          <LiveArtifactsStage view={view} />
          <Panel title="Derivation Lanes" fill badge={`${graph.nodes.length} / ${graph.edges.length}`}>
            <AttemptGraphSvg graph={graph} activeNodeId={view?.active.node_id ?? ""} selectedNode={selectedNode} onSelect={setSelectedNode} />
          </Panel>
        </section>

        <aside className="column inspector-rail">
          <Panel title="Review And Interrupts" badge={openIssueCount ? `${openIssueCount} open` : "clear"}>
            <Review view={view} />
            <div className="answer-row">
              <input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="Answer active interrupt" />
              <button className="primary" onClick={sendAnswer}>Send</button>
            </div>
          </Panel>
          <Panel title="Execution State" badge={executionState?.mode ?? "not initialized"}>
            <ExecutionStatePanel state={executionState} />
          </Panel>
          <Panel title="Active Node Contract" badge={view?.active.node_id || "none"}>
            <ActiveContract view={view} />
          </Panel>
          <Panel title="Capability Browser" badge={`${view?.analysis.capabilities_view?.capabilities.length ?? 0} caps`}>
            <CapabilityBrowser
              view={view}
              onToggle={async (capabilityId, enabled) => {
                await toggleCapability(capabilityId, enabled);
                await refresh();
              }}
            />
          </Panel>
          <Panel title="Trace / Rethinking">
            <Rethinking view={view} selectedNode={selectedNode} />
          </Panel>
          <Panel title="Debug Context Surface" fill>
            <ContextSurface view={view} />
          </Panel>
          <Panel title="Report">
            <ReportSummary view={view} />
          </Panel>
        </aside>
      </main>
    </div>
  );
}

function Panel(props: { title: string; badge?: string; fill?: boolean; className?: string; children: ReactNode }) {
  return (
    <section className={`panel ${props.fill ? "fill" : ""} ${props.className ?? ""}`.trim()}>
      <div className="panel-head">
        <div className="panel-title">{props.title}</div>
        {props.badge && <span className="chip">{props.badge}</span>}
      </div>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

function LiveProcessStage(props: {
  view: WorkbenchView | null;
  streamEvents: RuntimeEventCard[];
  streamJobs: Array<Record<string, unknown>>;
  streamStatus: string;
}) {
  const view = props.view;
  const executionState = view?.execution_state;
  const workOrder = view?.analysis.active_work_order;
  const task = executionState?.current_task ?? {};
  const events = buildProcessEvents(view, props.streamEvents, props.streamJobs);
  const lastDelta = workOrder?.last_attempt_delta ?? {};
  const execution = (lastDelta.execution ?? {}) as Record<string, unknown>;
  const jobs = props.streamJobs.length ? props.streamJobs : (view?.activity.jobs ?? []);
  const activeJobs = jobs.filter((job) => {
    const status = String((job as Record<string, unknown>).status ?? "");
    return status === "queued" || status === "running";
  });

  return (
    <Panel
      title="Live Analysis Process"
      badge={executionState?.mode ?? workOrder?.mode ?? "not initialized"}
      className="process-panel"
    >
      <div className="process-hero">
        <div className="process-hero-main">
          <div className="process-kicker">Current Step</div>
          <h2>{task.title || workOrder?.active_node?.title || task.node_id || "Waiting for analysis"}</h2>
          <p>{task.goal || view?.status.goal || workOrder?.run_goal || "Start an analysis to watch the run unfold."}</p>
        </div>
        <div className="process-hero-meta">
          <span className={`chip ${badgeClass(executionState?.mode)}`}>{executionState?.mode ?? "idle"}</span>
          <span className="chip">{view?.active.branch_id || workOrder?.branch_id || "main"}</span>
          <span className={`chip ${props.streamStatus === "live" ? "good" : "warn"}`}>
            {props.streamStatus === "live" ? "event stream" : "polling"}
          </span>
          <span className="chip">{activeJobs.length ? `${activeJobs.length} live job` : "no live jobs"}</span>
        </div>
      </div>
      <div className="process-stage-grid">
        <div className="process-stage-main">
          <ThinkingPath view={view} />
          <ProcessTimeline events={events} />
          <AgentTerminal view={view} streamEvents={props.streamEvents} jobs={jobs} />
        </div>
        <div className="process-stage-side">
          <div className="console-card">
            <div className="console-head">
              <span>Kernel / Code</span>
              <span className={`badge ${badgeClass(String(execution.returncode ?? executionState?.mode ?? ""))}`}>
                {execution.returncode != null ? `return ${String(execution.returncode)}` : executionState?.mode ?? "idle"}
              </span>
            </div>
            <div className="console-grid">
              <Metric label="attempts" value={view?.status.attempts ?? 0} />
              <Metric label="obs" value={view?.status.observations ?? 0} />
              <Metric label="artifacts" value={view?.status.artifacts ?? 0} />
              <Metric label="issues" value={executionState?.issues.length ?? 0} />
            </div>
            <KernelRuntimeRefs delta={lastDelta} />
          </div>
          <JobStack jobs={jobs} />
        </div>
      </div>
    </Panel>
  );
}

function ProcessTimeline(props: { events: ProcessEvent[] }) {
  if (!props.events.length) {
    return <p className="small muted">No runtime activity yet.</p>;
  }
  return (
    <div className="process-timeline">
      {props.events.map((event) => (
        <div className={`process-event ${event.source}`} key={`${event.source}-${event.id}`}>
          <div className="process-dot" />
          <div className="process-event-body">
            <div className="process-event-top">
              <span className="process-event-kind">{event.kind.replace(/_/g, " ")}</span>
              {event.status && <span className={`badge ${badgeClass(event.status)}`}>{truncate(event.status, 28)}</span>}
            </div>
            <div className="process-event-title">{truncate(event.title, 92)}</div>
            {event.summary && <div className="process-event-summary">{truncate(event.summary, 180)}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentTerminal(props: {
  view: WorkbenchView | null;
  streamEvents: RuntimeEventCard[];
  jobs: Array<Record<string, unknown>>;
}) {
  const view = props.view;
  const workOrder = view?.analysis.active_work_order;
  const selected = workOrder?.selected_capability;
  const attempts = view?.activity.recent_attempts ?? [];
  const latestAttempt = attempts[attempts.length - 1];
  const events = [
    ...(view?.activity.runtime_events ?? []),
    ...props.streamEvents
  ].slice(-8);
  const visibleTools = (workOrder?.allowed_tools ?? []).slice(0, 8);
  const lines = [
    `$ mode ${view?.execution_state?.mode ?? "idle"} run=${view?.run_id ?? "none"}`,
    `$ goal ${truncate(view?.status.goal || workOrder?.run_goal || "No goal recorded", 140)}`,
    selected
      ? `$ strategy capability=${selected.id ?? selected.capability_id ?? "unknown"} ready=${String(Boolean(selected.ready))}`
      : "$ strategy waiting_for_capability_selection",
    selected?.next_repair ? `$ repair_hint ${truncate(selected.next_repair, 160)}` : "",
    visibleTools.length ? `$ tools ${visibleTools.join(" ")}` : "$ tools none_visible",
    ...props.jobs.slice(0, 3).map((job) => {
      const status = recordValue(job, ["status", "state"]) || "pending";
      const type = recordValue(job, ["job_type", "type"]) || "job";
      const id = recordValue(job, ["job_id", "id"]);
      return `$ job ${type} ${status}${id ? ` id=${id}` : ""}`;
    }),
    latestAttempt ? `$ attempt ${latestAttempt.attempt_id} ${latestAttempt.outcome_status || latestAttempt.status}` : "",
    latestAttempt?.execution
      ? `$ result returncode=${String(latestAttempt.execution.returncode ?? "?")} stdout_chars=${latestAttempt.execution.stdout_chars ?? 0} obs=${latestAttempt.execution.observations_registered ?? latestAttempt.observations}`
      : "",
    latestAttempt?.execution?.stderr_tail
      ? `$ stderr ${truncate(latestAttempt.execution.stderr_tail.replace(/\s+/g, " "), 220)}`
      : "",
    ...events.map((event) => {
      if (event.card_type === "execution_output") {
        const stream = String(event.title || "").toLowerCase().includes("stderr") ? "stderr" : "stdout";
        return `$ ${stream} ${truncate((event.summary || "").replace(/\s+$/g, ""), 180)}`;
      }
      return `$ event ${event.card_type ?? event.event_type ?? "runtime"} ${truncate(event.title ?? "", 80)} ${event.summary ? `:: ${truncate(event.summary, 120)}` : ""}`;
    }),
  ].filter(Boolean);
  const code = latestAttempt?.code_preview?.trim() ?? "";
  return (
    <div className="agent-terminal">
      <div className="terminal-head">
        <span>Agent Terminal Strategy</span>
        <span className="badge">read-only</span>
      </div>
      <pre className="terminal-log">{lines.join("\n")}</pre>
      {code && (
        <details className="terminal-code">
          <summary>Latest code cell</summary>
          <pre>{code}</pre>
        </details>
      )}
    </div>
  );
}

function KernelRuntimeRefs(props: { delta: Record<string, unknown> }) {
  const refs = Array.isArray(props.delta.runtime_refs) ? props.delta.runtime_refs : [];
  const observations = props.delta.observations_registered;
  if (!refs.length && observations == null) {
    return <pre className="console-output">No kernel refs captured for the latest attempt yet.</pre>;
  }
  return (
    <pre className="console-output">
      {[
        observations != null ? `observations_registered=${String(observations)}` : "",
        ...refs.slice(0, 10).map((item) => `ref ${String(item)}`)
      ].filter(Boolean).join("\n")}
    </pre>
  );
}

function JobStack(props: { jobs: Array<Record<string, unknown>> }) {
  const jobs = props.jobs.slice(0, 4);
  if (!jobs.length) return <div className="job-stack empty">No queued jobs.</div>;
  return (
    <div className="job-stack">
      {jobs.map((job, index) => {
        const id = recordValue(job, ["job_id", "id"]) || `job-${index}`;
        const status = recordValue(job, ["status", "state"]);
        return (
          <div className="job-row" key={id}>
            <span>{recordValue(job, ["job_type", "type"]) || "job"}</span>
            <span className={`badge ${badgeClass(status)}`}>{status || "pending"}</span>
          </div>
        );
      })}
    </div>
  );
}

function LiveArtifactsStage(props: { view: WorkbenchView | null }) {
  const artifacts = props.view?.artifacts.recent ?? [];
  return (
    <Panel title="Generated Artifacts / Plots" badge={`${artifacts.length} recent`} className="artifact-stage">
      {artifacts.length ? (
        <div className="artifact-strip">
          {artifacts.slice().reverse().slice(0, 6).map((artifact) => (
            <ArtifactPreviewCard artifact={artifact} key={artifact.artifact_id} />
          ))}
        </div>
      ) : (
        <p className="small muted">Plots, tables, and registered outputs will appear here as the kernel produces them.</p>
      )}
    </Panel>
  );
}

function ArtifactPreviewCard(props: { artifact: ArtifactCard }) {
  const artifact = props.artifact;
  const visual = isVisualArtifact(artifact);
  return (
    <div className={`artifact-preview ${visual ? "visual" : ""}`}>
      {visual && artifact.file_url ? (
        <img src={artifact.file_url} alt={artifact.summary || artifact.kind || artifact.artifact_id} />
      ) : (
        <div className="artifact-icon">{artifact.kind || "artifact"}</div>
      )}
      <div className="artifact-preview-text">
        <div className="item-title">{truncate(artifact.kind || artifact.artifact_id, 42)}</div>
        <div className="item-sub">{truncate(artifact.summary || artifact.path || artifact.artifact_id, 92)}</div>
      </div>
    </div>
  );
}

function ThinkingPath(props: { view: WorkbenchView | null }) {
  const view = props.view;
  const workOrder = view?.analysis.active_work_order;
  const executionState = view?.execution_state;
  if (!workOrder && !executionState) return <p className="small muted">Not initialized.</p>;

  const progress = workOrder?.node_progress ?? {};
  const selectedCapability = workOrder?.selected_capability;
  const missing = (selectedCapability?.missing_inputs?.length
    ? selectedCapability.missing_inputs
    : progress.missing_completion ?? []
  ).map(briefValue).filter(Boolean);
  const memorySummary = workOrder?.observation_memory?.summary ?? {};
  const coverageLabels = typeof memorySummary.coverage_labels === "object" && memorySummary.coverage_labels
    ? memorySummary.coverage_labels as Record<string, unknown>
    : {};
  const issueRows = [
    ...(workOrder?.open_issues?.runtime_issues ?? []),
    ...(workOrder?.open_issues?.triggers ?? []),
    ...(workOrder?.open_issues?.findings ?? []),
    ...(workOrder?.open_issues?.audit_next_actions ?? [])
  ];
  const issueText = issueRows.map(briefValue).find(Boolean);
  const rethink = workOrder?.rethinking;
  const nextAction = workOrder?.recommended_actions?.[0]
    ?? executionState?.recommended_actions?.[0]
    ?? selectedCapability?.next_repair
    ?? "inspect current node contract";
  const capabilityTitle = selectedCapability?.title
    ?? selectedCapability?.id
    ?? selectedCapability?.capability_id
    ?? "No selected capability";
  const packageHint = selectedCapability?.packages_hint
    ?? [...(selectedCapability?.packages ?? []), ...(selectedCapability?.functions ?? [])].slice(0, 4).join(", ");
  const activeNode = (workOrder?.active_node ?? executionState?.current_task ?? {}) as {
    id?: string;
    node_id?: string;
    title?: string;
    purpose?: string;
  };

  const stages = [
    {
      label: "Goal",
      title: workOrder?.run_goal || view?.status.goal || "No goal recorded",
      body: view?.status.workspace || workOrder?.workspace?.path || "",
      badges: [workOrder?.branch_id || view?.active.branch_id || "main"].filter(Boolean)
    },
    {
      label: "Focus",
      title: activeNode.title || activeNode.id || activeNode.node_id || "Run",
      body: activeNode.purpose || executionState?.current_task?.purpose || "",
      badges: [
        `${progress.attempts ?? 0} attempts`,
        `${progress.observations ?? 0} obs`,
        `${progress.artifacts ?? 0} artifacts`,
        progress.completed ? "complete" : "open"
      ]
    },
    {
      label: "Capability",
      title: capabilityTitle,
      body: selectedCapability?.next_repair || selectedCapability?.description || "",
      badges: [
        selectedCapability?.ready ? "ready" : missing.length ? "needs inputs" : "available",
        ...missing.slice(0, 2).map((item) => `missing: ${item}`),
        packageHint
      ].filter(Boolean)
    },
    {
      label: "Evidence",
      title: rethink?.summary || issueText || "Observation memory",
      body: Object.keys(coverageLabels).length
        ? Object.entries(coverageLabels).slice(0, 4).map(([key, value]) => `${key}: ${briefValue(value)}`).join(" / ")
        : briefValue(workOrder?.observation_memory?.needs_review?.[0]) || "",
      badges: [
        `${numberValue(memorySummary.variables ?? memorySummary.variable_count)} vars`,
        `${numberValue(memorySummary.strict_conflicts ?? memorySummary.conflicts)} conflicts`,
        `${issueRows.length} issues`
      ]
    },
    {
      label: "Next",
      title: nextAction,
      body: (workOrder?.allowed_tools ?? []).slice(0, 4).join(", "),
      badges: [
        workOrder?.mode ?? executionState?.mode ?? "normal",
        `${workOrder?.allowed_tools?.length ?? 0} tools`
      ]
    }
  ];

  return (
    <div className="thinking-path">
      <div className="thought-flow">
        {stages.map((stage, index) => (
          <div className="thought-stage" key={`${stage.label}-${index}`}>
            <div className="thought-index">{index + 1}</div>
            <div className="thought-content">
              <div className="thought-label">{stage.label}</div>
              <div className="thought-title">{truncate(String(stage.title || stage.label), 86)}</div>
              {stage.body && <div className="thought-body">{truncate(String(stage.body), 132)}</div>}
              <div className="badge-row">
                {stage.badges.slice(0, 5).map((badge, badgeIndex) => (
                  <span className={`badge ${badgeClass(String(badge))}`} key={`${stage.label}-${badgeIndex}`}>{truncate(String(badge), 38)}</span>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="thought-footer">
        {(workOrder?.recommended_actions ?? []).slice(0, 3).map((action, index) => (
          <span className="thought-next" key={`${action}-${index}`}>{action}</span>
        ))}
      </div>
    </div>
  );
}

function Metric(props: { label: string; value: number }) {
  return <div className="metric"><b>{props.value}</b><span>{props.label}</span></div>;
}

function DeveloperInspector(props: { run: (steps: number) => Promise<void>; step: () => Promise<void> }) {
  return (
    <details className="dev-inspector">
      <summary>Developer Inspector</summary>
      <div className="dev-controls">
        <button onClick={() => props.run(0)}>Init</button>
        <button onClick={props.step}>Step</button>
        <button onClick={() => props.run(5)}>Run 5</button>
      </div>
    </details>
  );
}

function ExecutionStatePanel(props: { state?: ExecutionState }) {
  const state = props.state;
  if (!state) return <p className="small muted">Not initialized.</p>;
  const task = state.current_task ?? {};
  const issues = state.issues ?? [];
  const question = state.question as RuntimeIssue | undefined;
  const evidence = state.evidence_summary ?? {};
  const recentAttempts = evidence.recent_attempts ?? [];
  const recentArtifacts = evidence.recent_artifacts ?? [];
  return (
    <div className="execution-state">
      <div className="item">
        <div className="item-title">{task.title || task.node_id || "Run"} <span className={`badge ${badgeClass(state.mode)}`}>{state.mode}</span></div>
        <div className="item-sub">{task.goal || task.purpose || ""}</div>
      </div>
      <div className="metrics compact">
        <Metric label="attempts" value={evidence.attempts ?? 0} />
        <Metric label="observations" value={evidence.observations ?? 0} />
        <Metric label="artifacts" value={evidence.artifacts ?? 0} />
        <Metric label="issues" value={issues.length} />
      </div>
      {question?.kind === "question" && (
        <div className="item issue question">
          <div className="item-title">Question <span className="badge bad">{question.source || "human"}</span></div>
          <div className="item-sub">{question.question || question.summary}</div>
        </div>
      )}
      {issues.filter((item) => item.kind !== "question").slice(0, 4).map((issue) => (
        <div className="item issue" key={issue.issue_id}>
          <div className="item-title">{issue.kind} <span className={`badge ${badgeClass(issue.severity)}`}>{issue.severity || "open"}</span></div>
          <div className="item-sub">{issue.summary}</div>
        </div>
      ))}
      <div className="recent-evidence">
        {recentAttempts.slice(0, 3).map((attempt) => (
          <span className="badge" key={attempt.attempt_id}>{attempt.title || attempt.attempt_id}</span>
        ))}
        {recentArtifacts.slice(0, 3).map((artifact) => (
          <span className="badge good" key={artifact.artifact_id}>{artifact.kind || artifact.artifact_id}</span>
        ))}
      </div>
    </div>
  );
}

function AnalysisNodeList(props: { nodes: WorkbenchNode[]; activeNodeId: string; onSelect: (id: string) => void }) {
  if (!props.nodes.length) return <p className="small muted">No analysis graph loaded.</p>;
  return (
    <div className="node-list">
      {props.nodes.map((node) => (
        <button
          key={node.node_id}
          className={`node-card ${node.node_id === props.activeNodeId ? "active" : ""}`}
          onClick={() => props.onSelect(node.node_id)}
        >
          <span className="node-title">{node.title || node.node_id}</span>
          <span className="node-purpose">{node.purpose}</span>
          <span className="badge-row">
            {node.allowed_capabilities.slice(0, 4).map((cap) => <span className="badge" key={cap}>{cap}</span>)}
          </span>
          <span className="node-purpose">{node.hard_conditions} hard checks / {node.rubric_only_conditions} rubric</span>
        </button>
      ))}
    </div>
  );
}

function ActiveContract(props: { view: WorkbenchView | null }) {
  const contract = props.view?.analysis.active_node_contract;
  const caps = contract?.capabilities ?? [];
  const required = contract?.inputs?.required ?? contract?.runtime?.missing_inputs ?? [];
  const ready = contract?.runtime?.ready_capabilities ?? [];
  return (
    <div>
      <div className="item">
        <div className="item-title">{contract?.node?.title ?? contract?.node?.id ?? "No active node"}</div>
        <div className="item-sub">{contract?.node?.purpose ?? ""}</div>
      </div>
      <div className="badge-row">
        {caps.slice(0, 8).map((cap) => <span className="badge violet" key={cap.id ?? cap.capability_id}>{cap.id ?? cap.capability_id}</span>)}
      </div>
      <p className="small"><b>Inputs:</b> {required.length ? required.join(", ") : "none"}</p>
      <p className="small"><b>Ready:</b> {ready.length ? ready.join(", ") : "no ready capability reported"}</p>
    </div>
  );
}

function CapabilityBrowser(props: {
  view: WorkbenchView | null;
  onToggle: (capabilityId: string, enabled: boolean) => Promise<void>;
}) {
  const capabilityView = props.view?.analysis.capabilities_view;
  const caps = capabilityView?.capabilities ?? [];
  if (!caps.length) return <p className="small muted">No capabilities loaded.</p>;
  return (
    <div className="capability-browser">
      <ToolSurface surface={capabilityView?.llm_tool_surface} />
      <div className="capability-list">
        {caps.slice(0, 16).map((cap) => (
          <CapabilityRow
            cap={cap}
            key={cap.capability_id ?? cap.id}
            onToggle={props.onToggle}
          />
        ))}
      </div>
    </div>
  );
}

function ToolSurface(props: { surface?: LlmToolSurface }) {
  const surface = props.surface;
  const visible = surface?.visible_tools ?? [];
  const hidden = surface?.hidden_tools ?? [];
  const hiddenReasons = surface?.summary?.hidden_reasons ?? {};
  return (
    <div className="tool-surface">
      <div className="tool-surface-head">
        <div>
          <div className="item-title">LLM visible tools this turn</div>
          <div className="item-sub">{visible.length} visible / {hidden.length} hidden by current scope</div>
        </div>
        <span className="badge violet">{surface?.surface_type ?? "scoped_llm_tools"}</span>
      </div>
      <div className="tool-chip-row">
        {visible.slice(0, 12).map((tool) => (
          <span className="badge good" title={tool.description} key={tool.tool_id}>{tool.tool_id}</span>
        ))}
      </div>
      {Object.keys(hiddenReasons).length > 0 && (
        <div className="tool-reasons">
          {Object.entries(hiddenReasons).slice(0, 5).map(([reason, count]) => (
            <span className="badge warn" key={reason}>{reason}: {count}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function CapabilityRow(props: {
  cap: CapabilityCard;
  onToggle: (capabilityId: string, enabled: boolean) => Promise<void>;
}) {
  const cap = props.cap;
  const id = cap.capability_id ?? cap.id ?? "";
  const status = !cap.enabled ? "disabled" : cap.llm_actionable ? "actionable" : cap.ready ? "ready" : cap.allowed_in_active_node ? "available" : "other node";
  const missing = cap.missing_inputs ?? [];
  const unavailable = cap.why_unavailable ?? [];
  const visibleTools = (cap.tool_visibility ?? []).filter((tool) => tool.visible_to_llm);
  const hiddenTools = (cap.tool_visibility ?? []).filter((tool) => !tool.visible_to_llm);
  const detail = missing.length
    ? `missing: ${missing.join(", ")}`
    : unavailable.length
      ? unavailable.join(", ")
      : (cap.expected_observations ?? []).slice(0, 3).join(", ");
  return (
    <div className="capability-row">
      <div className="cap-main">
        <div className="item-title">{cap.title || id}</div>
        <div className="item-sub">{cap.description || id}</div>
        <div className="badge-row">
          <span className={`badge ${badgeClass(status)}`}>{status}</span>
          <span className="badge">{cap.permission_tier || "local_read"}</span>
          {cap.backend_hint && <span className="badge">{cap.backend_hint}</span>}
          {visibleTools.map((tool) => <ToolBadge key={tool.tool_id} tool={tool} />)}
          {hiddenTools.map((tool) => <ToolBadge key={tool.tool_id} tool={tool} />)}
        </div>
      </div>
      <div className="cap-side">
        <div className="item-sub">{detail || "no contract detail"}</div>
        {id && (
          <button
            className="small-button"
            onClick={() => props.onToggle(id, !cap.enabled)}
          >
            {cap.enabled ? "Disable" : "Enable"}
          </button>
        )}
      </div>
    </div>
  );
}

function ToolBadge(props: { tool: ToolVisibilityCard }) {
  const hidden = !props.tool.visible_to_llm;
  const title = hidden
    ? `${props.tool.tool_id}: ${(props.tool.why_hidden ?? []).join(", ")}`
    : props.tool.description || props.tool.tool_id;
  return (
    <span className={`badge ${hidden ? "warn" : "good"}`} title={title}>
      {hidden ? "hidden:" : "tool:"}{props.tool.tool_id}
    </span>
  );
}

function AttemptGraphSvg(props: {
  graph: AttemptGraph;
  activeNodeId: string;
  selectedNode: string;
  onSelect: (id: string) => void;
}) {
  const allNodes = props.graph.nodes;
  const allEdges = props.graph.edges;
  const focusId = pickFocusNode(allNodes, props.selectedNode, props.activeNodeId);
  const focusSet = buildFocusSet(allEdges, focusId);
  let nodes = focusSet.size ? allNodes.filter((node) => focusSet.has(node.node_id)) : allNodes;
  if (nodes.length < 8) nodes = allNodes;
  nodes = nodes.slice(0, 55);
  const nodeIds = new Set(nodes.map((node) => node.node_id));
  const edges = allEdges.filter((edge) => nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id)).slice(0, 90);
  const layout = useMemo(() => {
    const lanes = ["Inputs", "Attempts", "Artifacts", "Observations", "Conclusions"];
    const byLane = new Map<string, GraphNode[]>();
    for (const node of nodes) {
      const lane = laneForType(node.node_type);
      byLane.set(lane, [...(byLane.get(lane) ?? []), node]);
    }
    const positions = new Map<string, { x: number; y: number }>();
    const width = 980;
    const laneWidth = width / lanes.length;
    lanes.forEach((lane, laneIndex) => {
      const list = (byLane.get(lane) ?? []).slice(0, 9);
      const x = laneWidth * laneIndex + laneWidth / 2;
      list.forEach((node, itemIndex) => positions.set(node.node_id, { x, y: 76 + itemIndex * 64 }));
    });
    const maxLane = Math.max(...lanes.map((lane) => byLane.get(lane)?.length ?? 0), 4);
    return { lanes, laneWidth, positions, width, height: Math.max(390, 92 + 64 * Math.min(maxLane, 9)) };
  }, [nodes]);

  return (
    <svg className="graph" viewBox={`0 0 ${layout.width} ${layout.height}`}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
          <path d="M0,0 L0,6 L7,3 z" fill="#9aa4b2" />
        </marker>
      </defs>
      {layout.lanes.map((lane, index) => (
        <g key={lane}>
          <rect className="lane-bg" x={index * layout.laneWidth + 8} y={16} width={layout.laneWidth - 16} height={layout.height - 32} rx={8} />
          <text className="lane-label" x={index * layout.laneWidth + 22} y={40}>{lane}</text>
        </g>
      ))}
      {edges.map((edge) => {
        const a = layout.positions.get(edge.source_id);
        const b = layout.positions.get(edge.target_id);
        if (!a || !b) return null;
        const highlighted = edge.source_id === focusId || edge.target_id === focusId || (focusSet.has(edge.source_id) && focusSet.has(edge.target_id));
        const suspicious = /limit|contradict|trigger|finding|block|stale/i.test(edge.edge_type ?? "");
        return <path key={`${edge.source_id}-${edge.target_id}-${edge.edge_type}`} className={`graph-edge ${highlighted ? "highlight" : ""} ${suspicious ? "suspicious" : ""}`} d={`M${a.x + 68},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x - 68},${b.y}`} />;
      })}
      {nodes.map((node) => {
        const p = layout.positions.get(node.node_id);
        if (!p) return null;
        const active = node.node_id === props.activeNodeId || node.node_id === props.selectedNode || node.node_id === focusId;
        const dim = focusSet.size > 0 && !focusSet.has(node.node_id);
        return (
          <g key={node.node_id} className={`graph-node ${active ? "active" : ""} ${dim ? "dim" : ""}`} onClick={() => props.onSelect(node.node_id)}>
            <rect x={p.x - 76} y={p.y - 24} rx={7} width={152} height={48} />
            <circle cx={p.x - 61} cy={p.y - 7} r={5} fill={typeColors[node.node_type] ?? "#667085"} />
            <text className="type-label" x={p.x - 50} y={p.y - 4}>{truncate(node.node_type || "node", 13)}</text>
            <text x={p.x - 61} y={p.y + 13}>{truncate(node.label ?? node.node_id, 24)}</text>
          </g>
        );
      })}
    </svg>
  );
}

function RecentAttempts(props: { view: WorkbenchView | null }) {
  const attempts = props.view?.activity.recent_attempts ?? [];
  if (!attempts.length) return <p className="small muted">No attempts yet.</p>;
  return (
    <>
      {attempts.map((attempt) => (
        <div className="item" key={attempt.attempt_id}>
          <div className="item-title">{attempt.title || attempt.attempt_id} <span className={`badge ${badgeClass(attempt.outcome_status || attempt.status)}`}>{attempt.outcome_status || attempt.status}</span></div>
          <div className="item-sub">{attempt.analysis_node_id} / {attempt.capability_ids.join(", ")}</div>
          <div className="item-sub">{attempt.observations} obs / {attempt.artifacts} artifacts</div>
        </div>
      ))}
    </>
  );
}

function Artifacts(props: { view: WorkbenchView | null }) {
  const artifacts = props.view?.artifacts.recent ?? [];
  if (!artifacts.length) return <p className="small muted">No artifacts yet.</p>;
  return (
    <>
      {artifacts.map((artifact) => (
        <div className="item" key={artifact.artifact_id}>
          <div className="item-title">{artifact.kind || "artifact"} <span className="badge">{artifact.artifact_id}</span></div>
          <div className="item-sub">{artifact.summary || artifact.path}</div>
        </div>
      ))}
    </>
  );
}

function Review(props: { view: WorkbenchView | null }) {
  const issues = props.view?.execution_state?.issues ?? [];
  const question = issues.find((item) => item.kind === "question");
  const rows = issues.filter((item) => item.kind !== "question");
  return (
    <>
      {question ? (
        <div className="item">
          <div className="item-title">{question.source || "question"}</div>
          <div className="item-sub">{question.question || question.summary}</div>
        </div>
      ) : <p className="small muted">No question.</p>}
      {rows.slice(0, 8).map((row, index) => (
        <div className="item" key={`${row.issue_id}-${index}`}>
          <div className="item-title">{row.kind} <span className={`badge ${badgeClass(row.severity)}`}>{row.severity || "open"}</span></div>
          <div className="item-sub">{row.summary}</div>
        </div>
      ))}
    </>
  );
}

function Rethinking(props: { view: WorkbenchView | null; selectedNode: string }) {
  const rethinking = props.view?.review.rethinking;
  const actions = rethinking?.recommended_actions ?? [];
  return (
    <div>
      <p className="small strong">{props.selectedNode || props.view?.active.node_id || "No node selected"}</p>
      <p className="small">{rethinking?.summary || "No rethinking plan available."}</p>
      {actions.slice(0, 5).map((action, index) => (
        <div className="item" key={index}>
          <div className="item-title">{String(action.tool ?? "action")}</div>
          <div className="item-sub">{String(action.why ?? action.reason ?? "")}</div>
        </div>
      ))}
    </div>
  );
}

function ContextSurface(props: { view: WorkbenchView | null }) {
  const [debugContext, setDebugContext] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const context = debugContext ?? props.view?.agent_context ?? {};
  const hasDebugContext = Boolean(context.view_type || context.purpose);
  const compact = {
    view_type: context.view_type,
    purpose: context.purpose,
    llm_tool_surface: props.view?.analysis.capabilities_view?.llm_tool_surface?.summary,
    audit_preview: context.audit_preview,
    trace_driven_rethinking: context.trace_driven_rethinking,
    affordances: Array.isArray(context.affordances) ? context.affordances.slice(0, 6) : []
  };
  async function loadDebugContext() {
    setLoading(true);
    setError("");
    try {
      setDebugContext(await getContextReview());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }
  return (
    <div>
      {!hasDebugContext && (
        <div className="debug-load">
          <p className="small muted">Debug context is loaded on demand.</p>
          <button className="small-button" onClick={loadDebugContext} disabled={loading}>
            {loading ? "Loading" : "Load Debug"}
          </button>
          {error && <p className="small bad-text">{error}</p>}
        </div>
      )}
      {hasDebugContext && <pre className="context-json">{JSON.stringify(compact, null, 2)}</pre>}
    </div>
  );
}

function ReportSummary(props: { view: WorkbenchView | null }) {
  const report = props.view?.report;
  return (
    <div>
      <p className="small"><b>{report?.observation_count ?? 0}</b> observations / <b>{report?.artifact_count ?? 0}</b> artifacts</p>
      {(report?.conclusions ?? []).map((conclusion) => (
        <div className="item" key={conclusion.conclusion_id}>
          <div className="item-title"><span className="badge">{conclusion.grade}</span> {conclusion.conclusion_id}</div>
          <div className="item-sub">{conclusion.text}</div>
        </div>
      ))}
    </div>
  );
}
