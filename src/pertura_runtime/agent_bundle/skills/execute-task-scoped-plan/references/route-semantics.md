# Route Semantics

- **Capability route:** Run only nodes marked `ready`. A `planned` node waits for an in-plan dependency. A `blocked` node is not callable.
- **CodeAct route:** Use the exact registered inputs and frozen environment. A `bound_skill_pipeline` executes its steps in order without a wrapper; a `single_script` route uses its invocation. These modes are mutually exclusive. CodeAct output remains exploratory even when it matches the requested schema.
- **Evidence interpretation route:** Transform registered evidence into calibrated language and required tables without recomputing the evidence.
- **Blocked route:** Preserve blockers in the result and limitations. Do not invent metadata, dependencies, or outputs.

The task output contract controls filenames and required columns. The plan controls applicability and route. Neither grants scientific authority beyond the recorded source class.
