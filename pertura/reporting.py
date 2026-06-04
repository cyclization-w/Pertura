"""Report generation: synthesis, Markdown, and derivation graph HTML."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path


def generate_report(snap, ctx, *, provider: str = "openai",
                    include_narrative: bool = True,
                    graph: dict | None = None,
                    run_dir: str | Path | None = None,
                    include_audit: bool = True) -> dict:
    """Synthesize a complete analysis report."""
    from pertura.core import build_harness_manifest
    run_audit = _run_audit_summary(snap, graph, run_dir) if include_audit else {}
    provenance_manifest = _provenance_manifest(snap, graph)
    rethinking = _report_rethinking_summary(snap, graph, run_audit)
    report = {
        "run_id": snap.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": snap.workspace,
        "goal": snap.goal,
        "domain": snap.domain,
        "summary": {
            "attempts_total": len(snap.attempts),
            "attempts_succeeded": len([a for a in snap.attempts if a.status == "succeeded"]),
            "observations_total": len(snap.observations),
            "conclusions": _summary_conclusions(snap),
            "branches": len(snap.branches),
        },
        "findings": _findings_summary(snap),
        "coverage": _coverage_summary(ctx),
        "memory_signals": _memory_summary(ctx),
        "observation_detail": _observation_detail(snap),
        "harness_manifest": build_harness_manifest(),
        "run_audit": run_audit,
        "trace_driven_rethinking": rethinking,
        "provenance_manifest": provenance_manifest,
        "narrative": _generate_narrative(snap, ctx, provider) if include_narrative else "",
    }
    return report


def render_markdown(report: dict, graph_html_path: str = "") -> str:
    """Render the report as Markdown."""
    s = report["summary"]
    md = f"# Analysis Report - {report['run_id']}\n\n"
    md += f"**Workspace:** `{report['workspace']}`  \n"
    md += f"**Goal:** {report['goal'] or 'N/A'}  \n"
    md += f"**Domain:** {report['domain']}  \n"
    md += f"**Generated:** {report['generated_at']}\n\n"

    md += "## Summary\n\n"
    md += f"| Metric | Value |\n|---|---|\n"
    md += f"| Attempts | {s['attempts_total']} ({s['attempts_succeeded']} succeeded) |\n"
    md += f"| Observations | {s['observations_total']} |\n"
    md += f"| Branches | {s['branches']} |\n\n"

    for c in s["conclusions"]:
        md += f"**{c['grade'].upper()}**: {c['text']}\n\n"

    manifest = report.get("harness_manifest") or {}
    thesis = manifest.get("thesis") or {}
    primitives = thesis.get("distinctive_primitives") or []
    if thesis:
        md += "## Harness Thesis\n\n"
        md += f"**Principle:** `{thesis.get('core_principle', '')}`  \n"
        md += f"{thesis.get('one_sentence', '')}\n\n"
        for primitive in primitives[:3]:
            md += f"- **{primitive.get('label', '')}:** {primitive.get('why_it_matters', '')}\n"
        md += "\n"

    audit = report.get("run_audit") or {}
    if audit:
        audit_summary = audit.get("summary", {})
        md += "## Run Audit\n\n"
        md += f"**Status:** {'PASS' if audit.get('ok') else 'NEEDS ATTENTION'}  \n"
        md += f"**Severity:** {audit.get('severity', 'unknown')}  \n"
        md += f"**Issues:** {audit_summary.get('errors', 0)} errors, {audit_summary.get('warnings', 0)} warnings\n\n"
        for section in ("errors", "warnings"):
            for item in audit.get(section, [])[:8]:
                md += f"- [{item.get('severity', section)}] `{item.get('code')}`: {item.get('message')}\n"
        next_actions = audit.get("next_actions", [])
        if next_actions:
            md += "\n### Audit Next Actions\n\n"
            for item in next_actions[:8]:
                args = item.get("args") or {}
                args_text = f" `{json.dumps(args, ensure_ascii=False)}`" if args else ""
                md += f"- `{item.get('tool')}`{args_text}: {item.get('why')}\n"
        md += "\n"

    rethinking = report.get("trace_driven_rethinking") or {}
    if rethinking:
        md += "## Trace-Driven Rethinking\n\n"
        md += f"**Status:** `{rethinking.get('status', 'unknown')}`  \n"
        md += f"**Target:** `{rethinking.get('target_id', '') or 'auto'}`  \n"
        if rethinking.get("summary"):
            md += f"{rethinking.get('summary')}\n\n"
        roots = rethinking.get("suspected_roots", [])
        if roots:
            md += "### Suspected Roots\n\n"
            for root in roots[:6]:
                md += f"- `{root.get('root_id')}` ({root.get('root_type', '')}): {root.get('reason', '')}\n"
            md += "\n"
        actions = rethinking.get("recommended_actions", [])
        if actions:
            md += "### Rethinking Actions\n\n"
            for item in actions[:8]:
                args = item.get("args") or {}
                args_text = f" `{json.dumps(args, ensure_ascii=False)}`" if args else ""
                md += f"- `{item.get('tool')}`{args_text}: {item.get('why')}\n"
            md += "\n"

    provenance = report.get("provenance_manifest") or {}
    if provenance:
        md += "## Provenance Manifest\n\n"
        md += f"| Item | Count |\n|---|---:|\n"
        md += f"| Observations | {provenance.get('observations', {}).get('count', 0)} |\n"
        md += f"| Conclusions | {provenance.get('conclusions', {}).get('count', 0)} |\n"
        md += f"| Artifacts | {provenance.get('artifacts', {}).get('count', 0)} |\n"
        md += f"| Stale IDs | {len(provenance.get('stale_ids', []))} |\n\n"

    if report["narrative"]:
        md += "## Narrative\n\n" + report["narrative"] + "\n\n"

    if report["findings"]:
        md += "## Key Findings\n\n"
        for f in report["findings"]:
            md += f"- [{f['severity']}] {f['summary']}\n"
        md += "\n"

    if report["coverage"]:
        md += "## Evidence Coverage\n\n"
        md += "| Subject | Label | Methods | Observations | Contradictions |\n|---|---|---|---|---|\n"
        for c in report["coverage"]:
            md += f"| {c['subject']} | {c['label']} | {c['methods']} | {c['observations']} | {c['contradictions']} |\n"
        md += "\n"

    if report["memory_signals"]:
        md += "## Memory Signals\n\n"
        for m in report["memory_signals"]:
            md += f"- **{m['signal']}**: {m['subject']} - {m['summary']}\n"
        md += "\n"

    if report["observation_detail"]:
        md += "## Observation Details\n\n"
        md += "| Target | Type | Metric | Value | Method | Contrast |\n|---|---|---|---|---|---|\n"
        for o in report["observation_detail"][:30]:
            md += f"| {o['target']} | {o['type']} | {o['metric']} | {o['value']} | {o['method']} | {o['contrast']} |\n"
        md += "\n"

    if graph_html_path:
        md += f"## Derivation Graph\n\n[Open interactive graph]({graph_html_path})\n\n"

    return md


def render_html(report: dict, graph_json: dict | None = None) -> str:
    """Render an offline-safe HTML report.

    The default report intentionally avoids CDN scripts. Interactive graph
    viewers can be layered on top by the GUI, while this artifact remains safe
    to open as a standalone reviewer file.
    """
    graph_payload = json.dumps(graph_json or {}, ensure_ascii=False, default=str)
    coverage_rows = "".join(
        "<tr>"
        f"<td>{_h(c.get('subject', ''))}</td>"
        f"<td><span class='badge' style='background:{_coverage_color(c.get('label', ''))}'>{_h(c.get('label', ''))}</span></td>"
        f"<td>{_h(c.get('methods', ''))}</td>"
        f"<td>{_h(c.get('observations', ''))}</td>"
        f"<td>{_h(c.get('contradictions', ''))}</td>"
        "</tr>"
        for c in report.get("coverage", [])
    )
    memory_html = "".join(
        f"<p><strong>{_h(m.get('signal', '').upper())}</strong>: {_h(m.get('subject', ''))} - {_h(m.get('summary', ''))}</p>"
        for m in report.get("memory_signals", [])
    ) or "<p>No memory signals.</p>"
    observation_rows = "".join(
        "<tr>"
        f"<td>{_h(o.get('target', ''))}</td>"
        f"<td>{_h(o.get('type', ''))}</td>"
        f"<td>{_h(o.get('metric', ''))}</td>"
        f"<td>{_h(o.get('value', ''))}</td>"
        f"<td>{_h(o.get('method', ''))}</td>"
        f"<td>{_h(o.get('contrast', ''))}</td>"
        "</tr>"
        for o in report.get("observation_detail", [])[:30]
    )
    graph_summary = ""
    if graph_json:
        graph_summary = (
            "<h2>Derivation Graph</h2>"
            f"<p>Nodes: {_h(len((graph_json or {}).get('nodes', [])))}; "
            f"Edges: {_h(len((graph_json or {}).get('edges', [])))}</p>"
            f"<pre id='graph-json'>{_h(graph_payload)}</pre>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Report - {_h(report.get('run_id', ''))}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Inter,system-ui,sans-serif;background:#0f172a;color:#e2e8f0;max-width:960px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin-bottom:8px}}h2{{font-size:18px;margin:24px 0 8px;color:#94a3b8}}
p,li{{font-size:14px;line-height:1.6}}table{{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid#334155}}
th{{color:#94a3b8;font-weight:600}}
pre{{white-space:pre-wrap;word-break:break-word;background:#111827;border:1px solid#334155;border-radius:6px;padding:12px;max-height:360px;overflow:auto}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}}
</style>
</head>
<body>
<h1>Analysis Report</h1>
<p><strong>Run:</strong> {_h(report.get('run_id', ''))} &nbsp; <strong>Workspace:</strong> {_h(report.get('workspace', ''))} &nbsp; <strong>Goal:</strong> {_h(report.get('goal', ''))}</p>

<h2>Summary</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>Attempts</td><td>{_h(report.get('summary', {}).get('attempts_total', 0))} ({_h(report.get('summary', {}).get('attempts_succeeded', 0))} succeeded)</td></tr>
<tr><td>Observations</td><td>{_h(report.get('summary', {}).get('observations_total', 0))}</td></tr>
<tr><td>Branches</td><td>{_h(report.get('summary', {}).get('branches', 0))}</td></tr></table>

<h2>Harness Thesis</h2>
{_render_harness_html(report.get('harness_manifest') or {})}

<h2>Run Audit</h2>
{_render_audit_html(report.get('run_audit') or {})}

<h2>Trace-Driven Rethinking</h2>
{_render_rethinking_html(report.get('trace_driven_rethinking') or {})}

<h2>Provenance Manifest</h2>
{_render_provenance_html(report.get('provenance_manifest') or {})}

{graph_summary}

<h2>Narrative</h2>
<p>{_h(report.get('narrative', 'No narrative generated.'))}</p>

<h2>Coverage</h2>
<table><tr><th>Subject</th><th>Label</th><th>Methods</th><th>Observations</th><th>Contradictions</th></tr>
{coverage_rows}</table>

<h2>Memory Signals</h2>
{memory_html}

<h2>Observation Details</h2>
<table><tr><th>Target</th><th>Type</th><th>Metric</th><th>Value</th><th>Method</th><th>Contrast</th></tr>
{observation_rows}</table>
</body></html>"""


