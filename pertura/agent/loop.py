"""Workbench: the main entry point for LLM-driven analysis with provenance memory.

Tool-loop agent runtime: the LLM freely chooses bounded tools, while gated
dispatch and GraphController preserve analysis constraints.
Strict provenance recording: every action, observation, and artifact is event-logged.
"""

from __future__ import annotations

import json
import os
import sys
import csv
from pathlib import Path
from uuid import uuid4

from pertura.models import (
    Attempt, Observation, Interrupt,
    _now, _model_dump,
)
from pertura.core import Store, GraphController, ResponseCache, fork_store, diff_stores
from pertura.memory.compiler import compile_context
from pertura.domain import Domain
from pertura.capabilities import build_capability_registry

# Tool-loop prompt: agency protocol

_TOOL_LOOP_PROMPT = """You are a scientific analyst working inside Pertura.
Pertura is an audited perturb-seq runtime: you choose scientific moves freely,
and the runtime turns those moves into gated, replayable state.

Use the Active Work Order / Perturb-seq Turn Card as the decision surface for
this turn. The Active Work Order is the authoritative decision surface. It
contains the current stage, workflow gap, selected capability card, design
ledger, observation memory, previous outcome, and allowed tools.

Environment:
- workspace and artifacts_dir already exist in the kernel. Do not redefine them.
- Trust the workspace summary in the turn card. Do not call os.walk(),
  os.listdir(), or os.getcwd(); use workspace / workspace.iterdir() only when
  the card explicitly says inspection is still needed.
- Kernel state persists across cells. Do not reload data or re-import packages
  just because a previous cell ran.

Protocol:
1. Pick the next scientific move: run one focused capability, repair one failed
   cell, ask the user for an authority-bound design fact, summarize evidence, or
   request a workflow transition.
2. Before execute_code/submit_job/retry, read the selected capability card.
   Prefer capability_ids=[selected_capability.id], register the expected
   observations/artifacts, and explain any missing output.
3. If the workflow gap says the stage is ready to advance, do not repeat
   inspect_workspace/load_dataset. Complete the node or request the transition.
4. If the gap is a human fact, ask_user or update_design; do not invent PI
   facts such as control labels, guide column, target column, contrast, or MOI.
5. If a result is suspicious, empty, stale, unsupported, or blocking, use the
   rethinking/repair guidance in the turn card. If you need more debug context,
   call get_audit_toolbox() first, then the specific audit/review tool it names.
6. Commit state only through exposed action tools such as execute_code,
   submit_job, retry, ask_user, complete_node, request_node_transition, branch
   tools, or finish. Runtime gates decide whether each intent is valid.

Evidence rules:
- register_observation() for scientific findings worth using later.
- register_artifact() for generated tables, plots, reports, or checkpoints.
- Node progression depends on registered evidence, not printed stdout alone.

Expansion tools:
- Use get_context_review() only when the turn card is not enough; it expands
  audit_preview, runtime symbols, provenance, and risks.
- If the Active Work Order lists audit next actions, follow those local-read repair actions first before committing new code.
- get_harness_manifest explains runtime invariants when you need them.
- review_evidence_chain checks whether a claim or artifact is supported.
- plan_rethinking builds a trace/repair menu for suspicious or blocking output.
- Read the Active Work Order's rethinking section first when present; treat
  recommended_actions as the preferred trace/repair menu unless a human answer
  or explicit user request has higher priority.
"""


