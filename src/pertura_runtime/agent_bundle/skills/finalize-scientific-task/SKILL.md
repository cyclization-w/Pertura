---
name: finalize-scientific-task
description: Checkpoint and finalize a scientific task against its declared output contract. Use at task startup to create a conservative result checkpoint, and again before completion or closure to validate real artifacts and write benchmark_result.json before the final response.
---

# Finalize a Scientific Task

Apply this skill twice: checkpoint before costly work and close after the last scientific call.

## Checkpoint

1. Read the task identity, dataset identity, output contract, required artifact roles, and claim ceiling.
2. Write a schema-valid `benchmark_result.json` at the exact declared path.
3. Use `blocked` status, an honest limitation, and an empty artifact role list until outputs exist.
4. Do not list planned files as observed artifacts.

## Close

1. Stop new scientific execution.
2. Inspect only current task outputs and at most the reads allowed by completion mode.
3. Verify each required path exists and each table or JSON file contains the declared fields.
4. List only artifact roles backed by real files.
5. Use `completed` only when the required outputs are present; otherwise retain `blocked` and name the missing work.
6. Ground findings and limitations in the actual analysis unit, artifacts, status, and claim ceiling.
7. Write `benchmark_result.json` before returning the required final response object.

Never fabricate missing artifacts, self-award evaluator metrics, or start a replacement analysis during closure. Use [result checklist](references/result-checklist.md) for the final pass.
