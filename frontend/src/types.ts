export type WorkbenchStatus = {
  run_id?: string;
  phase?: string;
  state?: string;
  workspace?: string;
  goal?: string;
  attempts?: number;
  observations?: number;
  artifacts?: number;
  conclusions?: number;
  triggers_open?: number;
  interrupts_open?: number;
  branches?: number;
};

export type WorkbenchNode = {
  node_id: string;
  title: string;
  purpose: string;
  allowed_capabilities: string[];
  recommended_actions: string[];
  expected_outputs: string[];
  next_nodes: string[];
  strict_edges: boolean;
  hard_conditions: number;
  rubric_only_conditions: number;
};

export type CapabilityCard = {
  id?: string;
  capability_id?: string;
  title?: string;
  description?: string;
  stage?: string;
  kind?: string;
  tools?: string[];
  tool_names?: string[];
  required_inputs?: string[];
  missing_inputs?: string[];
  expected_observations?: string[];
  expected_artifacts?: string[];
  permission_tier?: string;
  backend_hint?: string;
  allowed_in_active_node?: boolean;
  enabled?: boolean;
  ready?: boolean;
  why_unavailable?: string[];
};

export type NodeContract = {
  node?: { id?: string; title?: string; purpose?: string };
  runtime?: {
    target_node_id?: string;
    missing_inputs?: string[];
    ready_capabilities?: string[];
  };
  inputs?: { required?: string[] };
  capabilities?: CapabilityCard[];
  audit_checklist?: string[];
};

export type AttemptCard = {
  attempt_id: string;
  title: string;
  status: string;
  analysis_node_id: string;
  branch_id: string;
  capability_ids: string[];
  outcome_status: string;
  outcome_summary: string;
  observations: number;
  artifacts: number;
};

export type ArtifactCard = {
  artifact_id: string;
  attempt_id: string;
  kind: string;
  summary: string;
  path: string;
  preview_url: string;
};

export type ReviewItem = {
  severity?: string;
  summary?: string;
  source?: string;
  question?: string;
  interrupt_id?: string;
};

export type WorkbenchView = {
  view_type: "workbench_view";
  schema_version: string;
  run_id: string;
  status: WorkbenchStatus;
  active: { node_id: string; branch_id: string; attempt_id: string };
  budget: Record<string, unknown>;
  analysis: {
    graph_summary: { nodes: number; edges: number };
    active_node_contract: NodeContract;
    domain: { name?: string; graph_id?: string; start_node_id?: string };
    nodes: WorkbenchNode[];
    capabilities_by_node: Record<string, string[]>;
    capabilities_view?: CapabilitiesView;
  };
  agent_context: Record<string, unknown>;
  review: {
    open_interrupts: ReviewItem[];
    open_triggers: ReviewItem[];
    open_findings: ReviewItem[];
    run_audit_summary: Record<string, unknown>;
    rethinking: {
      status?: string;
      summary?: string;
      recommended_actions?: Array<Record<string, unknown>>;
      suspected_roots?: Array<Record<string, unknown>>;
    };
  };
  activity: {
    recent_attempts: AttemptCard[];
    jobs: Array<Record<string, unknown>>;
    runtime_events?: Array<Record<string, unknown>>;
  };
  artifacts: {
    recent: ArtifactCard[];
    total: number;
  };
  report: {
    available: boolean;
    conclusions: Array<{
      conclusion_id: string;
      text: string;
      grade: string;
      support_count: number;
      limitation_count: number;
    }>;
    observation_count: number;
    artifact_count: number;
  };
  links: Record<string, string>;
};

export type GraphNode = {
  node_id: string;
  node_type: string;
  label?: string;
  status?: string;
  summary?: string;
};

export type GraphEdge = {
  source_id: string;
  target_id: string;
  edge_type: string;
};

export type AttemptGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type CapabilitiesView = {
  view_type: "capabilities_view";
  schema_version: string;
  run_id?: string;
  active_node_id?: string;
  disabled_capabilities?: string[];
  capabilities: CapabilityCard[];
};

export type DerivationLane = {
  lane: string;
  nodes: GraphNode[];
};

export type DerivationView = {
  view_type: "derivation_view";
  schema_version: string;
  run_id?: string;
  focus_node?: string;
  lane_order: string[];
  lanes: DerivationLane[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  focus_path: string[];
  issue_edges: GraphEdge[];
  folded_counts: Record<string, number>;
  summary: Record<string, number>;
};