def _coverage_color(label: str) -> str:
    return {"convergent": "#22c55e", "adequate": "#3b82f6", "thin": "#f59e0b", "conflicted": "#ef4444", "none": "#64748b"}.get(label, "#64748b")


def _h(value) -> str:
    return escape(str(value), quote=True)


def _render_harness_html(manifest: dict) -> str:
    thesis = manifest.get("thesis") or {}
    if not thesis:
        return "<p>No harness thesis manifest available.</p>"
    primitives = thesis.get("distinctive_primitives") or []
    items = "".join(
        f"<li><strong>{_h(item.get('label', ''))}</strong>: {_h(item.get('why_it_matters', ''))}</li>"
        for item in primitives[:3]
    )
    return (
        f"<p><strong>Principle:</strong> <code>{_h(thesis.get('core_principle', ''))}</code></p>"
        f"<p>{_h(thesis.get('one_sentence', ''))}</p>"
        f"<ul>{items}</ul>"
    )


def _run_audit_summary(snap, graph: dict | None, run_dir: str | Path | None) -> dict:
    from pertura.core.audit import audit_run
    return audit_run(snap, graph or {}, run_dir=run_dir)


def _report_rethinking_summary(snap, graph: dict | None, audit: dict | None) -> dict:
    from pertura.core.rethinking import plan_rethinking

    target_id = ""
    issue = ""
    for action in (audit or {}).get("next_actions", []) or []:
        args = action.get("args") or {}
        target_id = args.get("node_id") or action.get("target_id", "")
        issue = action.get("why", "") or action.get("issue_code", "")
        if target_id:
            break
    if not target_id and getattr(snap, "conclusions", None):
        target_id = snap.conclusions[-1].conclusion_id
        issue = "review latest conclusion before reporting"
    elif not target_id and getattr(snap, "observations", None):
        target_id = snap.observations[-1].observation_id
        issue = "review latest observation before reporting"

    plan = plan_rethinking(
        snap,
        target_id,
        issue=issue or "report-level review",
        depth=5,
        graph=graph or {},
    )
    return {
        "view_type": "rethinking_report_summary",
        "target_id": plan.get("target_id", target_id),
        "issue": plan.get("issue", issue),
        "status": plan.get("status", ""),
        "summary": plan.get("summary", ""),
        "evidence_status": (plan.get("evidence_review") or {}).get("status", ""),
        "upstream_node_count": (plan.get("upstream_trace") or {}).get("node_count", 0),
        "impact_summary": (plan.get("impact") or {}).get("affected", {}),
        "suspected_roots": [
            {
                "root_id": root.get("root_id", ""),
                "root_type": root.get("root_type", ""),
                "priority": root.get("priority", ""),
                "reason": root.get("reason", ""),
            }
            for root in (plan.get("suspected_roots") or [])[:8]
        ],
        "recommended_actions": (plan.get("recommended_actions") or [])[:10],
    }


