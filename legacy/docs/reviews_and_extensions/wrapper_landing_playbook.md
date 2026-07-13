# Wrapper Landing Playbook

> 收束已完成(catalog 单一真源 + ledger-backed trusted execution + pseudobulk 参考 runner)。
> 本文给出**加一个 wrapper 的标准落地流程**:照抄骨架、填四处、配 null 自测。
> 黄金参考实现:`runners/pseudobulk_de.py`(measured 级)。任何新 wrapper 都是它的同构复制。

---

## 0. 一个 wrapper 由四件东西组成

| 件 | 文件 | 职责 | 是否总是需要 |
|---|---|---|---|
| **Catalog entry** | `evidence/catalog.py` | 声明证据类型身份(kind/class/predicate/ceiling/roles/required) | 仅当是**新**证据类型 |
| **Runner** | `workflow/runners/<tool>.py` | 受控执行工具 + 写 ledger,**不注册、不输出 prose** | 仅当要 earn **measured/prediction 信任** |
| **Adapter/Parser** | runner 内或 `workflow/adapters/<tool>.py` | 把工具输出**结构化**成 registrar 需要的字段 | 总是 |
| **Whitelist 一行** | `core/policy.py` `trusted_runner_methods` | 让门认这个 method 为 trusted | 仅当 measured 级 |

**关键分层:** runner 只负责"跑 + 盖 ledger",adapter 只负责"结构化字段",registrar 只负责"落证据",门只负责"判强度"。**没有任何一件碰"生物学意义"或"强度"。**

---

## 1. 标准数据流(照抄 pseudobulk)

```
① Runner(受控执行)
   run_<tool>(workspace, *, explicit UID inputs, replicate/scope ...) -> dict
     · 只接受显式 UID / contrast / replicate 输入,不推断
     · 跑工具 → 写输出文件 out_path
     · input_hashes  = {k: file_sha256(input)}
     · execution_hash = canonical_execution_hash({runner meta + input_hashes + parameters})
     · output_hashes = {"<name>": file_sha256(out_path)}   ← 绑定实际输出文件
     · record_trusted_run(root, execution_hash=..., method=..., output_hashes=..., ...)
     · return {path, method, counts, execution_hash, execution_ledger_path, output_hashes, ...}
        ↓ 无注册、无 prose
② Adapter/Parser(结构化)
   从 result / out_path 抽出 registrar 需要的字段(contrast_left/n_left/... 或 model_name/...)
   纯结构抽取:不解释、不打分、不改 scope 权威
        ↓
③ Registrar(落证据)  registry.register_<type>(..., execution_hash=result["execution_hash"])
   · 证据的 source_sha256 由 registry 对输出文件计算
   · 该 source_sha256 必须 == ledger record 里 output_hashes 的某个值 ← 信任绑定
        ↓
④ 门(判强度)  resolve_claim(claim, registry, policy)
   measured 级:is_trusted_execution 读 registry.run_root 的规范 ledger,核对
     method∈whitelist ∧ writer_id==pertura_trusted_run ∧ method 匹配 ∧ source_sha256∈output_hashes
   prediction 级:天花板结构性封在 predicted_effect,不依赖 ledger 也不会越级
```

**信任只在 ① 赚取(受控执行写规范 ledger),门在 ④ 只核对。artifact 自报的任何字段都不产生信任。**

---

## 2. 两条信任线:先做哪种 wrapper 由它决定

| 证据 tier | 天花板 | 是否需要 runner+ledger 才能到顶 | 风险 |
|---|---|---|---|
| **prediction** | `predicted_effect` | **否**——天花板结构性封顶,parser 再糙也越不了级 | 最低 → **第一个 wrapper 选这里** |
| **measured** | `measured_association` / `measured_target_engagement` | **是**——必须走 trusted_run + ledger + output-hash 绑定 | 较高 → 通道验稳后再做 |

