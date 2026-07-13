# Pre-P2 Consolidation Inventory

> 目的:在 P2(外部工具 wrapper)开始**之前**,把会随 wrapper 数量**线性膨胀**的结构一次性收束。
> 原则:P2 每加一个工具,应该只改 **1 处数据**(一行 spec),而不是当前的 **8–10 处代码**。
> 约束:这份文档只做"结构收束",不改门的语义(strength = f(结构化证据 + UID scope + policy_hash) 这条不变)。

本文基于对以下模块的全量走读:
`core/schema.py` · `evidence/registry.py` · `resolver/resolver.py` · `core/policy.py` ·
`claude/tools/evidence_tools.py` · `workflow/harvest.py` · `workflow/models.py` · `runners/basic_de.py`

---

## 0. 一句话诊断

门核心(policy / resolver / safety layer)已经 **spec 化、自洽**。
但"**证据类型**"这条轴还停留在**手写时代**,而且是**三层并行手写**:

```
新增一种证据类型 = 改动以下所有点:
  1. ArtifactKind          (enum +1)
  2. EvidencePredicate     (enum +1)
  3. default_evidence_predicate()   (+1 分支)
  4. default_evidence_class()       (+1 分支)
  5. EvidenceRegistry.register_xxx  (+1 手写 60 行方法)
  6. register_xxx_artifact 家族 dispatcher (+1 分支)
  7. MCP @tool register_xxx_artifact (+1 工具)
  8. MeasuredPredicateSpec / resolver dispatch (measured 类 +1)
  9. policy.trusted_runner_methods  (trusted runner +1)
```

**≈ 8–10 处编辑 / 每种证据类型。** P2 要加 6–8 个 wrapper,就是 ~60 处手工编辑,且极易漏改导致 enum 与 mapping 不同步。**这就是"臃肿"的根。**

收束目标:把 1–9 折叠成**一张 `ArtifactSpec` 表 + 少量 custom hook**,让新增证据类型 = **一行数据**。

---

## 1. 优先级总览

| # | 收束项 | 类别 | 是否 P2 阻塞 |
|---|--------|------|:---:|
| C1 | 三并行 taxonomy + mapping 折叠成单一 registry | 结构去重 | **是**(不做则每 wrapper 8 处编辑) |
| C2 | registrar 两层结构(19 typed + 8 family)collapse 成 spec 驱动 | 结构去重 | **是** |
| C3 | MCP tool 层从 spec 生成,而非手写镜像 | 结构去重 | 强烈建议 |
| C4 | `is_trusted_execution` presence-only 缝 → ledger 核对 | 安全 | **是**(P2 全是 measured wrapper) |
| C5 | "harvest" 命名冲突(已占用) | 命名 | **是**(否则扩展方法文档与代码打架) |
| C6 | 缺 trusted pseudobulk DE runner(承重钥匙) | 缺件 | **是** |
| C7 | registrar 内 scope/quality 手工拼装重复 | 结构去重 | 建议(随 C2 一起) |
| C8 | EvidenceClass 别名双名(observation/observed_metadata 等) | 清洁度 | 否(低优先) |

下面逐项展开。

---

## C1 — 三并行 taxonomy 折叠

### 现状
证据类型的身份被拆到**三个必须手工保持同步**的地方:

- `ArtifactKind`(schema.py:10)—— **27 个**成员
- `EvidencePredicate`(schema.py:61)—— **16 个**成员
- `EvidenceClass`(schema.py:40)—— 6 个真实值
- `default_evidence_predicate()`(schema.py:429)—— 一大串 `if kind == ...` 手工映射
- `default_evidence_class()`(schema.py:462)—— 同上

这四者**没有单一真源**。加一种类型要在四处各写一遍,漏一处就出现"kind 有了但 predicate 落到 metadata_observation"的静默降级。

### 收束
建立**单一登记表** `ARTIFACT_TAXONOMY`,一行描述一种证据类型的全部身份:

