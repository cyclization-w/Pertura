import type { AttemptGraph, CapabilitiesView, DerivationView, WorkbenchView } from "./types";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getWorkbenchView(): Promise<WorkbenchView> {
  return json<WorkbenchView>("/api/workbench-view?max_items=8");
}

export function getAttemptGraph(): Promise<AttemptGraph> {
  return json<AttemptGraph>("/api/graph");
}

export function getDerivationView(focusNode = ""): Promise<DerivationView> {
  const suffix = focusNode ? `?focus_node=${encodeURIComponent(focusNode)}` : "";
  return json<DerivationView>(`/api/derivation-view${suffix}`);
}

export function getCapabilitiesView(): Promise<CapabilitiesView> {
  return json<CapabilitiesView>("/api/capabilities/view");
}

export function toggleCapability(capabilityId: string, enabled: boolean) {
  return json<CapabilitiesView>(`/api/capabilities/${encodeURIComponent(capabilityId)}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled })
  });
}

export function runWorkbench(workspace: string, goal: string, steps: number) {
  return json<Record<string, unknown>>("/api/run", {
    method: "POST",
    body: JSON.stringify({ workspace, goal, steps })
  });
}

export function stepWorkbench() {
  return json<Record<string, unknown>>("/api/step", { method: "POST" });
}

export function generateReport() {
  return json<Record<string, unknown>>("/api/report/generate", { method: "POST" });
}

export function answerInterrupt(interruptId: string, answer: string) {
  return json<Record<string, unknown>>(`/api/answer/${encodeURIComponent(interruptId)}`, {
    method: "POST",
    body: JSON.stringify({ answer })
  });
}
