# 进度记录

分阶段实施，每阶段完成后停下来交付、复核，再进入下一阶段。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 数据基础、语义定义、人工 fixture 与评测划分规则 | ✅ 已完成（阶段 1 + 阶段 1.1，2026-09-13） |
| 2 | 结构化 AnalysisPlan 校验、确定性 SQL 编译器、统一只读安全执行器 | ⬜ 未开始 |
| 3 | LangGraph 单轮指标查询、口径澄清与有限错误处理 | ⬜ 未开始 |
| 4 | 有限多步对比与贡献拆解、多轮追问与持久化 | ⬜ 未开始 |
| 5 | 结论证据校验、受控图表、Streamlit 界面与运行记录 | ⬜ 未开始 |
| 6 | 对照实验、独立保留集评测、离线 CI 与发布检查 | ⬜ 未开始 |

**项目定位（阶段 1.1 更新）**：基于 LangGraph 与业务语义层的经营分析 Agent。
核心指标的默认路径是「自然语言 → 结构化分析计划 → 确定性 SQL 编译 → 安全执行 →
结果与证据」。自由 Text-to-SQL **只作为评测基线**，不是默认执行路径。
不实现任意 Python 执行、多 Agent、任意文件上传、预测或生产数据库接入。
贡献拆解只是数值分解，**不得表述为因果推断**。

---

## 需求 / 阶段 / 代码 / 测试 / 验证状态 追踪表

图例：✅ 已实现并验证 ｜ 🟡 部分实现 ｜ ⬜ 待实现 ｜ ⚠️ 已实现但未验证

### 数据基础（阶段 1）

| 需求 | 代码 | 测试 | 状态 |
|---|---|---|---|
| 四表模型、粒度、主外键 | `eda/data/schema.sql`、`eda/domain/models.py` | `test_schema_constraints.py` | ✅ |
| 金额整数分、STRICT 表、CHECK 约束 | `eda/data/schema.sql` | `test_schema_constraints.py` | ✅ |
| 客户表不含个人敏感数据 | `eda/domain/models.py`、`eda/data/generator.py` | `test_semantic_config.py::test_no_dimension_sources_a_customer_registration_field` | ✅ |
| 手工 fixture + 人工推导期望值 | `eda/data/fixtures/*.csv` | `tests/data/expected_fixture_metrics.json`、`test_fixture_metrics.py` | ✅ |
| 固定种子演示数据、不依赖机器日期 | `eda/data/generator.py` | `test_generator.py` | ✅ |
| 建库脚本、默认拒绝覆盖 | `eda/data/build_db.py` | `test_build_db.py` | ✅ |
| 只读连接（mode=ro + query_only） | `eda/db.py` | `test_schema_constraints.py` | ✅ |
| 单一 SQL 执行入口 | `eda/db.py` | `test_project_constraints.py::test_sqlite_connect_lives_only_in_eda_db` | ✅ |

### 语义层（阶段 1.1）

