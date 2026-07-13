# Batch 0 — 信任边界:可直接套用的实现设计

> 目标:补上缺失的 run 级信任边界,让 strict/paper 在真实运行中生效、信任不可伪造、trusted 执行 agent 够得到。
> 修掉:H1(ledger 可写)· H2(calibration 不写 ledger)· H3(trusted runner 不可达 + 回执骗人)· H4(无真实可信路径)· H5(policy 由 LLM 选)· M4(recipe 用 smoke DE)。
> 形式:每步给锚点(函数名/特征行)+ 前后代码。**按顺序套,每步跑一次测试。**

安全影响排序:**Step 1(H5)+ Step 5(H1)是地基,没这两个其余全是摆设。** 建议顺序 1→2→3→4→5→6。

---

## Step 1 — run 级不可变 policy(H5/H4)【keystone】

**思想:** policy 由发起方在启动时固定,写进 manifest,穿透到 MCP + finalizer。LLM 不能选更松的;最多能请求更严(clamp)。

### 1a. `ClaudeRuntimeOptions` 增加 policy 字段
文件:`pertura_runtime/claude/options.py`
```python
# 在 ClaudeRuntimeOptions 数据类里加一个字段(与其它字段同风格):
    policy_profile: str = "smoke"   # run 级不可变;发起方设定,LLM 不可覆盖为更松
```
`describe_options()` 里把它带上(用于 manifest 审计):
```python
    # describe_options 返回的 dict 里加:
        "policy_profile": config.policy_profile,
```

### 1b. MCP server 接收 run policy
文件:`pertura_runtime/claude/tools/evidence_tools.py`
```python
from pertura_gate.core.policy import policy_for_profile, GatePolicy   # 已有 policy_for_profile

def create_evidence_mcp_server(workspace, registry, *, run_policy: GatePolicy):   # 新增 run_policy 形参
    ...
```
把 `evaluate_claims` / `render_evidence_report` 两个工具里的:
```python
# 旧(两处):
        policy = policy_for_profile(str(args.get("policy_profile") or "smoke"))
```
改成 **clamp**(用 run_policy,LLM 只能请求更严、不能更松):
```python
        policy = _clamp_policy(run_policy, args.get("policy_profile"))
```
并在文件底部加(profile 严格度序:smoke<strict<paper):
```python
_PROFILE_RANK = {"smoke": 0, "strict": 1, "paper": 2}

def _clamp_policy(run_policy: GatePolicy, requested: str | None) -> GatePolicy:
    """Run policy is the floor. LLM may request a STRICTER profile, never a weaker one."""
    if not requested:
        return run_policy
    req = str(requested).strip().lower()
    if _PROFILE_RANK.get(req, -1) > _PROFILE_RANK.get(run_policy.profile, 0):
        return policy_for_profile(req)
    return run_policy
```

### 1c. `build_agent_options` 把 run_policy 传给 MCP server
文件:`pertura_runtime/claude/options.py`(`build_agent_options` 里创建 MCP server 处)
```python
# 找到创建 pertura_evidence MCP server 的地方(调用 create_evidence_mcp_server(...)),加:
    run_policy=policy_for_profile(config.policy_profile)
# 顶部 import:
from pertura_gate.core.policy import policy_for_profile
```

### 1d. finalizer 用 run policy(H4 的关键:最终报告按真实 policy 出)
文件:`pertura_runtime/claude/finalizer.py`
- `build_runtime_final_summary(workspace, *, status, error=None)` → 加 `run_policy: GatePolicy = DEFAULT_POLICY` 形参。
- 顶部 import:`from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy`
- `_ensure_evidence_report(workspace)` → 加 `run_policy` 形参,内部两处 `render_evidence_report(...)` 都传 `policy=run_policy`。
- `_write_turn_final(...)` 和 `_write_analysis_state_manifest(...)` 里:
```python
# 旧:
        payload["artifact_intrinsic_ceiling"] = resolve_artifact_strength(artifact).ceiling.value
# 新:
        payload["artifact_intrinsic_ceiling"] = resolve_artifact_strength(artifact, policy=run_policy).ceiling.value
```

### 1e. agent 传 policy + 写进 manifest
文件:`pertura_runtime/claude/agent.py`
```python
# _finalize_with_runtime_summary 里:
    def _finalize_with_runtime_summary(self, *, status, error=None) -> str:
        run_policy = policy_for_profile(self.config.policy_profile)
        runtime_final = build_runtime_final_summary(self.workspace, status=status, error=error, run_policy=run_policy)
        ...
# run() 里 update_manifest 的 dict 加:
            "policy_profile": self.config.policy_profile,
            "policy_hash": policy_for_profile(self.config.policy_profile).policy_hash,
# 顶部 import:
from pertura_gate.core.policy import policy_for_profile
```

### 1f. CLI 暴露 `--profile`
文件:`pertura_runtime/claude/cli.py`(和/或 `pertura_workflow/cli.py`)
```python
    parser.add_argument("--profile", choices=["smoke", "strict", "paper"], default="smoke",
                        help="Run-level gate policy. Immutable for the run; the model cannot weaken it.")
    # 构造 ClaudeRuntimeOptions 时:policy_profile=args.profile
```

