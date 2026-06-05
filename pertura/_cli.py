"""CLI: single-command startup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from pertura.domain import Domain
from pertura.agent.loop import Workbench


def main():
    p = argparse.ArgumentParser(prog="pertura", description="LLM-driven analysis with provenance memory.")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Run analysis (non-interactive).")
    r.add_argument("workspace", help="Data directory.")
    r.add_argument("--domain", default=None, help="Domain name or path to domain.json.")
    r.add_argument("--goal", default="", help="Analysis goal.")
    r.add_argument("--steps", type=int, default=None)
    r.add_argument("--provider", choices=["openai", "anthropic"], default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--base-url", default=None)
    r.add_argument("--sandbox", choices=["kernel", "subprocess", "docker"], default=None)
    r.add_argument("--analysis-graph", default=None, help="Path to AnalysisGraphSpec JSON.")

    c = sub.add_parser("chat", help="Interactive analysis session.")
    c.add_argument("workspace", nargs="?", default=None, help="Data directory (optional).")
    c.add_argument("--domain", default=None)
    c.add_argument("--provider", choices=["openai", "anthropic"], default=None)
    c.add_argument("--model", default=None)
    c.add_argument("--base-url", default=None)
    c.add_argument("--analysis-graph", default=None, help="Path to AnalysisGraphSpec JSON.")

    s = sub.add_parser("serve", help="Start GUI.")
    s.add_argument("--domain", default=None)
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--analysis-graph", default=None, help="Path to AnalysisGraphSpec JSON.")

    spec = sub.add_parser("spec", help="Inspect or export analysis graph specs.")
    spec_sub = spec.add_subparsers(dest="spec_cmd")
    spec_export = spec_sub.add_parser("export", help="Export a domain's default AnalysisGraphSpec.")
    spec_export.add_argument("--domain", default="perturbseq")
    spec_export.add_argument("--out", required=True)
    spec_validate = spec_sub.add_parser("validate", help="Validate an AnalysisGraphSpec JSON file.")
    spec_validate.add_argument("path")
    spec_audit = spec_sub.add_parser("audit", help="Audit semantic quality of an AnalysisGraphSpec.")
    spec_audit.add_argument("path", nargs="?", default="", help="AnalysisGraphSpec JSON. Defaults to --domain's built-in graph.")
    spec_audit.add_argument("--domain", default="perturbseq", help="Domain used when no path is provided and for capability contracts.")
    spec_audit.add_argument("--strict", action="store_true", help="Treat warnings as not ok.")
    spec_audit.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    spec_compile = spec_sub.add_parser("compile", help="Compile natural-language conditions into executable ConditionSpec entries.")
    spec_compile.add_argument("path")
    spec_compile.add_argument("--out", required=True)
    spec_compile.add_argument("--provider", choices=["deterministic", "openai", "anthropic"], default="deterministic")
    spec_compile.add_argument("--domain-context", default="")
    spec_contract = spec_sub.add_parser("contract", help="Print a public contract for an analysis graph or node.")
    spec_contract.add_argument("path", nargs="?", default="", help="AnalysisGraphSpec JSON. Defaults to --domain's built-in graph.")
    spec_contract.add_argument("--domain", default="perturbseq", help="Domain used when no path is provided and for capability contracts.")
    spec_contract.add_argument("--node", default="", help="Optional analysis node id.")
    spec_contract.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    dom = sub.add_parser("domain", help="Browse domain nodes, capabilities, design fields, and core tools.")
    dom_sub = dom.add_subparsers(dest="domain_cmd")
    dom_inspect = dom_sub.add_parser("inspect", help="Show a developer-facing domain browser.")
    dom_inspect.add_argument("--domain", default="perturbseq")
    dom_inspect.add_argument("--no-core-tools", action="store_true", help="Hide core runtime tool catalog.")
    dom_inspect.add_argument("--json", action="store_true")
    dom_caps = dom_sub.add_parser("capabilities", help="List domain capabilities, optionally scoped to a node.")
    dom_caps.add_argument("--domain", default="perturbseq")
    dom_caps.add_argument("--node", default="", help="Optional analysis node id.")
    dom_caps.add_argument("--json", action="store_true")
    dom_tools = dom_sub.add_parser("tools", help="List core runtime tools and permission tiers.")
    dom_tools.add_argument("--readonly", action="store_true", help="Only show local-read tools.")
    dom_tools.add_argument("--json", action="store_true")

    i = sub.add_parser("init", help="Initialize .pertura/ in a project directory.")
    i.add_argument("path", nargs="?", default=".", help="Project path (default: current directory).")

    d = sub.add_parser("doctor", help="Check environment.")
    d.add_argument("--openai", action="store_true")

    claims = sub.add_parser("claims", help="List Pertura-v2 core paper claims and verification commands.")
    claims.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    harness = sub.add_parser("harness", help="Show the public Pertura-v2 harness thesis and vocabulary.")
    harness.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    toolbox = sub.add_parser("toolbox", help="List compact self-audit tools and when to use them.")
    toolbox.add_argument("--purpose", default="audit", help="deliberation, codegen, critic, audit, or report.")
    toolbox.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    ins = sub.add_parser("inspect", help="Inspect a Pertura run directory.")
    ins.add_argument("run_dir", help="Run directory containing events.db.")
    ins.add_argument("--recent", type=int, default=10, help="Number of recent events to show.")
    ins.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    ctx = sub.add_parser("context", help="Show the bounded LLM context dashboard for a run.")
    ctx.add_argument("run_dir", help="Run directory containing events.db.")
    ctx.add_argument("--purpose", default="audit", help="deliberation, codegen, critic, or audit.")
    ctx.add_argument("--max-items", type=int, default=8, help="Maximum recent items per bounded section.")
    ctx.add_argument("--token-budget", type=int, default=6000, help="Soft context budget for the dashboard.")
    ctx.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    audit = sub.add_parser("audit", help="Audit a completed or in-progress run.")
    audit.add_argument("run_dir", help="Run directory containing events.db.")
    audit.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    trace = sub.add_parser("trace", help="Trace upstream evidence or downstream impact for a graph node.")
    trace.add_argument("run_dir", help="Run directory containing events.db.")
    trace.add_argument("node_id", help="Observation, artifact, attempt, conclusion, or other graph node id.")
    trace.add_argument("--depth", type=int, default=4, help="Maximum graph walk depth.")
    trace.add_argument("--impact", action="store_true", help="Walk downstream impact instead of upstream provenance.")
    trace.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    evidence = sub.add_parser("evidence", help="Review whether a node's evidence chain is verified.")
    evidence.add_argument("run_dir", help="Run directory containing events.db.")
    evidence.add_argument("node_id", nargs="?", default="", help="Conclusion, observation, or artifact id. Defaults to latest conclusion/observation.")
    evidence.add_argument("--limit", type=int, default=12, help="Maximum support ids to inspect.")
    evidence.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    rethink = sub.add_parser("rethink", help="Plan trace-driven repair, branch, or reinterpretation for a questionable node.")
    rethink.add_argument("run_dir", help="Run directory containing events.db.")
    rethink.add_argument("node_id", nargs="?", default="", help="Finding, conclusion, observation, artifact, or attempt id. Defaults to inferred latest issue.")
    rethink.add_argument("--issue", default="", help="Short description of the suspicious/weak/stale result.")
    rethink.add_argument("--depth", type=int, default=5, help="Maximum trace/impact depth.")
    rethink.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    capsule = sub.add_parser("capsule", help="Export a portable run capsule for audit/review.")
    capsule.add_argument("run_dir", help="Run directory containing events.db.")
    capsule.add_argument("--out", default="", help="Output JSON path. Defaults to RUN_DIR/run_capsule.json.")
    capsule.add_argument("--verify", action="store_true", help="Verify an existing capsule against the run store.")
    capsule.add_argument("--capsule-path", default="", help="Capsule JSON to verify. Defaults to RUN_DIR/run_capsule.json.")
    capsule.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    diff = sub.add_parser("diff", help="Compare two Pertura run directories.")
    diff.add_argument("run_a", help="First run directory.")
    diff.add_argument("run_b", help="Second run directory.")
    diff.add_argument("--json", action="store_true", help="Print full machine-readable diff.")

    rep = sub.add_parser("replay", help="Replay a run and verify stored projections.")
    rep.add_argument("run_dir", help="Run directory containing events.db.")
    rep.add_argument("--no-strict", action="store_true", help="Report mismatches instead of failing.")
    rep.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    fk = sub.add_parser("fork", help="Fork a run at a specific event id.")
    fk.add_argument("source_run_dir", help="Source run directory containing events.db.")
    fk.add_argument("event_id", help="Inclusive fork point event_id.")
    fk.add_argument("--out", default="", help="Output run directory. Defaults beside source run.")
    fk.add_argument("--run-id", default="", help="New run id. Defaults to source-derived id.")
    fk.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    args = p.parse_args()

    if args.cmd == "chat":
        return _chat(args)
    if args.cmd == "run":
        if args.model:
            os.environ["OPENAI_MODEL"] = args.model
        if args.base_url:
            os.environ["OPENAI_BASE_URL"] = args.base_url
        cfg = _resolve_cli_config(args, workspace=args.workspace)
        wb = Workbench(domain=_load_domain(cfg["domain"], analysis_graph_path=cfg["analysis_graph"]),
                       provider=cfg["provider"], sandbox=cfg["sandbox"])
        result = wb.run(args.workspace, goal=args.goal, steps=cfg["steps"])
        print(json.dumps(result, indent=2))
        _print_run_summary(wb)
        return 0
    if args.cmd == "serve":
        cfg = _resolve_cli_config(args)
        wb = Workbench(domain=_load_domain(cfg["domain"], analysis_graph_path=cfg["analysis_graph"]))
        wb.serve(args.port)
        return 0
    if args.cmd == "spec":
        return _spec_cmd(args)
    if args.cmd == "domain":
        return _domain_cmd(args)
    if args.cmd == "init":
        from pertura.skills import init_pertura_dir
        p = Path(args.path).resolve()
        bb = init_pertura_dir(p)
        print(f"Created {bb}")
        print("  PERTURA.md    edit with your project instructions")
        print("  settings.json configure domain, provider, budget")
        print("  analysis_graph.json editable analysis-node graph")
        print("  domain.json   editable domain/capability pack")
        print("  skills/       add domain-specific SKILL.md files")
        print("  hooks/        add pre/post execution hooks")
        print("Next: pertura spec audit .pertura/analysis_graph.json --domain perturbseq")
        return 0
    if args.cmd == "doctor":
        return _doctor(args)
    if args.cmd == "claims":
        return _claims_cmd(args)
    if args.cmd == "harness":
        return _harness_cmd(args)
    if args.cmd == "toolbox":
        return _toolbox_cmd(args)
    if args.cmd == "inspect":
        return _inspect_cmd(args)
    if args.cmd == "context":
        return _context_cmd(args)
    if args.cmd == "audit":
        return _audit_cmd(args)
    if args.cmd == "trace":
        return _trace_cmd(args)
    if args.cmd == "evidence":
        return _evidence_cmd(args)
    if args.cmd == "rethink":
        return _rethink_cmd(args)
    if args.cmd == "capsule":
        return _capsule_cmd(args)
    if args.cmd == "diff":
        return _diff_cmd(args)
    if args.cmd == "replay":
        return _replay_cmd(args)
    if args.cmd == "fork":
        return _fork_cmd(args)
    p.print_help()
    return 2


# Rich terminal setup

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    console = Console()
    RICH = True
except ImportError:
    RICH = False


def _print(*args, **kwargs):
    if RICH:
        for a in args:
            if isinstance(a, Panel):
                console.print(a, **kwargs)
            else:
                console.print(a, **kwargs)
    else:
        text = " ".join(str(a) for a in args)
        print(text)


def _panel(content, title="", style="", **kw):
    if RICH:
        return Panel(content, title=title, border_style=style, padding=(0, 1), **kw)
    return str(content)


def _workbench_view_for_cli(wb, *, max_items: int = 5) -> dict:
    """Use the same bounded projection as the GUI for terminal dashboards."""
    try:
        from pertura._api import workbench_view_payload
        return workbench_view_payload(wb, max_items=max_items, token_budget=4500)
    except Exception as exc:
        return {"error": str(exc)}


def _lane_counts_for_cli(wb) -> dict[str, int]:
    try:
        graph = wb._store.read_graph() if getattr(wb, "_store", None) else {}
    except Exception:
        graph = {}
    lanes = {"Inputs": 0, "Attempts": 0, "Artifacts": 0, "Observations": 0, "Conclusions": 0}
    for node in (graph or {}).get("nodes", []):
        lane = _lane_for_cli(node.get("node_type", ""))
        lanes[lane] = lanes.get(lane, 0) + 1
    return lanes


def _lane_for_cli(node_type: str) -> str:
    text = str(node_type or "").lower()
    if text in {"workspace", "dataset", "metadata", "description", "parameter_set", "analysis_node", "branch"}:
        return "Inputs"
    if text in {"attempt", "tool_call", "code_cell", "intervention", "diagnosis", "backward_trace"}:
        return "Attempts"
    if text in {"artifact", "outcome"}:
        return "Artifacts"
    if text in {"conclusion", "report"}:
        return "Conclusions"
    return "Observations"


def _print_workbench_dashboard(wb, *, title: str = "Workbench") -> None:
    view = _workbench_view_for_cli(wb)
    if view.get("error"):
        _print(_panel(f"[red]{view['error']}[/]", title=title, style="red"))
        return

    status = view.get("status", {}) or {}
    active = view.get("active", {}) or {}
    review = view.get("review", {}) or {}
    analysis = view.get("analysis", {}) or {}
    contract = analysis.get("active_node_contract", {}) or {}
    runtime = contract.get("runtime", {}) or {}
    caps = contract.get("capabilities", []) or []
    recent = (view.get("activity", {}) or {}).get("recent_attempts", []) or []
    open_items = [
        *review.get("open_interrupts", []),
        *review.get("open_triggers", []),
        *review.get("open_findings", []),
    ]
    lanes = _lane_counts_for_cli(wb)

    if not RICH:
        _print(f"{title}: phase={status.get('phase') or status.get('state')} node={active.get('node_id') or '-'}")
        _print(
            "  "
            f"attempts={status.get('attempts', 0)} obs={status.get('observations', 0)} "
            f"artifacts={status.get('artifacts', 0)} issues={len(open_items)}"
        )
        _print("  lanes: " + " -> ".join(f"{k}:{v}" for k, v in lanes.items()))
        if caps:
            _print("  capabilities: " + ", ".join((c.get("id") or c.get("capability_id") or "") for c in caps[:6]))
        if open_items:
            _print("  review: " + "; ".join(str(i.get("summary") or i.get("question") or "")[:90] for i in open_items[:3]))
        return

    lane_text = "  ".join(f"[dim]{name}[/] [bold]{count}[/]" for name, count in lanes.items())
    ready = set(runtime.get("ready_capabilities", []) or [])
    cap_rows = []
    for cap in caps[:7]:
        cid = cap.get("id") or cap.get("capability_id") or ""
        tag = "[green]ready[/]" if cid in ready else "[dim]contract[/]"
        cap_rows.append(f"{cid} {tag}")
    cap_text = "\n".join(cap_rows) if cap_rows else "[dim]No active capability contract.[/]"

    review_lines = []
    for item in open_items[:4]:
        summary = item.get("summary") or item.get("question") or item.get("source") or ""
        severity = item.get("severity") or ("interrupt" if item.get("interrupt_id") else "review")
        review_lines.append(f"[yellow]{severity}[/] {str(summary)[:110]}")
    review_text = "\n".join(review_lines) if review_lines else "[green]No open findings or interrupts.[/]"

    recent_lines = []
    for item in recent[:4]:
        recent_lines.append(
            f"{str(item.get('title') or item.get('attempt_id') or '')[:42]}  "
            f"[dim]{item.get('analysis_node_id') or '-'}[/]  "
            f"{item.get('outcome_status') or item.get('status') or '-'}  "
            f"{item.get('observations', 0)}/{item.get('artifacts', 0)} obs/art"
        )
    recent_text = "\n".join(recent_lines) if recent_lines else "[dim]No attempts yet.[/]"

    body = (
        f"[bold]phase[/] {status.get('phase') or status.get('state') or '-'}   "
        f"[bold]node[/] {active.get('node_id') or '-'}   "
        f"[bold]branch[/] {active.get('branch_id') or '-'}\n"
        f"[bold]attempts[/] {status.get('attempts', 0)}   "
        f"[bold]obs[/] {status.get('observations', 0)}   "
        f"[bold]artifacts[/] {status.get('artifacts', 0)}   "
        f"[bold]open review[/] {len(open_items)}   "
        f"[bold]budget[/] {view.get('budget', {}).get('max_attempts', '-')}\n\n"
        f"[bold]Derivation lanes[/]\n{lane_text}\n\n"
        f"[bold]Active capabilities[/]\n{cap_text}\n\n"
        f"[bold]Recent attempts[/]\n{recent_text}\n\n"
        f"[bold]Review[/]\n{review_text}"
    )
    _print(Panel(body, title=title, border_style="bright_blue", padding=(0, 1)))


# Chat

def _chat(args) -> int:
    import readline  # noqa

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url

    workspace = args.workspace or "/tmp/pertura_ws"
    cfg = _resolve_cli_config(args, workspace=args.workspace or "")
    wb = Workbench(domain=_load_domain(cfg["domain"], analysis_graph_path=cfg["analysis_graph"]), provider=cfg["provider"])

    # Startup banner

    _print()
    ws_label = f"[bold white]{workspace}[/]" if args.workspace else f"[dim]{workspace} (no data yet)[/]"
    _print(_panel(
        f"  {ws_label}\n"
        f"  domain: [cyan]{cfg['domain']}[/]  -  model: [cyan]{cfg['provider']}[/]\n"
        f"  [dim]Set workspace: [bold]ws /path/to/data[/]  -  [bold]quit[/] to exit.[/]",
        title="Pertura", style="bright_blue",
    ))
    _print("  [dim]Initializing...[/]", end="\r")

    wb.run(workspace, goal="", steps=0)  # init only

    snap = wb._store.read_snapshot()
    _print("  [green]Ready.[/] " + _status_line(snap))
    _print_workbench_dashboard(wb, title="Workbench State")

    # Loop

    while True:
        try:
            if RICH:
                cmd = console.input("\n[bold bright_blue]>[/] ").strip()
            else:
                cmd = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            _print()
            break

        if not cmd:
            continue
        if cmd.lower() in ("q", "quit", "exit"):
            _print(_panel("[dim]Shutting down...[/]", style="dim"))
            break

        if cmd.lower().startswith("ws "):
            new_ws = cmd[3:].strip()
            workspace = new_ws
            _print(_panel(f"Workspace -> [bold]{new_ws}[/]", title="Config", style="dim"))
            wb.run(new_ws, goal="", steps=0)
            continue

        # Handle interrupt if open
        snapshot = wb._store.read_snapshot()
        open_intr = next((i for i in snapshot.interrupts if i.status == "open"), None)

        if open_intr:
            _print(_panel(
                f"[bold bright_yellow]Answered:[/] {cmd}",
                title="Interrupt Resolved", style="bright_yellow",
            ))
            wb.answer(open_intr.interrupt_id, cmd)
            action = _run_with_progress(wb)
            if action != "error":
                _print(*_step_result(action, wb))
                _print_workbench_dashboard(wb, title="Workbench State")
            continue

        # Normal instruction
        _print(_panel(f"[bold white]{cmd}[/]", title="You", style="dim"))
        wb._emit("goal_recorded", {"goal": {"goal_id": f"goal_{uuid4().hex[:8]}", "text": cmd, "status": "active"}})

        action = _run_with_progress(wb)
        _print(*_step_result(action, wb))
        _print_workbench_dashboard(wb, title="Workbench State")

    # Cleanup
    wb.report()
    _print(f"\n[dim]Report -> {wb._store.run_dir / 'report.html'}[/]")
    _print(f"[dim]Notebooks -> {wb._store.run_dir / 'notebooks'}[/]")
    return 0


def _run_with_progress(wb) -> str:
    """Run fully automatically until a stopping point.

    One user input runs plan->execute->review->intervene->plan->... continuously.
    Stops only on: waiting_for_human, complete, blocked, error.
    Each execution shows an inline result card.
    """
    phases = {
        "planning": "Planning...", "executing_attempt": "Running code...",
        "reviewing_outcome": "Reviewing...", "diagnosing": "Diagnosing...",
        "planning_intervention": "Planning fix...", "waiting_for_human": "Needs input.",
        "complete": "Done.", "paused": "Paused.",
    }
    action = "no_action"
    try:
        for _ in range(15):  # safety limit: ~5 full plan->execute->review cycles
            snap = wb._store.read_snapshot()
            phase = snap.phase if snap else ""
            _print(f"  [dim]{phases.get(phase, 'Working...')}[/]")
            action = wb.step(1)[0]

            # Show LLM's thinking before action
            if action in ("planned_attempt", "planned_intervention", "applied_intervention"):
                fresh = wb._store.read_snapshot()
                rd = fresh.review_decisions[-1] if fresh.review_decisions else None
                if rd:
                    _print(f"  [dim]-> {rd.assessment_summary[:140]}[/]")

            # Code preview: like CC's code blocks
            if action == "planned_attempt":
                fresh_snap = wb._store.read_snapshot()
                a = next((a for a in fresh_snap.attempts if a.attempt_id == fresh_snap.active_attempt), None)
                if a and a.notebook_cells:
                    code = a.notebook_cells[0].get("source", "") if isinstance(a.notebook_cells[0], dict) else ""
                    lines = code.strip().split("\n")
                    if RICH:
                        from rich.syntax import Syntax
                        preview = Syntax(code[:2000], "python", theme="material", line_numbers=False,
                                         background_color="default")
                        _print(_panel(preview, title=f"Code ({a.stage or '?'})", style="bright_black"))
                    else:
                        pre = "\n".join(f"  | {l[:90]}" for l in lines[:10])
                        if len(lines) > 10:
                            pre += f"\n  | ... ({len(lines)} lines total)"
                        _print(f"  +-- Code ({a.stage or '?'}) --\n{pre}\n  +{'-' * 30}")

            if action == "executed_attempt":
                o = snap.outcomes[-1] if snap.outcomes else None
                a = next((a for a in snap.attempts if a.attempt_id == o.attempt_id), None) if o else None
                obs = len([x for x in snap.observations if x.attempt_id == o.attempt_id]) if o else 0
                status = o.status if o else "?"
                sc = "bright_green" if status == "success" else "bright_red"
                sym = "OK" if status == "success" else "FAIL"
                _print(f"  [{sc}]{sym}[/] [{sc}]{status}[/] - [green]{obs} obs[/]")
                if o and o.metrics:
                    stderr = (o.metrics.get("stderr", "") or "").strip()
                    if stderr:
                        _print(f"  [yellow]{stderr[-400:]}[/]")

            if action == "waiting_for_human":
                break
            if action in ("complete", "blocked", "error", "responded"):
                break
            # Everything else: continue looping

        return action
    except Exception as exc:
        _print(_panel(f"[red]{exc}[/]", title="Failed", style="bright_red"))
        return "error"


def _status_line(snap) -> str:
    phase = getattr(snap, "phase", "?")
    pc = {"executing": "green", "reviewing": "bright_yellow", "diagnosing": "yellow",
          "planning": "bright_blue", "waiting_for_human": "bright_red",
          "complete": "green", "paused": "dim"}.get(phase, "dim")
    node = ""
    if getattr(snap, "active_node_id", ""):
        node = f"  node: [bright_cyan]{snap.active_node_id}[/]"
    design = ""
    if snap.design:
        fields = []
        for k, v in snap.design.items():
            if isinstance(v, list):
                fields.append(f"{k}=[{len(v)} items]")
            elif isinstance(v, str) and v:
                fields.append(f"{k}={v}")
        if fields:
            meta = getattr(snap, "design_meta", {})
            confirmed = []
            for f in fields:
                key = f.split("=")[0]
                src = meta.get(key, {}).get("source", "")
                tag = "[green]ok[/]" if src in {"pi_confirmed", "user_confirmed"} else "[yellow]?[/]"
                confirmed.append(f"{f} {tag}")
            design = f"  design: [dim]{', '.join(confirmed)}[/]"
    return (
        f"phase: [{pc}]{phase}[/]  |  "
        f"attempts: [cyan]{len(getattr(snap, 'attempts', []))}[/]  |  "
        f"obs: [green]{len(getattr(snap, 'observations', []))}[/]  |  "
        f"branches: [magenta]{len(getattr(snap, 'branches', []))}[/]"
        + node + design
    )


def _step_result(action: str, wb):
    """Render the result of one step. Returns list of items for _print()."""
    snap = wb._store.read_snapshot()

    if action in ("planned_attempt",):
        a = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt), None)
        body = f"[bold]{a.title if a else 'Planning'}[/]" if a else "Planning next step..."
        if a:
            body += f"\nStage: [bright_cyan]{a.stage}[/]"
            if a.notebook_cells:
                body += f"\nCells: {len(a.notebook_cells)} code blocks"
        return [_render("Plan", body, "bright_blue"), _status_line(snap)]

    if action in ("executed_attempt",):
        a = next((a for a in snap.attempts if a.attempt_id == snap.active_attempt), None)
        o = next((o for o in snap.outcomes if o.attempt_id == snap.active_attempt), None)
        obs_count = len([x for x in snap.observations if x.attempt_id == snap.active_attempt])
        status = o.status if o else "?"
        sc = "bright_green" if status == "success" else "bright_red"
        body = f"[bold]{a.title if a else 'Attempt'}[/] -> [{sc}]{status}[/]"
        if obs_count:
            body += f"  -  [green]{obs_count} observations[/]"
        if o and o.summary:
            body += f"\n[dim]{o.summary[:300]}[/]"

        from pertura.memory.compiler import compile_context
        ctx = compile_context(snap)
        for m in ctx.memory[:3]:
            if m.signal == "new":
                continue
            clr = {"conflict": "bright_red", "warning": "bright_yellow", "thin": "dim", "agreement": "bright_green"}.get(m.signal, "dim")
            body += f"\n  [{clr}]* {m.signal.upper()}[/] {m.subject}"

        return [_render("Run", body, sc), "  " + _status_line(snap)]

    if action in ("applied_intervention", "planned_intervention"):
        recent = snap.interventions[-1] if snap.interventions else None
        body = f"[bold]{recent.intervention_type}[/]\n{recent.summary[:250]}" if recent else "Intervention applied."
        return [_render("Fix", body, "bright_yellow"), "  " + _status_line(snap)]

    if action == "waiting_for_human":
        for intr in snap.interrupts:
            if intr.status == "open":
                opts = "  -  ".join(intr.options) if intr.options else "Type your answer below"
                body = f"[bold bright_red]Input needed: {intr.question}[/]\n\n{opts}"
                return [_render("Paused", body, "bright_red")]
        return [_render("Paused", "Waiting for input.", "bright_red")]

    if action == "responded":
        rs = getattr(snap, "assistant_responses", [])
        body = rs[-1].text if rs else ""
        return [_render("Response", body, "bright_blue")]

    if action == "complete":
        return [_render("Done", "[bright_green]Analysis complete. Type anything to continue or [bold]quit[/].[/]", "bright_green")]

    if action in ("blocked", "no_snapshot", "no_action"):
        return [_render("Blocked", f"[yellow]Step returned '{action}'.[/]", "bright_yellow")]

    return [_render("Step", f"Action: {action}", "dim")]


def _render(title: str, body: str, style: str):
    if RICH:
        return Panel(body.strip() or title, title=title, border_style=style, padding=(0, 1))
    return f"[{title}] {body.strip()}"


# Run / Doctor / Helpers

def _print_run_summary(wb):
    s = wb.status
    rpt = wb.report()
    _print(f"\n[bold]Done.[/] {s.get('attempts', 0)} attempts, {s.get('observations', 0)} observations")
    _print_workbench_dashboard(wb, title="Run Dashboard")
    cons = rpt.get("conclusions", []) or rpt.get("summary", {}).get("conclusions", [])
    mem = rpt.get("memory_signals", []) or rpt.get("memory", [])
    cov = rpt.get("coverage", [])
    if cons:
        _print("[bold]Conclusions:[/]")
        for c in (cons if isinstance(cons, list) else [cons]):
            if isinstance(c, dict):
                _print(f"  [{c.get('grade', '?')}] {c.get('text', str(c))[:200]}")
    if mem:
        _print("[bold]Memory:[/]")
        for m in mem[:5]:
            _print(f"  [{m.get('signal', '?')}] {m.get('subject', '')}")
    if cov:
        _print("[bold]Coverage:[/]")
        for c in cov[:5]:
            _print(f"  {c.get('subject', '')}: {c.get('label', '')}")


def _inspect_cmd(args) -> int:
    payload = _inspect_run_dir(Path(args.run_dir), recent=args.recent)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Run:[/] {payload.get('run_id') or '?'}")
    _print(f"  dir: {payload['run_dir']}")
    _print(f"  events: {payload['event_count']}  phase: {payload['snapshot'].get('phase', '?')}")
    _print(
        "  graph: "
        f"{payload['graph'].get('nodes', 0)} nodes / {payload['graph'].get('edges', 0)} edges"
    )
    replay = payload.get("replay", {})
    if replay:
        _print(
            "  replay: "
            f"snapshot={replay.get('snapshot_matches_store')} "
            f"graph={replay.get('graph_matches_store')}"
        )
    _print("[bold]Event types:[/]")
    for event_type, count in payload["event_types"].items():
        _print(f"  {event_type}: {count}")
    if payload["recent_events"]:
        _print("[bold]Recent events:[/]")
        for event in payload["recent_events"]:
            keys = ",".join(event.get("payload_keys", []))
            _print(f"  {event['event_id']}  {event['event_type']}  actor={event['actor']}  keys={keys}")
    return 0


def _inspect_run_dir(run_dir: Path | str, *, recent: int = 10) -> dict:
    from pertura.core import Store
    from pertura.core.replay import ReplayError, replay_store

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    events = store.read_events()
    snap = store.read_snapshot()
    graph = store.read_graph() or {"nodes": [], "edges": []}
    event_types: dict[str, int] = {}
    for event in events:
        event_types[event.event_type] = event_types.get(event.event_type, 0) + 1
    event_types = dict(sorted(event_types.items(), key=lambda item: item[0]))
    replay = {}
    if events:
        try:
            result = replay_store(store, strict=False)
            replay = {
                "snapshot_matches_store": result.snapshot_matches_store,
                "graph_matches_store": result.graph_matches_store,
            }
        except ReplayError as exc:
            replay = {"error": str(exc)}
    recent_events = [] if recent <= 0 else events[-recent:]
    return {
        "run_dir": str(run_dir),
        "run_id": snap.run_id if snap else "",
        "event_count": len(events),
        "event_types": event_types,
        "snapshot": {
            "phase": snap.phase if snap else "",
            "attempts": len(snap.attempts) if snap else 0,
            "observations": len(snap.observations) if snap else 0,
            "artifacts": len(snap.artifacts) if snap else 0,
            "findings": len(snap.findings) if snap else 0,
            "branches": len(snap.branches) if snap else 0,
            "active_node_id": snap.active_node_id if snap else "",
            "active_branch": snap.active_branch if snap else "",
            "active_attempt": snap.active_attempt if snap else "",
        },
        "graph": {
            "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
        },
        "replay": replay,
        "recent_events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "actor": event.actor,
                "timestamp": event.timestamp,
                "payload_keys": sorted((event.payload or {}).keys()),
            }
            for event in recent_events
        ],
    }


def _context_cmd(args) -> int:
    payload = _context_run_dir(
        Path(args.run_dir),
        purpose=args.purpose,
        max_items=args.max_items,
        token_budget=args.token_budget,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    protected = payload.get("protected_context", {})
    runtime_symbols = payload.get("runtime_symbols", {})
    active_contract = payload.get("active_contract", {})
    risks = payload.get("risks_and_gates", {})
    audit_preview = payload.get("audit_preview", {})
    budget = payload.get("budget_report", {})
    _print(f"[bold]Context Review:[/] {payload.get('run_id') or '?'}")
    _print(f"  purpose: {payload.get('purpose')}  phase: {protected.get('phase', '?')}")
    _print(f"  active node: {protected.get('active_node_id') or '?'}")
    selected = active_contract.get("selected_capability") or {}
    if selected:
        _print(f"  selected capability: {selected.get('id')}")
    _print(f"  runtime symbols: {len(runtime_symbols)}")
    if runtime_symbols:
        for name, meta in list(runtime_symbols.items())[:5]:
            detail = meta.get("shape") or meta.get("type") or meta.get("kind", "")
            _print(f"    {name}: {detail}")
    _print(f"  risks: {len(risks.get('findings', []))} findings, {len(risks.get('open_interrupts', []))} interrupts")
    if audit_preview:
        _print(
            "  audit: "
            f"ok={audit_preview.get('ok')} severity={audit_preview.get('severity')} "
            f"issues={', '.join(audit_preview.get('top_issue_codes', [])[:4]) or 'none'}"
        )
        next_actions = audit_preview.get("next_actions", [])
        if next_actions:
            _print(f"  audit next actions: {', '.join(item.get('tool', '') for item in next_actions[:5])}")
    rethinking = payload.get("trace_driven_rethinking", {})
    if rethinking:
        _print(
            "  rethinking: "
            f"status={rethinking.get('status')} target={rethinking.get('target_id') or '?'} "
            f"actions={', '.join(item.get('tool', '') for item in rethinking.get('recommended_actions', [])[:4]) or 'none'}"
        )
    _print(f"  affordances: {', '.join(item.get('tool', '') for item in payload.get('affordances', [])[:6])}")
    _print(f"  budget estimate: {budget.get('used_estimate', 0)} / {budget.get('token_budget', '?')}")
    return 0


def _context_run_dir(
    run_dir: Path | str,
    *,
    purpose: str = "audit",
    max_items: int = 8,
    token_budget: int = 6000,
) -> dict:
    from pertura.core import Store
    from pertura.core.views import build_view

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    snap = store.read_snapshot()
    if not snap:
        raise ValueError(f"No snapshot found in {run_dir}")
    return build_view(
        snap,
        store.read_graph() or {"nodes": [], "edges": []},
        purpose=purpose,
        max_items=max_items,
        token_budget=token_budget,
    )


def _audit_cmd(args) -> int:
    payload = _audit_run_dir(Path(args.run_dir))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if payload.get("ok") else 1
    summary = payload.get("summary", {})
    _print(f"[bold]Run Audit:[/] {payload.get('run_id') or '?'}")
    _print(
        "  "
        f"ok={payload.get('ok')} severity={payload.get('severity')} "
        f"errors={summary.get('errors', 0)} warnings={summary.get('warnings', 0)} "
        f"attempts={summary.get('attempts', 0)} observations={summary.get('observations', 0)} "
        f"artifacts={summary.get('artifacts', 0)} conclusions={summary.get('conclusions', 0)}"
    )
    for section in ("errors", "warnings"):
        items = payload.get(section, [])
        if not items:
            continue
        _print(f"[bold]{section.title()}:[/]")
        for item in items[:12]:
            _print(f"  - {item.get('code')}: {item.get('message')}")
        if len(items) > 12:
            _print(f"  ... {len(items) - 12} more")
    advice = payload.get("advice", [])
    if advice:
        _print("[bold]Advice:[/]")
        for item in advice[:8]:
            _print(f"  - {item.get('message')}")
    next_actions = payload.get("next_actions", [])
    if next_actions:
        _print("[bold]Next Actions:[/]")
        for item in next_actions[:8]:
            args = item.get("args") or {}
            args_text = f" {args}" if args else ""
            _print(f"  - {item.get('tool')}{args_text}: {item.get('why')}")
    return 0 if payload.get("ok") else 1


def _audit_run_dir(run_dir: Path | str) -> dict:
    from pertura.core import Store
    from pertura.core.audit import audit_run

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    snap = store.read_snapshot()
    if not snap:
        raise ValueError(f"No snapshot found in {run_dir}")
    return audit_run(snap, store.read_graph() or {"nodes": [], "edges": []}, run_dir=run_dir)


def _trace_cmd(args) -> int:
    payload = _trace_run_dir(
        Path(args.run_dir),
        args.node_id,
        depth=args.depth,
        impact=bool(args.impact),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if payload.get("found") else 1
    _print(
        f"[bold]{'Impact' if payload.get('direction') == 'downstream' else 'Trace'}:[/] "
        f"{payload.get('node_id')}  direction={payload.get('direction')} depth={payload.get('depth')}"
    )
    _print(f"  found={payload.get('found')} nodes={payload.get('node_count', 0)} edges={payload.get('edge_count', 0)}")
    if payload.get("relation_summary"):
        by_category = payload["relation_summary"].get("by_category", {})
        if by_category:
            _print(f"  relation categories: {', '.join(f'{k}={v}' for k, v in by_category.items())}")
    if payload.get("nodes"):
        _print("[bold]Nodes:[/]")
        for node in payload["nodes"][:12]:
            _print(f"  - {node.get('node_id')} [{node.get('node_type')}] {node.get('label')}")
    if payload.get("edges"):
        _print("[bold]Edges:[/]")
        for edge in payload["edges"][:12]:
            _print(f"  - {edge.get('source_id')} -[{edge.get('edge_type')}]-> {edge.get('target_id')}")
    return 0 if payload.get("found") else 1


def _trace_run_dir(run_dir: Path | str, node_id: str, *, depth: int = 4, impact: bool = False) -> dict:
    from pertura.core import Store
    from pertura.core.graph import impact_of_change, trace_upstream

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    graph = store.read_graph() or {"nodes": [], "edges": []}
    safe_depth = max(1, min(int(depth or 4), 20))
    view = impact_of_change(graph, node_id, depth=safe_depth) if impact else trace_upstream(graph, node_id, depth=safe_depth)
    found = any(node.get("node_id") == node_id for node in graph.get("nodes", []))
    return {
        "run_dir": str(run_dir),
        "run_id": graph.get("run_id", ""),
        "node_id": node_id,
        "found": found,
        "direction": view.get("direction", "downstream" if impact else "upstream"),
        "depth": view.get("depth", safe_depth),
        "node_count": len(view.get("nodes", [])),
        "edge_count": len(view.get("edges", [])),
        "nodes": view.get("nodes", []),
        "edges": view.get("edges", []),
        "relation_summary": view.get("relation_summary", {}),
        "affected": view.get("affected", {}),
    }


def _evidence_cmd(args) -> int:
    payload = _evidence_run_dir(Path(args.run_dir), args.node_id, limit=args.limit)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if payload.get("ok") else 1
    _print(f"[bold]Evidence Review:[/] {payload.get('node_id') or '?'}  status={payload.get('status')}")
    _print(f"  ok={payload.get('ok')} found={payload.get('found')} type={payload.get('node_type', '?')}")
    if payload.get("summary"):
        _print(f"  {payload.get('summary')}")
    failed = [item for item in payload.get("checks", []) if not item.get("ok")]
    if failed:
        _print("[bold]Failed Checks:[/]")
        for item in failed[:8]:
            _print(f"  - {item.get('check')}: {item}")
    next_actions = payload.get("next_actions", [])
    if next_actions:
        _print("[bold]Next Actions:[/]")
        for item in next_actions[:6]:
            _print(f"  - {item.get('tool')} {item.get('args') or {}}: {item.get('why')}")
    return 0 if payload.get("ok") else 1


def _evidence_run_dir(run_dir: Path | str, node_id: str = "", *, limit: int = 12) -> dict:
    from pertura.core import Store
    from pertura.core.evidence_chain import review_evidence_chain

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    snap = store.read_snapshot()
    if not snap:
        raise ValueError(f"No snapshot found in {run_dir}")
    return {
        "run_dir": str(run_dir),
        "run_id": snap.run_id,
        **review_evidence_chain(
            snap,
            node_id=node_id,
            graph=store.read_graph() or {"nodes": [], "edges": []},
            limit=limit,
        ),
    }


def _rethink_cmd(args) -> int:
    payload = _rethinking_run_dir(
        Path(args.run_dir),
        args.node_id,
        issue=getattr(args, "issue", "") or "",
        depth=getattr(args, "depth", 5),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Trace-driven Rethinking:[/] {payload.get('run_id') or '?'}")
    _print(f"  target: {payload.get('target_id') or '?'}")
    _print(f"  status: {payload.get('status')}")
    if payload.get("issue"):
        _print(f"  issue: {payload.get('issue')}")
    if payload.get("summary"):
        _print(f"  summary: {payload.get('summary')}")
    roots = payload.get("suspected_roots", [])
    if roots:
        _print("[bold]Suspected roots:[/]")
        for root in roots[:8]:
            _print(f"  - {root.get('root_id')} ({root.get('root_type')}): {root.get('reason')}")
    actions = payload.get("recommended_actions", [])
    if actions:
        _print("[bold]Recommended actions:[/]")
        for action in actions[:10]:
            _print(f"  - {action.get('tool')} {action.get('args') or {}}: {action.get('why')}")
    return 0


def _rethinking_run_dir(run_dir: Path | str, node_id: str = "", *, issue: str = "", depth: int = 5) -> dict:
    from pertura.core import Store
    from pertura.core.rethinking import plan_rethinking

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    snap = store.read_snapshot()
    if not snap:
        raise ValueError(f"No snapshot found in {run_dir}")
    graph = store.read_graph() or {"nodes": [], "edges": []}
    return {
        "run_dir": str(run_dir),
        "run_id": snap.run_id,
        **plan_rethinking(snap, node_id, issue=issue, depth=depth, graph=graph),
    }


def _capsule_cmd(args) -> int:
    if getattr(args, "verify", False):
        payload = _capsule_verify_run_dir(Path(args.run_dir), capsule_path=getattr(args, "capsule_path", "") or "")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return 0 if payload.get("ok") else 1
        _print(f"[bold]Capsule Verify:[/] {payload.get('run_id') or '?'}")
        _print(f"  capsule: {payload.get('capsule_path')}")
        _print(f"  ok: {payload.get('ok')}")
        failed = [item for item in payload.get("checks", []) if not item.get("ok")]
        if failed:
            _print("[bold]Mismatches:[/]")
            for item in failed[:8]:
                _print(f"  - {item.get('key')}: expected={item.get('expected')} actual={item.get('actual')}")
        return 0 if payload.get("ok") else 1
    payload = _capsule_run_dir(Path(args.run_dir), out_path=getattr(args, "out", "") or "")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Run Capsule:[/] {payload.get('run_id') or '?'}")
    _print(f"  path: {payload.get('path')}")
    audit = payload.get("audit", {})
    _print(f"  audit: ok={audit.get('ok')} severity={audit.get('severity')}")
    _print(f"  events: {payload.get('replay', {}).get('event_count', 0)}")
    if payload.get("claim_checks"):
        _print("[bold]Core claims:[/]")
        for claim in payload["claim_checks"][:6]:
            summary = claim.get("summary", {})
            claim_label = claim.get("paper_claim_id") or claim.get("claim_id")
            _print(
                "  - "
                f"{claim_label}: {claim.get('status')} "
                f"({summary.get('passed', 0)}/{summary.get('total', 0)} checks)"
            )
    if payload.get("operator_commands"):
        _print("[bold]Operator commands:[/]")
        for command in payload["operator_commands"][:6]:
            _print(f"  - {command}")
    return 0


def _capsule_run_dir(run_dir: Path | str, *, out_path: str | Path = "") -> dict:
    from datetime import datetime, timezone
    from pertura.core import Store, build_harness_manifest
    from pertura.core.replay import run_integrity
    from pertura.reporting import _provenance_manifest

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    store = Store(run_dir)
    snap = store.read_snapshot()
    if not snap:
        raise ValueError(f"No snapshot found in {run_dir}")
    graph = store.read_graph() or {"nodes": [], "edges": []}
    inspect_payload = _inspect_run_dir(run_dir, recent=5)
    replay_payload = _replay_run_dir(run_dir, strict=True)
    integrity_payload = run_integrity(store)
    audit_payload = _audit_run_dir(run_dir)
    context_payload = _context_run_dir(run_dir, purpose="audit", max_items=6, token_budget=6000)
    provenance = _provenance_manifest(snap, graph, limit=80)
    harness_manifest = build_harness_manifest()
    trace_commands = _capsule_trace_commands(run_dir, audit_payload, provenance)
    out = Path(out_path) if out_path else run_dir / "run_capsule.json"
    claim_checks = _capsule_claim_checks(
        run_dir,
        snap=snap,
        graph=graph,
        audit_payload=audit_payload,
        context_payload=context_payload,
        replay_payload=replay_payload,
        provenance=provenance,
    )
    claim_verification = _capsule_claim_verification(claim_checks, harness_manifest=harness_manifest)
    capsule = {
        "capsule_type": "pertura_run_capsule",
        "version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "run_id": snap.run_id,
        "workspace": snap.workspace,
        "goal": snap.goal,
        "domain": snap.domain,
        "harness_manifest": harness_manifest,
        "summary": {
            "phase": snap.phase,
            "attempts": len(snap.attempts),
            "observations": len(snap.observations),
            "artifacts": len(snap.artifacts),
            "conclusions": len(snap.conclusions),
            "active_node_id": snap.active_node_id,
        },
        "integrity": integrity_payload,
        "analysis_spec": {
            "graph_id": (snap.analysis_spec or {}).get("graph_id", ""),
            "start_node_id": (snap.analysis_spec or {}).get("start_node_id", ""),
            "nodes": [
                {
                    "node_id": node.get("node_id", ""),
                    "title": node.get("title", ""),
                    "allowed_capabilities": node.get("allowed_capabilities", []),
                }
                for node in (snap.analysis_spec or {}).get("nodes", [])[:40]
            ],
        },
        "capabilities": [
            {
                "id": cap.get("id") or cap.get("capability_id", ""),
                "stage": cap.get("stage", ""),
                "expected_observations": cap.get("expected_observations", []),
                "expected_artifacts": cap.get("expected_artifacts", []),
            }
            for cap in snap.capabilities[:80]
            if isinstance(cap, dict)
        ],
        "audit": {
            "ok": audit_payload.get("ok", False),
            "severity": audit_payload.get("severity", "error"),
            "summary": audit_payload.get("summary", {}),
            "top_issue_codes": [
                item.get("code", "")
                for item in [*audit_payload.get("errors", []), *audit_payload.get("warnings", [])][:12]
            ],
            "advice": audit_payload.get("advice", [])[:12],
            "next_actions": audit_payload.get("next_actions", [])[:12],
        },
        "claim_verification": claim_verification,
        "claim_checks": claim_checks,
        "context_preview": {
            "audit_preview": context_payload.get("audit_preview", {}),
            "trace_driven_rethinking": context_payload.get("trace_driven_rethinking", {}),
            "active_contract": context_payload.get("active_contract", {}),
            "budget_report": context_payload.get("budget_report", {}),
        },
        "provenance_manifest": provenance,
        "replay": {
            "event_count": replay_payload.get("event_count", 0),
            "strict": replay_payload.get("strict", True),
            "snapshot_matches_store": replay_payload.get("snapshot_matches_store"),
            "graph_matches_store": replay_payload.get("graph_matches_store"),
        },
        "inspect": {
            "event_types": inspect_payload.get("event_types", {}),
            "graph": inspect_payload.get("graph", {}),
            "recent_events": inspect_payload.get("recent_events", []),
        },
        "operator_commands": [
            f"pertura inspect {run_dir} --json",
            f"pertura context {run_dir} --json",
            f"pertura audit {run_dir} --json",
            *trace_commands,
            f"pertura replay {run_dir} --json",
            f"pertura capsule {run_dir} --verify --capsule-path {out} --json",
        ][:16],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(capsule, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {**capsule, "path": str(out)}


def _capsule_verify_run_dir(run_dir: Path | str, *, capsule_path: str | Path = "") -> dict:
    from pertura.core import Store
    from pertura.core.replay import run_integrity

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    path = Path(capsule_path) if capsule_path else run_dir / "run_capsule.json"
    if not path.exists():
        raise FileNotFoundError(f"No capsule JSON found at {path}")
    capsule = json.loads(path.read_text(encoding="utf-8"))
    current = run_integrity(Store(run_dir))
    expected = capsule.get("integrity", {}) if isinstance(capsule, dict) else {}
    keys = [
        "version",
        "run_id",
        "event_count",
        "event_log_sha256",
        "snapshot_sha256",
        "graph_sha256",
        "analysis_spec_sha256",
        "snapshot_matches_store",
        "graph_matches_store",
    ]
    checks = []
    for key in keys:
        expected_value = expected.get(key)
        actual_value = current.get(key)
        checks.append({
            "key": key,
            "ok": expected_value == actual_value,
            "expected": expected_value,
            "actual": actual_value,
        })
    ok = bool(checks) and all(item["ok"] for item in checks)
    return {
        "verification_type": "capsule_integrity_verification",
        "ok": ok,
        "run_dir": str(run_dir),
        "run_id": current.get("run_id", ""),
        "capsule_path": str(path),
        "capsule_type": capsule.get("capsule_type", "") if isinstance(capsule, dict) else "",
        "capsule_run_id": capsule.get("run_id", "") if isinstance(capsule, dict) else "",
        "checks": checks,
        "current_integrity": current,
        "capsule_integrity": expected,
    }


def _capsule_trace_commands(run_dir: Path, audit_payload: dict, provenance: dict) -> list[str]:
    commands = []
    for action in audit_payload.get("next_actions", []):
        if action.get("tool") == "review_evidence_chain":
            node_id = (action.get("args") or {}).get("node_id") or action.get("target_id", "")
            if node_id:
                commands.append(f"pertura evidence {run_dir} {node_id} --json")
        elif action.get("tool") == "plan_rethinking":
            node_id = (action.get("args") or {}).get("node_id") or action.get("target_id", "")
            if node_id:
                commands.append(f"pertura rethink {run_dir} {node_id} --json")
        elif action.get("tool") == "trace_upstream":
            node_id = (action.get("args") or {}).get("node_id") or action.get("target_id", "")
            if node_id:
                commands.append(f"pertura trace {run_dir} {node_id} --json")
    for conclusion in (provenance.get("conclusions", {}) or {}).get("items", [])[:4]:
        conclusion_id = conclusion.get("conclusion_id", "")
        if conclusion_id:
            commands.append(f"pertura evidence {run_dir} {conclusion_id} --json")
            commands.append(f"pertura trace {run_dir} {conclusion_id} --json")
            commands.append(f"pertura rethink {run_dir} {conclusion_id} --json")
    seen = set()
    out = []
    for command in commands:
        if command in seen:
            continue
        seen.add(command)
        out.append(command)
    return out[:8]


def _capsule_claim_checks(
    run_dir: Path,
    *,
    snap,
    graph: dict,
    audit_payload: dict,
    context_payload: dict,
    replay_payload: dict,
    provenance: dict,
) -> list[dict]:
    from pertura.core.claims import core_claim

    spec_nodes = (snap.analysis_spec or {}).get("nodes", []) if snap.analysis_spec else []
    graph_node_count = len([node for node in graph.get("nodes", []) if node.get("node_type") == "analysis_node"])
    capability_count = len(snap.capabilities)
    observation_memory = (context_payload.get("protected_context", {}) or {}).get("observation_memory", {})
    memory_summary = observation_memory.get("summary", {}) if isinstance(observation_memory, dict) else {}
    provenance_items = (
        provenance.get("observations", {}).get("count", 0)
        + provenance.get("conclusions", {}).get("count", 0)
        + provenance.get("artifacts", {}).get("count", 0)
    )
    audit_next_actions = audit_payload.get("next_actions", [])
    commands = {
        "context": f"pertura context {run_dir} --json",
        "audit": f"pertura audit {run_dir} --json",
        "replay": f"pertura replay {run_dir} --json",
        "trace": f"pertura trace {run_dir} <node_id> --json",
        "rethink": f"pertura rethink {run_dir} <node_id> --json",
    }
    analysis_checks = [
        _claim_check("analysis spec stored in run snapshot", bool(spec_nodes), f"analysis_spec nodes={len(spec_nodes)}"),
        _claim_check("analysis nodes projected into audit graph", graph_node_count > 0, f"analysis graph nodes={graph_node_count}"),
        _claim_check("capability contracts loaded", capability_count > 0, f"capabilities={capability_count}"),
        _claim_check("run audit surface available", bool(audit_payload.get("summary")), "audit summary missing"),
    ]
    memory_checks = [
        _claim_check("scientific observations recorded", len(snap.observations) > 0, f"observations={len(snap.observations)}"),
        _claim_check("observation memory summary exposed in context", bool(memory_summary), "observation memory summary missing"),
        _claim_check(
            "coverage entries exposed in protected context",
            len(context_payload.get("protected_context", {}).get("coverage", []) or []) > 0,
            "coverage entries missing",
        ),
        _claim_check(
            "intent entries exposed in protected context",
            len(context_payload.get("protected_context", {}).get("intent", []) or []) > 0,
            "intent entries missing",
        ),
    ]
    deliberative_checks = [
        _claim_check("run audit surface available", bool(audit_payload.get("summary")), "audit summary missing"),
        _claim_check("strict replay snapshot matches store", replay_payload.get("snapshot_matches_store") is True, "snapshot replay mismatch"),
        _claim_check("strict replay graph matches store", replay_payload.get("graph_matches_store") is True, "graph replay mismatch"),
        _claim_check("provenance manifest has reviewable items", provenance_items > 0, f"provenance items={provenance_items}"),
        _claim_check("audit/trace/rethink/replay commands emitted", all(commands[k] for k in ("audit", "trace", "rethink", "replay")), "operator commands missing"),
    ]
    return [
        _capsule_claim(
            paper_claim_id="analysis_graph",
            claim_id=str(core_claim("analysis_graph")["capsule_claim_id"]),
            title=str(core_claim("analysis_graph")["capsule_title"]),
            checks=analysis_checks,
            evidence={
                "analysis_spec_nodes": len(spec_nodes),
                "graph_analysis_nodes": graph_node_count,
                "capabilities_loaded": capability_count,
                "active_node_id": snap.active_node_id,
                "audit_node_coverage": audit_payload.get("coverage", {}),
            },
            commands=[commands["context"], commands["audit"]],
        ),
        _capsule_claim(
            paper_claim_id="observation_memory",
            claim_id=str(core_claim("observation_memory")["capsule_claim_id"]),
            title=str(core_claim("observation_memory")["capsule_title"]),
            checks=memory_checks,
            evidence={
                "observations": len(snap.observations),
                "memory_summary": memory_summary,
                "coverage_entries": len(context_payload.get("protected_context", {}).get("coverage", []) or []),
                "intent_entries": len(context_payload.get("protected_context", {}).get("intent", []) or []),
            },
            commands=[commands["context"]],
        ),
        _capsule_claim(
            paper_claim_id="deliberative_audit",
            claim_id=str(core_claim("deliberative_audit")["capsule_claim_id"]),
            title=str(core_claim("deliberative_audit")["capsule_title"]),
            checks=deliberative_checks,
            evidence={
                "audit_ok": audit_payload.get("ok"),
                "audit_severity": audit_payload.get("severity"),
                "audit_next_actions": audit_next_actions[:6],
                "replay": {
                    "event_count": replay_payload.get("event_count", 0),
                    "snapshot_matches_store": replay_payload.get("snapshot_matches_store"),
                    "graph_matches_store": replay_payload.get("graph_matches_store"),
                },
                "provenance_manifest_items": provenance_items,
            },
            commands=[commands["audit"], commands["trace"], commands["rethink"], commands["replay"]],
        ),
    ]


def _claim_check(name: str, ok: bool, detail: str = "") -> dict:
    ok = bool(ok)
    return {
        "name": name,
        "ok": ok,
        "detail": "" if ok else detail,
    }


def _claim_summary(checks: list[dict]) -> dict:
    passed = sum(1 for item in checks if item.get("ok"))
    total = len(checks)
    return {
        "passed": passed,
        "failed": total - passed,
        "total": total,
    }


def _claim_status(checks: list[dict]) -> str:
    if checks and all(item.get("ok") for item in checks):
        return "supported"
    if any(item.get("ok") for item in checks):
        return "partial"
    return "missing"


def _capsule_claim(
    *,
    claim_id: str,
    paper_claim_id: str,
    title: str,
    checks: list[dict],
    evidence: dict,
    commands: list[str],
) -> dict:
    from pertura.core.claims import standalone_claim_command

    summary = _claim_summary(checks)
    status = _claim_status(checks)
    return {
        "claim_id": claim_id,
        "paper_claim_id": paper_claim_id,
        "title": title,
        "status": status,
        "ok": status == "supported",
        "summary": summary,
        "checks": checks,
        "evidence": evidence,
        "commands": commands,
        "independent_command": standalone_claim_command(paper_claim_id),
    }


def _capsule_claim_verification(claim_checks: list[dict], *, harness_manifest: dict | None = None) -> dict:
    supported = sum(1 for item in claim_checks if item.get("status") == "supported")
    thesis = (harness_manifest or {}).get("thesis", {})
    return {
        "verification_type": "core_claim_verification_matrix",
        "scope": "run_capsule_and_independent_claim_segments",
        "harness_principle": thesis.get("core_principle", ""),
        "capsule_supported_claims": supported,
        "capsule_claims_total": len(claim_checks),
        "independent_runner": "python -m pertura.claim_tests --json",
        "independent_commands": [
            item["independent_command"]
            for item in claim_checks
            if item.get("independent_command")
        ],
        "note": (
            "Capsule claim checks are run-specific. Independent commands exercise "
            "the reusable harness claims with small fixtures."
        ),
    }


def _replay_cmd(args) -> int:
    try:
        payload = _replay_run_dir(Path(args.run_dir), strict=not args.no_strict)
    except Exception as exc:
        print(f"Replay failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Replay:[/] {payload.get('run_id') or '?'}")
    _print(f"  dir: {payload['run_dir']}")
    _print(f"  events: {payload['event_count']}  strict: {payload['strict']}")
    _print(
        "  projections: "
        f"snapshot={payload['snapshot_matches_store']} "
        f"graph={payload['graph_matches_store']}"
    )
    _print(
        "  graph: "
        f"{payload['graph']['nodes']} nodes / {payload['graph']['edges']} edges"
    )
    return 0


def _replay_run_dir(run_dir: Path | str, *, strict: bool = True) -> dict:
    from pertura.core import Store
    from pertura.core.replay import replay_store

    run_dir = Path(run_dir)
    if not (run_dir / "events.db").exists():
        raise FileNotFoundError(f"No events.db found in {run_dir}")
    result = replay_store(Store(run_dir), strict=strict)
    return {
        "run_dir": str(run_dir),
        "run_id": result.run_id,
        "event_count": result.event_count,
        "strict": strict,
        "snapshot_matches_store": result.snapshot_matches_store,
        "graph_matches_store": result.graph_matches_store,
        "snapshot": {
            "phase": result.snapshot.phase,
            "attempts": len(result.snapshot.attempts),
            "observations": len(result.snapshot.observations),
            "artifacts": len(result.snapshot.artifacts),
            "findings": len(result.snapshot.findings),
            "branches": len(result.snapshot.branches),
        },
        "graph": {
            "nodes": len(result.graph.get("nodes", [])),
            "edges": len(result.graph.get("edges", [])),
        },
    }


def _fork_cmd(args) -> int:
    try:
        payload = _fork_run_dir(
            Path(args.source_run_dir),
            args.event_id,
            new_run_dir=Path(args.out) if args.out else None,
            new_run_id=args.run_id or None,
        )
    except Exception as exc:
        print(f"Fork failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Fork:[/] {payload['run_id']}")
    _print(f"  dir: {payload['run_dir']}")
    _print(f"  events: {payload['event_count']}")
    _print(f"  copied_cache: {payload['copied_cache']}")
    return 0


def _fork_run_dir(
    source_run_dir: Path | str,
    event_id: str,
    *,
    new_run_dir: Path | str | None = None,
    new_run_id: str | None = None,
) -> dict:
    from pertura.core import fork_store

    result = fork_store(
        Path(source_run_dir),
        event_id,
        new_run_dir=Path(new_run_dir) if new_run_dir is not None else None,
        new_run_id=new_run_id,
    )
    return result.as_dict()


def _diff_cmd(args) -> int:
    payload = _diff_run_dirs(Path(args.run_a), Path(args.run_b))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print(f"[bold]Diff:[/] {payload['run_a']} -> {payload['run_b']}")
    summary = payload["summary"]
    _print("[bold]Graph[/]")
    _print(
        "  nodes: "
        f"+{summary['graph_nodes_added']} "
        f"-{summary['graph_nodes_removed']} "
        f"~{summary['graph_nodes_changed']}"
    )
    _print(
        "  edges: "
        f"+{summary['graph_edges_added']} "
        f"-{summary['graph_edges_removed']}"
    )
    _print("[bold]Observations[/]")
    _print(
        "  by_id: "
        f"+{summary['observations_by_id_added']} "
        f"-{summary['observations_by_id_removed']} "
        f"~{summary['observations_by_id_changed']}"
    )
    _print(
        "  by_variable: "
        f"+{summary['observations_by_variable_added']} "
        f"-{summary['observations_by_variable_removed']} "
        f"~{summary['observations_by_variable_changed']}"
    )
    _print("[bold]Conclusions[/]")
    _print(
        "  "
        f"+{summary['conclusions_added']} "
        f"-{summary['conclusions_removed']} "
        f"~{summary['conclusions_changed']}"
    )
    return 0


def _diff_run_dirs(run_a: Path | str, run_b: Path | str) -> dict:
    from pertura.core import diff_stores

    diff = diff_stores(Path(run_a), Path(run_b))
    summary = {
        "graph_nodes_added": len(diff["graph"]["nodes"]["added"]),
        "graph_nodes_removed": len(diff["graph"]["nodes"]["removed"]),
        "graph_nodes_changed": len(diff["graph"]["nodes"]["changed"]),
        "graph_edges_added": len(diff["graph"]["edges"]["added"]),
        "graph_edges_removed": len(diff["graph"]["edges"]["removed"]),
        **_mapping_counts("observations_by_id", diff["observations"]["by_id"]),
        **_mapping_counts("observations_by_variable", diff["observations"]["by_variable"]),
        **_mapping_counts("conclusions", diff["conclusions"]),
    }
    return {
        "run_a": diff["run_a"],
        "run_b": diff["run_b"],
        "summary": summary,
        "diff": diff,
    }


def _claims_cmd(args) -> int:
    payload = _claims_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print("[bold]Pertura-v2 Core Claims[/]")
    _print(f"  runner: {payload['independent_runner']}")
    for claim in payload["claims"]:
        _print(f"  - {claim['paper_claim_id']}: {claim['title']}")
        _print(f"    command: {claim['standalone_command']}")
        _print(f"    capsule id: {claim['capsule_claim_id']}")
    return 0


def _claims_payload() -> dict:
    from pertura.core.claims import (
        CORE_CLAIMS,
        capsule_claim_id,
        source_tree_claim_command,
        standalone_claim_command,
        standalone_claim_command_array,
    )
    from pertura.core import build_harness_manifest

    claims = []
    for claim_id, payload in CORE_CLAIMS.items():
        claims.append({
            "paper_claim_id": claim_id,
            "title": payload["title"],
            "capsule_claim_id": capsule_claim_id(claim_id),
            "capsule_title": payload["capsule_title"],
            "standalone_script": payload["standalone_script"],
            "standalone_command": standalone_claim_command(claim_id),
            "standalone_command_array": standalone_claim_command_array(claim_id),
            "source_tree_command": source_tree_claim_command(claim_id),
        })
    manifest = build_harness_manifest()
    return {
        "view_type": "core_claim_manifest",
        "scope": "pertura_v2_paper_claims",
        "harness_thesis": manifest["thesis"],
        "developer_vocabulary": manifest["developer_vocabulary"],
        "independent_runner": "python -m pertura.claim_tests --json",
        "claims": claims,
    }


def _harness_cmd(args) -> int:
    payload = _harness_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    thesis = payload["thesis"]
    _print("[bold]Pertura-v2 Harness Thesis[/]")
    _print(f"  principle: {thesis['core_principle']}")
    _print(f"  {thesis['one_sentence']}")
    _print("[bold]Core primitives:[/]")
    for primitive in thesis.get("distinctive_primitives", []):
        _print(f"  - {primitive['primitive_id']}: {primitive['label']}")
    return 0


def _harness_payload() -> dict:
    from pertura.core import build_harness_manifest

    return build_harness_manifest()


def _toolbox_cmd(args) -> int:
    payload = _toolbox_payload(purpose=getattr(args, "purpose", "audit"))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    _print("[bold]Pertura Self-Audit Toolbox[/]")
    _print(f"  purpose: {payload['purpose']}")
    _print(f"  strategy: {payload['policy']['context_strategy']}")
    _print(f"  recommended: {', '.join(payload.get('recommended_first_tools', []))}")
    for item in payload["tools"]:
        _print(f"  - {item['tool']} (cost: {item['expansion_cost']})")
        _print(f"    when: {item['use_when']}")
    return 0


def _toolbox_payload(*, purpose: str = "audit") -> dict:
    from pertura.core import build_audit_toolbox, harness_thesis

    payload = build_audit_toolbox(purpose=purpose)
    return {
        **payload,
        "harness_thesis": harness_thesis(),
        "operator_surface": "pertura toolbox",
        "note": "Static discovery view. LLM tool calls pass the live snapshot for run-aware recommendations.",
    }


def _mapping_counts(prefix: str, value: dict) -> dict:
    return {
        f"{prefix}_added": len(value.get("added", [])),
        f"{prefix}_removed": len(value.get("removed", [])),
        f"{prefix}_changed": len(value.get("changed", [])),
    }


def _doctor(args) -> int:
    _print("[bold]Environment[/]\n")
    _print(f"  Python: {sys.version.split()[0]}")
    key = os.getenv("OPENAI_API_KEY") or _config().get("openai_api_key")
    vlm_key = (os.getenv("PETURA_VLM_API_KEY")
               or os.getenv("BLACKBOARD_VLM_API_KEY")
               or _config().get("vlm_api_key"))
    _print(f"  OPENAI_API_KEY: [{'green' if key else 'dim'}]{'set' if key else 'not set'}[/]")
    _print(f"  VLM (plot viewer): [{'green' if vlm_key else 'dim'}]{'configured' if vlm_key else 'not set - set PETURA_VLM_API_KEY'}[/]")
    groups = {
        "core": ["pydantic"],
        "cli": ["rich"],
        "llm-openai": ["openai"],
        "llm-anthropic": ["anthropic"],
        "server": ["fastapi", "uvicorn"],
        "kernel": ["jupyter_client", "ipykernel"],
        "notebook": ["nbformat"],
        "perturbseq": ["anndata", "scanpy", "pandas", "scipy", "statsmodels"],
    }
    missing_by_group = {}
    for group, packages in groups.items():
        missing = []
        for pkg in packages:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)
        missing_by_group[group] = missing
        status = "OK" if not missing else f"missing: {', '.join(missing)}"
        color = "green" if not missing else "dim"
        _print(f"  {group}: [{color}]{status}[/]")
    if any(missing_by_group.values()):
        if RICH:
            console.print("  install extras: ", end="")
            console.print('pip install -e ".[cli,server,kernel,perturbseq]"', style="dim", markup=False)
        else:
            _print('  install extras: pip install -e ".[cli,server,kernel,perturbseq]"')
    if args.openai and key:
        from pertura.planner import _call_llm
        try:
            _call_llm("Say OK.", "Respond with {'status':'ok'}.", {"type":"object","properties":{"status":{"type":"string"}},"required":["status"],"additionalProperties":False})
            _print("  OpenAI: [green]connected[/]")
        except Exception as e:
            _print(f"  OpenAI: [red]error - {e}[/]")
    return 0


def _load_project_settings(start: str | Path | None = None) -> dict:
    """Find .pertura/settings.json from a project path or cwd."""
    starts: list[Path] = []
    if start:
        starts.append(Path(start))
    starts.append(Path.cwd())

    seen: set[Path] = set()
    for raw in starts:
        try:
            base = raw.expanduser().resolve()
        except OSError:
            continue
        if base.is_file():
            base = base.parent
        for candidate in (base, *base.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            path = candidate / ".pertura" / "settings.json"
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                return {"path": str(path), "project_root": str(candidate), "settings": payload}
    return {"path": "", "project_root": "", "settings": {}}


def _resolve_cli_config(args, *, workspace: str | Path | None = None) -> dict:
    """Resolve CLI flags plus project settings into runtime configuration."""
    project = _load_project_settings(workspace)
    settings = project.get("settings", {}) or {}
    project_root = Path(project["project_root"]) if project.get("project_root") else Path.cwd()

    domain = getattr(args, "domain", None) or settings.get("domain") or "perturbseq"
    domain_text = str(domain)
    if domain_text.endswith(".json") or "/" in domain_text or "\\" in domain_text:
        domain_path = Path(domain_text).expanduser()
        if not domain_path.is_absolute():
            domain_path = project_root / domain_path
        domain = str(domain_path)
    analysis_graph = getattr(args, "analysis_graph", None) or settings.get("analysis_graph") or ""
    if analysis_graph:
        graph_path = Path(analysis_graph).expanduser()
        if not graph_path.is_absolute():
            graph_path = project_root / graph_path
        analysis_graph = str(graph_path)

    provider = getattr(args, "provider", None) or settings.get("provider") or "openai"
    sandbox = getattr(args, "sandbox", None) or settings.get("sandbox") or "kernel"
    raw_steps = getattr(args, "steps", None)
    steps = raw_steps if raw_steps is not None else int(settings.get("max_attempts") or 5)

    return {
        "domain": str(domain),
        "analysis_graph": analysis_graph,
        "provider": provider,
        "sandbox": sandbox,
        "steps": steps,
        "settings_path": project.get("path", ""),
        "project_root": project.get("project_root", ""),
    }


def _load_domain(name_or_path: str, *, analysis_graph_path: str = "") -> Domain:
    path = Path(name_or_path)
    if path.exists() and path.suffix == ".json":
        domain = Domain(**json.loads(path.read_text(encoding="utf-8")))
        return _with_analysis_graph(domain, analysis_graph_path)
    if name_or_path == "perturbseq":
        from pertura.domain import perturbseq
        return _with_analysis_graph(perturbseq.DOMAIN, analysis_graph_path)
    try:
        mod = __import__(name_or_path, fromlist=["DOMAIN"])
        return _with_analysis_graph(mod.DOMAIN, analysis_graph_path)
    except ImportError:
        return _with_analysis_graph(Domain(name=name_or_path, agenda=["inspect", "analyze", "report"]), analysis_graph_path)


def _with_analysis_graph(domain: Domain, analysis_graph_path: str = "") -> Domain:
    domain = domain.model_copy(deep=True)
    if not analysis_graph_path:
        return domain
    from pertura.spec.models import load_analysis_graph
    spec = load_analysis_graph(analysis_graph_path)
    return domain.model_copy(update={"analysis_graph": spec.model_dump(mode="json")})


def _spec_cmd(args) -> int:
    from pertura.spec.models import load_analysis_graph, save_analysis_graph, validate_analysis_graph
    if args.spec_cmd == "export":
        domain = _load_domain(args.domain)
        if not domain.analysis_graph:
            print(f"Domain {args.domain!r} has no analysis graph spec.", file=sys.stderr)
            return 1
        out = save_analysis_graph(domain.analysis_graph, args.out)
        print(str(out))
        return 0
    if args.spec_cmd == "validate":
        spec = load_analysis_graph(args.path)
        validate_analysis_graph(spec)
        print(json.dumps({"ok": True, "graph_id": spec.graph_id, "nodes": len(spec.nodes)}, indent=2))
        return 0
    if args.spec_cmd == "audit":
        payload = _spec_audit_payload(
            path=getattr(args, "path", ""),
            domain_name=getattr(args, "domain", "perturbseq"),
            strict=bool(getattr(args, "strict", False)),
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_audit(payload)
        return 0 if payload.get("ok") else 1
    if args.spec_cmd == "compile":
        from pertura.spec.compiler import compile_conditions
        spec = load_analysis_graph(args.path)
        report = compile_conditions(
            spec,
            domain_context=args.domain_context,
            provider=args.provider,
        )
        out = save_analysis_graph(report.spec, args.out)
        report_path = Path(str(out) + ".compile_report.json")
        report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "out": str(out),
            "compile_report": str(report_path),
            "executable": len(report.executable),
            "rubric_only": len(report.rubric_only),
            "unmapped": len(report.unmapped),
        }, indent=2))
        return 0
    if args.spec_cmd == "contract":
        payload = _spec_contract_payload(
            path=getattr(args, "path", ""),
            domain_name=getattr(args, "domain", "perturbseq"),
            node_id=getattr(args, "node", ""),
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        _print_contract(payload)
        return 0
    print("Use: pertura spec export|validate", file=sys.stderr)
    return 2


def _spec_audit_payload(*, path: str = "", domain_name: str = "perturbseq", strict: bool = False) -> dict:
    from pertura.capabilities import CapabilityRegistry
    from pertura.spec.contracts import audit_analysis_graph
    from pertura.spec.models import load_analysis_graph, spec_from_dict

    domain = _load_domain(domain_name)
    if path:
        graph = load_analysis_graph(path)
    else:
        graph = spec_from_dict(domain.analysis_graph)
        if graph is None:
            raise ValueError(f"Domain {domain_name!r} has no analysis graph spec.")
    registry = CapabilityRegistry(getattr(domain, "capabilities", []) or [])
    return audit_analysis_graph(graph, capabilities=registry, strict=strict)


def _spec_contract_payload(*, path: str = "", domain_name: str = "perturbseq", node_id: str = "") -> dict:
    from pertura.capabilities import CapabilityRegistry
    from pertura.spec.contracts import graph_contract, node_contract
    from pertura.spec.models import load_analysis_graph, spec_from_dict, validate_analysis_graph

    domain = _load_domain(domain_name)
    if path:
        graph = load_analysis_graph(path)
    else:
        graph = spec_from_dict(domain.analysis_graph)
        if graph is None:
            raise ValueError(f"Domain {domain_name!r} has no analysis graph spec.")
        validate_analysis_graph(graph)
    registry = CapabilityRegistry.from_domain(domain.model_copy(update={"analysis_graph": graph.model_dump(mode="json")}))
    if node_id:
        return node_contract(graph, node_id, capabilities=registry)
    return graph_contract(graph, capabilities=registry)


def _domain_cmd(args) -> int:
    if args.domain_cmd == "inspect":
        domain = _load_domain(args.domain)
        payload = domain.describe(include_core_tools=not bool(getattr(args, "no_core_tools", False)))
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_domain_browser(payload)
        return 0
    if args.domain_cmd == "capabilities":
        domain = _load_domain(args.domain)
        payload = domain.describe(include_core_tools=False)
        caps = payload.get("capabilities", [])
        node_id = getattr(args, "node", "")
        if node_id:
            allowed = set(payload.get("capabilities_by_node", {}).get(node_id, []))
            caps = [item for item in caps if item.get("id") in allowed]
        out = {
            "domain": payload.get("domain", {}),
            "node_id": node_id,
            "capabilities": caps,
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            _print_capability_browser(out)
        return 0
    if args.domain_cmd == "tools":
        from pertura.tools import tool_catalog

        payload = {"tools": tool_catalog(readonly=bool(getattr(args, "readonly", False)))}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_tool_browser(payload)
        return 0
    print("Use: pertura domain inspect|capabilities|tools", file=sys.stderr)
    return 2


def _print_audit(payload: dict) -> None:
    summary = payload.get("summary", {})
    status = "OK" if payload.get("ok") else "NEEDS WORK"
    _print(f"[bold]Spec audit:[/] {payload.get('graph_id', '?')}  [{ 'green' if payload.get('ok') else 'yellow'}]{status}[/]")
    _print(
        f"  nodes: {summary.get('nodes', 0)}  "
        f"errors: {summary.get('errors', 0)}  "
        f"warnings: {summary.get('warnings', 0)}  "
        f"missing capabilities: {summary.get('missing_capabilities', 0)}"
    )
    if payload.get("errors"):
        _print("[bold red]Errors:[/]")
        for issue in payload["errors"][:12]:
            _print(f"  - {issue['node_id']}: {issue['message']}")
    if payload.get("warnings"):
        _print("[bold yellow]Warnings:[/]")
        for issue in payload["warnings"][:12]:
            _print(f"  - {issue['node_id']}: {issue['message']}")
    if payload.get("advice"):
        _print("[bold]Advice:[/]")
        for item in payload["advice"]:
            _print(f"  - {item['message']}")


def _print_domain_browser(payload: dict) -> None:
    domain = payload.get("domain", {})
    summary = payload.get("summary", {})
    _print(f"[bold]Domain:[/] {domain.get('name', '?')}  graph={domain.get('graph_id', '')}")
    _print(
        f"  nodes: {summary.get('nodes', 0)}  "
        f"capabilities: {summary.get('capabilities', 0)}  "
        f"design fields: {summary.get('design_fields', 0)}  "
        f"hard conditions: {summary.get('hard_conditions', 0)}  "
        f"rubric-only: {summary.get('rubric_only_conditions', 0)}"
    )
    _print("[bold]Nodes:[/]")
    for node in payload.get("nodes", []):
        _print(
            f"  {node.get('node_id')}: {node.get('title') or ''}  "
            f"caps={len(node.get('allowed_capabilities', []))}  "
            f"next={', '.join(node.get('next_nodes', [])) or 'any'}"
        )
    fields = payload.get("design", {}).get("fields", [])
    if fields:
        _print("[bold]Design fields:[/] " + ", ".join(fields))
    _print("Use `pertura domain capabilities --node NODE` to inspect action contracts.")


def _print_capability_browser(payload: dict) -> None:
    domain = payload.get("domain", {})
    node_id = payload.get("node_id", "")
    label = f"{domain.get('name', '?')}" + (f" / {node_id}" if node_id else "")
    _print(f"[bold]Capabilities:[/] {label}")
    for cap in payload.get("capabilities", []):
        tools = ", ".join(item.get("tool_id", "") for item in cap.get("implementation_tools", [])) or "none"
        outputs = ", ".join([*cap.get("expected_observations", []), *cap.get("expected_artifacts", [])]) or "none"
        inputs = ", ".join(cap.get("required_inputs", [])) or "none"
        _print(f"  {cap.get('id')}: {cap.get('title') or ''}")
        _print(f"    inputs: {inputs}")
        _print(f"    outputs: {outputs}")
        _print(f"    tools: {tools}")


def _print_tool_browser(payload: dict) -> None:
    _print("[bold]Core runtime tools:[/]")
    for tool in payload.get("tools", []):
        _print(f"  {tool.get('tool_id')}: {tool.get('permission')}")
        if tool.get("description"):
            _print(f"    {tool.get('description')}")


def _print_contract(payload: dict) -> None:
    if payload.get("contract_type") == "analysis_graph_contract":
        _print(f"[bold]Analysis graph:[/] {payload['graph_id']}  version={payload['version']}")
        _print(f"  nodes: {payload['node_count']}  edges: {payload['edge_count']}  start: {payload['start_node_id']}")
        if payload.get("missing_capabilities"):
            _print(f"  [yellow]missing capabilities:[/] {', '.join(payload['missing_capabilities'])}")
        _print("[bold]Nodes:[/]")
        for node in payload.get("nodes", []):
            card = node["node"]
            quality = node["quality"]
            outputs = node["outputs"]
            _print(
                f"  {card['id']}: {card.get('title') or ''}  "
                f"caps={quality['capability_count']}  "
                f"inputs={len(node['inputs']['required'])}  "
                f"obs={len(outputs['expected_observations'])}  "
                f"artifacts={len(outputs['expected_artifacts'])}"
            )
        return
    node = payload["node"]
    _print(f"[bold]Analysis node:[/] {node['id']}  {node.get('title') or ''}")
    if node.get("purpose"):
        _print(f"  purpose: {node['purpose']}")
    _print(f"  graph: {payload['graph_id']}  version={payload['version']}")
    if payload.get("missing_capabilities"):
        _print(f"  [yellow]missing capabilities:[/] {', '.join(payload['missing_capabilities'])}")
    inputs = payload["inputs"]
    _print(f"  inputs: {', '.join(inputs['required']) or 'none'}")
    if inputs.get("design_fields"):
        _print(f"  design fields: {', '.join(inputs['design_fields'])}")
    outputs = payload["outputs"]
    _print(f"  observations: {', '.join(outputs['expected_observations']) or 'none'}")
    _print(f"  artifacts: {', '.join(outputs['expected_artifacts']) or 'none'}")
    if payload["actions"].get("recommended"):
        _print("[bold]Recommended actions:[/]")
        for action in payload["actions"]["recommended"]:
            _print(f"  - {action}")
    if payload.get("audit_checklist"):
        _print("[bold]Audit checklist:[/]")
        for item in payload["audit_checklist"][:12]:
            _print(f"  - {item}")
        if len(payload["audit_checklist"]) > 12:
            _print(f"  ... {len(payload['audit_checklist']) - 12} more")


def _config() -> dict:
    cfg = Path.home() / ".pertura" / "config.json"
    legacy = Path.home() / ".blackboard" / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text())
    return json.loads(legacy.read_text()) if legacy.exists() else {}


if __name__ == "__main__":
    raise SystemExit(main())