class Workbench:
    """LLM-driven analysis with provenance memory.

    Usage:
        wb = Workbench(domain=my_domain)
        wb.run("./data", goal="Analyze this dataset", steps=5)
        print(wb.status)
        wb.serve()
    """

    def __init__(self, domain: Domain, *, provider: str = "openai",
                 sandbox: str = "kernel", docker_image: str = ""):
        self.domain = domain
        self.provider = provider
        self.sandbox = sandbox
        self.docker_image = docker_image
        self._store: Store | None = None
        self._run_id: str = ""
        self._kernel = None
        self._controller: GraphController | None = None
        self._cancel_event = None
        self._replay_mode = "off"
        self._response_cache: ResponseCache | None = None

    # Public API

    @property
    def status(self) -> dict:
        if not self._store: return {"state": "not_initialized"}
        snap = self._store.read_snapshot()
        if not snap: return {"state": "no_snapshot"}
        return {
            "run_id": snap.run_id, "phase": snap.phase,
            "workspace": snap.workspace, "goal": snap.goal,
            "attempts": len(snap.attempts),
            "observations": len(snap.observations),
            "conclusions": len(snap.conclusions),
            "triggers_open": len([t for t in snap.triggers if t.status == "open"]),
            "interrupts_open": len([i for i in snap.interrupts if i.status == "open"]),
            "branches": len(snap.branches),
        }

    def runtime_status(self, *, recent: int = 20) -> dict:
        """Return a compact frozen runtime snapshot for debugging/UI."""
        if not self._store:
            return {"state": "not_initialized"}
        snap = self._store.read_snapshot()
        if not snap:
            return {"state": "no_snapshot"}
        events = self._store.read_events()[-recent:]
        return {
            **self.status,
            "active_node_id": snap.active_node_id,
            "active_branch": snap.active_branch,
            "active_attempt": snap.active_attempt,
            "budget": _model_dump(snap.budget),
            "disabled_capabilities": list(getattr(snap, "disabled_capabilities", []) or []),
            "recent_jobs": [_model_dump(item) for item in getattr(snap, "jobs", [])[-recent:]],
            "open_interrupts": [_model_dump(item) for item in snap.interrupts if item.status == "open"],
            "open_approvals": [_model_dump(item) for item in snap.approvals if item.status == "open"],
            "open_triggers": [_model_dump(item) for item in snap.triggers if item.status == "open"],
            "recent_findings": [_model_dump(item) for item in snap.findings[-recent:]],
            "recent_behavior_runs": [_model_dump(item) for item in snap.behavior_runs[-recent:]],
            "recent_events": [_model_dump(item) for item in events],
        }

    @property
    def graph(self) -> dict | None:
        return self._store.read_graph() if self._store else None

    def close(self):
        if self._kernel:
            self._kernel.shutdown()
            self._kernel = None

    def run(self, workspace: str, *, goal: str = "", steps: int = 5) -> dict:
        self._init(workspace, goal)
        result = {"steps": 0, "stop_reason": None}
        for _ in range(steps):
            action = self._step()
            result["steps"] += 1
            maintenance = self._post_step_maintenance(action)
            terminal = next((item for item in maintenance if item in ("waiting_for_human", "complete", "blocked", "cancelled")), "")
            if terminal:
                result["stop_reason"] = terminal
                break
            if action in ("waiting_for_human", "complete", "blocked", "cancelled"):
                result["stop_reason"] = action
                break
        if result["stop_reason"] is None:
            result["stop_reason"] = "max_steps"
        return result

    def run_until_pause(
        self,
        workspace: str = "",
        *,
        goal: str = "",
        max_turns: int = 20,
        max_repairs: int = 5,
        no_progress_limit: int = 3,
    ) -> dict:
        """Run autonomously until a product-level pause condition is reached."""
        if self._store is None:
            self._init(workspace, goal)

        result = {
            "turns": 0,
            "actions": [],
            "stop_reason": "",
            "max_turns": max_turns,
            "max_repairs": max_repairs,
            "no_progress_limit": no_progress_limit,
        }
        no_progress = 0
        repair_turns = 0
        for _ in range(max(0, int(max_turns))):
            if self._cancel_requested():
                result["stop_reason"] = "cancelled"
                return result
            before = _progress_marker(self._store.read_snapshot() if self._store else None)
            action = self._step()
            result["turns"] += 1
            result["actions"].append(action)
            maintenance_actions = self._post_step_maintenance(action)
            result["actions"].extend(maintenance_actions)
            if self._cancel_requested() or action == "cancelled" or "cancelled" in maintenance_actions:
                result["stop_reason"] = "cancelled"
                return result

            snap = self._store.read_snapshot() if self._store else None
            interrupt_reason = _interrupt_stop_reason(snap)
            if interrupt_reason:
                result["stop_reason"] = interrupt_reason
                return result
            if action == "complete" or "complete" in maintenance_actions or (snap is not None and snap.phase == "complete"):
                result["stop_reason"] = "complete"
                return result

            after = _progress_marker(snap)
            progressed = after != before
            repair_like = action == "blocked" or (_is_repair_state(snap) and not progressed)
            repair_turns = repair_turns + 1 if repair_like else 0
            if repair_turns >= max(1, int(max_repairs)):
                result["stop_reason"] = "max_repairs"
                return result

            no_progress = no_progress + 1 if after == before else 0
            if no_progress >= max(1, int(no_progress_limit)):
                result["stop_reason"] = "no_progress"
                return result

        result["stop_reason"] = "max_turns"
        return result

    def step(self, n: int = 1) -> list[str]:
        actions = []
        for _ in range(n):
            a = self._step()
            actions.append(a)
            if a in ("waiting_for_human", "complete", "blocked", "cancelled"):
                break
        return actions

    def set_cancel_event(self, cancel_event):
        self._cancel_event = cancel_event

    def run_with_cache(self, workspace: str, *, goal: str = "", steps: int = 5,
                       replay_mode: str = "loose") -> dict:
        """Run analysis with content-addressed caching enabled.

        replay_mode:
          - "loose": cache hits return stored responses; cache misses make fresh calls
          - "strict": cache misses raise an error (deterministic replay verification)
          - "off": no caching (default run() behavior)
        """
        self._replay_mode = replay_mode
        return self.run(workspace, goal=goal, steps=steps)

    def fork(self, run_id: str, at_event_id: str, *, goal: str = "",
             workspace: str = "") -> "Workbench":
        """Fork an existing run at a specific event. Returns a new Workbench.

        All events up to at_event_id share the parent's response cache.
        New events branch from there independently.
        """
        parent_dir = Path("runs") / run_id
        if not (parent_dir / "events.db").exists():
            raise ValueError(f"Run not found: {run_id}")

        fork = fork_store(parent_dir, at_event_id)

        wb = Workbench(domain=self.domain, provider=self.provider,
                       sandbox=self.sandbox, docker_image=self.docker_image)
        wb._store = fork.store
        wb._run_id = fork.run_id
        wb._controller = GraphController(fork.store, fork.run_id)
        if goal:
            wb._emit("goal_recorded", {"goal": {
                "goal_id": f"goal_fork_{uuid4().hex[:6]}",
                "text": goal or f"Fork of {run_id} at {at_event_id}",
                "status": "active"}})
        if workspace:
            _scan_workspace(wb, workspace)
        return wb

    @staticmethod
    def diff(run_a: str, run_b: str) -> dict:
        """Diff graph, observation memory, and conclusions between two runs."""
        return diff_stores(Path("runs") / run_a, Path("runs") / run_b)

    def answer(self, interrupt_id: str, response: str):
        snap = self._store.read_snapshot()
        for intr in snap.interrupts:
            if intr.interrupt_id == interrupt_id and intr.status == "open":
                choose_next = getattr(intr, "default_action", "") == "choose_next_node"
                target_node_id = ""
                if choose_next:
                    target_node_id = _resolve_interrupt_option(response, getattr(intr, "options", []) or [])
                self._emit("interrupt_resolved",
                          {"interrupt_id": interrupt_id, "answer": response})
                from pertura.spec.design_answer import (
                    compile_design_answer,
                    expected_fields_from_interrupt,
                )
                design = compile_design_answer(
                    response,
                    expected_fields=expected_fields_from_interrupt(snap, intr),
                    provider="deterministic",
                )
                if design:
                    self._emit("design_updated", {
                        "design": design,
                        "reason": f"interrupt_answer:{interrupt_id}",
                        "source": "pi_confirmed",
                        "confidence": "high",
                    })
                if intr.trigger_id:
                    self._emit("trigger_resolved",
                              {"trigger_id": intr.trigger_id, "answer": response})
                if choose_next and target_node_id:
                    from pertura.agent.gated_dispatch import gated_dispatch

                    fresh = self._store.read_snapshot()
                    current_id = getattr(fresh, "active_node_id", "")
                    if current_id and not _active_visit_completed(fresh, current_id):
                        complete_action = gated_dispatch(
                            self,
                            "complete_node",
                            "",
                            {"summary": f"User chose next workflow stage {target_node_id}; completing {current_id} first."},
                            {
                                "node_id": current_id,
                                "reason": f"workflow_autopilot_choice:{interrupt_id}",
                            },
                            fresh,
                        )
                        if complete_action in {"waiting_for_human", "blocked"}:
                            return
                        fresh = self._store.read_snapshot()
                    action = gated_dispatch(
                        self,
                        "request_node_transition",
                        "",
                        {"summary": f"User chose next workflow stage {target_node_id}."},
                        {
                            "target_node_id": target_node_id,
                            "reason": f"workflow_autopilot_choice:{interrupt_id}",
                        },
                        fresh,
                    )
                    if action in {"waiting_for_human", "blocked"}:
                        return
                self._emit("run_resumed", {})
                return
        raise ValueError(f"No open interrupt found: {interrupt_id}")

    def _post_step_maintenance(self, action: str) -> list[str]:
        if self._store is None or action in {"waiting_for_human", "cancelled", "complete"}:
            return []
        snap = self._store.read_snapshot()
        if snap is None:
            return []
        actions = self._resolve_stale_runtime_triggers(snap)
        snap = self._store.read_snapshot()
        if snap is None or action == "blocked":
            return actions
        if any(getattr(item, "status", "") == "open" for item in getattr(snap, "interrupts", []) or []):
            return actions
        if any(
            getattr(item, "status", "") in {"planned", "running"}
            for item in getattr(snap, "attempts", []) or []
            if getattr(item, "attempt_id", "") == getattr(snap, "active_attempt", "")
        ):
            return actions
        actions.extend(self._apply_auto_navigation(snap))
        return actions

    def _resolve_stale_runtime_triggers(self, snap) -> list[str]:
        actions: list[str] = []
        attempts = list(getattr(snap, "attempts", []) or [])
        attempt_index = {getattr(item, "attempt_id", ""): idx for idx, item in enumerate(attempts)}
        outcomes_by_attempt = {}
        for outcome in getattr(snap, "outcomes", []) or []:
            outcomes_by_attempt.setdefault(getattr(outcome, "attempt_id", ""), []).append(outcome)
        for trigger in getattr(snap, "triggers", []) or []:
            if getattr(trigger, "status", "") != "open":
                continue
            if getattr(trigger, "trigger_type", "") != "runtime_error":
                continue
            failed_attempt_id = getattr(trigger, "attempt_id", "")
            failed_index = attempt_index.get(failed_attempt_id)
            failed_attempt = attempts[failed_index] if failed_index is not None else None
            if failed_index is None or failed_attempt is None:
                continue
            resolved_by = ""
            for later in attempts[failed_index + 1:]:
                if getattr(later, "analysis_node_id", "") != getattr(failed_attempt, "analysis_node_id", ""):
                    continue
                for outcome in outcomes_by_attempt.get(getattr(later, "attempt_id", ""), []):
                    metrics = getattr(outcome, "metrics", {}) or {}
                    material_output = (
                        _positive_metric_count(metrics.get("observations_registered"))
                        or _positive_metric_count(metrics.get("artifacts_registered"))
                    )
                    if getattr(outcome, "status", "") == "success" and metrics.get("returncode", 0) == 0 and material_output:
                        resolved_by = getattr(later, "attempt_id", "")
                        break
                if resolved_by:
                    break
            if not resolved_by:
                continue
            self._emit("trigger_resolved", {
                "trigger_id": getattr(trigger, "trigger_id", ""),
                "reason": f"Superseded by successful attempt {resolved_by}.",
                "resolved_by_attempt_id": resolved_by,
            })
            actions.append("trigger_resolved")
        return actions

    def _apply_auto_navigation(self, snap) -> list[str]:
        from pertura.core.workflow_controller import evaluate_workflow_autopilot
        from pertura.agent.gated_dispatch import gated_dispatch

        decision = evaluate_workflow_autopilot(snap)
        action_name = decision.get("action", "")
        if action_name not in {"auto_complete", "auto_advance", "choose_next"}:
            return []
        current_id = decision.get("current_node_id") or getattr(snap, "active_node_id", "")
        if not current_id:
            return []
        if action_name == "choose_next":
            candidates = decision.get("candidates") or []
            if not candidates:
                return []
            actions: list[str] = []
            if not _active_visit_completed(snap, current_id):
                action = gated_dispatch(
                    self,
                    "complete_node",
                    "",
                    {"summary": decision.get("reason", "Workflow autopilot completion gate passed before choosing next stage.")},
                    {"node_id": current_id, "reason": decision.get("reason", "")},
                    snap,
                )
                actions.append(action)
                if action in {"waiting_for_human", "blocked"}:
                    return actions
                snap = self._store.read_snapshot() if self._store else snap
            if _has_open_workflow_choice(snap, current_id):
                return actions + ["waiting_for_human"]
            self._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="workflow_autopilot",
                question="Multiple next workflow stages are ready. Choose which stage should run next.",
                options=[item.get("node_id", "") for item in candidates if item.get("node_id")],
                default_action="choose_next_node",
            ))})
            return actions + ["waiting_for_human"]
        actions: list[str] = []
        if not _active_visit_completed(snap, current_id):
            action = gated_dispatch(
                self,
                "complete_node",
                "",
                {"summary": decision.get("reason", "Workflow autopilot completion gate passed.")},
                {"node_id": current_id, "reason": decision.get("reason", "")},
                snap,
            )
            actions.append(action)
            if action in {"waiting_for_human", "blocked"}:
                return actions
            snap = self._store.read_snapshot() if self._store else snap
        target = decision.get("target_node_id", "")
        if action_name == "auto_advance" and target:
            action = gated_dispatch(
                self,
                "request_node_transition",
                "",
                {"summary": decision.get("reason", "Workflow autopilot selected the single ready next stage.")},
                {"target_node_id": target, "reason": decision.get("reason", "")},
                snap,
            )
            actions.append(action)
        return actions

    def update_design(self, design: dict, *, reason: str = "user_update",
                      source: str = "user_confirmed",
                      confidence: str = "high"):
        """Record structured PI/user design information as an event."""
        if not self._store:
            raise ValueError("Workbench is not initialized.")
        self._emit("design_updated", {
            "design": design,
            "reason": reason,
            "source": source,
            "confidence": confidence,
        })

    def set_capability_enabled(self, capability_id: str, enabled: bool, *,
                               reason: str = "user_toggle"):
        """Enable or disable a capability for the current run only."""
        if not self._store:
            raise ValueError("Workbench is not initialized.")
        self._emit("capability_toggled", {
            "capability_id": capability_id,
            "enabled": bool(enabled),
            "reason": reason,
        })

    def load_analysis_spec(self, analysis_spec: dict, *, reason: str = "user_spec"):
        """Load or replace the editable analysis graph for the current run/domain."""
        from pertura.spec.models import spec_from_dict, validate_analysis_graph
        spec = spec_from_dict(analysis_spec)
        if spec is None:
            raise ValueError("analysis_spec is empty.")
        spec, compile_report = _compile_analysis_spec(spec, self.domain)
        validate_analysis_graph(spec)
        self.domain.analysis_graph = spec.model_dump(mode="json")
        self.domain.capabilities = build_capability_registry(self.domain).to_list()
        if self._store:
            self._emit("analysis_spec_compiled", {
                "report": _compact_compile_report(compile_report),
                "reason": reason,
            })
            self._emit("capabilities_loaded", {
                "capabilities": self.domain.capabilities,
                "reason": reason,
            })
            self._emit("analysis_spec_loaded", {"analysis_spec": self.domain.analysis_graph, "reason": reason})
            snap = self._store.read_snapshot()
            if snap and not snap.active_node_id:
                self._emit("node_entered", {
                    "node_id": spec.start_node_id,
                    "branch_id": snap.active_branch,
                    "reason": "analysis_spec_loaded",
                })

    def report(self) -> dict:
        from pertura.reporting import generate_report, render_markdown, render_html
        snap = self._store.read_snapshot()
        ctx = compile_context(snap) if snap else None
        if not snap or not ctx:
            return {"error": "no_data"}
        graph = self._store.read_graph() if self._store else {}
        report = generate_report(
            snap, ctx, provider=self.provider,
            include_narrative=True,
            graph=graph,
            run_dir=self._store.run_dir if self._store else None,
        )
        if self._store:
            report_dir = self._store.run_dir
            md_path = report_dir / "report.md"
            md_path.write_text(render_markdown(report,
                graph_html_path="derivation_graph.html"), encoding="utf-8")
            html_path = report_dir / "report.html"
            html_path.write_text(render_html(report, graph), encoding="utf-8")
            json_path = report_dir / "report.json"
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                            default=str), encoding="utf-8")
            report["paths"] = {"markdown": str(md_path), "html": str(html_path),
                              "json": str(json_path)}
        return report

    def report_preview(self) -> dict:
        """Return a pure read-only report view for GUI/API polling."""
        from pertura.reporting import generate_report
        snap = self._store.read_snapshot() if self._store else None
        ctx = compile_context(snap) if snap else None
        if not snap or not ctx:
            return {"error": "no_data"}
        graph = self._store.read_graph() if self._store else {}
        report = generate_report(
            snap, ctx, provider=self.provider,
            include_narrative=False,
            graph=graph,
            run_dir=self._store.run_dir if self._store else None,
        )
        report["status"] = self.status
        return report

    def serve(self, port: int = 8765, *, host: str = "127.0.0.1", ui: str = "auto"):
        from pertura._api import create_app
        import uvicorn
        app = create_app(self, ui=ui)
        print(f"\n  Pertura GUI:    http://{host}:{port}")
        print(f"  API docs:       http://{host}:{port}/docs\n")
        uvicorn.run(app, host=host, port=port, log_level="warning")

    # Engine

    def _init(self, workspace: str, goal: str):
        run_id = f"run_{_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        run_dir = (Path("runs") / run_id).resolve()
        self._store = Store(run_dir)
        self._run_id = run_id
        self._controller = GraphController(self._store, self._run_id)
        self._response_cache = (
            ResponseCache(self._store.run_dir)
            if getattr(self, "_replay_mode", "off") != "off"
            else None
        )
        self.domain.capabilities = build_capability_registry(self.domain).to_list()
        domain_context = self.domain.runtime_context()
        self._emit("run_started", {"config": {
            "run_id": run_id, "workspace": workspace, "goal": goal,
            "domain": self.domain.name, "protocol": domain_context.get("protocol", ""),
            "budget": {"max_attempts": 20, "max_branches": 3, "max_repairs": 3},
            "capabilities": self.domain.capabilities,
        }})
        if self.domain.analysis_graph:
            spec, compile_report = _compile_analysis_spec(self.domain.analysis_graph, self.domain)
            self.domain.analysis_graph = spec.model_dump(mode="json")
            self.domain.capabilities = build_capability_registry(self.domain).to_list()
            self._emit("capabilities_loaded", {
                "capabilities": self.domain.capabilities,
                "reason": "run_init",
            })
            self._emit("analysis_spec_compiled", {
                "report": _compact_compile_report(compile_report),
                "reason": "run_init",
            })
            self._emit("analysis_spec_loaded", {
                "analysis_spec": self.domain.analysis_graph,
            })
            start_node = self.domain.analysis_graph.get("start_node_id", "workspace_inspection")
            self._emit("node_entered", {
                "node_id": start_node,
                "branch_id": "main",
                "reason": "default_start_node",
            })
        _scan_workspace(self, workspace)
        if goal:
            self._emit("goal_recorded", {"goal": {
                "goal_id": "goal_main", "text": goal, "status": "active"}})

    def _step(self) -> str:
        snap = self._store.read_snapshot()
        if not snap: return "no_snapshot"

        if any(i.status == "open" for i in snap.interrupts):
            return "waiting_for_human"

        active = next((a for a in snap.attempts
                      if a.attempt_id == snap.active_attempt
                      and a.status in ("planned", "running")), None)
        if self._cancel_requested():
            return self._pause_for_cancel(active.attempt_id if active else "")
        if active:
            from pertura.agent.execute import _execute_attempt
            return _execute_attempt(self, active)

        auto_actions = self._apply_auto_navigation(snap)
        if auto_actions:
            return auto_actions[-1]

        if not _has_key(self.provider):
            self._emit("interrupt_opened", {"interrupt": _model_dump(Interrupt(
                interrupt_id=f"irq_{uuid4().hex[:12]}",
                source="missing_api_key",
                question=f"No {self.provider} API key configured.",
                default_action="configure_key",
            ))})
            return "waiting_for_human"

        from pertura.agent.tool_loop import run_tool_loop
        domain_context = self.domain.runtime_context()
        try:
            action, code, assessment, decision = run_tool_loop(
                result=None, obs_count=0, snap=snap,
                attempt=Attempt(attempt_id="first", stage="start",
                               title="Start", objective="Begin analysis"),
                provider=self.provider, emit=self._emit, is_first=True,
                coding_guidelines=domain_context.get("coding_guidelines", ""),
                protocol=domain_context.get("protocol", ""),
                tools=domain_context.get("tools", ""),
            )
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            action, code, assessment = "ask_user", "", {}
            decision = {"reason": "Deliberation failed"}

        self._emit("review_decision_recorded", {
            "review_id": f"rev_first_{uuid4().hex[:8]}", "attempt_id": "",
            "decision": action,
            "assessment_status": assessment.get("status", ""),
            "assessment_summary": assessment.get("summary", ""),
            "reason": decision.get("reason", ""),
        })

        from pertura.agent.gated_dispatch import gated_dispatch
        return gated_dispatch(self, action, code, assessment, decision, snap)

    def _emit(self, event_type: str, payload: dict):
        if self._controller is None:
            self._controller = GraphController(self._store, self._run_id)
        self._controller.append_event(event_type, payload)

    def _cancel_requested(self) -> bool:
        return bool(self._cancel_event and self._cancel_event.is_set())

    def _pause_for_cancel(self, attempt_id: str = "") -> str:
        if attempt_id:
            self._emit("attempt_stopped", {
                "attempt_id": attempt_id,
                "reason": "job_cancel_requested",
            })
        self._emit("run_paused", {"reason": "job_cancel_requested"})
        return "cancelled"


