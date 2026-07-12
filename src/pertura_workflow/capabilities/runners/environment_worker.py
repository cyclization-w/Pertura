from __future__ import annotations

import json
import sys
from pathlib import Path

from pertura_core import CapabilityRunRequest, CapabilitySpec, DatasetContract
from pertura_workflow.capabilities.executors import execute_capability


def main(config_path: str) -> None:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    spec = CapabilitySpec.model_validate(config["spec"])
    request = CapabilityRunRequest.model_validate(config["request"])
    contract = DatasetContract.model_validate(config["contract"])
    context = dict(config.get("runtime_context") or {})
    context["inside_environment_worker"] = True
    result = execute_capability(
        spec,
        request,
        contract,
        config["staging_dir"],
        runtime_context=context,
    )
    Path(config["result_path"]).write_text(
        result.model_dump_json(),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main(sys.argv[1])
