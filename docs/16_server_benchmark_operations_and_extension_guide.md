# Pertura 服务器 Benchmark、对照实验与后续扩展操作手册

本文档面向接手服务器工作的 Codex/维护者。目标是使用冻结的 Pertura checkpoint，在不污染数据、不泄漏 evaluation split、不改变 claim ceiling 的前提下，完成：

1. 真实数据获取、转换、锁定、划分和注册；
2. scientific capability benchmark；
3. Pertura agent workflow benchmark；
4. Pertura、prompt-only、free CodeAct/no-gate 和其他 LLM 的公平比较；
5. 根据 benchmark 结果进行可追溯修复，并规划 capability、用户体验和 provider adapter 的后续扩展。

本文档描述的是研究 benchmark 工作流，不把当前 alpha 表述为生产级发布。`v0.2.0a7-prebench` 的 expanded capabilities 仍是 `exploratory` / `synthetic_only`，真实 benchmark 通过前不能晋升为 trusted。

---

## 1. 首先理解三条不同的状态主线

Pertura 同时存在三类状态，服务器端不得混用。

| 主线 | 保存内容 | 能否创建科学结果 |
|---|---|---|
| Project store | project、run、conversation、turn、DataAsset、本地位置、report revision | 否 |
| Authority store | ResultEnvelope、dependency、receipt、session seal、promotion decision | 是，仅 runtime 内部 |
| PerturaBench | source/artifact/subset lock、case、metric、verdict、server plan | 否，只评价产品运行 |

普通用户数据注册只需要 DataAsset 和 DatasetContract。正式 benchmark 还必须增加 source manifest、artifact lock、subset lock 和 split discipline。

---

## 2. 服务器 Codex 的不可违反规则

服务器 Codex 必须遵守以下 hard rules：

1. 不修改或覆盖 `v0.2.0a7-prebench` tag。
2. 正式运行安装 wheel，不使用 editable install 作为最终 verdict 的执行身份。
3. 数据准备可以联网；analysis capability 默认离线。
4. 所有大型数据位于 repo 外部的只读 cache/object-store mount。
5. 不把绝对缓存路径写入 canonical lock identity；绝对路径只进入 ignored/local sidecar。
6. calibration 和 evaluation target/perturbation 集合不相交。
7. evaluation split 不用于阈值选择、module 学习、state reference 拟合或 prompt 调整。
8. 不把 cell 当作 independent replicate。
9. 不把 multi-guide 自动当作 transcriptomic doublet。
10. 不把 prediction、prior 或 hypothesis 改写成 measurement。
11. 不把 Kang 描述为 Perturb-seq；它只是 replicate-aware statistical golden set。
12. `not_available`、`environment_missing`、`judge_unavailable` 不能记作 passed。
13. candidate capability 的成功状态不能解除 trusted/release gate。
14. 每个 agent case 使用新的 project、analysis run、conversation、provider session 和 authority namespace。
15. benchmark 条件之间不共享 conversation history、working notes 或输出目录。

如果无法确认设计字段，记录 blocker 或生成确认任务；不得根据列名相似度静默确认。

---

## 3. 推荐服务器目录结构

示例：

```text
/srv/pertura/
├── repo/                         # checkout，只读 benchmark checkpoint
├── wheels/                       # wheel、sdist、hash manifest
├── cache/                        # 数据与 portable locks；repo 外
│   ├── datasets/
│   ├── resources/
│   └── predictions/
├── runs/
│   ├── scientific/
│   ├── agent/
│   └── comparative/
├── plans/
│   ├── server-plan.template.json
│   └── server-plan.bound.json
└── manifests/
    ├── benchmark-checkpoint.json
    ├── resource-lock-set.json
    └── prediction-lock-set.json
```

建议权限：

- benchmark job 对 source cache 只读；
- conversion/subset preparation job 对自己的 staging 可写；
- 每个 agent execution root 独占；
- 不把 provider key 写进 repo、prompt、events 或 ProjectStore。

---

## 4. 固定代码与 wheel 身份

在服务器上：

```bash
git clone https://github.com/cyclization-w/Pertura.git /srv/pertura/repo
cd /srv/pertura/repo
git fetch --tags
git checkout v0.2.0a7-prebench
git status --short
git rev-parse HEAD
```

`git status --short` 必须为空。记录 commit：

