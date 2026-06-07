"""LLM tool loop: bounded tool-calling until a state-changing action appears."""

from __future__ import annotations

import json
import os

from pertura.tools import execute_tool, tool_schemas
from pertura.memory.compiler import compile_context
from pertura.core import build_active_work_order, build_view


_STATE_CHANGE_TOOLS = {
    "request_node_transition",
    "execute_code",
    "submit_job",
    "retry",
    "open_branch",
    "switch_branch",
    "close_branch",
    "complete_node",
    "skip_node",
    "ask_user",
    "update_design",
    "finish",
}


def _request_model_label(provider: str) -> str:
    from pertura.planner import _model
    provider = (provider or "openai").lower()
    return f"{provider}:{_model(provider)}"


def _provider_key(provider: str) -> str | None:
    from pertura.planner import _api_key, _anthropic_key
    if (provider or "openai").lower() == "anthropic":
        return _anthropic_key()
    return _api_key()


def _provider_tool_schemas(openai_tools: list[dict], provider: str) -> list[dict]:
    """Return tool schemas in the native format expected by the provider."""
    if (provider or "openai").lower() != "anthropic":
        return openai_tools
    converted = []
    for tool in openai_tools:
        fn = tool.get("function", {})
        schema = fn.get("parameters") or {"type": "object", "properties": {}, "required": []}
        converted.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": schema,
        })
    return converted


def _put_cached_response(request_hash: str, emit, response: dict, *, provider: str) -> None:
    if not (request_hash and emit and hasattr(emit, "__self__")):
        return
    wb = emit.__self__
    if not (hasattr(wb, "_response_cache") and wb._response_cache is not None):
        return
    from pertura.planner import _model
    wb._response_cache.put(request_hash, response, model=_model(provider))


def _block_attr(block, name: str):
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _anthropic_text(blocks: list) -> str:
    parts = []
    for block in blocks:
        if _block_attr(block, "type") == "text":
            parts.append(str(_block_attr(block, "text") or ""))
    return "\n".join(part for part in parts if part)


def _anthropic_block_dict(block) -> dict:
    block_type = _block_attr(block, "type")
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": str(_block_attr(block, "id") or ""),
            "name": str(_block_attr(block, "name") or ""),
            "input": _block_attr(block, "input") or {},
        }
    if block_type == "text":
        return {"type": "text", "text": str(_block_attr(block, "text") or "")}
    if isinstance(block, dict):
        return dict(block)
    return {"type": str(block_type or "text"), "text": str(block)}


