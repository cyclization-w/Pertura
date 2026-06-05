import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  answerInterrupt,
  generateReport,
  getAttemptGraph,
  getWorkbenchView,
  runWorkbench,
  stepWorkbench
} from "./api";
import type { AttemptGraph, GraphNode, WorkbenchNode, WorkbenchView } from "./types";

const typeColors: Record<string, string> = {
  workspace: "#667085",
  analysis_node: "#6d28d9",
  branch: "#2563eb",
  attempt: "#15803d",
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
  if (text.includes("success") || text.includes("complete") || text.includes("ready")) return "good";
  return "";
}

function truncate(value: string, n = 64) {
  return value.length > n ? `${value.slice(0, n - 1)}...` : value;
}

export default function App() {
  const [view, setView] = useState<WorkbenchView | null>(null);
  const [graph, setGraph] = useState<AttemptGraph>({ nodes: [], edges: [] });
  const [workspace, setWorkspace] = useState("");
  const [goal, setGoal] = useState("");
  const [answer, setAnswer] = useState("");
  const [selectedNode, setSelectedNode] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [nextView, nextGraph] = await Promise.all([getWorkbenchView(), getAttemptGraph()]);
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

  async function run(steps: number) {
    await runWorkbench(workspace || "data", goal, steps);
    await refresh();
  }

  async function sendAnswer() {
    const interrupt = view?.review.open_interrupts[0];
    if (!interrupt?.interrupt_id || !answer) return;
    await answerInterrupt(interrupt.interrupt_id, answer);
    setAnswer("");
    await refresh();
  }

  const status = view?.status ?? {};
  const openIssueCount = (status.triggers_open ?? 0) + (status.interrupts_open ?? 0);

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
          <button className="primary" onClick={() => run(0)}>Init</button>
          <button onClick={async () => { await stepWorkbench(); await refresh(); }}>Step</button>
          <button className="success" onClick={() => run(5)}>Run 5</button>
          <button onClick={async () => { await generateReport(); await refresh(); }}>Report</button>
          <span className={`chip ${badgeClass(status.phase ?? status.state)}`}>{status.phase ?? status.state ?? "idle"}</span>
        </div>
      </header>

      {error && <div className="errorbar">{error}</div>}

      <main className="layout">
        <aside className="column">
          <Panel title="Run">
            <div className="metrics">
              <Metric label="attempts" value={status.attempts ?? 0} />
              <Metric label="observations" value={status.observations ?? 0} />
              <Metric label="artifacts" value={status.artifacts ?? 0} />
              <Metric label="open issues" value={openIssueCount} />
            </div>
            <p className="small strong">{status.goal || "No goal"}</p>
            <p className="small mono">{status.workspace || ""}</p>
          </Panel>
          <Panel title="Analysis Graph" fill badge={`${view?.analysis.nodes.length ?? 0} nodes`}>
            <AnalysisNodeList
              nodes={view?.analysis.nodes ?? []}
              activeNodeId={view?.active.node_id ?? ""}
              onSelect={setSelectedNode}
            />
          </Panel>
        </aside>

        <section className="column center">
          <Panel title="Active Node Contract" badge={view?.active.node_id || "none"}>
            <ActiveContract view={view} />
          </Panel>
          <Panel title="Attempt Graph" fill badge={`${graph.nodes.length} / ${graph.edges.length}`}>
            <AttemptGraphSvg graph={graph} activeNodeId={view?.active.node_id ?? ""} selectedNode={selectedNode} onSelect={setSelectedNode} />
          </Panel>
          <div className="split">
            <Panel title="Recent Attempts">
              <RecentAttempts view={view} />
            </Panel>
            <Panel title="Artifacts">
              <Artifacts view={view} />
            </Panel>
          </div>
        </section>

        <aside className="column right">
          <Panel title="Review And Interrupts" badge={openIssueCount ? `${openIssueCount} open` : "clear"}>
            <Review view={view} />
            <div className="answer-row">
              <input value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="Answer active interrupt" />
              <button className="primary" onClick={sendAnswer}>Send</button>
            </div>
          </Panel>
          <Panel title="Trace / Rethinking">
            <Rethinking view={view} selectedNode={selectedNode} />
          </Panel>
          <Panel title="LLM Context Surface" fill>
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

function Panel(props: { title: string; badge?: string; fill?: boolean; children: ReactNode }) {
  return (
    <section className={`panel ${props.fill ? "fill" : ""}`}>
      <div className="panel-head">
        <div className="panel-title">{props.title}</div>
        {props.badge && <span className="chip">{props.badge}</span>}
      </div>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

function Metric(props: { label: string; value: number }) {
  return <div className="metric"><b>{props.value}</b><span>{props.label}</span></div>;
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

function AttemptGraphSvg(props: {
  graph: AttemptGraph;
  activeNodeId: string;
  selectedNode: string;
  onSelect: (id: string) => void;
}) {
  const nodes = props.graph.nodes.slice(0, 90);
  const nodeIds = new Set(nodes.map((node) => node.node_id));
  const edges = props.graph.edges.filter((edge) => nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id)).slice(0, 160);
  const layout = useMemo(() => {
    const byType = new Map<string, GraphNode[]>();
    for (const node of nodes) {
      const type = node.node_type || "other";
      byType.set(type, [...(byType.get(type) ?? []), node]);
    }
    const types = [...byType.keys()];
    const positions = new Map<string, { x: number; y: number }>();
    types.forEach((type, typeIndex) => {
      const list = byType.get(type) ?? [];
      const x = 72 + typeIndex * Math.max(142, 860 / Math.max(types.length, 1));
      list.forEach((node, itemIndex) => positions.set(node.node_id, { x, y: 42 + itemIndex * 56 }));
    });
    return { positions, width: Math.max(980, 150 * Math.max(types.length, 1)), height: Math.max(360, 70 * Math.max(...types.map((type) => byType.get(type)?.length ?? 0), 4)) };
  }, [nodes]);

  return (
    <svg className="graph" viewBox={`0 0 ${layout.width} ${layout.height}`}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
          <path d="M0,0 L0,6 L7,3 z" fill="#9aa4b2" />
        </marker>
      </defs>
      {edges.map((edge) => {
        const a = layout.positions.get(edge.source_id);
        const b = layout.positions.get(edge.target_id);
        if (!a || !b) return null;
        return <path key={`${edge.source_id}-${edge.target_id}-${edge.edge_type}`} className="graph-edge" d={`M${a.x + 44},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x - 44},${b.y}`} />;
      })}
      {nodes.map((node) => {
        const p = layout.positions.get(node.node_id);
        if (!p) return null;
        const active = node.node_id === props.activeNodeId || node.node_id === props.selectedNode;
        return (
          <g key={node.node_id} className={`graph-node ${active ? "active" : ""}`} onClick={() => props.onSelect(node.node_id)}>
            <rect x={p.x - 52} y={p.y - 18} rx={6} width={104} height={36} />
            <circle cx={p.x - 39} cy={p.y} r={5} fill={typeColors[node.node_type] ?? "#667085"} />
            <text x={p.x - 29} y={p.y + 4}>{truncate(node.label ?? node.node_id, 18)}</text>
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
  const review = props.view?.review;
  const interrupt = review?.open_interrupts[0];
  const rows = [
    ...(review?.open_triggers ?? []).map((item) => ({ kind: "trigger", ...item })),
    ...(review?.open_findings ?? []).map((item) => ({ kind: "finding", ...item }))
  ];
  return (
    <>
      {interrupt ? (
        <div className="item">
          <div className="item-title">{interrupt.source || "interrupt"}</div>
          <div className="item-sub">{interrupt.question}</div>
        </div>
      ) : <p className="small muted">No open interrupt.</p>}
      {rows.slice(0, 8).map((row, index) => (
        <div className="item" key={`${row.kind}-${index}`}>
          <div className="item-title">{row.kind} <span className={`badge ${badgeClass(row.severity)}`}>{row.severity}</span></div>
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
  const context = props.view?.agent_context ?? {};
  const compact = {
    view_type: context.view_type,
    purpose: context.purpose,
    audit_preview: context.audit_preview,
    trace_driven_rethinking: context.trace_driven_rethinking,
    affordances: Array.isArray(context.affordances) ? context.affordances.slice(0, 6) : []
  };
  return <pre className="context-json">{JSON.stringify(compact, null, 2)}</pre>;
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