```bash
GIT_COMMIT=$(git rev-parse HEAD)
```

复制本地已经验证的 wheel/sdist，或从该 tag 的干净 checkout 重建：

```bash
python -m build --outdir /srv/pertura/wheels
python scripts/check_distribution_contents.py \
  /srv/pertura/wheels/*.whl \
  /srv/pertura/wheels/*.tar.gz
sha256sum /srv/pertura/wheels/*
```

正式运行使用独立环境安装 wheel：

```bash
python -m venv /srv/pertura/runtime-venv
source /srv/pertura/runtime-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "/srv/pertura/wheels/pertura-0.2.0a7-py3-none-any.whl[llm,omics,perturbseq,dashboard]"
python -c "from importlib.metadata import version; print(version('pertura'))"
```

预期版本为 `0.2.0a7`。

---

## 5. 安装并诊断科学环境

Pertura 当前声明七个环境 profile：

```text
edger-v1
python-science-v1
perturbseq-python-v1
sceptre-v1
composition-v1
interpretation-v1
virtual-eval-v1
```

显式安装：

```bash
for profile in \
  edger-v1 \
  python-science-v1 \
  perturbseq-python-v1 \
  sceptre-v1 \
  composition-v1 \
  interpretation-v1 \
  virtual-eval-v1
do
  pertura env setup "$profile" || exit 1
  pertura env doctor "$profile" || exit 1
done
```

环境缺失是 infrastructure status，不是 scientific failure。不得在 capability 运行期间自动安装或 fallback 到另一方法。

---

## 6. 数据集选择原则

不要让一个数据集承担所有问题。四个数据集各自覆盖不同科学边界。

| 数据集 | 规模/类型 | 主要用途 | 不应宣称 |
|---|---|---|---|
| Replogle K562 essential CRISPRi | 官方 raw single-cell H5AD，约 10.6 GB | intake、guide integrity/assignment、ambient/MOI、retained cells、CRISPRi efficacy/reliability、P4 effect interpretation | 没有独立 replicate 时不能支持 strict replicate-aware strong DE |
| Papalexi THP-1 ECCITE | SeuratData 转 H5AD | control-derived state reference、kNN mapping、Mixscape responder/escape、control-only modules | 不能作为所有 replicate-aware expression 方法的通用 golden |
| Norman K562 CRISPRa | 官方 H5AD，约 446 MB | high-MOI/combinations、SCEPTRE、CRISPRa reliability、virtual split/baselines/evaluator/next panel | multi-guide 不能自动被当作 doublet；不能路由到 low-MOI pseudobulk |
| Kang 8-vs-8 PBMC | 8+8 donor/sample statistical dataset | edgeR pseudobulk、Propeller composition、confounding/replicate golden | 不是 Perturb-seq，不用于 guide biology claim |

### 6.1 数据选择的最低要求

对每个数据集先建立设计表：

```text
dataset_id
modality
primary perturbation/condition column
control definition
guide column / guide matrix
target mapping
independent replicate or donor
batch
state label source
dose/time
raw count layer
known limitations
```

所有字段标记为 `observed | inferred | confirmed | unresolved`。只有 publication、official metadata 或人工确认可以把关键字段提升为 confirmed。

### 6.2 calibration/evaluation 划分

- 固定 seed `1729`；
- target/perturbation 层稳定 hash 60/40；
- calibration 用于阈值/profile 选择；
- evaluation 只用于一次性最终评价；
- controls 可以作为两边共同的参照数据，但不能把 evaluation outcome/label 用于训练或阈值调整；
- 对 expert profile，每个 modality 至少 50 个 target，evaluation 至少 20 个；
- 任何 split 修改都生成新的 split/spec/lock hash。

---

## 7. 获取和转换数据

设置路径：

```bash
export PERTURA_REPO=/srv/pertura/repo
export PERTURA_CACHE=/srv/pertura/cache
cd "$PERTURA_REPO"
python -m pertura_bench validate --repo .
```

### 7.1 直接下载数据

Replogle：

```bash
python -m pertura_bench fetch \
  replogle_k562_essential_2022 \
  --cache "$PERTURA_CACHE" \
  --repo .
```

Norman：

```bash
python -m pertura_bench fetch \
  norman_k562_crispra_2019 \
  --cache "$PERTURA_CACHE" \
  --repo .
```