**测试影响:** 新增 `evaluate_claims`/`render` 的 clamp 行为测试(LLM 传 smoke 时 run=paper 仍按 paper);现有默认 smoke 行为不变(默认 run_policy=smoke)。

---

## Step 2 — calibration runner 写 ledger(H2)【机械,解锁真实 calibration】

文件:`pertura_workflow/runners/control_calibration.py`
把两个 runner 里的本地 hash + 输出,改成与 `pseudobulk_de.py` 同构:

顶部:
```python
from pertura_gate.evidence.execution_ledger import canonical_execution_hash, file_sha256
from pertura_workflow.trusted_run import record_trusted_run

RUNNER_NAME = "control_calibration"
RUNNER_VERSION = "control_calibration_v1"
```
在每个 runner **写完 out_path 之后、return 之前**,替换现在的 `payload["execution_hash"] = _execution_hash(payload)`:
```python
    input_hashes = {"expression_csv": file_sha256(expression_path), "metadata_csv": file_sha256(metadata_path)}
    parameters = {  # NTC 版用 control_uid;permutation 版用 contrast_uid/left/baseline
        "calibration_type": payload["calibration_type"],
        "method": <NTC_METHOD 或 LABEL_PERMUTATION_METHOD>,
        "layer": layer, "alpha": alpha, "seed": seed,
        # NTC: "control_uid": control_uid ; permutation: "contrast_uid":..., "left_uid":..., "baseline_uid":...
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")  # 先落盘
    output_hashes = {"calibration": file_sha256(out_path)}
    execution_hash = canonical_execution_hash({
        "runner_name": RUNNER_NAME, "runner_version": RUNNER_VERSION,
        "method": parameters["method"], "input_hashes": input_hashes, "parameters": parameters,
    })
    ledger = record_trusted_run(root, execution_hash=execution_hash, runner_name=RUNNER_NAME,
        runner_version=RUNNER_VERSION, method=parameters["method"],
        input_hashes=input_hashes, output_hashes=output_hashes, parameters=parameters)
    payload["execution_hash"] = execution_hash
    payload["execution_ledger_relative_path"] = ledger["execution_ledger_relative_path"]
```
**删掉本地 `_execution_hash` 函数**(不再用,统一 canonical hash)。

> 注意 output-hash 绑定:registry 对注册文件算的 `source_sha256` 必须 ∈ ledger 的 output_hashes。上面 `output_hashes` 用的是 out_path 的 sha,与注册的同一文件,一致。

**测试影响:** `test_phase1b` 的 `_register_calibration` 现在手动 `record_trusted_run` seed——改成**真跑 runner** 拿 receipt(见 Step 3/4 模式),或保留 seed 但确认 method 一致。真实 calibration 现在能过 paper 了。

---

## Step 3 — 暴露 boundary 的 trusted 执行工具给 agent(H3)

文件:`pertura_runtime/claude/tools/evidence_tools.py`,新增两个 MCP `@tool`,内部调 workflow runner,返回 **run_receipt**:
```python
from pertura_workflow.runners.pseudobulk_de import run_pseudobulk_de_for_registered_contrast
from pertura_workflow.runners.control_calibration import run_ntc_vs_ntc_calibration, run_label_permutation_null

    @tool("run_trusted_pseudobulk_de",
          "Run the trusted pseudobulk DE runner. Writes the execution ledger and returns a run_receipt. "
          "This is the ONLY way to produce ledger-backed measured DE; you cannot author execution_hash yourself.",
          {"expression_csv": str, "metadata_csv": str, "contrast_uid": str, "left_uid": str,
           "baseline_uid": str, "replicate_column": str, "layer": str})
    async def run_trusted_pseudobulk_de(args):
        result = run_pseudobulk_de_for_registered_contrast(workspace.root, **_de_args(args))
        return {"success": True, "run_receipt": _receipt(result), "output_relative_path": result["relative_path"], ...}
```
`_receipt(result)` 取:
```python
def _receipt(result):
    return {k: result[k] for k in ("execution_hash", "method", "execution_ledger_relative_path") if k in result}
```
同法加 `run_trusted_control_calibration`(调 NTC / label-permutation runner)。

**注册这两个工具进 `create_evidence_mcp_server` 的 tool 列表。** 更新 prompt(prompt.py)告诉 LLM:measured/calibration 证据必须**先调 run_trusted_* 拿 receipt,再注册**;不能自己写 execution_hash。

---

## Step 4 — 注册改收 receipt,去掉 LLM 手填 hash(H3/M4)