| 需求 | 代码 | 测试 | 状态 |
|---|---|---|---|
| `semantic/metrics.yaml`（ID/中文名/同义词/单位/基表粒度/操作/输入字段/过滤/允许维度/零分母） | `semantic/metrics.yaml` | `test_semantic_config.py` | ✅ |
| `semantic/dimensions.yaml`（ID/来源字段/类型/允许操作/拆分比率展示名） | `semantic/dimensions.yaml` | `test_semantic_config.py` | ✅ |
| 地区必须用 `order_region` | `semantic/dimensions.yaml` | `test_semantic_config.py`、`test_semantic_consistency.py::test_region_metrics_would_differ_if_signup_region_were_used` | ✅ |
| `semantic/relationships.yaml`（关联键/基数/重复累计注意） | `semantic/relationships.yaml` | `test_semantic_config.py`、`test_semantic_consistency.py` | ✅ |
| `schema_version` 显式声明与校验 | `eda/semantic/models.py` | `test_semantic_config.py::test_unsupported_schema_version_is_rejected` | ✅ |
| Pydantic 校验：重复 ID / 未知字段 / 悬空引用 / 无效关联键 / 无效操作 / 展示名封闭词表 | `eda/semantic/models.py` | `test_semantic_config.py`（负例覆盖） | ✅ |
| 配置是受控资产，不接收上传 | `eda/semantic/loader.py`（`safe_load`，无 CLI 开关） | `test_semantic_config.py::test_no_cli_lets_a_user_point_at_another_semantic_directory` | ✅ |
| 不用 eval/exec，无表达式执行器 | `eda/metrics/operations.py`（三个手写函数） | `test_project_constraints.py`、`test_semantic_config.py::test_semantic_config_declares_no_executable_expression` | ✅ |
| 单一业务定义来源（不维护两份公式） | `eda/metrics/definitions.py` 从语义层派生 | `test_semantic_consistency.py`（配置↔代码↔数据库↔独立参考查询） | ✅ |
| 报表从统一定义读元数据 | `eda/metrics/report.py` | `test_report_cli.py`、`test_semantic_consistency.py` | ✅ |
| 口径统一为「有效订单成交额（简化口径）」 | `semantic/metrics.yaml` | `test_config_and_definitions.py::test_gmv_metric_is_named_effective_order_gmv` | ✅ |
| 日期闭区间规则明确 | `semantic/metrics.yaml`、`eda/metrics/definitions.py` | `test_semantic_consistency.py::test_date_range_includes_both_endpoints` | ✅ |
| 类别订单数不可加 | `semantic/dimensions.yaml` | `test_fixture_metrics.py::test_category_breakdown_warns_that_order_counts_overlap` | ✅ |
| 分类/商品比率的分子只含该类别/商品金额，展示为「订单平均贡献额」 | `semantic/metrics.yaml`（caveat）、`semantic/dimensions.yaml`（`aov_display_name_zh`）、`build_breakdown_sql` | `test_report_cli.py`、`test_fixture_metrics.py::test_breakdowns_match_the_hand_calculation` | ✅ |
| 演示数据只声明覆盖范围，不声明每日完整 | `semantic/dimensions.yaml`（date 维度说明） | `test_semantic_consistency.py::test_date_breakdown_only_returns_days_that_have_orders` | ✅ |
| 评测划分规范（fixture / dev / heldout） | `evaluation/README.md` | `test_project_constraints.py::test_runtime_code_never_reads_the_held_out_evaluation_set` | ✅（规范已定，数据集待建） |

### 尚未实现（按阶段）

| 需求 | 阶段 | 代码 | 测试 | 状态 |
|---|---|---|---|---|
| AnalysisPlan schema 与校验 | 2 | `eda/plan/`（待建） | — | ⬜ |
| 确定性 AnalysisPlan → SQL 编译器 | 2 | `eda/sql/compiler.py`（待建） | — | ⬜ |
| SQLGlot AST 安全校验 + 表白名单 | 2 | `eda/sql/validator.py`（待建） | — | ⬜ |
| 统一只读安全执行器（行数/超时上限） | 2 | `eda/sql/executor.py`（待建） | — | ⬜ |
| SQL 注入 / 绕过 对抗测试 | 2 | — | `tests/test_sql_executor_adversarial.py`（待建） | ⬜ |
| DeepSeek 客户端（配置驱动） | 3 | `eda/llm/client.py`（待建） | — | ⬜ |
| LangGraph 单轮指标查询 | 3 | `eda/graph/`（待建） | — | ⬜ |
| 口径澄清（歧义时先问） | 3 | `eda/graph/clarify.py`（待建） | — | ⬜ |
| 有限错误处理与修复次数上限 | 3 | `eda/graph/repair.py`（待建） | — | ⬜ |
| 有限多步对比（`compare` 操作） | 4 | `eda/plan/multistep.py`（待建） | — | ⬜ |
| 贡献拆解（`contribution`，仅数值分解） | 4 | `eda/plan/multistep.py`（待建） | — | ⬜ |
| checkpoint 持久化与会话隔离 | 4 | `eda/graph/checkpoint.py`（待建） | — | ⬜ |
| 结论证据校验 | 5 | `eda/audit/`（待建） | — | ⬜ |
| 受控确定性图表 | 5 | `eda/viz/`（待建） | — | ⬜ |
| Streamlit 界面与运行记录 | 5 | `app/streamlit_app.py`（待建） | — | ⬜ |
| 开发集 | 3 | `evaluation/dev/`（待建） | — | ⬜ |
| 保留测试集 | 6 | `evaluation/heldout/`（待建） | — | ⬜ |
| 对照实验（语义层 vs 自由 Text-to-SQL 基线） | 6 | `evaluation/`（待建） | — | ⬜ |
| 离线 CI | 6 | `.github/workflows/`（待建） | — | ⬜ |
| 打包发布检查（`semantic/*.yaml` 未进 wheel） | 6 | `pyproject.toml` | — | ⬜ |
| DeepSeek 模型名 / 鉴权 / 响应格式的真实验证 | 3 | `eda/config.py`（值已就位） | 需人工执行 `live_llm` 测试 | ⚠️ 未验证 |
| Python 3.11 上的运行验证 | — | — | — | ⚠️ 本机无 3.11，未验证 |
| 非 Windows 平台验证 | — | — | — | ⚠️ 未验证 |