fetch 会验证官方 size 和 MD5、计算 SHA-256，并写入 artifact lock 与 local sidecar。不要手工改 lock；下载中断或 checksum mismatch 必须失败。

### 7.2 显式转换数据

Papalexi：

```bash
python -m pertura_bench convert \
  papalexi_thp1_eccite \
  --cache "$PERTURA_CACHE" \
  --repo . \
  --rscript Rscript
```

该脚本会显式调用 SeuratData acquisition，并要求固定的 Seurat/SeuratData/thp1.eccite 版本。

Kang：

```bash
python -m pertura_bench convert \
  kang18_8vs8_pbmc \
  --cache "$PERTURA_CACHE" \
  --repo . \
  --rscript Rscript
```

conversion lock 必须记录 conversion script hash 和 package versions。

### 7.3 当前 cache-layout 兼容说明

当前 fetch/convert CLI 会在 cache root 写入 flat lock/sidecar；`resolve_real_artifact_chain` 同时支持 flat compatibility 路径和结构化 `datasets/<dataset>/converted/` 路径。正式运行期间不要移动 artifact 或手改 sidecar。后续应统一为结构化 content-addressed layout，见改进路线。

---

## 8. 先检查真实数据 schema，再创建 subset spec

不要预先猜测列名。使用 backed read 只检查结构和 metadata：

```bash
python - <<'PY'
import anndata as ad
from pathlib import Path

path = Path("/replace/with/locked/artifact.h5ad")
adata = ad.read_h5ad(path, backed="r")
print("shape", adata.shape)
print("obs columns", list(adata.obs.columns))
print("var columns", list(adata.var.columns))
print("layers", list(adata.layers.keys()))
for name in adata.obs.columns:
    values = adata.obs[name]
    print(name, values.dtype, values.nunique(dropna=False))
adata.file.close()
PY
```

把检查结果保存为非权威的 schema report，人工确认后再创建：

```text
benchmarks/subsets/<dataset_id>.calibration.json
benchmarks/subsets/<dataset_id>.evaluation.json
```

示例结构：

```json
{
  "schema_version": "pertura-benchmark-subset-spec-file-v1",
  "spec": {
    "label_column": "REPLACE_WITH_CONFIRMED_COLUMN",
    "labels": ["REPLACE_WITH_FROZEN_LABELS"],
    "max_cells_per_label": 500,
    "seed": 1729,
    "selection": {
      "basis": "confirmed target/condition split",
      "split_manifest_hash": "sha256:..."
    }
  }
}
```

文件中的 placeholder 必须在执行前全部替换；subset spec 应在 benchmark branch 中 review/commit。

创建 subset：

```bash
for dataset in \
  replogle_k562_essential_2022 \
  papalexi_thp1_eccite \
  norman_k562_crispra_2019 \
  kang18_8vs8_pbmc
do
  for split in calibration evaluation
  do
    python -m pertura_bench subset "$dataset" \
      --split "$split" \
      --cache "$PERTURA_CACHE" \
      --repo . \
      --from-lock-chain || exit 1
  done
done
```

subset lock 必须绑定当前 artifact lock、subset spec hash、subset script hash 和输出 SHA-256。

---

## 9. 补全真实参数目录：正式运行前的当前 blocker

当前 `src/pertura_bench/cases/real_parameters.v1.json` 只为四个数据集配置了：

```text
intake.materialize.v1
diagnostic.dataset_integrity.v1
```

其他 capability 虽然已有 dataset mapping，但没有真实列名、设计参数和依赖参数。服务器 Codex 不得把缺失映射记为 capability failure，也不得猜参数。

### 9.1 正确做法

1. 从 locked artifact 读取 schema；
2. 根据 publication/official metadata 确认 control、guide、target、replicate/donor、batch、state、dose/time；
3. 为 dataset × capability 填写 `parameters`；
4. 将 design confirmation 写入 `contract_confirmations`；
5. bump `catalog_version`；
6. 运行 schema/case validation；
7. commit 到独立 benchmark-preparation branch；
8. 重新生成 server-plan template 和 hash。

示意：