# Helpers

def _progress_marker(snap) -> tuple[int, int, int, str]:
    if snap is None:
        return (0, 0, 0, "")
    return (
        len(getattr(snap, "attempts", []) or []),
        len(getattr(snap, "observations", []) or []),
        len(getattr(snap, "artifacts", []) or []),
        getattr(snap, "active_node_id", "") or "",
    )


def _interrupt_stop_reason(snap) -> str:
    if snap is None:
        return ""
    open_interrupts = [
        item for item in (getattr(snap, "interrupts", []) or [])
        if getattr(item, "status", "") == "open"
    ]
    if not open_interrupts:
        return ""
    if any(getattr(item, "source", "") == "missing_api_key" for item in open_interrupts):
        return "missing_key"
    return "human_interrupt"


def _is_repair_state(snap) -> bool:
    if snap is None:
        return False
    from pertura.core.execution_state import compile_execution_state
    return compile_execution_state(snap).get("mode") == "repairing"


def _resolve_interrupt_option(response: str, options: list[str]) -> str:
    text = str(response or "").strip()
    if not text:
        return ""
    if text in options:
        return text
    lowered = text.lower()
    for option in options:
        if lowered == str(option).lower():
            return option
    try:
        import json

        payload = json.loads(text)
        if isinstance(payload, dict):
            for key in ("target_node_id", "node_id", "answer", "choice"):
                value = str(payload.get(key) or "").strip()
                if value in options:
                    return value
                if value:
                    for option in options:
                        if value.lower() == str(option).lower():
                            return option
    except Exception:
        pass
    return text if text in options else ""