def run_tool_loop(result: dict | None, obs_count: int, snap, attempt,
                  provider: str, *, emit=None, is_first: bool = False,
                  coding_guidelines: str = "", protocol: str = "",
                  tools: str = "") -> tuple[str, str, dict, dict]:
    """Run a native LLM tool loop and return the first state-changing action."""
    from pertura.agent.loop import _TOOL_LOOP_PROMPT
    provider = (provider or "openai").lower()

    view_purpose = "deliberation" if is_first or result is None else "critic"
    runtime_state = (result or {}).get("kernel_state", {}) if result else {}
    focus_ids = _focus_ids(snap, attempt)
    context_envelope = build_view(
        snap,
        purpose=view_purpose,
        focus_ids=focus_ids,
        runtime_state=runtime_state,
        token_budget=6000,
    )
    context_view = compile_context(snap, max_items=8)
    system = _TOOL_LOOP_PROMPT
    system += _scoped_domain_prompt(context_view, tools=tools, coding_guidelines=coding_guidelines, protocol=protocol)

    if is_first:
        outcome_text = "status: initial\nsummary: No previous attempt."
    else:
        stdout = (result.get("stdout", "") or "")[-1500:] if result else ""
        stderr = (result.get("stderr", "") or "")[-1500:] if result else ""
        status = "success" if (result or {}).get("returncode") == 0 else "error"
        outcome_text = f"status: {status}\nstdout: {stdout}\nstderr: {stderr}\nobservations_count: {obs_count}"

    latest_goal = snap.goals[-1].text if snap.goals else (snap.goal or "")
    last_delta = _last_attempt_delta(snap, attempt, result, context_envelope) if not is_first else {}
    trace_driven_rethinking = _trace_driven_rethinking(
        snap,
        attempt,
        result,
        obs_count,
        context_envelope,
    ) if not is_first else {}
    all_tools = tool_schemas(readonly=False, snap=snap, scoped=True)
    visible_tool_names = [
        item.get("function", {}).get("name", "")
        for item in all_tools
        if item.get("function", {}).get("name")
    ]
    active_work_order = build_active_work_order(
        snap,
        context_view,
        context_envelope,
        outcome_text=outcome_text,
        last_attempt_delta=last_delta,
        trace_driven_rethinking=trace_driven_rethinking,
        tool_names=visible_tool_names,
    )

    user_payload = {
        "active_work_order": {
            key: value for key, value in active_work_order.items()
            if key != "markdown"
        },
        "compact_context": {
            "active_node_id": context_view.active_node_id,
            "analysis_node": context_view.analysis_node,
            "current_node_progress": context_view.current_node_progress,
            "reachable_nodes": context_view.reachable_nodes,
            "open_interrupts": context_view.open_interrupts,
            "open_triggers": context_view.open_triggers,
            "recent_findings": context_view.recent_findings,
            "observation_memory": context_view.observation_memory,
            "audit_preview": context_envelope.get("audit_preview", {}),
            "trace_driven_rethinking": context_envelope.get("trace_driven_rethinking", {}),
            "runtime_state": context_envelope.get("runtime_state", {}),
        },
        "user_said": latest_goal,
        "previous_code": (attempt.notebook_cells[0].get("source", "")[:1500]
                         if (not is_first and attempt and attempt.notebook_cells)
                         else ""),
        "outcome": outcome_text,
        "current_node": context_view.analysis_node,
        "current_node_progress": context_view.current_node_progress,
        "recommended_actions": context_view.analysis_node.get("recommended_actions", []),
        "expected_outputs": context_view.analysis_node.get("expected_outputs", []),
        "last_attempt_delta": last_delta,
        "trace_driven_rethinking": trace_driven_rethinking,
    }
    user = (
        active_work_order["markdown"]
        + "\n\n# Compact Machine-Readable Context\n"
        + _json_for_prompt(user_payload, max_chars=9000)
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    provider_tools = _provider_tool_schemas(all_tools, provider)
    from pertura.core import hash_llm_request
    request_hash = hash_llm_request(
        system, json.dumps(messages, ensure_ascii=False),
        provider_tools, _request_model_label(provider),
    )
    from pertura.core.fixtures import RecordedLLMFixtures, fixture_mode
    fixtures = RecordedLLMFixtures()
    if fixture_mode() in {"replay", "strict"}:
        item = fixtures.require(request_hash)
        response = item.get("response", {})
        if "action" in response:
            return (
                response.get("action", "respond"),
                response.get("code", ""),
                response.get("assessment", {}),
                response.get("decision", {}),
            )
        return _parse_final_response(response.get("content", ""))

    if emit and hasattr(emit, "__self__"):
        wb = emit.__self__
        if hasattr(wb, "_response_cache") and wb._response_cache is not None:
            cached = wb._response_cache.get(request_hash)
            if cached is not None:
                if "action" in cached:
                    return (
                        cached.get("action", "respond"),
                        cached.get("code", ""),
                        cached.get("assessment", {}),
                        cached.get("decision", {}),
                    )
                return _parse_final_response(cached.get("content", ""))
            if getattr(wb, "_replay_mode", "off") == "strict":
                raise RuntimeError(f"Strict replay cache miss: {request_hash}")

    key = _provider_key(provider)
    if not key:
        return "finish", "", {"status": "no_key", "summary": "No API key"}, {}

    if provider == "anthropic":
        return _run_anthropic_tool_loop(
            key=key, system=system, messages=messages,
            provider_tools=provider_tools, request_hash=request_hash,
            fixtures=fixtures, emit=emit, snap=snap,
        )
    return _run_openai_tool_loop(
        key=key, messages=messages, all_tools=all_tools,
        request_hash=request_hash, fixtures=fixtures, emit=emit, snap=snap,
    )


def _run_openai_tool_loop(
    *,
    key: str,
    messages: list[dict],
    all_tools: list[dict],
    request_hash: str,
    fixtures,
    emit,
    snap,
) -> tuple[str, str, dict, dict]:
    from pertura.planner import _model
    content = ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)
    except ImportError:
        return "finish", "", {"status": "no_openai", "summary": "openai not installed"}, {}

    for _ in range(6):
        response = client.chat.completions.create(
            model=_model("openai"),
            messages=messages,
            tools=all_tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=4096,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            content = msg.content or ""
            break

        messages.append({"role": "assistant", "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": tc.type,
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]})

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            tool_name = tc.function.name
            if tool_name in _STATE_CHANGE_TOOLS:
                code = args.pop("script", "") if tool_name == "submit_job" else args.pop("code", "")
                assessment = {"status": tool_name, "summary": (msg.content or "")[:200]}
                _put_cached_response(request_hash, emit, {
                    "action": tool_name,
                    "code": code,
                    "assessment": assessment,
                    "decision": args,
                }, provider="openai")
                fixtures.put(request_hash, {
                    "action": tool_name,
                    "code": code,
                    "assessment": assessment,
                    "decision": args,
                }, metadata={"provider": "openai", "model": _model("openai")})
                return tool_name, code, {"status": tool_name, "summary": (msg.content or "")[:200]}, args

            tool_result = execute_tool(tool_name, args, snap=snap)
            result_str = json.dumps(tool_result, ensure_ascii=False, default=str)[:3000]
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
            if emit:
                from pertura.hooks import post_tool_call as _ptc
                for evt_type, payload, _actor in _ptc(
                    tool_name, args, str(result_str)[:300],
                    attempt_id=getattr(snap, "active_attempt", ""),
                ):
                    emit(evt_type, payload)

    _put_cached_response(request_hash, emit, {"content": content}, provider="openai")
    fixtures.put(request_hash, {"content": content}, metadata={"provider": "openai", "model": _model("openai")})

    return _parse_final_response(content)