```json
{
  "datasets": {
    "example_dataset": {
      "contract_confirmations": {
        "control": {"column": "...", "values": ["..."]},
        "replicate": {"column": "..."}
      },
      "capabilities": {
        "some.capability.v1@0.1.0": {
          "parameters": {
            "input_path": {"artifact_ref": "primary"},
            "confirmed_column": "..."
          }
        }
      }
    }
  }
}
```

不要直接复制示意字段；最终字段必须通过该 capability 的 JSON Schema。

---

## 10. 两层数据注册

### 10.1 正式 benchmark 注册

Formal benchmark 先通过：

```text
source manifest
→ artifact lock
→ subset spec
→ subset lock
→ calibration/evaluation identity
```

### 10.2 产品运行注册

手工 product smoke 可以创建 Project/DataAsset：

```bash
pertura project init /srv/pertura/runs/manual-project
pertura assets add \
  /srv/pertura/runs/manual-project \
  /srv/pertura/cache/path/to/locked/artifact.h5ad \
  --role primary_dataset \
  --kind observed
pertura inspect /srv/pertura/runs/manual-project
pertura assets doctor /srv/pertura/runs/manual-project
```

DataAsset 注册不赋予 measured authority。正式 `agent run-server` 会为每个 case 自动创建新 Project，并把 locked evaluation subset 注册为 primary observed asset。

---

## 11. Scientific capability benchmark

### 11.1 先运行 frozen subset

单 capability：

```bash
python -m pertura_bench run diagnostic.dataset_integrity.v1 \
  --tier frozen_subset \
  --dataset replogle_k562_essential_2022 \
  --split evaluation \
  --cache "$PERTURA_CACHE" \
  --output /srv/pertura/runs/scientific \
  --repo .
```

单 dataset matrix：

```bash
python -m pertura_bench run-matrix \
  --tier frozen_subset \
  --dataset replogle_k562_essential_2022 \
  --split evaluation \
  --cache "$PERTURA_CACHE" \
  --output /srv/pertura/runs/scientific \
  --repo .
```

只有在 subset、参数映射、环境和依赖 DAG 通过后，才运行 `full_dataset`。

### 11.2 结果解释

| outcome/status | 解释 |
|---|---|
| passed | case 与当前 metric/required output 一致 |
| failed | 代码、解析、科学 metric 或 lock identity 失败 |
| not_available | artifact/subset/lock 不存在；不是成功也不是科学失败 |
| blocked | 可能是正确的科学阻断，也可能是参数/依赖错误；按 expected blocker 审核 |
| environment_missing | 基础设施未准备；单独报告 |

每个 real verdict 至少保存：

```text
case hash
capability ID/version
runner hash
environment lock hash
source/artifact/subset hashes
parameter catalog hash
scientific result digest
metrics
runtime
peak memory
failure reasons
```

### 11.3 Dataset × capability 主要映射

- Replogle：intake、dataset/guide/ambient/assignment/MOI/retained、guide efficacy/reliability、effect matrix、modules/programs/enrichment/regulator/evidence map、method null。
- Papalexi：state fit/map/annotation、control NMF、Mixscape responder。
- Norman：SCEPTRE、guide/target sensitivity、effect matrix、P4 interpretation、virtual split/ingest/leakage/baselines/evaluation/next panel、method null。
- Kang：design balance、Propeller、method null；edgeR 另有独立 golden/release path。

---

## 12. 当前 agent server benchmark 如何运行

列出 case：

```bash
python -m pertura_bench agent server-cases
```

当前八个 case：

```text
agent_replogle_qc
agent_replogle_target
agent_papalexi_state
agent_papalexi_stale
agent_norman_sceptre
agent_norman_virtual
agent_kang_edger
agent_kang_propeller
```

设置 provider 和 judge：

```bash
export ANTHROPIC_API_KEY=...
export PERTURA_CLAUDE_MODEL=...
export DEEPSEEK_API_KEY=...
export DEEPSEEK_BASE_URL=https://api.deepseek.com
```

运行：

```bash
python -m pertura_bench agent run-server agent_replogle_qc \
  --cache "$PERTURA_CACHE" \
  --output /srv/pertura/runs/agent \
  --repo .
```

每个 execution root 应保存：

```text
input_manifest.json
events.jsonl
turn_finals/*.json
turn_finals/*.md
authority_projection.json
execution_verdict.json
judge/grade.json
usage.json
```