def _provenance_manifest(snap, graph: dict | None = None, *, limit: int = 80) -> dict:
    stale_ids = _stale_ids(snap)
    observations = []
    for obs in snap.observations[-limit:]:
        observations.append({
            "observation_id": obs.observation_id,
            "target": obs.target,
            "metric": obs.metric,
            "attempt_id": obs.attempt_id,
            "branch_id": obs.branch_id,
            "artifact_id": obs.artifact_id,
            "input_ids": obs.input_ids[:8],
            "design_fields_used": obs.design_fields_used[:8],
            "stale": obs.observation_id in stale_ids,
        })
    conclusions = []
    for conclusion in snap.conclusions[-limit:]:
        conclusions.append({
            "conclusion_id": conclusion.conclusion_id,
            "grade": conclusion.grade,
            "support_ids": conclusion.support_ids[:12],
            "limitation_ids": conclusion.limitation_ids[:12],
            "stale": conclusion.conclusion_id in stale_ids or any(item in stale_ids for item in conclusion.support_ids),
        })
    artifacts = []
    for artifact in snap.artifacts[-limit:]:
        artifacts.append({
            "artifact_id": artifact.artifact_id,
            "attempt_id": artifact.attempt_id,
            "kind": artifact.kind,
            "path": artifact.path,
            "input_ids": list((artifact.metadata or {}).get("input_ids", []))[:8],
            "stale": artifact.artifact_id in stale_ids,
        })
    graph_nodes = len((graph or {}).get("nodes", [])) if isinstance(graph, dict) else 0
    graph_edges = len((graph or {}).get("edges", [])) if isinstance(graph, dict) else 0
    return {
        "manifest_type": "provenance_manifest",
        "truncated": (
            len(snap.observations) > limit
            or len(snap.conclusions) > limit
            or len(snap.artifacts) > limit
        ),
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "stale_ids": sorted(stale_ids),
        "observations": {"count": len(snap.observations), "items": observations},
        "conclusions": {"count": len(snap.conclusions), "items": conclusions},
        "artifacts": {"count": len(snap.artifacts), "items": artifacts},
    }