```python
@dataclass(frozen=True)
class ArtifactTypeSpec:
    kind: ArtifactKind
    evidence_class: EvidenceClass
    predicate: EvidencePredicate
    default_roles: tuple[ArtifactRole, ...]

ARTIFACT_TAXONOMY: dict[ArtifactKind, ArtifactTypeSpec] = { ... }
```

`default_evidence_predicate()` / `default_evidence_class()` 退化成对这张表的查表,不再是 `if` 梯子。**enum 仍在(类型安全),但映射只有一份。**

---

## C2 — Registrar 两层结构 collapse(核心收束)

### 现状:27 个 registrar = 19 typed + 8 family

**第一层(19 typed)** —— `register_measured_de` / `register_module_effect` / `register_predicted_effect` …
每个 ~50–80 行,结构**完全同形**:

```
命名参数 → 塞进 scope / quality / predicate 三个 dict
→ 固定 kind / evidence_class / roles / relation
→ 过滤空值 → append
```

对比 `register_measured_de`(registry.py:382)与 `register_predicted_effect`(registry.py:463):除了字段名映射和固定的 kind/relation,骨架一字不差。

**第二层(8 family dispatcher)** —— `register_measured_effect_artifact`(registry.py:1235)等,是手写路由梯子:

```python
if subtype == "measured_de" and required.issubset(kwargs):
    return self.register_measured_de(...)
if subtype == "module_effect":
    return self.register_module_effect(...)
...
```

**每加一个 typed registrar,就要回来改对应的 dispatcher。O(2) 维护。**

### 收束:`ArtifactSpec` 表 + 单入口 `register(spec, **fields)`

```python
@dataclass(frozen=True)
class ArtifactSpec:
    kind: ArtifactKind
    relation: str                          # predicate.relation 固定值
    scope_fields: dict[str, str]           # 命名参数 -> scope key
    quality_fields: dict[str, str]         # 命名参数 -> quality key
    required_fields: frozenset[str] = frozenset()
    custom_hook: Callable | None = None    # 少数需要派生逻辑的类型挂这里

def register(self, spec_name: str, *, path, scope=None, **fields) -> EvidenceArtifact:
    spec = _ARTIFACT_SPECS[spec_name]
    # 校验 required_fields(admission checkpoint,唯一一处)
    # 按 scope_fields / quality_fields 拼装
    # 固定 kind / evidence_class / roles(查 C1 的 ARTIFACT_TAXONOMY)
    # custom_hook 处理派生(如 measured_de 的 raw_label / resolve_manifest_scope)
    ...
```

效果:
- 19 typed registrar → **19 行 spec 数据**
- 8 family dispatcher → **删除**(单一 `register()` 本身就是通用 surface,`harvest`/`recipes` 直接调它)
- P2 一个 wrapper → **一行 spec + 一个 parser**,不再新写方法、不再改 dispatcher

### 明确保留 custom hook(不要强行 spec 化)
以下带真实业务逻辑,作为 `custom_hook` 保留,**不是**纯拼装:
- `register_perturbation_design_manifest` —— 要 build manifest(design_manifest.py)
- `register_measured_de` —— 要派生 `raw_label`、调 `resolve_manifest_scope`(registry.py:406–425)
- resolver 侧三个特判(**不动**):`_resolve_prediction_measured_concordance` / `_resolve_curated_enrichment` / `_resolve_replication_summary`(它们做跨 artifact 绑定,是真逻辑,不是样板)

---

## C3 — MCP tool 层从 spec 生成

### 现状
`evidence_tools.py` 有 **19 个 `register_*_artifact` MCP @tool**(evidence_tools.py:25–786),
是 typed registrar 的**逐一手写镜像**。每个 tool 手写一遍 schema + 解包 + 调 registrar。

这是**第三层手写增殖**:同一种证据类型,身份写三遍(enum / registrar / tool)。