---

## 环境事实（实际执行结果，非计划）

| 项目 | 实际情况 |
|---|---|
| 操作系统 | Windows 11 (10.0.22631)，PowerShell |
| 计划的 Python | 3.11 |
| **实际的 Python** | **3.12.6**（`py -3.12`，项目内 `.venv`）。本机只有 3.12 与 Anaconda 3.10.9，**没有 3.11**；未安装新解释器 |
| SQLite | 3.45.3（随 CPython 3.12.6），满足 STRICT 表所需的 >= 3.37 |
| 依赖检查 | `python -m pip check` → `No broken requirements found.`（exit 0） |
| Git | 分支 `feat/semantic-foundation`，阶段 1 已提交（commit `3be0f29`，39 文件）。阶段 1.1 的改动**未提交**，按约束未执行 commit / push / merge / tag |
| 现有数据库 | `data/business.db`、`data/fixture.db` 保持原样，阶段 1.1 未删除、未覆盖、未重新生成；测试全部使用 pytest 临时目录 |

### 依赖文件的用途（已核对文件实际格式，非凭文件名推断）

| 文件 | 实际格式 | 用途 |
|---|---|---|
| `requirements.txt` | 手工维护的直接依赖清单，`==` 固定版本 | 只列本项目直接 import 的包，简短可评审 |
| `requirements.lock.txt` | `pip freeze` 输出：纯 `name==version`，**无 hash**，**不是** pip-tools / uv 锁文件 | 完整环境（含间接依赖），平台相关（含 `pyarrow`、`sqlite-vec` 等 win_amd64 wheel），在 CPython 3.12.6 / Windows 上生成 |

与锁文件格式匹配的重建命令：

```cmd
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

只装直接依赖、让 pip 自行解析间接依赖：

```cmd
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

阶段 1.1 把 `PyYAML` 从**间接依赖提升为直接依赖**（`eda/semantic/loader.py` 直接
import 它）。它本来已经作为 `langchain-core` / `streamlit` 的间接依赖存在于环境中，
版本 6.0.3，所以本次没有安装任何新包，`requirements.lock.txt` 与环境仍逐字节一致。

### DeepSeek 接口核对

已查阅官方文档 https://api-docs.deepseek.com/ （2026-09-13）：

- Base URL（OpenAI 兼容）：`https://api.deepseek.com`
- 当前模型名：`deepseek-flash`、`deepseek-v4-pro`
  （旧名 `deepseek-v4-flash` 仍被接受但对应模型已下线；文档未再列出 `deepseek-chat`）

这些值写在 `.env.example` 与 `eda/config.py` 的默认值里，**没有硬编码进业务逻辑**。
阶段 1 与 1.1 都没有发起任何模型调用，因此上述模型名**尚未经过真实调用验证**。

---

## 阶段 1.1 交付内容

### 新增文件

```
semantic/metrics.yaml
semantic/dimensions.yaml
semantic/relationships.yaml
eda/semantic/__init__.py
eda/semantic/models.py
eda/semantic/loader.py
eda/metrics/operations.py
evaluation/README.md
tests/test_semantic_config.py
tests/test_semantic_consistency.py
```

