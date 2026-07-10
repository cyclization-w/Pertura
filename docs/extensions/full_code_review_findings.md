# Full Code Review — Findings (Pre-P2)

> 全量代码审查结果。按严重度排列。每条给出 file:line + 触发场景 + 建议。
> 覆盖度见文末。**trust-critical 路径已逐行审查**;runtime 外围/bench/preflight 为结构级。

---

## 🔴 HIGH — 信任漏洞(直接威胁核心 claim)

### H1. 执行 ledger 在运行时可被 CodeAct 写入(C4 信任锚点未落实)
**位置:** `pertura_runtime/claude/permissions.py:12-37, 91-99`
**问题:** 只读守卫只保护 `workspace.input_dir`,**不保护 `artifacts/execution_ledger.jsonl`**。该文件在 `workspace.root` 下,不在 input_dir 下,因此 LLM 的 `Write` / `Bash` 可以直接往规范 ledger 追加一行。
**触发:** LLM 产出一个 DE 文件,算出其 sha256,然后 `Write` 到 `artifacts/execution_ledger.jsonl` 一条 `{writer_id:"pertura_trusted_run", method:"sceptre", execution_hash:H, output_hashes:{x:该sha}}`,再注册 measured artifact(method=sceptre, execution_hash=H)。→ `is_trusted_execution` 全部通过 → strict/paper 下伪造出 measured。
**根因:** C4 把信任锚点定为"只有 trusted_run 通道能写 ledger",但**运行时没有强制这条隔离**。`writer_id` 只是源码里的明文常量,可复制。
**建议:** 把 ledger 路径(以及 `artifacts/` 下所有 gate 内部文件)加入 permissions 的受保护集合,CodeAct 只读;或把 ledger 移出 CodeAct 可写树。这是 H 级里最该先堵的。

### H2. Calibration runner 不写 ledger → 真实 calibration 永远无法可信
**位置:** `pertura_workflow/runners/control_calibration.py:98,187,340`(用本地 `_execution_hash`,从不调 `record_trusted_run`)vs `resolver.py:724-727`(`is_trusted_control_calibration` 在 strict/paper 要求 ledger 命中)
**问题:** pseudobulk DE runner 正确接了 `record_trusted_run`,但两个 calibration runner 没接——它们只把 execution_hash 写进输出 JSON,不进规范 ledger。而 paper 的 `require_trusted_calibration_for_required_checks=True` 要求 calibration 可信,可信又要求 ledger 命中。
**触发:** 真实跑 `run_ntc_vs_ntc_calibration` / `run_label_permutation_null` → 输出 execution_hash 不在 ledger → `is_trusted_control_calibration` 返回 False → paper 下需要 calibration 的 measured 声明**永远无法满足**。
**为什么测试没抓到:** `test_phase1b` 用 `record_trusted_run` **手动 seed** ledger,绕过了真实 runner。测试全绿但真实路径断裂。
**建议:** calibration runner 改用 `canonical_execution_hash` + `record_trusted_run`,与 pseudobulk 对齐。附带:calibration 用的哈希算法(本地 `_execution_hash`)也与 `canonical_execution_hash` 不一致,需统一。

### H3. 可信执行通道对 agent 不可达 + 回执误导(能力闭环断裂)
**位置:** `evidence_tools.py:202`(注册工具向 LLM 索要惰性 `execution_hash`)、`evidence_tools.py:909` + `finalizer.py:100`(用 DEFAULT smoke policy 报 `artifact_intrinsic_ceiling`);`pertura_runtime` 内无任何 runner/trusted_run 入口
**问题:** ① strict/paper 下产生合法 ledger 的唯一途径(trusted_run/pseudobulk/calibration runner)对 Claude 既不暴露也不告知;② 注册回执与 turn-final 用 smoke policy 计算天花板,即便当前 policy 会降级也报 `measured_association`。
**触发:** Claude 在 strict/paper 下按工具签名填 execution_hash → 惰性 → 静默降级;回执却显示成功;直到 evaluate_claims 才降级,且它没有工具补 trusted 执行。**Claude 被系统性误导。**
**建议:** ①暴露 `run_trusted_*` 工具(内部走 trusted_run,返回 receipt);②注册工具改收 receipt,去掉手填 execution_hash;③回执/finalizer 用当前 policy 跑 claim-conditioned resolver,返回真实天花板 + 缺项 + 下一步。

---

## 🟠 MED — 正确性缺陷

### M1. CRISPR-KO 方向逻辑漏洞(fail-open)
**位置:** `warrant.py:296-305`
**问题:** `is_ko=True` 时,"expected≠observed 冲突"检查是挂在 `if is_ko` 上的 `elif`(301),KO 时永不执行。
**触发:** KO artifact 报 `observed_direction="up"`(敲除却上调,自相矛盾)→ 无 reason → 放行成 `measured_target_engagement`。应判 observational。

### M2. sgNTC 类控制标签被误判为靶基因(污染 UID)
**位置:** `design_manifest.py:521-529`(`_is_control_label` / `_looks_like_control_label` 只匹配前导 `negctrl` 或精确 token)
**触发:** guide 标签 `sgNTC` / `sgNegCtrl` / `gNTC` → 不匹配 control → 被 `_gene_token` 造成 `target:SGNTC`,污染 design manifest 的 perturbation/contrast。preflight 做过 sgNTC 硬化,**manifest 解析器没同步**。