### 收束
既然 C2 已经把每种类型收成一行 `ArtifactSpec`,MCP tool 可从同一张 spec **批量生成**:
`scope_fields ∪ quality_fields ∪ required_fields` 直接推出 tool 的输入 schema。

- 保留少数需要特殊 prompt/描述的 tool 手写;
- 其余从 spec 循环 `create_sdk_mcp_server` 注册。

结果:P2 一个 wrapper 的 MCP 暴露 = **零额外代码**(spec 一填就自动有 tool)。

> 注:family dispatcher(8 个)并未暴露为 MCP tool(仅内部 `harvest`/`recipes` 用),
> 所以 C3 只针对 19 个 typed tool。

---

## C4 — 信任缝:`is_trusted_execution` presence-only(P2 阻塞)

### 现状(resolver.py:685)
```python
def is_trusted_execution(artifact, policy) -> bool:
    method = _norm(... or artifact.method or "")
    if method not in trusted_methods:
        return False
    if policy.trusted_runner_requires_execution_hash and not artifact.execution_hash:
        return False   # 只检查 execution_hash 非空
    return True
```

`register_measured_de` **接受调用方传入的 `execution_hash`**(registry.py:403),
而这里**只验证该字段非空 + method 在白名单**,**从不核对这个 hash 是否真由 Pertura 执行产生**。

→ 调用方可手写 `method="sceptre", execution_hash="sha256:whatever"` 直接得 measured。
**这与 Phase 1b 已修的 calibration laundering 同源,只是发生在 measured 主层。**

### 为什么是 P2 阻塞
P2 每个 wrapper 都产 measured/prediction 证据。没有可信执行核对,wrapper 的信任**要么可伪造,要么全卡 smoke**。

### 收束(与 C5/C6 同一套机制)
1. 建 **execution ledger**(见 C5 命名):Pertura 亲手跑 runner/wrapper 时,把
   `{tool, input_hash, output_hash, execution_hash}` 追加到 `artifacts/execution_ledger.jsonl`。
2. `is_trusted_execution` 改为:method 在白名单 **且** `execution_hash ∈ ledger`。
3. 所有 measured registrar 统一走"由受控执行盖 execution_hash",registrar 不再接受任意外部 hash(或接受但门只认 ledger 里的)。

---

## C5 — 命名冲突:"harvest" 已被占用(P2 阻塞)

### 现状
`workflow/harvest.py` + `workflow/models.py:HarvestMode` **已经**把 "harvest" 定义为:
> **扫描 preflight 候选文件、按 mode 决定是否 auto-register 到 evidence registry**
> (`candidate_only` / `auto_register_strict` / `interactive_confirm`)

这与 `harvester_extension_method.md` 里把"**受控执行外部工具的通道**"也叫 `harvest_tool()` / trusted-harvest **直接冲突**。同名两义,后面必然打架。

### 收束(命名决策,先定再写代码)
给两件事各起**互不重叠**的名字。建议:

| 概念 | 现用词 | 建议改用 |
|------|--------|----------|
| 扫描 preflight 候选并 auto-register | harvest(保留) | **保留 `harvest`** |
| 受控执行 runner/外部工具 + 盖 execution_hash | harvest_tool(冲突) | **`trusted_run` / `execution_channel`** |
| 执行台账文件 | —— | `artifacts/execution_ledger.jsonl` |

扩展方法文档 Part A 里的 `harvest_tool` 全部改称 `trusted_run`,避免与既有 `HarvestMode` 混。

---

## C6 — 缺 trusted pseudobulk DE runner(承重钥匙)

### 现状(runners/basic_de.py:9)
```python
DEFAULT_METHOD = "basic_mean_difference_v1"
```
- 该 method **不在** `policy.trusted_runner_methods`(policy.py:62,白名单是 `pseudobulk`/`sceptre`/…)
  → strict/paper 下永远停在 smoke。
- 它是 **cell-level、无 replicate 聚合**(registry 每 cell 一个数,basic_de.py:56–68)→ pseudoreplication。