def _stale_ids(snap) -> set[str]:
    stale: set[str] = set()
    for finding in snap.findings:
        if finding.finding_type == "potentially_stale_dependency":
            stale.update(item for item in finding.affected_ids if item)
    return stale


def _render_audit_html(audit: dict) -> str:
    if not audit:
        return "<p>No run audit available.</p>"
    summary = audit.get("summary", {})
    status = "PASS" if audit.get("ok") else "NEEDS ATTENTION"
    rows = (
        f"<tr><td>Status</td><td>{_h(status)}</td></tr>"
        f"<tr><td>Severity</td><td>{_h(audit.get('severity', 'unknown'))}</td></tr>"
        f"<tr><td>Issues</td><td>{_h(summary.get('errors', 0))} errors, {_h(summary.get('warnings', 0))} warnings</td></tr>"
    )
    issues = []
    for section in ("errors", "warnings"):
        for item in audit.get(section, [])[:8]:
            issues.append(f"<p><strong>{_h(item.get('code'))}</strong>: {_h(item.get('message'))}</p>")
    actions = []
    for item in audit.get("next_actions", [])[:8]:
        args = item.get("args") or {}
        args_text = f" <code>{_h(json.dumps(args, ensure_ascii=False))}</code>" if args else ""
        actions.append(f"<li><code>{_h(item.get('tool'))}</code>{args_text}: {_h(item.get('why'))}</li>")
    action_html = f"<h3>Audit Next Actions</h3><ul>{''.join(actions)}</ul>" if actions else ""
    return f"<table><tr><th>Field</th><th>Value</th></tr>{rows}</table>{''.join(issues) or '<p>No audit issues.</p>'}{action_html}"