**结论:第一个 wrapper 选 prediction 封顶的(CellOracle / GEARS / scGPT 之一)。** 即使 adapter 有瑕疵,证据也只到 `predicted_effect`,不会误升 measured——用它验证"catalog + adapter + 注册 + 判定"整条链路最安全。

---

## 3. 落地清单(每个 wrapper 照此走)

### Step A — 决定证据类型,查 catalog 是否已有
- 目标证据是 `predicted_effect` / `composition_effect` / `module_effect` / `perturbation_efficiency` 之一?
  → **catalog 已有,跳过 Step A**,直接复用现有 `type_id`。
- 是全新类型(如未来的 GI / trajectory)?
  → 在 `EVIDENCE_CATALOG` 加一行 `EvidenceTypeDefinition`(kind/class/predicate/intrinsic_ceiling/roles/registration)。
  → 若 kind 也是新的,先在 `schema.py` 的 `ArtifactKind`/`EvidencePredicate` 加成员(catalog 会引用)。

### Step B — 写 runner(仅 measured/prediction 需要受控执行时)
- 新建 `workflow/runners/<tool>.py`,**同构复制** pseudobulk:
  - 只接受显式 UID/contrast/replicate 输入,校验非空;
  - 跑工具(可 subprocess 调 R/外部 CLI),写 out_path;
  - 算 input_hashes / execution_hash / output_hashes;
  - `record_trusted_run(...)`;
  - `return` 结构化 dict,**不 import registry、不注册、不写自然语言结论**。

### Step C — 写 adapter/parser
- 把 runner result(或 out_path)映射成 registrar 的命名参数。
- **只做结构抽取**:数值、列名、counts、method 名。
- **禁止**:推断 control、改 scope 权威、生成 "KLF1 上调红细胞基因" 这类 prose、给强度打分。

### Step D — 注册 + 白名单
- 调 `registry.register_<type>(..., execution_hash=result["execution_hash"])`。
- measured 级:在 `policy.trusted_runner_methods` 加一行工具 method 名(与 runner 的 `METHOD` 一致)。
- prediction 级:**不加白名单**(天花板本就封顶,加了也无意义)。

### Step E — 配 null 自测(每个 wrapper 必带)
见 §5。**不带 null 自测的 wrapper 不算完成。**

---

## 4. 两个 worked skeleton

### 4a. Prediction wrapper(第一个,最安全)—— 以 GEARS/CellOracle 为例

```python
# workflow/runners/celloracle_prediction.py
METHOD = "celloracle_v1"           # 不进 trusted_runner_methods(prediction 封顶)
RUNNER_NAME = "celloracle"
RUNNER_VERSION = "celloracle_v1"

def run_celloracle_prediction(workspace, *, perturbation_uid, context_uid, model_inputs, output_path=None) -> dict:
    root = Path(workspace).resolve()
    # ... subprocess 调 CellOracle,写 out_path(预测表)...
    input_hashes = {"grn": file_sha256(grn_path), "expr": file_sha256(expr_path)}
    parameters = {"perturbation_uid": perturbation_uid, "context_uid": context_uid, ...}
    execution_hash = canonical_execution_hash({"runner_name": RUNNER_NAME, "runner_version": RUNNER_VERSION,
                                               "method": METHOD, "input_hashes": input_hashes, "parameters": parameters})
    output_hashes = {"prediction_table": file_sha256(out_path)}
    ledger = record_trusted_run(root, execution_hash=execution_hash, runner_name=RUNNER_NAME,
                                runner_version=RUNNER_VERSION, method=METHOD,
                                input_hashes=input_hashes, output_hashes=output_hashes, parameters=parameters)
    return {"path": str(out_path), "relative_path": ..., "method": METHOD, "model_name": "CellOracle",
            "execution_hash": execution_hash, "execution_ledger_path": ledger["execution_ledger_path"],
            "perturbation": perturbation_uid, "context": context_uid}

# 注册(adapter 就是这几行映射)
registry.register_predicted_effect(
    path=result["relative_path"], model_name=result["model_name"],
    perturbation=result["perturbation"], target_context=result["context"],
    execution_hash=result["execution_hash"],
    metadata={"execution_ledger_path": result["execution_ledger_path"]},
)
# → resolve 最高 predicted_effect,parser 有瑕疵也不会越级
```