def _run_anthropic_tool_loop(
    *,
    key: str,
    system: str,
    messages: list[dict],
    provider_tools: list[dict],
    request_hash: str,
    fixtures,
    emit,
    snap,
) -> tuple[str, str, dict, dict]:
    from pertura.planner import _model
    content = ""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key, base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
    except ImportError:
        return "finish", "", {"status": "no_anthropic", "summary": "anthropic not installed"}, {}

    for _ in range(6):
        anthropic_messages = [msg for msg in messages if msg.get("role") != "system"]
        response = client.messages.create(
            model=_model("anthropic"),
            system=system,
            messages=anthropic_messages,
            tools=provider_tools,
            temperature=0.1,
            max_tokens=4096,
        )
        blocks = list(getattr(response, "content", []) or [])
        text = _anthropic_text(blocks)
        tool_uses = [block for block in blocks if _block_attr(block, "type") == "tool_use"]

        if not tool_uses:
            content = text
            break

        messages.append({"role": "assistant", "content": [_anthropic_block_dict(block) for block in blocks]})
        tool_results = []
        for block in tool_uses:
            tool_name = str(_block_attr(block, "name") or "")
            args = _block_attr(block, "input") or {}
            if not isinstance(args, dict):
                args = {}
            if tool_name in _STATE_CHANGE_TOOLS:
                code = args.pop("script", "") if tool_name == "submit_job" else args.pop("code", "")
                assessment = {"status": tool_name, "summary": text[:200]}
                _put_cached_response(request_hash, emit, {
                    "action": tool_name,
                    "code": code,
                    "assessment": assessment,
                    "decision": args,
                }, provider="anthropic")
                fixtures.put(request_hash, {
                    "action": tool_name,
                    "code": code,
                    "assessment": assessment,
                    "decision": args,
                }, metadata={"provider": "anthropic", "model": _model("anthropic")})
                return tool_name, code, assessment, args

            tool_result = execute_tool(tool_name, args, snap=snap)
            result_str = json.dumps(tool_result, ensure_ascii=False, default=str)[:3000]
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": str(_block_attr(block, "id") or ""),
                "content": result_str,
            })
            if emit:
                from pertura.hooks import post_tool_call as _ptc
                for evt_type, payload, _actor in _ptc(
                    tool_name, args, str(result_str)[:300],
                    attempt_id=getattr(snap, "active_attempt", ""),
                ):
                    emit(evt_type, payload)
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    _put_cached_response(request_hash, emit, {"content": content}, provider="anthropic")
    fixtures.put(request_hash, {"content": content}, metadata={"provider": "anthropic", "model": _model("anthropic")})
    return _parse_final_response(content)