judge 固定为 `deepseek-v4-pro`、temperature 0、无 fallback。缺少凭据返回 `judge_unavailable`，不能自动换模型。

### 12.1 当前 agent runner 的真实限制

当前 runner 已能验证：

- turn checkpoint；
- structured TurnFinal；
- no silent fallback；
- candidate claim ceiling。

但它还没有完整实现论文级比较所需的：

- 多 condition（full/prompt-only/no-gate）；
- 其他 provider runtime；
- 每个 case 多随机重复；
- case-level tool-choice/parameter/scope/stale expected gates；
- scripted multi-turn confirmation（当前所有 server case 实际只执行一个 turn）；
- 统一的 external claim grader；
- paired statistical comparison。

因此当前八个 case 是 Pertura server execution skeleton，不应直接被描述为已经完成的跨系统论文 benchmark。

---

## 13. Pertura、prompt-only、no-gate 与其他 LLM 的公平比较

### 13.1 推荐四个 system conditions

| condition | 数据/资源 | Pertura capabilities | Pertura promotion/finalizer | 目的 |
|---|---|---|---|---|
| `pertura_full` | 相同 locked subset | 是 | 是 | 完整系统 |
| `capability_no_promotion` | 相同 locked subset | 是 | 否，模型直接叙述 committed outputs | 隔离 claim gate 的贡献 |
| `prompt_only_codeact` | 相同 locked subset和统计环境 | 否 | 否；仅给避免过度 claim 的 prompt | 比较 prompt guardrail |
| `free_codeact_no_gate` | 相同 locked subset和统计环境 | 否 | 否；无 claim-specific prompt | 完全自由 baseline |

旧 `p07_harness.py` 中按 task ID 构造的 baseline 不能作为论文级 no-gate baseline。正式 baseline 必须真实运行 agent、保存事件和产物。

### 13.2 Provider 因子

每个支持的 provider/model 都运行相同 condition catalog。当前状态：

- Claude adapter：可运行；
- OpenAI Agents SDK adapter：只有 schema/instructions skeleton，不可运行；
- 其他 provider：未实现。

在 OpenAI 或其他 LLM adapter 完成之前，不能声称跨 provider parity。

### 13.3 公平性约束

所有条件必须共享：

- 同一个 dataset/subset lock；
- 同一个 task text 和 expected scientific scope；
- 同一个离线知识资源版本；
- 相同最大 wall time、CPU、memory；
- 可比较的 token/cost budget；
- 相同网络策略；
- 相同 evaluation rubric；
- fresh workspace/session；
- 随机化执行顺序；
- evaluator 不知道 condition label。

Pertura baseline 可以共享原始数据和底层统计包，但不能把 Pertura 的 runtime-derived claim decision 偷渡给 no-gate 条件。

### 13.4 重复数

- pilot：每个 case × model × condition 3 次；
- final：建议至少 5 次；
- provider 不支持 seed 时也必须记录 run index、temperature、model revision 和时间；
- 统计比较按相同 case 配对，不把单次随机运行当独立科学 replicate。

### 13.5 输出规范

```text
runs/comparative/
└── <case_id>/
    └── <condition>/
        └── <provider_model>/
            └── <replicate_id>/
                ├── input_manifest.json
                ├── events.jsonl
                ├── raw_final.txt
                ├── normalized_final.json
                ├── artifacts/
                ├── usage.json
                ├── execution_verdict.json
                └── judge/grade.json
```

baseline 的 normalized output 只用于外部评价，不能写入 Pertura authority store 或获得 receipt。

---

## 14. 论文 benchmark 的评分

### 14.1 Hard gates

至少包括：

- 是否选择正确方法；
- 是否使用正确 asset/column/contrast；
- 是否满足 dependency、scope 和 stale 规则；
- 是否把 cell 当 replicate；
- 是否发生 silent fallback；
- 是否把 multi-guide 当 doublet；
- 是否把 prediction/prior/hypothesis 提升为 measurement；
- blocked/needs-input 是否符合 gold；
- 输出 schema 和 artifact 是否完整；
- 是否泄漏 evaluation split。

任意 strong overclaim、prediction→measurement、cell-as-replicate 自动失败。

### 14.2 Scientific metrics