文件:`pertura_runtime/claude/tools/evidence_tools.py`,`register_measured_de_artifact` 的 schema:
```python
# 删掉 "method": str, "execution_hash": str, "code_sha256": str 这几个 LLM 自由字段
# 加:
        "run_receipt": dict,
```
handler 里:
```python
        receipt = _optional_dict(args.get("run_receipt"))
        artifact = registry.register_measured_de(
            ...,
            method=_optional_text(receipt.get("method")),          # 来自 receipt,不是 LLM
            execution_hash=_optional_text(receipt.get("execution_hash")),
        )
```
`register_control_calibration_artifact` 同改(method/execution_hash 取自 receipt)。
> 安全底座仍是 ledger:即使 LLM 伪造 receipt,execution_hash 不在规范 ledger → 不可信。receipt 只是去掉"自己造 hash"的误导性入口。

**回执按当前 policy 报真强度(补 H3 反馈闭环):** `_registration_result` 增加 `run_policy` 闭包,把:
```python
    intrinsic = resolve_artifact_strength(artifact)   # 旧:smoke
```
改成用当前 run policy 跑一次 claim-conditioned 预评估,并在返回里加 `"gated_ceiling_under_run_policy"` + `"missing_for_measured"`(缺哪些 check)+ `"next_action"`。让 Claude 立刻知道差什么、下一步调哪个工具。

---

## Step 5 — ledger 写入隔离(H1)【地基】

文件:`pertura_runtime/claude/permissions.py`
让 CodeAct 的 Write/Edit/Bash **不能写 `artifacts/`**(ledger + 证据登记都在这)。`_is_protected_path` 现在只查 input_dir,扩展:
```python
def _is_protected_path(workspace, path) -> bool:
    resolved = _resolve_user_path(workspace.root, path)
    candidates = [workspace.input_dir]
    if workspace.input_source is not None:
        candidates.append(workspace.input_source)
    candidates.append(workspace.artifacts_dir)          # ← 新增:保护 artifacts/(ledger、registry)
    for candidate in candidates:
        if _is_relative_to(resolved, candidate):
            return True
    return False
```
`_mentions_protected_path` 的 token 列表加:
```python
        "artifacts/execution_ledger.jsonl",
        "artifacts\\execution_ledger.jsonl",
        "artifacts/evidence_artifacts.jsonl",
```
> 说明:MCP 工具(注册、run_trusted_*)不受此守卫(它们不是 Write/Edit/Bash 工具),照常写 artifacts/。**只有 CodeAct 的自由文件操作被挡。** LLM 的正常分析产物写 `outputs/`,不受影响。
> 加固(belt-and-suspenders,可选):运行 CodeAct 子进程时对 `artifacts/` 设 OS 只读;或把 ledger 放 workspace 外的 run-root。

**测试影响:** 新增回归测试:CodeAct `Write`/`Bash` 到 `artifacts/execution_ledger.jsonl` 被拒;`import ...record_trusted_run` 经 Bash 被 `_mentions_protected_path` 挡。

---

## Step 6 — classic recipe 默认切 pseudobulk(M4)

文件:`pertura_workflow/recipes/classic.py`
- 把 `run_basic_de_for_registered_contrast` 换成 `run_pseudobulk_de_for_registered_contrast`(需要 `replicate_column`,从 config 的 `basic_de.replicate_column` / scope 取)。
- `register_measured_de(...)` 现在**传 execution_hash**(来自 pseudobulk result):
```python
    measured = registry.register_measured_de(
        ...,
        method=de.get("method") or basic_de_result["method"],   # "pseudobulk_de",已在白名单
        execution_hash=basic_de_result.get("execution_hash"),
        metadata={"execution_ledger_relative_path": basic_de_result.get("execution_ledger_relative_path")},
    )
```
- calibration 分支(`_run_or_load_control_calibrations`)现在 runner 已写 ledger(Step 2),execution_hash 会被 registrar 带上,strict/paper 下可信。
- `basic_de.py` 顶部加注释标 legacy(仅 smoke-demo,不接 ledger),从默认路径移除。

**测试影响:** `test_classic_recipe` / `test_p21_classic_workflow` 的 fixture 需要 replicate 列;断言从 smoke 升到可过 strict。

---

## 完成后关闭了什么

| 修复 | 由哪步关闭 |
|---|---|
| H5 policy 由 LLM 选 | Step 1(run 级 policy + clamp) |
| H4 无真实可信路径 | Step 1+3+6(finalizer 用真 policy + agent 可跑 trusted + recipe 合规) |
| H3 trusted 不可达 + 回执骗人 | Step 3(run_trusted_* 工具)+ Step 4(receipt + 真强度回执) |
| H2 calibration 不写 ledger | Step 2 |
| H1 ledger 可写 | Step 5 |
| M4 recipe 用 smoke DE | Step 6 |

**验收总标准:一次 `--profile paper` 的真实运行:agent 调 run_trusted_pseudobulk_de → 注册(receipt)→ evaluate_claims 得 measured;而 CodeAct 手写一条 ledger + 注册 → 只到 observation。两条都有回归测试。** 到这一步,strict/paper 第一次在真实运行 + 对抗下成立。

## 不动的东西
gate 判定语义、resolver/warrant 逻辑、catalog/ledger/policy 抽象——全保留。本批只接线,不改判定。