def _json_for_prompt(payload: dict, *, max_chars: int = 20000) -> str:
    """Serialize a prompt payload without cutting JSON mid-structure."""
    original = json.dumps(payload, ensure_ascii=False, default=str)
    if len(original) <= max_chars:
        return original
    profiles = [
        {"string_limit": 2000, "list_limit": 12, "dict_limit": 48, "max_depth": 6},
        {"string_limit": 1000, "list_limit": 8, "dict_limit": 32, "max_depth": 5},
        {"string_limit": 500, "list_limit": 5, "dict_limit": 24, "max_depth": 4},
        {"string_limit": 240, "list_limit": 3, "dict_limit": 16, "max_depth": 3},
    ]
    for profile in profiles:
        compact = _compact_prompt_value(payload, **profile)
        if isinstance(compact, dict):
            compact["_prompt_truncation"] = {
                "truncated": True,
                "original_chars": len(original),
                "profile": profile,
            }
        text = json.dumps(compact, ensure_ascii=False, default=str)
        if len(text) <= max_chars:
            return text
    minimal = {
        "context_envelope": _compact_prompt_value(
            payload.get("context_envelope", {}),
            string_limit=160,
            list_limit=2,
            dict_limit=12,
            max_depth=2,
        ),
        "user_said": _truncate_string(str(payload.get("user_said", "")), 600),
        "outcome": _truncate_string(str(payload.get("outcome", "")), 900),
        "current_node": _compact_prompt_value(
            payload.get("current_node", {}),
            string_limit=160,
            list_limit=2,
            dict_limit=12,
            max_depth=2,
        ),
        "last_attempt_delta": _compact_prompt_value(
            payload.get("last_attempt_delta", {}),
            string_limit=160,
            list_limit=2,
            dict_limit=12,
            max_depth=2,
        ),
        "_prompt_truncation": {
            "truncated": True,
            "original_chars": len(original),
            "mode": "minimal",
        },
    }
    minimal_text = json.dumps(minimal, ensure_ascii=False, default=str)
    if len(minimal_text) <= max_chars:
        return minimal_text
    emergency = {
        "user_said": _truncate_string(str(payload.get("user_said", "")), 240),
        "outcome": _truncate_string(str(payload.get("outcome", "")), 320),
        "_prompt_truncation": {
            "truncated": True,
            "original_chars": len(original),
            "mode": "emergency_minimal",
        },
    }
    return json.dumps(emergency, ensure_ascii=False, default=str)