def _render_rethinking_html(rethinking: dict) -> str:
    if not rethinking:
        return "<p>No trace-driven rethinking summary available.</p>"
    roots = "".join(
        f"<li><code>{_h(root.get('root_id', ''))}</code> ({_h(root.get('root_type', ''))}): {_h(root.get('reason', ''))}</li>"
        for root in rethinking.get("suspected_roots", [])[:6]
    )
    actions = "".join(
        f"<li><code>{_h(item.get('tool', ''))}</code> {_h(json.dumps(item.get('args') or {}, ensure_ascii=False))}: {_h(item.get('why', ''))}</li>"
        for item in rethinking.get("recommended_actions", [])[:8]
    )
    return (
        "<table><tr><th>Field</th><th>Value</th></tr>"
        f"<tr><td>Status</td><td>{_h(rethinking.get('status', 'unknown'))}</td></tr>"
        f"<tr><td>Target</td><td>{_h(rethinking.get('target_id', '') or 'auto')}</td></tr>"
        f"<tr><td>Evidence status</td><td>{_h(rethinking.get('evidence_status', ''))}</td></tr>"
        f"<tr><td>Upstream nodes</td><td>{_h(rethinking.get('upstream_node_count', 0))}</td></tr>"
        "</table>"
        f"<p>{_h(rethinking.get('summary', ''))}</p>"
        f"<h3>Suspected Roots</h3><ul>{roots or '<li>None</li>'}</ul>"
        f"<h3>Recommended Actions</h3><ul>{actions or '<li>None</li>'}</ul>"
    )


def _render_provenance_html(provenance: dict) -> str:
    if not provenance:
        return "<p>No provenance manifest available.</p>"
    return (
        "<table><tr><th>Item</th><th>Count</th></tr>"
        f"<tr><td>Observations</td><td>{_h(provenance.get('observations', {}).get('count', 0))}</td></tr>"
        f"<tr><td>Conclusions</td><td>{_h(provenance.get('conclusions', {}).get('count', 0))}</td></tr>"
        f"<tr><td>Artifacts</td><td>{_h(provenance.get('artifacts', {}).get('count', 0))}</td></tr>"
        f"<tr><td>Stale IDs</td><td>{_h(len(provenance.get('stale_ids', [])))}</td></tr>"
        "</table>"
    )


def _summary_conclusions(snap) -> list[dict]:
    return [{"grade": c.grade, "text": c.text} for c in snap.conclusions]


def _findings_summary(snap) -> list[dict]:
    return [
        {"severity": f.severity, "type": f.finding_type, "summary": f.summary, "action": f.suggested_action}
        for f in snap.findings[-20:]
    ]


def _coverage_summary(ctx) -> list[dict]:
    return [
        {"subject": c.subject, "label": c.label, "methods": c.methods,
         "observations": c.observations, "contradictions": c.contradictions}
        for c in (ctx.coverage if ctx else [])
    ]


def _memory_summary(ctx) -> list[dict]:
    return [
        {"signal": m.signal, "subject": m.subject, "summary": m.summary, "value": m.current_value}
        for m in (ctx.memory if ctx else [])
    ]


def _observation_detail(snap) -> list[dict]:
    return [
        {"target": o.target, "type": o.type, "metric": o.metric, "value": o.value,
         "method": o.method, "contrast": o.contrast}
        for o in snap.observations[-50:]
    ]


def _generate_narrative(snap, ctx, provider: str) -> str:
    """Use LLM to synthesize a narrative from the evidence."""
    from pertura.planner import _call_llm, _api_key, _anthropic_key
    has_key = _api_key() if provider == "openai" else _anthropic_key()
    if not has_key:
        return "Narrative requires an LLM API key."

    coverage = _coverage_summary(ctx)
    memory = _memory_summary(ctx)
    obs = _observation_detail(snap)[:20]
    findings = _findings_summary(snap)[:10]
    conclusions = _summary_conclusions(snap)

    prompt = json.dumps({
        "goal": snap.goal,
        "domain": snap.domain,
        "attempts": len(snap.attempts),
        "observations": obs,
        "coverage": coverage,
        "memory_signals": memory,
        "findings": findings,
        "conclusions": conclusions,
    }, ensure_ascii=False, default=str)[:8000]

    system = "You are a scientific writer. Write a concise narrative summary (3-5 paragraphs) of the analysis. Include: what was done, key findings, evidence quality, limitations, and recommendations."

    try:
        result = _call_llm(system, prompt, {
            "type": "object", "properties": {"narrative": {"type": "string"}},
            "required": ["narrative"], "additionalProperties": False,
        }, provider=provider)
        return result.get("narrative", "")
    except Exception:
        return "Narrative generation failed."