def _has_open_workflow_choice(snap, current_id: str) -> bool:
    for intr in getattr(snap, "interrupts", []) or []:
        if getattr(intr, "status", "") != "open":
            continue
        if getattr(intr, "source", "") != "workflow_autopilot":
            continue
        if getattr(intr, "default_action", "") != "choose_next_node":
            continue
        if current_id and current_id in (getattr(intr, "question", "") or ""):
            return True
        return True
    return False


def _active_visit_completed(snap, node_id: str) -> bool:
    return any(
        getattr(item, "node_id", "") == node_id
        and getattr(item, "branch_id", "") == getattr(snap, "active_branch", "main")
        and getattr(item, "status", "") == "completed"
        for item in getattr(snap, "node_visits", []) or []
    )


def _positive_metric_count(value) -> bool:
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _has_key(provider: str) -> bool:
    from pertura.core.fixtures import fixture_mode
    if fixture_mode() in {"replay", "strict"}:
        return True
    from pertura.planner import _api_key, _anthropic_key
    if provider == "anthropic":
        return bool(_anthropic_key())
    return bool(_api_key())


def _compile_analysis_spec(analysis_spec, domain: Domain):
    """Compile user/domain authored node conditions before loading them."""
    from pertura.spec.compiler import compile_conditions
    from pertura.spec.models import spec_from_dict
    from pertura.planner import _api_key

    spec = spec_from_dict(analysis_spec)
    provider = "openai" if (_api_key() and os.getenv("PETURA_COMPILE_LLM", "")) else "deterministic"
    domain_context = "\n\n".join(
        item for item in [
            f"Domain: {domain.name}",
            domain.runtime_context().get("condition_context", ""),
        ]
        if item
    )
    report = compile_conditions(spec, provider=provider, domain_context=domain_context)
    return report.spec, report