### 修改文件

```
eda/metrics/definitions.py        # 改为语义层的编译产物；新增 resolve_metric / FILTER_DIMENSIONS
eda/metrics/core.py               # 字段重命名 + 兼容别名；说明文字改为读配置
eda/metrics/report.py             # 标签/单位/口径全部来自配置；修正中文宽字符对齐
eda/metrics/__init__.py           # 导出新增符号
tests/test_config_and_definitions.py  # 注册表键改用 canonical id；新增命名断言
tests/test_project_constraints.py     # 新增：语义配置不含答案、不含可执行表达式、不触达保留集
pyproject.toml                    # 新增 PyYAML 直接依赖、eda.semantic 包、打包说明
requirements.txt                  # 新增 PyYAML；补充两个依赖文件的用途与重建命令
README.md / docs/architecture.md / docs/metrics.md / docs/progress.md
```

### 实际执行结果

```
.venv\Scripts\python.exe -m pip check
-> No broken requirements found.（exit 0）

.venv\Scripts\python.exe -m pytest -q
-> 226 passed

新增的语义层测试（test_semantic_config.py + test_semantic_consistency.py）
-> 105 passed

阶段 1 的 7 个原有测试文件（含阶段 1.1 新增断言）
-> 121 passed
```

关系元数据方向修正（2026-09-13）：`fan_out` 统一定义为
`from_table LEFT JOIN to_table` 时左侧输入行是否被放大。
`many_to_one` / `one_to_one` 必须为 `none`，`one_to_many` 必须为
`duplicates_left`。因此 `order_items__orders` 从错误的
`duplicates_left` 修正为 `none`；订单头字段在既有明细行上重复出现的粒度风险仍保留在
`double_count_note_zh`，并继续要求订单数使用 `COUNT(DISTINCT order_id)`、未来订单头金额
不得在明细粒度直接 `SUM`。新增 3 个展开后的测试用例验证该基数矩阵，完整测试由
215 增至 218。

拆分比率展示口径修正（2026-09-13）：在 `DimensionSpec` 中新增严格字段
`aov_display_name_zh`，只允许「客单价」或「订单平均贡献额」。`month` / `date` /
`region` / `channel` 声明为「客单价」，`category` / `product` 声明为
「订单平均贡献额」。`report.py` 只读取该受控元数据，不包含维度 ID 特判。
新增测试逐项验证六个维度的表头，并用原 fixture 期望值重新核对类别成交额、去重订单数与
比率数值；完整测试由 218 增至 226。指标公式、fixture 与期望值文件均未修改。

回归口径核对：阶段 1 的 106 项全部保留并通过，没有删除或放宽任何断言。
原有测试文件从 106 变成 121，增加的 15 条来源清楚：

| 来源 | 条数 |
|---|---|
| `test_no_dynamic_code_execution` 按文件参数化，`eda/` 下新增 4 个 .py 文件（16 → 20） | +4 |
| `test_project_constraints.py` 新增守护：语义配置不含 fixture 答案 / 不含可执行表达式 / 运行时不触达保留集 | +3 |
| `test_config_and_definitions.py` 新增 `test_gmv_metric_is_named_effective_order_gmv` | +1 |
| `test_report_cli.py` 新增六个维度的表头用例及一条 fixture 数值不变校验 | +7 |

唯一被**修改**的原有测试是
`test_config_and_definitions.py::test_revenue_definition_is_labelled_as_simplified_not_net`：
`METRIC_REGISTRY["revenue_cents"]` 改为 `resolve_metric("revenue_cents")`（因为注册表现在
按 canonical id 建键），并额外断言它解析到 `effective_order_gmv_cents`。
原有的两条断言（名称含「简化」、caveat 含「净收入」）逐字保留，断言只增不减。
`tests/data/expected_fixture_metrics.json` 与 `eda/data/fixtures/*.csv` **零改动**，
`data/business.db`、`data/fixture.db` 的修改时间与阶段 1 建库时一致，未被触碰。

