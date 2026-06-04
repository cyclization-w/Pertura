"""Core paper-claim manifest for Pertura-v2 artifact checks."""

from __future__ import annotations

CORE_CLAIMS: dict[str, dict[str, object]] = {
    "analysis_graph": {
        "title": "User-editable analysis graph + gate",
        "capsule_claim_id": "editable_analysis_graph_and_gate",
        "capsule_title": "User-editable analysis-node spec + gate",
        "standalone_script": "tests/test_claim_analysis_graph.py",
    },
    "observation_memory": {
        "title": "Scientific observation memory",
        "capsule_claim_id": "scientific_observation_memory",
        "capsule_title": "Scientific observation memory with conflict/coverage/intent",
        "standalone_script": "tests/test_claim_observation_memory.py",
    },
    "deliberative_audit": {
        "title": "Deliberative audit + trace-driven rethinking",
        "capsule_claim_id": "deliberative_agent_with_commit_audit",
        "capsule_title": "Deliberative LLM exploration with audited commit and rethinking loop",
        "standalone_script": "tests/test_claim_deliberative_audit.py",
    },
}


def core_claim_ids() -> list[str]:
    return list(CORE_CLAIMS)


def core_claim(claim_id: str) -> dict[str, object]:
    if claim_id not in CORE_CLAIMS:
        known = ", ".join(sorted(CORE_CLAIMS))
        raise KeyError(f"Unknown core claim: {claim_id}. Known claims: {known}")
    return CORE_CLAIMS[claim_id]


def capsule_claim_id(claim_id: str) -> str:
    return str(core_claim(claim_id)["capsule_claim_id"])


def standalone_claim_command(claim_id: str) -> str:
    return f"python -m pertura.claim_tests --claim {claim_id}"


def standalone_claim_command_array(claim_id: str) -> list[str]:
    core_claim(claim_id)
    return ["python", "-m", "pertura.claim_tests", "--claim", claim_id]


def source_tree_claim_command(claim_id: str) -> str:
    return f"python {core_claim(claim_id)['standalone_script']}"


def claim_id_for_script(script_name: str) -> str:
    normalized = script_name.replace("\\", "/")
    for claim_id, payload in CORE_CLAIMS.items():
        if str(payload["standalone_script"]).replace("\\", "/").endswith(normalized):
            return claim_id
    known = ", ".join(str(item["standalone_script"]) for item in CORE_CLAIMS.values())
    raise KeyError(f"Unknown standalone claim script: {script_name}. Known scripts: {known}")