### 4b. Measured wrapper —— 以 Milo composition 为例(信任必须 earn)

```python
# workflow/runners/milo_composition.py
METHOD = "milo"                    # 必须 == policy.trusted_runner_methods 里的 "milo"
# ... 同 pseudobulk 骨架:显式 replicate/neighbourhood 输入 → 跑 Milo → out_path
#     → input/execution/output hashes → record_trusted_run → return dict

registry.register_composition_effect(
    path=result["relative_path"], method=result["method"], scope=scope,
    eligibility=eligibility,                      # 含 replicate_scope / control_calibration
    execution_hash=result["execution_hash"],
    metadata={"execution_ledger_path": result["execution_ledger_path"]},
)
# → strict/paper:门核对规范 ledger(writer_id + method + source_sha256∈output_hashes)
#   通过才到 measured_association;composition 不需 guide_power(catalog 已按 predicate 分好)
```

---

## 5. 每个 wrapper 必带的 null 自测

对照 tier 选,**必须变红→修绿**,不是可选:

| 自测 | 适用 | 期望 |
|---|---|---|
| **假 execution_hash / 无 ledger** | measured | claim 只到 observation |
| **自报外部 ledger 路径** | measured | 只到 observation(规范 ledger 才算) |
| **output_hash 不绑本 artifact** | measured | 只到 observation |
| **label-permutation null**(打乱标签重跑) | measured effect 类 | 效应消失 / calibration 不过 → 不升 measured |
| **scope mismatch**(claim 与 artifact UID 不一致) | 全部 | 只到 observation |
| **prediction 不被当 measured** | prediction | 天花板恒为 predicted_effect,即便喂 measured-looking 字段 |

前三条已有 phase1a 版本可直接照抄(`test_phase1a_statistical_safety.py`)。

---

## 6. 建议 wrapper 顺序(风险从低到高)

```
1. CellOracle / GEARS / scGPT   —— prediction 封顶,验证整链路(最安全的第一个)
2. Mixscape                     —— perturbation_efficiency(target engagement),measured 但语义窄
3. Milo / scCODA                —— composition_effect,measured,含 replicate/calibration 全套
4. decoupler / GSEApy           —— curated_enrichment(绑定已注册 measured DE artifact)
5. Augur / cNMF / Hotspot       —— module/global effect
6. SCEPTRE                      —— measured DE 旗舰(R,最重,最后做)
```

每一个都走 §3 的 A–E,且第 1 个跑通后,后面每个 = **catalog 复用/一行 + runner 同构复制 + adapter 映射 + (measured 则)白名单一行 + null 自测**。

---

## 7. 验收标准(单个 wrapper 完成的判据)

1. runner 不 import registry、不产 prose、只返回结构化 dict + ledger 路径。
2. adapter 不改 scope 权威、不打分、不解释生物学。
3. measured 级:白名单 method 名与 runner `METHOD` 一致;strict/paper 下经真 runner 产物可达 measured。
4. §5 的 null 自测全绿(尤其"假 ledger / 自报路径 / output_hash 不绑"三条对 measured 必配)。
5. prediction 级:喂任何 measured-looking 字段,天花板仍恒为 predicted_effect。
6. 全量测试保持全绿。

## 8. 不变量复述(wrapper 不能破坏的底线)

- **信任只从受控执行 + 绑定输出文件赚取**(§1 的 ①);artifact 自报字段一律不产生信任。
- **catalog 是证据类型唯一真源**;新类型只在 catalog 加行,不在 resolver/registry/MCP 到处手写分支。
- **runner/adapter/registrar/gate 四层各司其职**,任何一层都不碰"强度"与"生物学意义"——强度只由门按结构化事实判定。