报表输出人工核对（fixture 库，全窗口）：
`有效订单成交额（简化口径） 4,552.00 元 (455200 分) / 有效订单数 5 单 / 客单价 910.40 元`，
与 `docs/metrics.md` 第 6 节的手工推导逐项一致。
按 `category` 拆分显示「订单平均贡献额(元)」（例如手机数码 730.67），
按 `region` 拆分仍显示「客单价(元)」（例如华东 744.33）；仅表头变化，数值未变。

### 重命名映射（阶段 1 → 阶段 1.1）

| 阶段 1 标识符 | 阶段 1.1 canonical | 兼容方式 |
|---|---|---|
| 指标 `revenue_cents` | `effective_order_gmv_cents` | `semantic/metrics.yaml` 的 `legacy_ids`；`resolve_metric("revenue_cents")` 可解析 |
| `CoreMetrics.revenue_cents` | `CoreMetrics.effective_order_gmv_cents` | 保留只读别名属性 |
| `CoreMetrics.revenue_yuan` | `CoreMetrics.gmv_yuan` | 保留只读别名属性 |
| `BreakdownRow.revenue_cents` | `BreakdownRow.effective_order_gmv_cents` | 保留只读别名属性 |
| `Breakdown.total_revenue_cents` | `Breakdown.total_effective_order_gmv_cents` | 保留只读别名属性 |
| 展示名「营业额（简化口径）」 | 「有效订单成交额（简化口径）」 | 无需兼容，仅展示文本 |

别名保留是为了让阶段 1 的回归测试与期望值文件零改动。如果你希望彻底移除这些别名，
需要同步更新 `tests/data/expected_fixture_metrics.json` 的键名与
`tests/test_fixture_metrics.py` 的属性访问——这是一个可选的清理项，不是必需。

### 阶段 1.1 未实现 / 未验证

- **通用 AnalysisPlan → SQL 编译器未实现**（按要求留给阶段二）。当前只有
  `build_core_metrics_sql` / `build_breakdown_sql` 两个固定形状的 SQL 生成函数；
- `compare`、`time_series`、`contribution` 三个分析操作在配置里声明为「业务支持」，
  但代码**未实现**，请求时会抛 `UnsupportedOperationError`；
- 开发集与保留集**尚未生成**，`evaluation/README.md` 只定义了规范；
- 没有运行任何模型评测，没有任何 LLM 调用；
- `semantic/*.yaml` 位于仓库根目录，**不会被打进 wheel**；当前只支持从源码目录运行
  （`pytest` 用 `pythonpath = ["."]`）。打包方案留给阶段六；
- 语义层只在 Windows / SQLite 3.45.3 / CPython 3.12.6 上验证过。

---

## 阶段 1 交付内容（保留记录）

### 入口

| 入口 | 作用 |
|---|---|
| `python -m eda.data.build_db` | 建库。`--dataset demo/fixture`、`--db`、`--seed`、`--start-date`、`--end-date`、`--orders`、`--customers`、`--force-overwrite` |
| `python -m eda.metrics.report` | 只读报表，人工核对口径。`--db`、`--start`、`--end`、`--region`、`--category`、`--by`、`--limit`、`--show-definitions` |
| `python -m pytest` | 测试。不访问网络，不调用 LLM |

### 阶段 1 实际执行结果

```
.venv\Scripts\python.exe -m pytest -q
-> 106 passed（用户本机复现结果一致）

.venv\Scripts\python.exe -m eda.data.build_db --dataset demo
-> customers 400 / products 30 / orders 3000 / order_items 5486，exit 0

.venv\Scripts\python.exe -m eda.data.build_db --dataset demo     （第二次）
-> "target database already exists ... Refusing to touch it"，exit 2，原库未被修改

.venv\Scripts\python.exe -m eda.data.build_db --dataset fixture
-> customers 4 / products 5 / orders 8 / order_items 12，exit 0
```

阶段 1 开发过程中有 2 个测试先失败后修正：
`test_generator_never_reads_the_system_clock` 和
`test_expected_answers_are_not_baked_into_runtime_code` 最初用纯文本匹配源码，
把文档字符串里的说明文字误判成违规。修法是把检查改成在**语法树**上做（更精确、更严格），
而不是放宽断言。
