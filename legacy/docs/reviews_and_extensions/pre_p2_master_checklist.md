# Pre-P2 Master Checklist — 单一真源

> 合并 `pre_p2_consolidation.md` + `pre_p2_architecture_audit.md` + `full_code_review_findings.md` 三份的全部结论。
> 本文是唯一真源。含:根因诊断 → 架构裁决 → 完整清单(按根因分组)→ 修复批次 → 不动的东西。

---

## 1. 根因诊断:一句话

> **判定层(gate)做到了极致且代码无安全反模式;但"信任如何被真实赚取和强制"从未在真实运行中被落实——信任的各个部件是分开造、分开单测的,运行时(agent → MCP → finalizer)从没被改造成强制它们的脊椎。**

### 为什么会这样(演化史)
1. **判定层先建、建得好**(Pertura-v1 血统):resolver / warrant / scope / design_manifest / catalog —— 正确、干净。
2. **信任层后加、逐块加**(Phase 1a/1b/C4/C6):strict/paper policy、ledger、trusted runner —— 每块**单独**做,每块配一个**手工把管线拼起来**的单测(seed ledger、显式传 strict policy)。
3. **运行时没跟上**:`agent.py` / MCP 工具 / `finalizer.py` 仍是"信任前"世界——默认 smoke、LLM 自报证据、LLM 选 policy、CodeAct 可写 ledger。

### 最关键的认知点:测试给了假信心
**231 全绿从未行使过一条真实的可信路径。** 每个信任测试都**手工扮演了那个缺失的脊椎**(自己 seed ledger、自己传 strict policy、自己拼 receipt)。所以"绿"证明的是"部件在被正确拼装时能工作",而**生产里没有任何组件在做这个拼装**。这就是为什么 H1–H5 各自看是分散 bug,合起来是**同一个缺口**。

---

## 2. 架构裁决:加一个组件,不是重构,也不是打补丁

**不需要重构 gate。** 核心抽象(EvidenceArtifact、三 checkpoint、policy_hash、ledger、catalog、resolver)是**对的**,判定逻辑正确,无安全反模式。

**但也不能只零散打补丁。** 因为 H1–H5 是同一个缺失架构组件的六个症状——逐个补会打地鼠(补了 H1,H5 让它失效;修了 H2,H3 让它够不到)。

**正解:补一个缺失的架构组件——run 级"信任边界 / 执行控制器"(RunContext / TrustBoundary),把运行时路由过它。** 它承担现在**散落/缺失、错误地落在模型身上**的四项职责:

| 职责 | 现状(错) | 应由信任边界持有 | 修掉 |
|---|---|---|---|
| **不可变 run policy** | LLM 传 `policy_profile`,默认 smoke | 发起方设定、写进 manifest、进 policy_hash | H5/H4 |
| **唯一可信执行通道** | runner 在 workflow 里,agent 够不到 | 边界是 runner 的唯一入口,跑完写 ledger | H2/H3 |
| **ledger 写入权** | CodeAct 可 Write/import 直接写 | 只有边界能写;CodeAct 沙箱只读 | H1 |
| **注册凭 receipt** | 注册工具收 LLM 手填 execution_hash | 注册只接受边界发的 run_receipt | H3/M4 |

**一句话:gate 是"法官",判得很好;缺的是"法警/书记员"——那个 run 级权威,负责固定规则、独占执行、看管台账。补上它,H1–H5 作为它的自然结果一起消失。**

---

## 3. 完整清单(按根因分组)

### 组 A —— 缺失信任边界的症状(一个根因,一起修)

| ID | 严重度 | 问题 | 位置 |
|---|---|---|---|
| **H5** | 🔴 | policy 由 LLM 选、默认 smoke、无 run 级强制 | evidence_tools.py:798,827; agent.py 无 policy |
| **H4** | 🔴 | 无任何真实路径(recipe/agent)产出可信 measured/calibration | classic.py:261; agent 全链 |
| **H3** | 🔴 | trusted runner 对 agent 不可达 + 注册索要惰性 hash + 回执 smoke 假成功 | evidence_tools.py:202,909; finalizer.py:100; mcp_server 无 run 工具 |
| **H2** | 🔴 | calibration runner 不写 ledger → 真实 calibration 永不可信 | control_calibration.py:98,187,340; resolver.py:724 |
| **H1** | 🔴 | ledger 可被 CodeAct 写(Write/import record_trusted_run) | permissions.py:12-37; python_env 装了 pertura |
| **M4** | 🟠 | classic recipe 默认 smoke DE、measured 不传 execution_hash | recipes/classic.py:20,261 |

### 组 B —— 独立正确性 bug(与脊椎无关,单独修)

