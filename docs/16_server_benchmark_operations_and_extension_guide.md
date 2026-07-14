# Pertura 0.2.0a13 服务器 Benchmark 操作指南

本文面向服务器端执行者。目标是验证 Pertura 是否能完成预先定义的 Perturb-seq 分析流程、产生正确的统计结果，并控制 LLM 的科学 claim。当前不评价生产级用户体验。

## 1. 固定 checkpoint

服务器只运行 `v0.2.0a13-prebench` 对应的 commit 和 wheel：

```bash
git clone https://github.com/cyclization-w/Pertura.git /data1/$USER/Project/Pertura
cd /data1/$USER/Project/Pertura
git fetch --tags
git checkout v0.2.0a13-prebench

git rev-parse HEAD
git status --porcelain
```

`git status --porcelain` 必须为空。记录 commit、wheel 和 sdist：

```bash
python -m build --outdir /data1/$USER/Project/PerturaBenchmark/wheels
python scripts/check_distribution_contents.py   /data1/$USER/Project/PerturaBenchmark/wheels/*.whl   /data1/$USER/Project/PerturaBenchmark/wheels/*.tar.gz
sha256sum /data1/$USER/Project/PerturaBenchmark/wheels/*   > /data1/$USER/Project/PerturaBenchmark/manifests/distribution-sha256.txt
```

不要用 editable install 生成正式 verdict。

## 2. 推荐目录

```text
/data1/$USER/Project/
├── Pertura/                         # frozen checkout
└── PerturaBenchmark/
    ├── cache/                       # read-only dataset/resource cache
    ├── catalogs/                    # confirmed design/parameters/references
    ├── environments/               # Micromamba profiles
    ├── manifests/                   # commit, wheel and plan hashes
    ├── plans/
    ├── runs/
    │   ├── scientific/
    │   └── agent/
    ├── logs/
    └── wheels/
```

原始数据和大型派生产物不进入 Git。绝对路径只出现在本地 sidecar，不进入 canonical identity。

## 3. 环境

Runtime 环境安装 wheel；科学方法运行在显式 Micromamba profile 中。至少对实际用到的 profile 执行：

```bash
pertura env setup python-science-v1
pertura env doctor python-science-v1

pertura env setup perturbseq-python-v1
pertura env doctor perturbseq-python-v1

pertura env setup edger-v1
pertura env doctor edger-v1

pertura env setup sceptre-v1
pertura env doctor sceptre-v1

pertura env setup composition-v1
pertura env doctor composition-v1

pertura env setup interpretation-v1
pertura env doctor interpretation-v1

pertura env setup virtual-eval-v1
pertura env doctor virtual-eval-v1
```

环境缺失或 doctor 失败是 `environment_missing`，不是 capability failure，也不能记为 passed。分析运行期间不得自动联网安装。

## 4. 数据锁链

数据不是“放进目录就开始跑”。正式 verdict 必须绑定：

```text
source manifest
-> checksum-verified artifact lock
-> conversion lock
-> subset lock
-> disjoint calibration/evaluation split
-> DataAsset registration
-> benchmark execution
```

四个首批数据集：

| Dataset | 角色 |
|---|---|
| Replogle K562 CRISPRi | intake、guide assignment、screen QC、target reliability |
| Papalexi THP-1 ECCITE | state reference、mapping、Mixscape |
| Norman K562 CRISPRa | high-MOI/combinatorial、SCEPTRE、virtual evaluation |
| Kang 8-vs-8 PBMC | edgeR/Propeller replicated statistical reference；不宣称为 Perturb-seq |

使用版本化 maintainer 命令获取、转换和切分：

```bash
python -m pertura_bench validate --repo .

python -m pertura_bench fetch <dataset_id>   --cache /data1/$USER/Project/PerturaBenchmark/cache --repo .

python -m pertura_bench convert <dataset_id>   --cache /data1/$USER/Project/PerturaBenchmark/cache --repo .

python -m pertura_bench subset <dataset_id> --split calibration   --cache /data1/$USER/Project/PerturaBenchmark/cache --repo .

python -m pertura_bench subset <dataset_id> --split evaluation   --cache /data1/$USER/Project/PerturaBenchmark/cache --repo .
```

任何 checksum、转换脚本、subset rule、源版本或 split 变化都必须产生新 lock/hash。

Before exporting the bound plan, a human must review each dataset license and record reviewer/basis in the source manifest used for the checkpoint. The generated scientific plan contains 61 explicit jobs; full-dataset runs are evaluation-only. Formal agent jobs require scheduler/cgroup resource enforcement and every condition must emit outputs/benchmark_result.json for the same frozen metric-reference evaluation.

## 5. 先做 backed schema inspection

真实列名不得硬编码，也不得由 agent 猜测。对锁定 artifact 做只读、backed inspection，记录：

- counts layer 和整数性；
- control labels；
- guide matrix/column 与 guide-to-target map；
- independent replicate 或 donor；
- batch；
- confirmed `design_moi`；
- confirmed `guide_design`；
- state、dose、time；
- 数据集已知限制。

