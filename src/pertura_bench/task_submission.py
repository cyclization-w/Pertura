from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from pertura_bench.agent_models import AgentBenchmarkResult
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.project.models import TurnDraft


SUBMISSION_SERVER_NAME = "benchmark_io"
SUBMISSION_TOOL_NAME = "submit_task_bundle"
SUBMISSION_ALLOWED_TOOL = f"mcp__{SUBMISSION_SERVER_NAME}__{SUBMISSION_TOOL_NAME}"


class TaskSubmissionService:
    """Condition-neutral, task-scoped benchmark measurement boundary."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._task_id: str | None = None
        self._dataset_id: str | None = None
        self._output_root: Path | None = None
        self._submitted_turn_draft: str | None = None

    def bind_task(self, *, task_id: str, dataset_id: str) -> None:
        output_root = (self.workspace_root / "outputs" / "tasks" / task_id).resolve()
        expected_parent = (self.workspace_root / "outputs" / "tasks").resolve()
        if output_root.parent != expected_parent:
            raise ValueError("task identity escapes the canonical output root")
        output_root.mkdir(parents=True, exist_ok=True)
        self._task_id = task_id
        self._dataset_id = dataset_id
        self._output_root = output_root
        self._submitted_turn_draft = None
        for name in ("submitted_turn_draft.json", "submission_receipt.json"):
            (output_root / name).unlink(missing_ok=True)

    def submitted_turn_draft(self) -> str | None:
        return self._submitted_turn_draft

    def submit_task_bundle(self, args: dict[str, Any] | None) -> dict[str, Any]:
        if (
            self._output_root is None
            or self._task_id is None
            or self._dataset_id is None
        ):
            return _rejected("submission service is not bound to a task")
        payload = dict(args or {})
        errors: list[dict[str, Any]] = []
        try:
            result = AgentBenchmarkResult.model_validate(
                payload.get("benchmark_result")
            )
        except ValidationError as exc:
            errors.extend(_validation_errors("benchmark_result", exc))
            result = None
        try:
            draft = TurnDraft.model_validate(payload.get("turn_draft"))
        except ValidationError as exc:
            errors.extend(_validation_errors("turn_draft", exc))
            draft = None
        if result is not None:
            if result.case_id != self._task_id:
                errors.append(_field_error("benchmark_result.case_id", self._task_id))
            if result.dataset_id != self._dataset_id:
                errors.append(
                    _field_error("benchmark_result.dataset_id", self._dataset_id)
                )
        if errors:
            return {
                "accepted": False,
                "submission_id": None,
                "canonical_hash": None,
                "errors": errors,
            }
        assert result is not None and draft is not None
        result_payload = result.model_dump(mode="json")
        draft_payload = draft.model_dump(mode="json")
        result_path = self._output_root / "benchmark_result.json"
        draft_path = self._output_root / "submitted_turn_draft.json"
        receipt_path = self._output_root / "submission_receipt.json"
        _atomic_json(result_path, result_payload)
        _atomic_json(draft_path, draft_payload)
        submission_id = f"submission_{uuid4().hex}"
        receipt = {
            "schema_version": "pertura-benchmark-submission-receipt-v1",
            "accepted": True,
            "submission_id": submission_id,
            "task_id": self._task_id,
            "dataset_id": self._dataset_id,
            "benchmark_result_sha256": file_sha256(result_path),
            "turn_draft_sha256": file_sha256(draft_path),
            "benchmark_result_hash": canonical_hash(result_payload),
            "turn_draft_hash": canonical_hash(draft_payload),
        }
        receipt["canonical_hash"] = canonical_hash(receipt)
        _atomic_json(receipt_path, receipt)
        self._submitted_turn_draft = json.dumps(
            draft_payload, sort_keys=True, ensure_ascii=False
        )
        return {
            "accepted": True,
            "submission_id": submission_id,
            "canonical_hash": receipt["canonical_hash"],
            "errors": [],
        }


def create_task_submission_mcp_server(service: TaskSubmissionService):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    description = (
        "Atomically submit the scientific benchmark result and final TurnDraft. "
        "Both objects are validated before any accepted receipt is written. "
        f"benchmark_result schema: {json.dumps(AgentBenchmarkResult.model_json_schema(), sort_keys=True)}; "
        f"turn_draft schema: {json.dumps(TurnDraft.model_json_schema(), sort_keys=True)}"
    )

    async def submit(args: dict[str, Any]) -> dict[str, Any]:
        response = service.submit_task_bundle(args)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        response,
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name=SUBMISSION_SERVER_NAME,
        version="1.0.0",
        tools=[
            tool(
                SUBMISSION_TOOL_NAME,
                description,
                {"benchmark_result": dict, "turn_draft": dict},
            )(submit)
        ],
    )


def validate_submission_receipt(
    output_root: Path, *, task_id: str, dataset_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    root = Path(output_root)
    result_path = root / "benchmark_result.json"
    draft_path = root / "submitted_turn_draft.json"
    receipt_path = root / "submission_receipt.json"
    if not receipt_path.is_file():
        return None, "typed submission receipt is missing"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"typed submission receipt is invalid: {exc}"
    expected_hash = receipt.pop("canonical_hash", None)
    if (
        receipt.get("schema_version") != "pertura-benchmark-submission-receipt-v1"
        or receipt.get("accepted") is not True
        or receipt.get("task_id") != task_id
        or receipt.get("dataset_id") != dataset_id
        or expected_hash != canonical_hash(receipt)
    ):
        return None, "typed submission receipt identity or hash is invalid"
    if not result_path.is_file() or not draft_path.is_file():
        return None, "typed submission files are missing"
    if receipt.get("benchmark_result_sha256") != file_sha256(result_path):
        return None, "benchmark result no longer matches its submission receipt"
    if receipt.get("turn_draft_sha256") != file_sha256(draft_path):
        return None, "TurnDraft no longer matches its submission receipt"
    receipt["canonical_hash"] = expected_hash
    return receipt, None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validation_errors(prefix: str, exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join((prefix, *(str(item) for item in error["loc"]))),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]


def _field_error(field: str, expected: str) -> dict[str, Any]:
    return {
        "field": field,
        "message": f"must equal the bound value {expected!r}",
        "type": "identity_mismatch",
    }


def _rejected(message: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "submission_id": None,
        "canonical_hash": None,
        "errors": [{"field": "task", "message": message, "type": "not_bound"}],
    }
