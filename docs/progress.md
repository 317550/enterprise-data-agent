# 进度记录

分阶段实施，每阶段完成后停下来交付、复核，再进入下一阶段。

阶段 4B-3 真实模型验收更新（依据用户手工验证反馈）：deepseek-flash 年份比较成功，
2023 年基期 0、2024 年当期 455200，absolute_change=455200，change_rate=null；
同一 thread/checkpoint 的地区贡献及改按类别贡献均成功，分项合计各为 455200。
三轮 model_call_count 均为 1，query_count 依次为 2/4/4，exit_code 均为 0。
统一使用 conversation-plan-v2、conversation-v2、semantic_version=1.1.0、
reference_date=2024-12-31、timeout_seconds=60。首次 recovered=true 为恢复
in_progress 后处理新输入，不是重放结果；后两轮 recovered=false。
此前发生间歇性 DNS 解析超时；WLAN DNS 从 114.114.114.114 调整为
119.29.29.29、223.5.5.5 后连续三次 HTTPS 探测成功。这是本次环境排查记录，
不据此声称 DeepSeek 夜间停服或全局可用。Key 仅经进程环境变量临时传入，验证后清除。
完整分项见 [阶段 4B-3](stage4b-3-conversation.md)。仅上述三个受控场景完成真实验证；
日历完整不等于数据覆盖已验证，也不证明不存在所有未知安全缺陷。
本次仅更新四份文档，不修改生产代码或测试，不重跑测试；历史阶段 3/4A 独立记录保留。

2026-09-15 阶段 4B-3 增量续做：保留已恢复的 14 个变更文件，直接完整读取两个
未跟踪文件及其余当前源码/差异；分支 feat/comparative-analysis，HEAD 5daf3c4。
没有操作 backup-stage4b3-interrupted stash。比较/环比/贡献已接入原会话图，
复用 4B-2 逐步原语，checkpoint 升为 conversation-v2，语义版本保持 1.1.0。
恢复现场九文件定向为 384 passed、1 failed in 99.55s；失败为新增恢复测试对
JSON 列表和模型 tuple 的直接比较，已改为经 Session 校验后严格比较业务状态。
同时补强构造字段隐藏、损坏比较草稿和模糊时间不得继承旧期间的边界。
最终定向 **393 passed in 99.67s**；完整离线回归 **897 passed in 118.48s**，
原 806 项保留。pip check、UTF-8/AST/尾随空白检查和 diff check 通过；五进程 Fake
演示全部成功，数据库 SHA256/mtime 不变。该离线开发验收当时没有真实模型调用或 Git 提交操作；后续三轮真实验收见上文。
实现、预算、白名单、CLI 演示和最终验收见[阶段 4B-3](stage4b-3-conversation.md)。

2026-09-14 阶段 4A.1 最终审查：保留现有全部未提交修改，实际读取包含未跟踪文件的
10 个 conversation 模块、5 个定向测试文件及相关文档/配置。五节点、条件边、共享
两次调用预算、一次查询、瞬态状态隔离、显式新话题、真实 TurnDecision 适配和锁边界
均经审查。仅修复共享 HTTPS 传输的 URL/连接生命周期异常脱敏缺口，增加 5 个已复现
失败的回归用例，并加强既有默认 CLI 断网断言；未重写图或扩大业务能力。

按指定顺序验证：初始定向 **98 passed in 47.98s**；修复对应 mock 与原单轮测试
**134 passed in 6.25s**；最终定向 **103 passed in 46.48s**；随后完整回归一次
**601 passed in 62.41s**（原 596 项保留，新增 5 项异常边界回归）。pip check 与
git diff --check 均通过；新文件也检查尾随空白。详细节点路径、字段白名单、手工命令、
资产哈希与完整修改清单见[阶段 4A 说明](stage4a-conversation.md)。

LangGraph / SQLite saver / core 实装版本 1.2.11 / 3.1.1 / 4.2.0 与声明和锁文件一致；
requirements 的既有 pins 已满足 pyproject 收紧后的下限，没有新的依赖版本需要改写。
business.db 与 fixture.db 的 SHA256、UTC mtime 均未变化，fixture、标准答案、
语义配置和阶段二代码无 diff。Git 中无环境文件、真实凭据或运行时数据库/锁文件。
保持 `feat/conversation-state`，未 commit/push/merge/tag/切换分支；真实模型调用为零，
未进入阶段 4B。至此停止。