- intake：barcode/count/layer 一致性；
- assignment：precision、recall、posterior calibration、ambient detection；
- state：ARI、mapping rejection、cross-seed stability；
- module/program：recovery、consensus stability、leakage detection；
- reliability：macro-F1、per-class recall、false-block rate；
- edgeR：logFC/F/PValue/FDR 与 direct reference；
- SCEPTRE：null calibration、type-I error、power、effect concordance；
- Propeller：proportion effect error、type-I error、FDR；
- virtual：baseline win、direction/rank、discriminability、collapse、coverage；
- 所有方法：determinism、runtime、peak memory、failure detection。

### 14.3 Agent metrics

建议同时报告：

```text
Task Completion Rate
Correct Method Selection
Dependency/Scope Compliance
Overclaim Rate (claim-level and case-level)
False-Block Rate
Evidence/Result Trace Coverage
Artifact Validity
Narrative Score
Runtime / Tokens / Cost
```

Overclaim Rate 不能单独使用，因为“阻断所有结论”也能得到低 overclaim。必须与 task completion 和 false-block rate 一起报告。

### 14.4 Narrative judge 与人工复核

当前 rubric 四项各 0–4：

```text
scientific completeness
clarity
limitations/uncertainty
actionability
```

通过条件：平均 ≥3.0，任何维度不得 <2。

人工复核：

- 100% failed cases；
- 至少 20% passed cases；
- 所有模型/condition 分层抽样；
- 人工 reviewer 不看 condition label；
- judge regrade 不修改 execution workspace。

---

## 15. Server plan 和 checkpoint binding

生成 template：

```bash
python -m pertura_bench export-server-plan \
  --repo . \
  --output /srv/pertura/plans/server-plan.template.json
```

当前 CLI 只导出 `executable: false` template。代码中已有 `bind_server_plan()`，但还没有正式 maintainer CLI。服务器正式 job 前必须补充一个小型 `bind-server-plan` 命令，或使用经过 review 的脚本调用该 API。

绑定字段：

```text
git_commit
wheel_sha256
case_catalog_hash
agent_case_catalog_hash
skill_bundle_hash
capability_spec_hash
judge_manifest_hash
report_turn_schema_hash
template_digest
resource_lock_set_hash
prediction_bundle_set_hash
server_plan_hash
```

如果某轮不使用 external resource 或 prediction bundle，也要写显式空 lock-set manifest 并对其 canonical payload 求 hash，不能填零或任意字符串。

这是服务器正式 benchmark 前应优先关闭的 operational gap。

---

## 16. 失败处理和修复流程

不要在同一个 output workspace 中边改代码边继续跑。流程固定为：

```text
冻结 checkpoint 运行
→ 生成不可变失败 verdict
→ 分类 failure
→ 新 Git branch
→ 最小修复 + regression test
→ 新 alpha version/commit/wheel/hash/plan
→ 只重跑受影响 cases + guardrail cases
→ 比较新旧 verdict
```

分类：

| 类型 | 是否改 capability version |
|---|---|
| parser、Windows/path、OOM、chunking、参数序列化 bug | 通常不改科学 version，但 runner hash 变化，旧 verdict 失效 |
| contrast、estimand、统计流程、输出语义改变 | 必须新 capability version |
| v0.2 public schema/tool breaking change | 不改 v0.2；创建 v3/v0.3 |
| 新分析流程 | 新 exploratory capability 0.1.0，无 claim permission |

任何修复都不能补签历史 result。

---

## 17. 后续改进方向

### 17.1 服务器 benchmark 基础设施：最高优先级

在扩展新 biology capability 前，先补齐：

1. `bind-server-plan` maintainer CLI；
2. resource/prediction lock-set manifest builder；
3. 统一结构化 cache layout，消除 flat compatibility 双路径；
4. dataset schema summary 和 real-parameter catalog validator/generator；
5. versioned subset specs 与 split manifests；
6. scientific verdict aggregator 和 paired comparison report；
7. scheduler adapter（Slurm 等）只消费 scheduler-neutral plan；
8. server agent case 的 multi-turn/expected-gate schema；
9. comparative condition harness；
10. provider/model/repetition matrix 与盲法 judge export。

这些是论文 benchmark 有效性的前置工作，不属于产品功能膨胀。

### 17.2 Capability 扩展方式

服务器前不再新增 capability。真实 benchmark 后只根据明确缺口扩展，仍遵循：