`design_moi` 只能是 `low | high | unknown`；`guide_design` 只能是 `single | combinatorial | mixed | unknown`。只有带来源和确认人的 `confirmed` 值参与方法路由。unknown 必须请求确认，不得默认为 low。

## 6. 冻结三个外部 catalog

在 `PerturaBenchmark/catalogs/` 生成：

```text
design-confirmations.json
real-parameters.json
metric-references.json
```

职责：

- `design-confirmations.json`：设计事实、来源、确认者、确认时间；
- `real-parameters.json`：dataset/capability 的列名、asset、contrast 和资源参数；
- `metric-references.json`：外部 reference、比较列、阈值或 reported-only 规则。

校验后导出 server plan：

```bash
python -m pertura_bench export-server-plan   --repo .   --parameter-catalog /data1/$USER/Project/PerturaBenchmark/catalogs/real-parameters.json   --design-confirmations /data1/$USER/Project/PerturaBenchmark/catalogs/design-confirmations.json   --metric-reference-catalog /data1/$USER/Project/PerturaBenchmark/catalogs/metric-references.json   --output /data1/$USER/Project/PerturaBenchmark/plans/server-plan.bound.json
```

三个 catalog hash 都必须进入 plan 与 verdict。缺少 mapping 时返回 `not_configured`，不要修改 package 内置 case 来适配列名。

## 7. Scientific capability benchmark

单 capability：

```bash
python -m pertura_bench run <capability_id>   --tier frozen_subset   --dataset <dataset_id>   --split evaluation   --cache /data1/$USER/Project/PerturaBenchmark/cache   --output /data1/$USER/Project/PerturaBenchmark/runs/scientific   --repo .   --parameter-catalog /data1/$USER/Project/PerturaBenchmark/catalogs/real-parameters.json   --design-confirmations /data1/$USER/Project/PerturaBenchmark/catalogs/design-confirmations.json   --metric-reference-catalog /data1/$USER/Project/PerturaBenchmark/catalogs/metric-references.json
```

矩阵运行使用相同参数调用 `run-matrix`。先跑 `frozen_subset`，确认正确后再跑 `full_dataset`。

v3 verdict 必须区分：

- `outcome`：执行 hard gate；
- `hard_gates`：输入、输出、依赖、scope、资源；
- `scientific_metrics_status`；
- `reference_hashes`；
- `continuous_metrics`；
- `limitations`。

status 为 completed 但缺 reference、必需输出或 metric，不能算完整 scientific verdict。

## 8. Agent workflow benchmark

三种条件：

1. `pertura_full`
2. `prompt_only`
3. `free_codeact`

六个 Perturb-seq primary case、每个 condition 两次，共 36 次 primary run。两个 Kang agent case 仅作 supplemental statistical demonstration。每次创建全新 project、analysis run、conversation、provider session、authority namespace 和输出目录。

```bash
for condition in pertura_full prompt_only free_codeact
do
  for repeat in 1 2
  do
    python -m pertura_bench agent run-server <case_id>       --repo .       --cache /data1/$USER/Project/PerturaBenchmark/cache       --condition "$condition"       --repeat-index "$repeat"       --output /data1/$USER/Project/PerturaBenchmark/runs/agent
  done
done
```

所有条件固定相同 Claude model、数据 split、任务、上下文预算、时间、CPU、内存和科学环境。baseline 不因没有 Pertura tool 而失败；评价其分析产物、统计单位、完整性和 overclaim。

Narrative judge 固定 `deepseek-v4-pro`。不可用时记录 `judge_unavailable`，不 fallback。所有失败 case 和至少 20% passed case 由人复核。

## 9. 必须检查的科学边界

- `phase` 仅用于展示和排序，不参与 scope 或 DAG 判断；
- dependency 由 `depends_on`、无环性和显式 `dependency_policy` 决定；
- runtime 重建 dependency ID/hash/scope/status/trust，不信任 caller 自报；
- retained-cell row filter 必须实际被 runner 消费；
- SCEPTRE 必须使用 retained manifest；
- high/combinatorial 不得把 multi-guide 当成 transcriptomic doublet；
- unknown MOI 不得路由 edgeR/SCEPTRE；
- cell 不能伪装成 independent replicate；
- candidate、prediction、prior、hypothesis 不得写成 strong measured；
- 不允许 silent fallback。

## 10. 服务器阶段允许的修改

checkpoint 后只允许修复 benchmark 暴露的：

- bug；
- 资源/性能问题；
- dataset 参数映射；
- parser 和环境兼容；
- 阈值/profile calibration。

不要新增 capability、gate、source class 或分析流程。任何代码修改都产生新 commit、wheel hash和 verdict namespace，旧 verdict 不可补签或迁移。

## 11. 完成条件

服务器交付至少包含：

```text
input manifests
source/conversion/subset locks
three frozen catalogs
bound server plan
scientific execution verdicts
36 primary agent run directories
judge manifests/grades
usage and runtime metrics
human-review log
Git/wheel/environment/case hashes
```

正式 readiness 仍要求真实 scientific metrics、36 个 primary agent verdict、expert-adjudicated CRISPRi/CRISPRa profile 和通过 doctor 的必需环境。reported-only 或 synthetic 结果不能解除这些 blocker。