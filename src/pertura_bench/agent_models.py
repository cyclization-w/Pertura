from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pertura_core.hashing import canonical_hash


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentWorkflowTurn(AgentModel):
    user_input: str
    provider_output: dict[str, Any] | str
    expected_status: Literal["completed", "needs_input", "blocked", "failed", "cancelled"]


class AgentWorkflowCase(AgentModel):
    schema_version: Literal["pertura-agent-workflow-case-v1"] = "pertura-agent-workflow-case-v1"
    case_id: str
    scenario: str
    description: str
    tier: Literal["deterministic_local", "server_agent"]
    dataset_id: str | None = None
    turns: tuple[AgentWorkflowTurn, ...] = ()
    expected_hard_gates: tuple[str, ...]
    expected_turns: int = 1
    expected_capability_dag: tuple[str, ...] = ()
    expected_statuses: tuple[str, ...] = ()
    required_result_roles: tuple[str, ...] = ()
    required_artifact_roles: tuple[str, ...] = ()
    scope_claim_constraints: tuple[str, ...] = ()
    benchmark_condition: Literal["pertura_full", "prompt_only", "free_codeact"] = "pertura_full"
    provider: str = "claude-agent-sdk"
    model: str | None = None
    provider_config_hash: str | None = None
    max_memory_gb: float = 4.0
    timeout_seconds: int = 300

    @property
    def case_hash(self) -> str:
        return canonical_hash(self)


class AgentHardGateResult(AgentModel):
    gate_id: str
    passed: bool
    detail: str


class AgentNarrativeScore(AgentModel):
    scientific_completeness: int = Field(ge=0, le=4)
    clarity: int = Field(ge=0, le=4)
    limitations_uncertainty: int = Field(ge=0, le=4)
    actionability: int = Field(ge=0, le=4)
    rationale: str
    automatic_failures: tuple[
        Literal[
            "strong_overclaim",
            "prediction_as_measurement",
            "cell_as_independent_replicate",
        ],
        ...,
    ] = ()

    @property
    def average(self) -> float:
        return sum((self.scientific_completeness, self.clarity, self.limitations_uncertainty, self.actionability)) / 4


class JudgeManifest(AgentModel):
    schema_version: Literal["pertura-agent-judge-manifest-v1"] = "pertura-agent-judge-manifest-v1"
    provider: str
    model: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    prompt_hash: str
    rubric_hash: str
    temperature: float = 0.0
    fallback_allowed: Literal[False] = False


class AgentWorkflowVerdict(AgentModel):
    schema_version: Literal["pertura-agent-workflow-verdict-v1"] = "pertura-agent-workflow-verdict-v1"
    case_id: str
    case_hash: str
    status: Literal["passed", "failed", "not_available", "judge_unavailable"]
    hard_gates: tuple[AgentHardGateResult, ...]
    benchmark_condition: Literal["pertura_full", "prompt_only", "free_codeact"] = "pertura_full"
    provider: str | None = None
    model: str | None = None
    provider_config_hash: str | None = None
    narrative_score: AgentNarrativeScore | None = None
    output_hash: str | None = None
    failure_reasons: tuple[str, ...] = ()


def narrative_passes(score: AgentNarrativeScore) -> bool:
    values = (
        score.scientific_completeness,
        score.clarity,
        score.limitations_uncertainty,
        score.actionability,
    )
    return not score.automatic_failures and score.average >= 3.0 and min(values) >= 2