2026-09-14 阶段 4A 更新：在 `feat/conversation-state` 的未提交现场继续，
原有 conversation 协议、合并代码与 13 项测试保留并增量完善。已完成严格协议、
条件继承/替换/清除、最小 StateGraph、独立 SQLite checkpoint、thread 隔离、
澄清续接、程序重启恢复、新话题重置、失败不提升候选，以及同一 thread 忙时拒绝。
请求、预算、原始模型响应与查询结果均放在 Runtime.context，只有有界业务状态持久化。
新增离线多轮 CLI；原单轮接口兼容。详见[阶段 4A 说明](stage4a-conversation.md)。

定向测试 58 passed；完整回归 **554 passed in 56.59s**（exit 0）：
原 488 项保留，新增 58 项多轮测试及 8 项随模块展开的代码约束检查。
使用 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`；系统 Python
的 pytest 7.1.2 不满足项目要求，Windows 测试临时目录需在已授权的沙箱外运行。
pip check / git diff --check 通过。business.db 与 fixture.db SHA256 未变。
未改 fixture、标准答案、语义配置或阶段二安全实现；未调用真实模型；
未 commit / push / merge / tag 或切换分支。阶段 4B 未开始。

2026-09-13 阶段三更新：`eda/agent/` 已实现单轮自然语言规划和 Fake 离线测试。
复用阶段二全链路，不允许模型生成或修复 SQL。默认离线，真实 HTTPS 适配器
只在显式 `--provider real` 时选择；真实烟雾测试仍待用户手工执行。
协议、预算、命令与边界见[阶段三说明](stage3-planning.md)。
最终离线验证：488 passed in 7.87s（exit 0）；原有 374 项全部保留，新增
105 项规划测试及 9 项随新模块展开的代码约束检查。pip check / diff check
均 exit 0。数据库 SHA256 / mtime 未变，未修改 fixture 或期望值；未联网调用模型，
未 commit / push / merge / tag，未切换分支。

2026-09-13 阶段二独立审查：亲自复现基线 311 passed，定向修复后完整测试
374 passed（exit 0）；pip check / git diff --check 均通过。两项示例输出正确，
business.db 与 fixture.db 的 SHA256 / mtime 均未变化。旧记录中“空 dbname
视为 main”的结论已由受连接拓扑约束的兼容替代，详见
[独立审查记录](stage2-review.md)。未提交、推送、切换分支或进入阶段三。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 数据基础、语义定义、人工 fixture 与评测划分规则 | ✅ 已完成（阶段 1 + 阶段 1.1，2026-09-13） |
| 2 | 结构化 AnalysisPlan 校验、确定性 SQL 编译器、统一只读安全执行器 | ✅ 已完成（2026-09-13） |
| 3 | 自然语言 → AnalysisPlan 单轮规划、澄清/拒绝与一次计划修复 | ✅ 离线实现完成；真实烟雾验证待执行 |
| 4 | 多轮追问与持久化；有限多步对比与贡献拆解 | ✅ 4A 与 4B-1/2/3 离线完成（2026-09-15）；4B-3 三个受控场景真实验证成功 |
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
| 单一 SQL 连接入口 | `eda/db.py`（`sqlite3.connect` 仅此文件） | `test_project_constraints.py::test_sqlite_connect_lives_only_in_eda_db` | ✅ |

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

### 安全查询执行（阶段 2）

| 需求 | 代码 | 测试 | 状态 |
|---|---|---|---|
| AnalysisPlan schema 与语义校验（`total` / `breakdown` / 简单排名） | `eda/plan/models.py` | `tests/test_analysis_plan.py` | ✅ |
| 确定性 AnalysisPlan → 参数化 SQL 编译 | `eda/sql/compiler.py` | `tests/test_sql_compiler.py` | ✅ |
| SQLGlot AST 结构与对象访问校验 | `eda/sql/validator.py`、`eda/sql/catalog.py` | `tests/test_sql_validator.py` | ✅ |
| SQLite authorizer 默认拒绝 | `eda/sql/authorizer.py` | `tests/test_sql_executor.py` | ✅ |
| 统一只读执行策略（行数/字节/超时/SQL 长度） | `eda/sql/executor.py`（只调用 `eda.db.connect_readonly`） | `tests/test_sql_executor.py` | ✅ |
| 结构化结果 + 完整性标记（top_n ≠ 截断） | `eda/query/service.py`、`eda/query/models.py` | `tests/test_query_service.py` | ✅ |
| 薄 CLI：读计划 JSON，打印结果 | `eda/query/cli.py`、`examples/plans/` | `tests/test_query_cli.py` | ✅ |
| 现有 metrics/report 经小型适配走统一执行器 | `eda/metrics/core.py` | 既有 `test_fixture_metrics.py` / `test_report_cli.py` | ✅ |
| SQL 注入 / 绕过对抗 | validator + authorizer + 绑定参数 | `test_sql_validator.py`、`test_sql_executor.py`、`test_sql_compiler.py` | ✅ |

### 后续工作与真实验证（旧阶段三方案已被当前协议覆盖）

| 需求 | 阶段 | 代码 | 测试 | 状态 |
|---|---|---|---|---|
| 单轮规划协议与模型接口 | 3 | `eda/agent/models.py`、`planner.py`、`fake.py`、`real.py` | `test_agent_planning.py` | ✅ 离线验证；真实适配器待烟雾验证 |
| 最小 LangGraph / Runtime.context | 4A | `eda/conversation/graph.py` | `test_conversation_graph.py` | ✅ 离线验证 |
| 口径澄清（歧义时先问） | 3 | `eda/agent/service.py` | `test_agent_planning.py` | ✅ |
| 一次计划修复与最多两次调用 | 3 | `eda/agent/service.py` | `test_agent_planning.py` | ✅ |
| 有限多步对比（`compare` 操作） | 4B | `eda/plan/multistep.py`（待建） | — | ⬜ |
| 贡献拆解（`contribution`，仅数值分解） | 4B | `eda/plan/multistep.py`（待建） | — | ⬜ |
| 严格多轮合并与显式清除 | 4A | `eda/conversation/models.py`、`merge.py` | `test_conversation_merge.py` | ✅ |
| checkpoint 持久化、隔离、恢复与并发 | 4A | `eda/conversation/checkpoint.py`、`service.py` | `test_conversation_checkpoint.py`、`test_conversation_cli.py` | ✅ 本机跨进程验证 |
| 多轮 CLI / 显式新话题 / 真实适配器 | 4A.1 | `eda/conversation/fake.py`、`real.py`、`cli.py` | `test_conversation_cli.py`、`test_conversation_real.py` | ✅ Fake/mock 验证；真实烟雾待执行 |
| 结论证据校验 | 5 | `eda/audit/`（待建） | — | ⬜ |
| 受控确定性图表 | 5 | `eda/viz/`（待建） | — | ⬜ |
| Streamlit 界面与运行记录 | 5 | `app/streamlit_app.py`（待建） | — | ⬜ |
| 开发集 | 3 | `evaluation/dev/`（待建） | — | ⬜ |
| 保留测试集 | 6 | `evaluation/heldout/`（待建） | — | ⬜ |
| 对照实验（语义层 vs 自由 Text-to-SQL 基线） | 6 | `evaluation/`（待建） | — | ⬜ |
| 离线 CI | 6 | `.github/workflows/`（待建） | — | ⬜ |
| 打包发布检查（`semantic/*.yaml` 未进 wheel） | 6 | `pyproject.toml` | — | ⬜ |
| DeepSeek 模型名 / 鉴权 / 响应格式的真实验证 | 3 | `eda/agent/real.py` | 需手工执行 `--provider real` CLI | ⚠️ 未验证 |
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
| Git | 当前实现分支 `feat/conversation-state`；全部阶段 4A/4A.1 修改未提交。按约束**未**执行 commit / push / merge / tag |
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

- 通用 AnalysisPlan → SQL 编译器已在阶段 2 落地；阶段 1.1 当时只有
  `build_core_metrics_sql` / `build_breakdown_sql` 两个固定形状的 SQL 生成函数，现仍保留给报表适配；
- `compare`、`time_series`、`contribution` 三个分析操作在配置里声明为「业务支持」，
  但代码**未实现**，请求时会抛 `UnsupportedOperationError`；
- 开发集与保留集**尚未生成**，`evaluation/README.md` 只定义了规范；
- 没有运行任何模型评测，没有任何 LLM 调用；
- `semantic/*.yaml` 位于仓库根目录，**不会被打进 wheel**；当前只支持从源码目录运行
  （`pytest` 用 `pythonpath = ["."]`）。打包方案留给阶段六；
- 语义层只在 Windows / SQLite 3.45.3 / CPython 3.12.6 上验证过。

---

## 阶段 2 交付内容

闭环：`AnalysisPlan JSON` → Pydantic/语义校验 → 确定性编译 → SQLGlot AST 校验 →
SQLite authorizer 只读执行 → 结构化结果。三个边界分开，没有混成一个大类：

| 边界 | 模块 | 只回答什么 |
|---|---|---|
| 业务请求是否合法 | `eda/plan/models.py` | 指标/维度/操作/字段组合是否被语义层批准 |
| SQL 结构与对象访问是否合法 | `eda/sql/validator.py` | 单条 SELECT、批准的表/列/函数、禁止写操作与绕过 |
| 数据库执行层最后防护 | `eda/sql/authorizer.py` | 默认拒绝；只放行 SELECT / 批准 READ / 批准函数 |

`eda/sql/executor.py` 只制定执行策略，连接必须来自 `eda.db.connect_readonly`。
`sqlite3.connect` 仍只出现在 `eda/db.py`。排名就是 `breakdown + order_by + top_n`。
现有 `compute_core_metrics` / `compute_breakdown` / report CLI 经函数内懒导入适配到
`execute_on_connection`，公开签名与数值不变。

资源限制是进程内尽力而为（`set_progress_handler` + 行数/字节预算），不是 OS 沙箱，
也不是 SQLite `timeout`（那只是锁等待）。

### 新增文件

```
eda/plan/{__init__,models}.py
eda/sql/{__init__,catalog,compiler,validator,authorizer,executor}.py
eda/query/{__init__,errors,models,service,cli}.py
examples/plans/fixture_gmv_total.json
examples/plans/fixture_gmv_by_category.json
tests/test_analysis_plan.py
tests/test_sql_compiler.py
tests/test_sql_validator.py
tests/test_sql_executor.py
tests/test_query_service.py
tests/test_query_cli.py
```

### 修改文件

```
eda/db.py                 # 正确编码的 mode=ro URI；分析查询改走执行器
eda/config.py / .env.example  # SQL 长度 / 结果字节 / 单值长度预算
eda/metrics/core.py       # 小型适配：函数内调用 execute_on_connection
pyproject.toml            # packages 增加 eda.plan / eda.sql / eda.query
README.md / docs/architecture.md / docs/progress.md
```

### 实际执行结果

```
.venv\Scripts\python.exe -m pip check
-> No broken requirements found.（exit 0）

.venv\Scripts\python.exe -m pytest -q
-> 311 passed（含审查修补后的定向测试）

.venv\Scripts\python.exe -m eda.query.cli --plan examples\plans\fixture_gmv_total.json --db data\fixture.db
-> metric_value = 455200（与手工推导一致）

.venv\Scripts\python.exe -m eda.query.cli --plan examples\plans\fixture_gmv_by_category.json --db data\fixture.db
-> 手机数码 219200 / 家用电器 125000 / 食品生鲜 60400 / 服饰鞋包 50600
```

阶段 1 + 1.1 的 226 项全部保留通过。阶段 2 首次闭环验收为 297 passed；审查修补后为
311 passed。fixture、期望值文件、`data/business.db` 与 `data/fixture.db` 均未改写。

本阶段未实现：LLM、LangGraph、图表、期间对比、贡献拆解。按约束未 commit / push。

阶段 2 审查修补（同日）：连接失败转为结构化 `db_error`；SQLite 绑定/中断/长度错误分别归
`invalid_params` / `timeout` / `resource_limit`；authorizer 只允许 `main`；标识符
`casefold`；第一版只接受 `JOIN ... ON`；`SQLITE_LIMIT_LENGTH` 按字节配置为
`sql_max_value_bytes`。未覆盖边界见当次交付说明。

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

## 2026-09-14 阶段 4B-1

开始前确认 feat/comparative-analysis、干净工作区、PR #4 合并提交 01b65aa，离线基线 601 passed in 30.36s。新增严格比较计划、完整日历期间、配置驱动可加性和纯 Decimal 比较/贡献计算。三份语义 YAML 与 SUPPORTED_SCHEMA_VERSION 同步升级 1.0.0 → 1.1.0，原 4A checkpoint 明确拒绝，不静默迁移。未修改阶段二执行器、会话图或 CLI；未接入自然语言规划、数据库查询或真实模型。详见 [阶段 4B-1](stage4b-1-comparative.md)。

最终定向 233 passed in 6.62s；完整回归 711 passed in 30.34s（保留原 601 项）。git diff --check 通过。停止于 4B-1，等待 4B-2；未提交或推送。