def _compact_prompt_value(
    value,
    *,
    string_limit: int,
    list_limit: int,
    dict_limit: int,
    max_depth: int,
    _depth: int = 0,
):
    if _depth >= max_depth:
        return _summarize_prompt_leaf(value, string_limit=string_limit)
    if isinstance(value, str):
        return _truncate_string(value, string_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        items = list(value.items())
        out = {}
        for key, child in items[:dict_limit]:
            out[str(key)] = _compact_prompt_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
        if len(items) > dict_limit:
            out["_truncated_keys"] = len(items) - dict_limit
        return out
    if isinstance(value, (list, tuple)):
        out = [
            _compact_prompt_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for child in list(value)[:list_limit]
        ]
        if len(value) > list_limit:
            out.append({"_truncated_items": len(value) - list_limit})
        return out
    return _truncate_string(str(value), string_limit)


def _summarize_prompt_leaf(value, *, string_limit: int):
    if isinstance(value, str):
        return _truncate_string(value, string_limit)
    if isinstance(value, dict):
        return {"_summary": "dict", "keys": list(value.keys())[:8], "total_keys": len(value)}
    if isinstance(value, (list, tuple)):
        return {"_summary": "list", "items": len(value)}
    return value if isinstance(value, (int, float, bool)) or value is None else _truncate_string(str(value), string_limit)


def _truncate_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    head = max(0, limit // 2)
    tail = max(0, limit - head - 40)
    return f"{value[:head]}...[truncated {len(value) - limit} chars]...{value[-tail:]}"


def _last_attempt_delta(snap, attempt, result: dict | None, context_envelope: dict | None = None) -> dict:
    if not attempt:
        return {}
    attempt_id = getattr(attempt, "attempt_id", "")
    outcome = next((item for item in reversed(snap.outcomes) if item.attempt_id == attempt_id), None)
    observations = [
        {
            "observation_id": obs.observation_id,
            "type": obs.type,
            "target": obs.target,
            "metric": obs.metric,
            "value": obs.value,
            "contrast": obs.contrast,
            "method": obs.method,
        }
        for obs in snap.observations
        if obs.attempt_id == attempt_id
    ][-8:]
    artifacts = [
        {
            "artifact_id": artifact.artifact_id,
            "kind": artifact.kind,
            "path": artifact.path,
            "summary": artifact.summary,
        }
        for artifact in snap.artifacts
        if artifact.attempt_id == attempt_id
    ][-8:]
    findings = [
        {
            "finding_id": finding.finding_id,
            "type": finding.finding_type,
            "severity": finding.severity,
            "summary": finding.summary,
            "action": finding.suggested_action,
        }
        for finding in snap.findings
        if finding.attempt_id == attempt_id
    ][-8:]
    runtime_symbols = (context_envelope or {}).get("runtime_symbols", {})
    runtime_refs = [
        symbol_id
        for symbol_id, asset in runtime_symbols.items()
        if (
            asset.get("created_by_attempt") == attempt_id
            or asset.get("kind") == "kernel_symbol"
        )
    ][:12]
    outcome_view = outcome.model_dump(mode="json") if outcome else {}
    if outcome_view:
        metrics = dict(outcome_view.get("metrics") or {})
        metrics.pop("kernel_state", None)
        metrics.pop("runtime_state", None)
        outcome_view["metrics"] = metrics
    return {
        "attempt_id": attempt_id,
        "title": getattr(attempt, "title", ""),
        "stage": getattr(attempt, "stage", ""),
        "analysis_node_id": getattr(attempt, "analysis_node_id", ""),
        "status": getattr(attempt, "status", ""),
        "design_fields_used": getattr(attempt, "design_fields_used", []),
        "outcome": outcome_view,
        "observations_registered": len(observations),
        "new_observations": observations,
        "new_artifacts": artifacts,
        "new_findings": findings,
        "runtime_refs": runtime_refs,
        "execution": {
            "returncode": (result or {}).get("returncode"),
            "timed_out": (result or {}).get("timed_out", False),
            "timed_out_at": (result or {}).get("timed_out_at", ""),
            "soft_timeout_hit": (result or {}).get("soft_timeout_hit", False),
            "execution_time": (result or {}).get("execution_time"),
        },
    }


def _trace_driven_rethinking(snap, attempt, result: dict | None, obs_count: int, context_envelope: dict | None = None) -> dict:
    """Return a compact rethinking plan when the last step should not simply continue."""
    if attempt is None:
        return {}
    reasons: list[str] = []
    returncode = (result or {}).get("returncode")
    if returncode not in (None, 0):
        reasons.append(f"last attempt returned nonzero code {returncode}")
    if (result or {}).get("timed_out") or (result or {}).get("soft_timeout_hit"):
        reasons.append(f"last attempt timeout state: {(result or {}).get('timed_out_at') or 'soft_timeout'}")
    if obs_count == 0:
        reasons.append("last attempt registered zero observations")
    audit_preview = (context_envelope or {}).get("audit_preview", {}) or {}
    issue_codes = set(audit_preview.get("top_issue_codes", []) or [])
    if issue_codes & {
        "stale_conclusion_evidence",
        "unverified_conclusion_evidence",
        "missing_conclusion_support",
        "unsupported_conclusion",
        "missing_capability_outputs",
    }:
        reasons.append("audit preview reports evidence/capability issues: " + ", ".join(sorted(issue_codes)))
    if not reasons:
        return {}
    target_id = _best_rethinking_target(snap, attempt, context_envelope)
    try:
        from pertura.core.rethinking import plan_rethinking
        plan = plan_rethinking(
            snap,
            node_id=target_id,
            issue="; ".join(reasons),
            depth=5,
        )
    except Exception as exc:
        return {
            "status": "plan_failed",
            "target_id": target_id,
            "reasons": reasons,
            "error": str(exc),
            "recommended_actions": [
                {"tool": "audit_run", "args": {}, "why": "recompute deterministic audit after failed rethinking plan"},
                {"tool": "trace_upstream", "args": {"node_id": target_id, "depth": 5}, "why": "manually inspect provenance for the suspect node"},
            ],
        }
    return {
        "status": plan.get("status", ""),
        "target_id": plan.get("target_id", target_id),
        "reasons": reasons,
        "summary": plan.get("summary", ""),
        "suspected_roots": plan.get("suspected_roots", [])[:6],
        "recommended_actions": plan.get("recommended_actions", [])[:8],
        "policy": plan.get("policy", {}),
    }


def _best_rethinking_target(snap, attempt, context_envelope: dict | None = None) -> str:
    focus_ids = list((context_envelope or {}).get("focus_ids", []) or [])
    for item in focus_ids:
        if item:
            return item
    attempt_id = getattr(attempt, "attempt_id", "")
    for finding in reversed(getattr(snap, "findings", []) or []):
        affected = list(getattr(finding, "affected_ids", []) or [])
        if getattr(finding, "attempt_id", "") == attempt_id and affected:
            return affected[0]
        if getattr(finding, "attempt_id", "") == attempt_id and getattr(finding, "finding_id", ""):
            return finding.finding_id
    for obs in reversed(getattr(snap, "observations", []) or []):
        if getattr(obs, "attempt_id", "") == attempt_id:
            return obs.observation_id
    for artifact in reversed(getattr(snap, "artifacts", []) or []):
        if getattr(artifact, "attempt_id", "") == attempt_id:
            return artifact.artifact_id
    return attempt_id


def _focus_ids(snap, attempt) -> list[str]:
    ids: list[str] = []
    if attempt:
        attempt_id = getattr(attempt, "attempt_id", "")
        if attempt_id:
            ids.append(attempt_id)
            ids.extend(
                obs.observation_id
                for obs in snap.observations
                if obs.attempt_id == attempt_id
            )
            ids.extend(
                artifact.artifact_id
                for artifact in snap.artifacts
                if artifact.attempt_id == attempt_id
            )
    ids.extend(finding_id for finding_id in _recent_affected_ids(snap))
    seen = set()
    out = []
    for item in ids:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out[:12]


def _recent_affected_ids(snap) -> list[str]:
    ids: list[str] = []
    for finding in snap.findings[-5:]:
        ids.extend(getattr(finding, "affected_ids", []) or [])
    return ids


def _scoped_domain_prompt(context_view, *, tools: str = "", coding_guidelines: str = "", protocol: str = "") -> str:
    """Return compact node-scoped domain guidance.

    Domain packs may contain long SOPs. The LLM turn should see the active node
    contract and only the generally relevant safety/registration rules, not the
    full domain document on every call.
    """
    node = context_view.analysis_node or {}
    progress = context_view.current_node_progress or {}
    payload = {
        "active_node": {
            "node_id": node.get("node_id", ""),
            "title": node.get("title", ""),
            "purpose": node.get("purpose", ""),
            "allowed_capabilities": node.get("allowed_capabilities", []),
            "recommended_actions": node.get("recommended_actions", []),
            "expected_outputs": node.get("expected_outputs", []),
        },
        "capability_contracts": context_view.capabilities,
        "completion_status": {
            "passed": progress.get("completion_passed", 0),
            "total": progress.get("completion_total", 0),
            "missing": progress.get("missing_completion", []),
        },
        "node_counts": {
            "attempts": progress.get("attempts", 0),
            "observations": progress.get("observations", 0),
            "artifacts": progress.get("artifacts", 0),
        },
    }
    chunks = [
        "\n\nACTIVE NODE CONTRACT:\n" + json.dumps(payload, ensure_ascii=False, default=str),
    ]
    relevant_rules = _extract_relevant_sections(
        coding_guidelines,
        keys=[
            "Behavioral Rules",
            "Registration",
            "Data & Schema",
            node.get("node_id", ""),
            node.get("title", ""),
        ],
        fallback_chars=1200,
    )
    if relevant_rules:
        chunks.append("\n\nNODE-SCOPED RULES:\n" + relevant_rules)
    relevant_tools = _extract_relevant_sections(
        tools,
        keys=[
            "General Rules",
            "Data Loading",
            "Observation",
            "Plotting",
            node.get("node_id", ""),
            node.get("title", ""),
            *node.get("allowed_capabilities", []),
        ],
        fallback_chars=1200,
    )
    if relevant_tools:
        chunks.append("\n\nNODE-SCOPED TOOL GUIDE:\n" + relevant_tools)
    if protocol and not relevant_rules and not relevant_tools:
        chunks.append("\n\nDOMAIN PROTOCOL SUMMARY:\n" + protocol[:1200])
    return "".join(chunks)


def _extract_relevant_sections(text: str, *, keys: list[str], fallback_chars: int = 1000) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    keys_l = [key.lower().replace("_", " ") for key in keys if key]
    selected: list[str] = []
    include = False
    budget = 0
    for line in lines:
        stripped = line.strip()
        heading = stripped.startswith("#") or stripped.startswith("[") or stripped[:3].isdigit()
        lowered = stripped.lower().replace("_", " ")
        if heading:
            include = any(key and key in lowered for key in keys_l)
        if include or any(key and key in lowered for key in keys_l):
            selected.append(line)
            budget += len(line)
        if budget > 2200:
            break
    result = "\n".join(selected).strip()
    if result:
        return result[:2600]
    return text[:fallback_chars]


def _parse_final_response(content: str):
    if not content.strip():
        return "execute_code", "", {"status": "empty", "summary": "No content"}, {}

    try:
        import re
        text = content.strip()
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            dj = json.loads(text[start:end + 1])
            action = dj.get("decision", dj.get("action", "execute_code"))
            code = dj.get("code", "")
            assessment = {"status": action, "summary": dj.get("reason", content[:200])}
            return action, code, assessment, dj
    except Exception:
        pass

    if "```python" not in content and "import " not in content[:200]:
        return "respond", "", {"status": "respond", "summary": content[:200]}, {"response": content}

    import re
    code = content
    m = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
    if m:
        code = m.group(1).strip()
    return "execute_code", code, {"status": "execute_code", "summary": "Code from LLM"}, {}
