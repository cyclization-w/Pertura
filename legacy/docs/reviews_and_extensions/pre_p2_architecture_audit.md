# Pre-P2 Architecture Audit

> 目的:在做 P2(外部生物学知识 / wrapper)之前,把**架构层面**仍会造成"人难懂 / Claude 迷惑 / 后续臃肿"的问题一次性收束。
> 范围:全量走读 `pertura_gate` / `pertura_runtime` / `pertura_workflow` / `pertura_bench`。
> 与 `pre_p2_consolidation.md`(catalog/registry 收束)互补——那份收"证据登记轴",这份收"其余所有轴"。

---

## 优先级总览

| 组 | 问题 | 类别 | 严重度 |
|---|---|---|---|
| **C** | agent 面向层整体落后于 C4 信任模型 | 能力闭环 | 🔴 最高(Claude 在 strict/paper 下必然失败+困惑) |
| **B1** | 两个 DE runner,默认 recipe 用的是 smoke 的那个 | 正确性 | 🔴 高 |
| **A** | 两条脊椎(stages vs evidence catalog)未对齐 | 概念混淆 | 🟠 中高 |
| **D** | resolver/identity 内部仍有手写 per-predicate 扇出 + 三套 scope 比较 | 结构臃肿 | 🟠 中 |
| **E** | benchmark 四套并行 harness | 混淆 | 🟠 中 |
| **F** | helper / canonicalize 多处重复 | 清洁度 | 🟡 低 |

---

## C 组 — Agent 面向层与 C4 信任模型脱节(最该先修)

> 一句话:**工具签名、prompt、回执三样都还停留在 C4 之前(LLM 自由 author 证据+hash),而信任已改成"必须走 out-of-loop 可信执行"。结果 Claude 在 strict/paper 下被系统性误导。**

**C1. 可信执行通道对 agent 完全不可达。**
`grep pseudobulk|trusted_run|execution_ledger src/pertura_runtime` → 零命中。`trusted_run.py` / `pseudobulk_de.py` 活在 `pertura_workflow`,**没有任何 MCP 工具或 CodeAct 入口让 Claude 调用**。→ strict/paper 下产生合法 ledger 的唯一途径 agent 够不到,measured **事实上无法达成**。

**C2. 注册工具向 Claude 索要已失效的字段。**
`register_measured_de_artifact`(evidence_tools.py:202)schema 暴露 `execution_hash` / `method` / `code_sha256` 让 Claude 填;C4 后手填 hash 惰性。工具形状与信任规则**互相矛盾**。

**C3. 注册回执 + finalizer 都用 DEFAULT smoke policy 报告强度,误导"成功"。**
`_registration_result`(evidence_tools.py:909)和 `finalizer.py:100` 都调 `resolve_artifact_strength(artifact)`(= 无 policy = smoke intrinsic),返回 `artifact_intrinsic_ceiling`。→ 即便当前是 strict/paper、实际会降到 observation,回执仍显示 `measured_association`。**反馈闭环断裂且信号错误**,Claude 直到 evaluate_claims 才发现被降级,且没有工具去补。

**C4. 19 个近义注册工具需 Claude 自行消歧**(measured_de / module / global / composition / efficiency);选错静默改 predicate/天花板。

**C5. `eligibility` 是不透明大 dict**;字段级要求只隐含在 resolver,无 schema/模板。

### C 组修复方向
1. 暴露 `run_pseudobulk_de`(及后续每个 trusted wrapper)为 MCP 工具 / CodeAct 入口,内部走 `trusted_run`,返回 ledger-backed receipt。
2. 注册工具的 LLM 面**去掉** `execution_hash`/`method` 手填,改收 runner 的 **run_receipt**(结构性杜绝 author-trust)。
3. `_registration_result` / finalizer 用**当前 policy** 跑 claim-conditioned resolver,返回真实天花板 + 缺哪几项 + 下一步调哪个工具。
4. 收敛注册工具(接 `pre_p2_consolidation.md` 的 C3):单一 `register_evidence(type_id, receipt)` 或给决策表。

---

## B 组 — 重复 runner / 入库路径

**B1. 🔴 两个 DE runner,默认 recipe 用的是 smoke 的那个。**
`recipes/classic.py:20` 和 `p21_classic_workflow` 仍用 `run_basic_de_for_registered_contrast`(`basic_mean_difference_v1`,cell-level、伪重复、**不在白名单**)。承重钥匙 `pseudobulk_de` **没接进默认可演示路径**。→ 跑 classic recipe 产出的 DE 只到 smoke。**demo 的默认链路是不合规的那条。**
- 修:classic recipe 默认 DE 切 `pseudobulk_de`;`basic_de` 标 legacy/仅 smoke-demo。

**B2. 两条入库路径并存。**
`harvest.py`(扫候选 auto-register,classic + cli 在用)vs `trusted_run.py`(ledger 受控执行)。边界未写清,classic recipe 走 harvest。
- 修:文档化边界(harvest=候选发现/诊断;trusted_run=可信执行);classic 的 measured 证据走 trusted_run。

---

## A 组 — 两条脊椎未对齐(概念混淆源)

**A1. procedure 轴 vs trust 轴,各自枚举"分析步骤/证据类型",无交叉引用。**