| ID | 严重度 | 问题 | 位置 |
|---|---|---|---|
| **M1** | 🟠 | CRISPR-KO 报 observed="up" fail-open 过成 target_engagement | warrant.py:296-305 |
| **M2** | 🟠 | sgNTC/sgNegCtrl 控制标签被误当靶基因,污染 manifest UID | design_manifest.py:521-529 |
| **M3** | 🟠 | eligibility 聚合 scope-bleed(需先裁决 run-level 共享边界) | resolver.py:517-524,580 |

### 组 C —— benchmark 有效性(AAAI 单独轨)

| ID | 严重度 | 问题 | 位置 |
|---|---|---|---|
| **M5** | 🟠 | p07 baseline 是按 task_id 硬编码的稻草人,非真实 no-gate/prompt-only | p07_harness.py:150-173 |
| **M6** | 🟠 | OCR 指标是脆弱关键词+朴素否定匹配,非验证过的度量 | surface_eval.py |

### 组 D —— 清洁度 / 架构(非阻塞)

| ID | 问题 | 位置 |
|---|---|---|
| L1 | 死代码 `compatible_or_exact`(对 unknown/weaker 返 True) | scope.py:70 |
| L2 | DE intrinsic 缺失原因恒报 "contrast.baseline" | warrant.py:255 |
| L3 | 部分 manifest scope 误判 mismatch(false-block) | design_manifest.py:388-391 |
| L4 | 所有对照池坍缩成 negctrl_pool(loose 路径) | canonical_scope.py:205 |
| L5 | helper(_first/_optional_int/_canonicalize…)多处重复 | 多处 |
| L6 | `"de" in name` 误分类文件(leiden→DE table) | preflight.py:122 |
| L7 | verify_source_hashes 完整性检查未接入 gate | registry.py:1451 |
| ARCH-1 | 两条脊椎(docs/stages vs evidence/catalog)未对齐 | — |
| ARCH-2 | warrant 仍有 9 个手写 per-predicate intrinsic(claim 侧已 spec 化) | warrant.py |
| ARCH-3 | 三套 scope 比较系统 + helper 重复 | identity/* |
| ARCH-4 | bench 四套并行 harness,无单一主 benchmark | pertura_bench/* |
| ARCH-5 | "catalog" 一词两义;harvest vs trusted_run 两入库;三套 next-step | 多处 |

### 已关闭 / 计划内推后(状态记录)
- ✅ 已关闭:C1 taxonomy、C2 registrar 单入口、C5 harvest 命名、C6 pseudobulk runner、原始 measured 伪造缝 & phase1b laundering(自报路径)
- ⏸ 计划内推后:C3(MCP 从 catalog 生成)、C7(拼装去重)、C8(enum 别名)

---

## 4. 修复批次

```
批次 0 —— 补信任边界(架构组件,组 A 的根治):
  1. RunContext/TrustBoundary:持有不可变 run policy(发起方设定,进 policy_hash)
  2. finalizer/render/evaluate 全部用 run policy,不再接受 LLM policy_profile 默认 smoke
  3. 边界独占 trusted 执行:pseudobulk/calibration/未来 wrapper 只经边界跑 + 写 ledger
  4. permissions 保护 artifacts/(尤其 ledger)→ CodeAct 只读;record_trusted_run 不对 CodeAct 暴露
  5. 注册工具改收 run_receipt,去掉 LLM 手填 execution_hash
  6. calibration runner 接 record_trusted_run(统一 canonical hash)
  7. classic recipe 默认 DE 切 pseudobulk
     → 完成后:strict/paper 第一次在真实运行中生效,H1–H5+M4 一起消失

批次 1 —— 独立正确性(组 B):
  M1 KO 方向 · M2 sgNTC 识别(manifest 解析器)· M3 eligibility scope-bleed(先裁决)

批次 2 —— benchmark(组 C,和投稿并行):
  M5 换真实 baseline(no-gate / prompt-only)· M6 换验证过的 overclaim 度量 + 人工标注

批次 3 —— 清洁度/架构(等价重构,保持全绿):
  L1–L7 · ARCH-1..5
```

**批次 0 有语义/能力变化,会连带改测试**(测试要从"手工拼装脊椎"改成"经真实边界跑")——这**恰恰是修复的证明**:测试改完仍绿,说明真实路径通了。

---

## 5. 不动的东西(避免过度重构)

- gate 判定语义:strength = f(结构化证据 + UID scope + policy_hash),永不 f(prose)。
- resolver / warrant 判定逻辑、三 checkpoint、catalog/ledger/policy 抽象——**保留,它们是对的**。
- resolver 三个跨证据特判(concordance/enrichment/replication)——真逻辑,保留。

**核心信息:补一个 run 级信任边界,把运行时路由过它;不重构 gate,不零散打补丁。批次 0 一落,你的核心 claim 第一次在真实运行 + 对抗下都成立。**