```text
spec
→ exploratory 0.1.0
→ validator + synthetic cases
→ frozen subset
→ full dataset
→ expert/scientific review
→ trusted 1.0.0
```

优先候选：

- multi-omic Perturb-seq：ADT/ATAC/MuData；
- chemical perturbation 与 dose/time response；
- replicate-aware longitudinal/composition methods；
- stronger combinatorial perturbation models；
- target reliability 的 CRISPRi/CRISPRa expert profiles；
- perturbation-specific response programs 和 cross-context validation；
- additional virtual-model baselines/conformal methods；
- spatial perturbation 和 lineage-aware designs。

扩展 capability 不增加 MCP tool，不增加 gate/source class。方法或 scientific semantics 改变时发布新 capability version。

### 17.3 用户体验扩展

论文后再增强：

- dataset/design mapping wizard；
- unresolved field confirmation UI；
- server job submit/status/retry/cancel；
- large-asset mount/object-store browser；
- report revision diff；
- benchmark comparison dashboard；
- expert annotation/adjudication UI；
- project export/import 和 collaboration；
- 更清晰的 blocker、环境修复和内存预算建议；
- reproducible notebook/report export。

Dashboard 仍不能直接创建 measured result 或提升 source class。

### 17.4 Adapter 扩展

所有 provider 复用：

- 同一五个 `ProductToolSpec`；
- 同一 `dispatch_product_tool()` handler；
- 同一 Project/Conversation/Turn；
- 同一 skills bundle；
- 同一 authority/dependency/promotion/report 语义。

OpenAI Agents SDK adapter 的正式实现顺序：

1. 实现 `start_or_resume_turn / repair_turn_draft / cancel_turn / close`；
2. 将五工具 neutral schema 转为 Agents SDK function/MCP tools；
3. 只加载被选中的共享 skill 正文；
4. 明确一种 continuation 策略，避免 history duplication；
5. provider structured output 映射到 TurnDraft；
6. session binding、cancel、repair、usage、event normalization；
7. schema/tool parity tests；
8. 相同 agent cases 的 cross-provider benchmark。

之后可以增加 generic MCP/Responses adapter，但 provider-specific 代码不得复制 scientific handlers、planner 或 promotion logic。

---

## 18. 服务器 Codex 的阶段性完成定义

### 阶段 A：数据准备完成

- 四个 source manifests 验证；
- Replogle/Norman artifact locks；
- Papalexi/Kang conversion locks；
- calibration/evaluation subset specs 和 locks；
- 无 checksum/script/sidecar drift；
- 数据在 repo 外，只读挂载可用。

### 阶段 B：scientific benchmark 可运行

- real parameter catalog 覆盖目标 capability DAG；
- 所需 environments doctor 通过；
- frozen subset cases 有真实 verdict；
- full dataset cases 有 metric/runtime/memory；
- evaluation 未参与调参。

### 阶段 C：agent benchmark 可运行

- 八个 Pertura cases 具备 multi-turn/expected gates；
- Claude run、TurnFinal、authority projection、judge 输出完整；
- judge 无 fallback；
- failures 和 20% passes 完成人工复核。

### 阶段 D：comparative benchmark 可发表

- full、capability-no-promotion、prompt-only、free-CodeAct 全部真实运行；
- 至少一个额外 provider adapter 正式可运行后才做跨 provider claim；
- paired repeats 和统计置信区间；
- OCR 与 FBR 同时报告；
- 所有结果绑定 checkpoint/wheel/data/env/case/judge hashes。

---

## 19. 最终交付清单

服务器返回的 handoff 至少包含：

```text
Git commit/tag
wheel/sdist SHA-256
bound server plan
source/artifact/subset locks
real parameter catalog hash
environment locks
resource/prediction lock sets
scientific verdicts
agent execution roots
comparative condition manifests
judge manifests/grades
human review log
runtime/memory/cost summary
known failures
recommended fixes
```

最重要的可追溯关系是：

```text
Git commit 决定代码身份
Data lock 决定输入身份
Environment lock 决定执行环境
Capability version 决定科学方法
Receipt/session 决定受控执行来源
Promotion policy 决定 claim ceiling
Case/judge manifest 决定 benchmark 评价规则
```

只要任何一项变化，就生成新的 benchmark identity；不要覆盖旧结果。