| 轴 | 在哪 | 枚举 |
|---|---|---|
| procedure(SOP) | `docs/stages/`(index.yaml + cards/*.md + contracts/*.yaml)+ `runtime/stages/catalog.py` | stage:preflight/design/qc/measured_de… |
| trust(证据) | `evidence/catalog.py` `EVIDENCE_CATALOG` | evidence type:measured_de/composition_effect… |

`measured_de` 同时定义在两处,手工同步,互不引用。
- 修:stage card front-matter 写 `evidence_type_id: measured_de` 指向 catalog;catalog 成唯一真源,stage 是它"面向 LLM 的程序视图"。

**A2. "catalog" 一词两义。** `evidence/catalog.py`(证据类型)vs `runtime/stages/catalog.py`(stage 卡片)。
- 修:`runtime/stages/catalog.py` → `stage_index.py` / `stage_registry.py`。

**A3. 三套"下一步做什么"。** stage `next_stage_recommendations`(yaml)、`recommend.py:recommend_next_evidence`(readiness)、gate block reasons。新人不知哪个权威。
- 修:明确职责分层(stage=程序建议,recommend=缺口驱动,gate=硬阻塞),文档写清;长期考虑合并 recommend 进 gate 反馈。

---

## D 组 — Resolver / Identity 内部仍有手写扇出 + 三套 scope

**D1. 三套 scope 比较系统层叠。**
- `identity/scope.py:compare_scope`
- `identity/canonical_scope.py:compare_canonical_scope`
- `identity/design_manifest.py:compare_manifest_scope` / `manifest_scope_is_strong`

`scope.py` 同时 import 另外两者做 fallback 链;而 `resolver.py` 又**直接** import `compare_scope` 和 `compare_manifest_scope`,绕过分层。且 `_is_control_token`/`_scope_tokens`/`_first` 在 scope.py 与 canonical_scope.py **各定义一遍**。三个比较器 + 调用方各选各的 = 难懂。
- 修:定一个**单一 scope 比较入口**(内部按 manifest-UID → canonical → loose 的明确优先级),其余作内部实现;去重 token helper。

**D2. 🟠 warrant.py 仍有 9 个手写 per-predicate `*_intrinsic` 函数。**
`differential_expression_intrinsic` / `target_engagement_intrinsic` / `module_score_intrinsic` / `global_shift_intrinsic` / `composition_shift_intrinsic` / `virtual_prediction_intrinsic` / `prediction_concordance_intrinsic` / `virtual_cell_state_transition_intrinsic` / `replication_intrinsic`。
**claim 侧已 spec 化(`_MEASURED_PREDICATE_SPECS`),但 intrinsic 侧没有。** 加一个新 measured 类型仍要新写一个 `*_intrinsic`。这是与 registry 同类的"半边查表半边手写"不对称。
- 修:把 intrinsic 侧也收进 catalog / spec(每类型声明 intrinsic 规则),消掉 9 个手写函数;和 catalog 的 `intrinsic_ceiling` 字段打通。

**D3. 通用 helper 多处重复。**
`_first`(3 文件)、`_optional_int`(4)、`_scope_tokens`(2)、`_is_control_token`(2)、`_canonicalize`(policy/ledger/models 各一);warrant.py 的 public helper(first/optional_int/norm/dedupe…)又在 resolver.py 以 `_` 私有版**再写一遍**。
- 修:建一个 `pertura_gate/_util.py`(或 core/util),收拢这些纯函数;warrant/resolver 共用。

---

## E 组 — Benchmark 四套并行 harness

`pertura_bench` 有四个各自独立的 harness,每个都有自己的 `*CaseResult` / `run_*_case` / `run_*_suite` / `write_*_summary` / `_render_summary_markdown`:
- `p07_harness.py`(gated-vs-baseline utility)
- `p21_classic_workflow.py`(classic recipe cases)
- `stage_benchmark.py`(stage cases)
- `surface_eval.py`

**没有单一"the benchmark",而是四套重叠脚手架。** 对 AAAI 是隐患——审稿人问"你的 benchmark 是什么",答案应是**一个**,不是四个。
- 修:抽公共 harness 骨架(case/suite/summary),四个变成同一 harness 的不同 case-set;确立**一个**主 benchmark(OCR/FBR + baselines),其余降为 diagnostic。

---

## F 组 — 低优先清洁度

- 多个 `canonical_hash`/`_canonicalize` 实现(policy.py / execution_ledger.py / workflow/models.py)——统一到一处。
- `basic_de.py` / `control_calibration.py`(runners)在 pseudobulk / control_calibration runner 就位后的 legacy 定位需明确(保留作 smoke-demo 还是退役)。

---

## 建议收束批次(P2 前)

```
批次 1(能力闭环,必须先做 —— 否则 wrapper 也没用):
  C1 暴露 trusted-run 工具给 agent
  C2 注册工具改收 receipt(去 author-trust)
  C3 回执/finalizer 按当前 policy 报真强度 + 下一步
  B1 classic recipe 默认 DE 切 pseudobulk
     → 完成后:Claude 在 strict/paper 下有合法路径拿 measured,信号不再骗它

批次 2(概念对齐,低风险等价重构):
  A1 stage card 引用 catalog type_id(两脊椎单向绑定)
  A2 stages/catalog 改名
  A3 三套 next-step 职责文档化
  C4 注册工具收敛(接 consolidation C3)
  C5 eligibility 给 schema/模板

批次 3(内部去重,等价重构,保持全绿):
  D1 单一 scope 比较入口 + token helper 去重
  D2 intrinsic 侧收进 catalog(消 9 个 *_intrinsic)
  D3 helper 收拢到 _util
  F  canonicalize 统一 / legacy runner 定位

批次 4(benchmark,和投稿准备并行):
  E  统一 harness,确立单一主 benchmark
```

## 验收原则
- 批次 1 有正确性/能力语义变化,**会连带改测试**(和 C4 修复同类)。
- 批次 2/3 目标是**等价重构**:验收 = 现有测试**零断言改动仍全绿**。
- 不变量全程不动:strength = f(结构化证据 + UID scope + policy_hash),信任只从受控执行赚取。
- **知识层(P2)在这些收束之后再加**——否则会往一个仍在错位的 agent 面上堆生物学知识。