### M3. Eligibility 聚合 scope-bleed
**位置:** `resolver.py:517-524` + `_eligibility_scope_can_support:580`
**问题:** measured claim 的 eligibility 扫全 registry 合并,而 `_eligibility_scope_can_support` 接受 `unknown`/`weaker`/无 manifest scope 的 artifact。
**触发:** measured claim 的 replicate/cell_qc 门可被一个 scope 未知/异上下文的 QC artifact 满足。(control_calibration 已单独硬化,其余 eligibility 字段未硬化。)
**注:** 可能部分是"run-level QC 共享"的有意设计——**需你裁决**哪些 eligibility 是 run 级共享、哪些必须 UID 强绑,再据此收紧。

### M4. 默认 recipe 用 smoke DE runner
**位置:** `recipes/classic.py:20`(`run_basic_de_for_registered_contrast`,`basic_mean_difference_v1`,cell-level、伪重复、不在白名单)
**触发:** 跑 classic recipe 产出的 DE 只到 smoke;承重钥匙 `pseudobulk_de` 未接入默认可演示路径。

---

## 🟡 LOW — 清洁度 / 健壮性

| # | 位置 | 问题 |
|---|---|---|
| L1 | `scope.py:70` | `compatible_or_exact` 死代码(零调用),且对 unknown/weaker 返 True(潜在宽松缝) |
| L2 | `warrant.py:255-256` | DE intrinsic 的缺失原因恒报 "contrast.baseline",即使缺的是 contrast_left(误导诊断) |
| L3 | `design_manifest.py:388-391` | claim 带 perturbation_uid 而 artifact 只带 contrast_uid 时误判 mismatch(false-block/FBR;当前因 scope_for_raw_label 同填三 UID 而不触发,潜在脆弱) |
| L4 | `canonical_scope.py:205-212` | 所有对照池坍缩成 `negctrl_pool`(仅 loose 路径;measured 走 manifest 不受影响) |
| L5 | 多处 | `_first`/`_optional_int`/`_canonicalize`/`_scope_tokens`/`_is_control_token` 在多文件重复定义 |
| L6 | `preflight.py:122` | `_classify_file` 用 `"de" in name` 判定 measured_de_table,子串过宽:`leiden_clusters.csv`/`order.csv`/`provider.csv` 等含 "de" 的文件被误分类(diagnostic-only,但污染候选/readiness) |
| L7 | `registry.py:1451` | `verify_source_hashes`/`source_hash_status`(检测文件被篡改)已实现但**未接入任何 gate 判定**——是"有能力未使用"的完整性检查 |

---

## 架构问题(详见 `pre_p2_architecture_audit.md`,此处仅索引)

- 两条脊椎(`docs/stages` vs `evidence/catalog`)未对齐;"catalog" 一词两义。
- `warrant.py` 仍有 9 个手写 per-predicate `*_intrinsic`(claim 侧已 spec 化,intrinsic 侧没有——不对称)。
- 三套 scope 比较系统(`compare_scope`/`compare_canonical_scope`/`compare_manifest_scope`)层叠 + helper 重复。
- 两条入库路径(`harvest` vs `trusted_run`);三套"下一步"(stage / recommend / gate block)。
- `pertura_bench` 四套并行 harness,无单一主 benchmark。

---

## 建议修复顺序(P2 前)

```
第一批(信任,必须先修 —— 否则 wrapper 的 measured 信任都可伪造/不可达):
  H1 ledger 写入隔离(permissions 保护 artifacts/)
  H2 calibration runner 接 record_trusted_run(+ 统一哈希)
  H3 暴露 trusted-run 工具 + 注册收 receipt + 回执按当前 policy 报真强度
  M4 classic recipe 默认切 pseudobulk

第二批(正确性):
  M1 KO 方向逻辑
  M2 sgNTC 控制标签识别(manifest 解析器)
  M3 eligibility scope-bleed(先裁决 run-level 共享边界)

第三批(清洁度):
  L1–L5 + 架构 audit 各项(等价重构,保持全绿)
```

---

## 🔴 HIGH — runtime/workflow/bench 补充(第二轮逐行)

### H4. 没有任何真实路径能产出可信 measured/calibration(贯穿性结论)
**位置:** `recipes/classic.py:261-273`(register_measured_de **完全不传 execution_hash**,method=`"registered_table"` 或 `basic_mean_difference_v1`,均不在白名单);`classic.py:206-223`(calibration 用不进 ledger 的 hash)
**问题:** 把 H2/H3/M4 连起来看:**classic recipe 端到端是 smoke-only**(measured 无 execution_hash + 非白名单 method → strict/paper 必降级);agent 路径不可达(H3);calibration runner 不写 ledger(H2)。
**结论:** **目前没有任何真实运行路径(recipe 或 agent)能产出可信 measured/calibration——只有孤立单测(pseudobulk + 手动 seed ledger)能。** 信任机制端到端**未被任何真实入口行使**。这是最需要正视的系统性状态。