**结论:目前没有任何原生 runner 能产出过 strict 的 measured DE。** 白名单写了 `pseudobulk`,但产出它的函数还没写。

### 收束
写 `runners/pseudobulk_de.py`:先按 replicate 轴(donor/batch)聚合成伪批,再在伪批层做检验,`method="pseudobulk_de"`(已在白名单),经 C4 的受控执行盖 ledger execution_hash。
它同时是 benchmark(label-permutation null / NTC 校准)的引擎。

---

## C7 — Registrar 内 scope/quality 拼装重复(随 C2 一起)

每个 typed registrar 都手工重复这段模式(registry.py:406–433 measured_de、489–511 predicted_effect …):
```python
merged_scope = {..., **dict(scope or {})}
merged_quality = {..., **dict(quality or {})}
{k: v for k, v in ... if v not in (None, "")}   # 过滤空值,每个方法各写一遍
```
C2 的单入口 `register()` 统一做这段拼装 + 空值过滤,**这段重复自动消失**。无需单列改动,随 C2 落地即可。

---

## C8 — EvidenceClass 别名双名(低优先)

`schema.py:40`:
```python
observed_metadata = "observed_metadata"
observation       = "observed_metadata"   # 同值别名
composite_summary = "composite_summary"
composite         = "composite_summary"   # 同值别名
```
两名一值,读代码时容易误以为是两类。**不阻塞任何东西**,但收束时可只留一个规范名、其余作兼容 alias 注释清楚。低优先,可最后做。

---

## 2. 建议落地顺序(P2 前的"地基批次")

```
第 1 批(结构收束,互相关联,一起做):
  C1  ARTIFACT_TAXONOMY 单一 taxonomy 表
  C2  ArtifactSpec + 单入口 register()(删 8 个 family dispatcher)
  C7  拼装去重(随 C2 自动完成)
  C3  MCP tool 从 spec 生成
     → 完成后:新增证据类型 = 1 行 spec

第 2 批(信任 + 命名,互相关联):
  C5  命名定名(trusted_run / execution_ledger)
  C4  execution ledger + is_trusted_execution 核对 ledger
     → 完成后:measured 伪造缝关闭,P2 wrapper 可 earn trust

第 3 批(承重钥匙):
  C6  pseudobulk_de runner(经第 2 批的受控执行)
     → 完成后:strict/paper 有第一把合规 measured 钥匙 + benchmark 引擎就位

第 4 批(低优先,可缓):
  C8  EvidenceClass 别名清理

—— 以上完成,P2 正式解锁:每个 wrapper = 1 行 spec + 1 个 parser + 1 行白名单 ——
```

## 3. 不要动的东西(避免过度重构)

- resolver 三个特判 `_resolve_prediction_measured_concordance` / `_resolve_curated_enrichment` / `_resolve_replication_summary` —— 真跨 artifact 逻辑,保留。
- `MeasuredPredicateSpec` 表(resolver.py:52)—— 已是 spec 驱动,C1/C2 与它风格一致,**它就是 registry 侧要模仿的样板**。
- 门的语义(strength = f(结构化证据 + UID scope + policy_hash))—— 全程不变。C1–C8 只动**如何登记与暴露证据**,不动**如何判定强度**。

## 4. 验收标准(收束完成的判据)

1. 新增一种证据类型,git diff 只出现在**一张 spec 表**里(+ 可选 parser),不触碰 enum 映射函数 / 不新写 registrar 方法 / 不新写 MCP tool。
2. `grep "if subtype ==" registry.py` 结果为空(family dispatcher 已删)。
3. 手写 `execution_hash="sha256:whatever"` 的 measured 证据在 strict/paper 下**只到 observation**(measured 伪造缝已关)。
4. 存在一个 `method="pseudobulk_de"` 的原生 runner,其产物在 paper 下可达 `measured_association`。
5. 代码中不再有第二处叫 "harvest" 的受控执行入口(命名唯一)。
6. 现有 221 测试全绿(收束是等价重构,不改语义)。
