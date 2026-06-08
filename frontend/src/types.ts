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
  llm_actionable?: boolean;
  tool_visibility?: ToolVisibilityCard[];
  why_unavailable?: string[];
};

export type ToolVisibilityCard = {
  tool_id: string;
  permission_tier?: string;
  description?: string;
  visible_to_llm?: boolean;
  why_hidden?: string[];
};

export type LlmToolSurface = {
  surface_type?: string;
  visible_count?: number;
  hidden_count?: number;
  visible_tools?: ToolVisibilityCard[];
  hidden_tools?: ToolVisibilityCard[];
  summary?: {
    visible_by_permission?: Record<string, number>;
    hidden_by_permission?: Record<string, number>;
    hidden_reasons?: Record<string, number>;
  };
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
  objective?: string;
  stage?: string;
  status: string;
  analysis_node_id: string;
  branch_id: string;
  capability_ids: string[];
  rationale?: string;
  repair_count?: number;
  code_preview?: string;
  outcome_status: string;
  outcome_summary: string;
  execution?: {
    returncode?: number;
    timed_out?: boolean;
    soft_timeout_hit?: boolean;
    execution_time?: number;
    stdout_chars?: number;
    stderr_tail?: string;
    observations_registered?: number;
    kernel_refs?: string[];
  };
  observations: number;
  artifacts: number;
};

export type ArtifactCard = {
  artifact_id: string;
  attempt_id: string;
  kind: string;
  summary: string;
  path: string;
  metadata?: Record<string, unknown>;
  preview_url: string;
  file_url?: string;
};

export type ReviewItem = {
  severity?: string;
  summary?: string;
  source?: string;
  question?: string;
  interrupt_id?: string;
};

export type RuntimeIssue = {
  issue_id: string;
  kind: "question" | "repair_issue" | "approval_issue" | "audit_issue" | string;
  source_event_type?: string;
  source?: string;
  severity?: string;
  status?: string;
  summary?: string;
  question?: string;
  affected_ids?: string[];
  suggested_action?: string;
  answer_endpoint?: string;
};

export type ExecutionState = {
  view_type: "execution_state";
  schema_version: string;
  mode: "not_initialized" | "ready" | "running" | "needs_user" | "repairing" | "complete" | "paused" | string;
  run_id: string;
  stop_reason?: string;
  current_task: {
    node_id?: string;
    title?: string;
    purpose?: string;
    branch_id?: string;
    goal?: string;
  };
  question?: RuntimeIssue | Record<string, never>;
  issues: RuntimeIssue[];
  recommended_actions?: string[];
  visible_capabilities?: string[];
  evidence_summary?: {
    attempts?: number;
    observations?: number;
    artifacts?: number;
    conclusions?: number;
    recent_attempts?: AttemptCard[];
    recent_artifacts?: ArtifactCard[];
  };
  activity?: {
    phase?: string;
    active_attempt?: string;
    jobs?: Array<Record<string, unknown>>;
    active_job?: Record<string, unknown>;
  };
  debug_refs?: Record<string, unknown>;
};

export type ActiveWorkOrder = {
  view_type?: "active_work_order" | string;
  mode?: string;
  run_goal?: string;
  active_node?: { id?: string; title?: string; purpose?: string };
  branch_id?: string;
  node_progress?: {
    attempts?: number;
    observations?: number;
    artifacts?: number;
    completed?: boolean;
    missing_completion?: Array<string | Record<string, unknown>>;
  };
  workspace?: {
    path?: string;
    files?: Array<Record<string, unknown>>;
  };
  available_capabilities?: ActiveCapabilityCard[];
  selected_capability?: ActiveCapabilityCard;
  observation_memory?: {
    summary?: Record<string, unknown>;
    needs_review?: Array<Record<string, unknown>>;
  };
  open_interrupts?: Array<Record<string, unknown>>;
  open_issues?: {
    runtime_issues?: RuntimeIssue[];
    triggers?: Array<Record<string, unknown>>;
    findings?: Array<Record<string, unknown>>;
    audit_next_actions?: Array<string | Record<string, unknown>>;
  };
  rethinking?: {
    status?: string;
    summary?: string;
    suspected_roots?: Array<Record<string, unknown>>;
    recommended_actions?: Array<Record<string, unknown>>;
  };
  last_attempt_delta?: Record<string, unknown>;
  outcome?: string;
  allowed_tools?: string[];
  recommended_actions?: string[];
  markdown?: string;
};

export type ActiveCapabilityCard = CapabilityCard & {
  ready?: boolean;
  packages?: string[];
  functions?: string[];
  packages_hint?: string;
  next_repair?: string;
  common_errors?: string[];
};

export type WorkbenchView = {
  view_type: "workbench_view";
  schema_version: string;
  run_id: string;
  execution_state: ExecutionState;
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
    active_work_order?: ActiveWorkOrder;
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
    runtime_events?: RuntimeEventCard[];
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
  llm_tool_surface?: LlmToolSurface;
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

export type RuntimeEventCard = {
  event_id?: string;
  event_type?: string;
  card_type?: string;
  timestamp?: string;
  title?: string;
  summary?: string;
};