def _compact_compile_report(report) -> dict:
    data = report.to_dict()
    data.pop("spec", None)
    return data


def _scan_workspace(wb: Workbench, workspace: str):
    p = Path(workspace)
    if not p.exists():
        return
    for f in sorted(p.iterdir()):
        if f.name.startswith("."):
            continue
        if f.is_dir():
            wb._emit("observation_registered", {"observation": _model_dump(Observation(
                observation_id=f"obs_ws_{uuid4().hex[:8]}",
                type="workspace_file", target=f"{f.name}/", metric="directory",
                value=f.name, method="auto_discover",
                attempt_id="", branch_id="main",
            ))})
        else:
            wb._emit("observation_registered", {"observation": _model_dump(Observation(
                observation_id=f"obs_ws_{uuid4().hex[:8]}",
                type="workspace_file", target=f.name, metric="file_size",
                value=f.stat().st_size, method="auto_discover",
                attempt_id="", branch_id="main",
            ))})
        _probe_workspace_entry(wb, f)


def _probe_workspace_entry(wb: Workbench, path: Path) -> None:
    """Register lightweight dataset/schema hints without loading large matrices."""
    try:
        lower = path.name.lower()
        if path.is_dir():
            names = {item.name.lower() for item in path.iterdir()} if path.exists() else set()
            if {"matrix.mtx", "matrix.mtx.gz"} & names or {"features.tsv", "features.tsv.gz"} & names:
                _register_probe_observation(
                    wb, target=path.name, metric="detected_format", value="10x_mtx_directory",
                    summary="Directory looks like a 10X matrix folder.",
                )
            return
        if lower.endswith(".h5ad"):
            _register_probe_observation(
                wb, target=path.name, metric="detected_format", value="h5ad",
                summary="AnnData file candidate. Prefer backed read for large data.",
            )
            return
        if lower.endswith(".h5") or lower.endswith(".hdf5"):
            _register_probe_observation(
                wb, target=path.name, metric="detected_format", value="hdf5_or_10x_h5",
                summary="HDF5 candidate. Inspect keys before choosing loader.",
            )
            return
        if lower.endswith((".csv", ".tsv", ".txt")):
            columns = _read_table_header(path)
            if columns:
                _register_probe_observation(
                    wb, target=path.name, metric="table_columns",
                    value=columns[:40],
                    summary=f"Table header detected with {len(columns)} columns.",
                )
                for column in _candidate_design_columns(columns):
                    _register_probe_observation(
                        wb, target=column, metric="candidate_design_column",
                        value=path.name,
                        summary=f"Column {column} may encode perturbation design or metadata.",
                    )
    except Exception as exc:
        _register_probe_observation(
            wb, target=path.name, metric="probe_warning", value=str(exc)[:200],
            summary="Workspace probe could not inspect this entry.",
        )


def _read_table_header(path: Path) -> list[str]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        sample = handle.readline()
    if not sample:
        return []
    try:
        return [item.strip() for item in next(csv.reader([sample], delimiter=delimiter)) if item.strip()]
    except Exception:
        return []


def _candidate_design_columns(columns: list[str]) -> list[str]:
    hints = (
        "guide", "grna", "sgrna", "perturb", "target", "gene",
        "control", "ntc", "batch", "condition", "sample", "moi",
    )
    out = []
    for column in columns:
        lower = column.lower()
        if any(hint in lower for hint in hints):
            out.append(column)
    return out[:12]


def _register_probe_observation(wb: Workbench, *, target: str, metric: str, value, summary: str) -> None:
    wb._emit("observation_registered", {"observation": _model_dump(Observation(
        observation_id=f"obs_probe_{uuid4().hex[:8]}",
        type="workspace_probe",
        target=target,
        metric=metric,
        value=value,
        method="workspace_probe",
        parameters={"summary": summary},
        attempt_id="",
        branch_id="main",
    ))})