### H1(强化). trusted_run 写入路径本身可从 CodeAct 直接调用
**位置:** `python_env.py`(CodeAct 用装了 pertura 的科学 python 环境)+ `permissions.py`(不拦)
**补充:** LLM 不必手搓 ledger JSON——可在 CodeAct 里直接 `import pertura_workflow.trusted_run; record_trusted_run(...)` 写一条合规 ledger。写入路径对不可信环境**完全开放**。

### H5. policy profile 由 LLM 自选、默认 smoke、无 run 级强制(gate 严格度落在被 gate 者手里)
**位置:** `evidence_tools.py:798,827`(`policy_for_profile(args.get("policy_profile") or "smoke")`);`agent.py`(运行时全程无任何 policy 配置);`finalizer.py`(渲染不传 policy → smoke)
**问题:** 整个 agent 运行时**没有 policy 管道**。唯一决定 policy 的是 MCP `evaluate_claims`/`render_evidence_report` 的 `policy_profile` 参数,**由 LLM 传入,默认 smoke**。没有任何 run 级配置能强制 strict/paper。
**后果:** ① 真实 agent run **默认就是 smoke**,H1–H4 的所有信任硬化(trusted method / ledger / calibration)在默认下**根本不生效**;② **被 gate 的模型自己选 gate 的严格度**——它想过就传 smoke。这动摇了"gate 在模型循环之外"的核心论断:严格度本身在循环之内、由模型决定。
**建议:** policy 必须是 **run 级不可变配置**(由运行发起方设定、写进 manifest、参与 policy_hash),而不是 LLM 可选参数。finalizer/render 必须用这个 run 级 policy。这是让"strict/paper 真正生效"的前置,比单修 H1–H4 更根本。

## 🟠 MED — benchmark 有效性

### M6. OCR 过度声明指标是脆弱的关键词匹配
**位置:** `pertura_bench/surface_eval.py`
**问题:** overclaim 检测靠固定词表(mechanism/validation/prediction…)+ 朴素否定(整句含 "not "/"without" 就跳过)。→ 用同义词过度声明会漏检(false negative);gated surface 的 caveat 因含否定词被跳过而"看起来干净"。**不是经过验证的过度声明度量。**
**影响(AAAI):** 与 M5 叠加,当前"gate 降低 overclaim"结论 = 一个词表匹配器给人造稻草人打分。**撑不起实证主张。**需要人工标注 + 语义判定的评测。

### M5. p07 "baseline" 是稻草人,不是真实对照
**位置:** `pertura_bench/p07_harness.py:150-173`(`render_deterministic_baseline`)
**问题:** benchmark 的 baseline 是**按 task_id 子串硬编码的、故意最大化过度声明的固定 prose**("self"/"tag"→validates mechanism,"dual"/"combo"→错误归因……),**不是 no-gate LLM,也不是 prompt-only guardrail**。
**影响(AAAI):** "gate 打败 baseline" 是在和一个人造的最坏 prose 比。审稿人一眼看穿这不是公平对照。**当前 benchmark 无法支撑"gate vs 现实替代方案"的论点**——这正是之前 Tier-0 缺 baseline 的具体体现。

## 覆盖度声明(诚实)

| 模块 | 覆盖 |
|---|---|
| `pertura_gate/*`(schema/catalog/policy/execution_ledger/resolver/warrant/scope/canonical_scope/design_manifest/binding) | ✅ 逐行 |
| `registry.py`(trust 相关段 + register_evidence + append/hash/manifest-scope) | ✅ 关键段逐行 |
| `permissions.py` / `pseudobulk_de.py` / `control_calibration.py` / `trusted_run.py` | ✅ 逐行 |
| `evidence_tools.py`(注册工具 + 回执) | 🟡 关键段逐行 |
| `claims.py`/`target_qc.py`/`python_env.py`/`mcp_server.py`/`recipes/classic.py`/`p07_harness.py` | ✅ 逐行 |
| `finalizer.py`(全)/`preflight.py`(全)/`renderer.py`(全)/`recommend.py`(全)/`agent.py`(全)/`surface_eval.py`(全)/`_register_family_artifact` | ✅ 逐行(第三轮) |
| `registry.py` 剩余同型 registrar(module/global/composition/enrichment/prior/cell_qc/experiment_design/guide_assignment/manifest)、`evidence_tools.py` 剩余 thin 工具、`classic.py` manifest-build 段 | 🟡 抽样 + 同型确认(与已审 registrar 同一安全模式) |
| `models.py`/`workspace.py`/`manifest.py`/`stream.py`/`options.py`/`cli.py`/`hooks.py`/`prompt.py`/`stages/*`/`bench`(p21/stage_benchmark) | 🟡 结构级 + 风险模式全项目扫描(无 eval/exec/os.system/shell/pickle/不安全反序列化) |

**结论:trust-critical + 逻辑承重代码已 100% 逐行;剩余为 dataclass/IO/thin-wrapper/同型 registrar,已扫描确认无风险模式、遵循同一安全模式。全项目无经典安全反模式。**
