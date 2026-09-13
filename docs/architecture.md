# 架构设计

本项目是一个**本地可复现、可测试的作品集项目**，不声称生产就绪。

**项目定位（阶段 1.1 更新）**：基于 **LangGraph 与业务语义层** 的经营分析 Agent。

不是「把自然语言翻译成 SQL」，而是「把自然语言映射到受控的业务语义，再由确定性代码
编译成 SQL」。这个区别决定了后面所有设计。

---

## 1. 默认执行路径

核心指标查询的默认路径是：

```
自然语言问题
   → 结构化分析计划（AnalysisPlan，受 Pydantic 与语义层校验）
   → 确定性 SQL 编译（无模型参与，纯代码）
   → 统一只读安全执行
   → 结果 + 证据（实际 SQL、口径声明、行数、耗时）
```

**自由 Text-to-SQL 只作为评测基线**，用来回答「语义层与确定性编译到底带来了多少收益」，
它**不是**默认执行路径。

明确不做的事：

- 不实现任意 Python 执行（无 `eval` / `exec` / 模型可控的 `subprocess`）；
- 不做多 Agent 系统；
- 不支持任意文件上传（包括不接收用户上传的语义配置）；
- 不做预测 / 预报；
- 不接入生产数据库；
- **贡献拆解只是数值分解，不得表述为因果推断**（「哪个地区贡献了多少变化量」可以说，
  「因为某地区导致了下降」不可以说）。

---

## 2. 最终阶段安排

| 阶段 | 内容 |
|---|---|
| 1 | 数据基础、语义定义、人工 fixture 与评测划分规则 |
| 2 | 结构化 AnalysisPlan 校验、确定性 SQL 编译器、统一只读安全执行器 |
| 3 | LangGraph 单轮指标查询、口径澄清与有限错误处理 |
| 4 | 有限多步对比与贡献拆解、多轮追问与持久化 |
| 5 | 结论证据校验、受控图表、Streamlit 界面与运行记录 |
| 6 | 对照实验、独立保留集评测、离线 CI 与发布检查 |

阶段 1 与 1.1 已完成。逐项实现 / 测试 / 验证状态见
[`docs/progress.md`](progress.md) 的追踪表。

---

## 3. 目标形态（阶段 6 完成后）

```
                 ┌──────────────────────────────────────────────┐
   用户提问  ──▶ │  Streamlit UI（阶段 5）                       │
                 └───────────────────┬──────────────────────────┘
                                     ▼
                 ┌──────────────────────────────────────────────┐
                 │  LangGraph 图（阶段 3 / 4）                   │
                 │                                              │
                 │  parse_plan → validate_plan → compile_sql    │
                 │      ▲              │              │         │
                 │  clarify（口径歧义） │              ▼         │
                 │      │              └──────▶ execute_sql     │
                 │  repair（有限次）◀── 失败 ──────┘   │         │
                 │                                     ▼        │
                 │                        verify_evidence       │
                 │                                     │        │
                 │                                     ▼        │
                 │                            explain_result     │
                 └────┬──────────────────┬───────────────┬──────┘
                      │                  │               │
              LLM（仅产出结构化     语义层（权威口径）   唯一 SQL 执行通道
              计划与解释文本）           │               │
                      ▼                  ▼               ▼
         ┌────────────────────┐ ┌──────────────┐ ┌──────────────────────┐
         │ eda/llm（阶段 3）   │ │ semantic/*.  │ │ eda/sql/executor      │
         │ key/base_url/model │ │ yaml +       │ │ （阶段 2）             │
         │ 全部来自配置        │ │ eda/semantic │ │ SQLGlot AST 校验       │
         └────────────────────┘ └──────┬───────┘ └──────────┬───────────┘
                                       │                     ▼
                                       │         ┌──────────────────────┐
                                       └────────▶│ eda/metrics（编译）   │
                                                 │ 确定性 SQL 生成       │
                                                 └──────────┬───────────┘
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │ eda/db.py            │
                                                 │ sqlite3 唯一出入口    │
                                                 └───┬──────────────┬───┘
                                                     │              │
                                      business.db（只读 mode=ro）  checkpoints.db
                                                                  （仅应用写入，
                                                                    模型不可见）
```

LLM 在这个架构里只做两件事：**把自然语言映射成结构化计划**，以及**把已经算好的结果
写成解释文字**。它不产出业务定义，也不决定 SQL 结构。

---

## 4. 版本化语义层（阶段 1.1 实现）

### 4.1 为什么需要它

阶段 1 的口径写在 Python 字面量里。这在只有 5 个指标时能工作，但一旦 Agent 要
「理解用户说的『成交额』指哪个指标」、「判断某个维度能不能用」、
「知道哪个指标在哪个维度上不可加」，这些知识必须是**数据**，而不是散落在代码里的文字。

### 4.2 三份配置

| 文件 | 内容 |
|---|---|
| `semantic/metrics.yaml` | 指标：ID、中文名、同义词、单位、基表与粒度、受支持的计算操作、输入字段、过滤口径、允许维度、零分母策略、注意事项 |
| `semantic/dimensions.yaml` | 维度：ID、来源视图与字段、类型、允许取值、允许操作（`group_by` / `filter`）、不可加指标声明 |
| `semantic/relationships.yaml` | 表粒度与主键、表间关联键与基数、重复累计注意事项、三个预关联分析视图的列与状态过滤 |

三份文件都带 `schema_version`（当前 `1.0.0`），版本不匹配会在加载时直接报错。

### 4.3 「两份公式」问题怎么解决

这是引入配置层最大的风险：配置说一套，代码算另一套。项目的处理方式是**分工**而不是复制：

- **业务定义的权威来源是 YAML**：什么状态算有效、金额取哪一列、哪个维度能筛选、
  分母为零怎么办——只在 YAML 里说一次；
- **Python 只实现有限且封闭的计算操作**：`eda/metrics/operations.py` 里只有
  `sum`、`count_distinct`、`ratio` 三个手写函数。**没有表达式解析器，没有模板语言，
  没有 `eval`**。想加第四种算法必须改代码并补测试；
- **`eda/metrics/definitions.py` 退化为「编译产物」**：它从语义模型读出 spec，
  调用操作注册表渲染出 SQL 文本，不再自己声明任何口径；
- **报表从统一定义读元数据**：`eda/metrics/report.py` 的标签、单位、注意事项全部来自
  YAML，它不可能把一个指标描述成与计算方式不同的东西。

一致性由测试强制，不靠自觉（`tests/test_semantic_consistency.py`）：

| 声明 | 校验对象 |
|---|---|
| 视图 / 表的列清单、主键 | 真实数据库的 `PRAGMA table_info` |
| 表间关联键 | 真实数据库的 `PRAGMA foreign_key_list` |
| 视图的状态过滤 | 视图实际返回的 `DISTINCT status` |
| 维度的允许取值 | 生成 DDL 的 `eda/domain/enums.py` |
| 指标的聚合方式 | 操作注册表渲染出的 SQL 文本 |
| 整体口径 | 手写的**独立参考查询**（直接查基表，绕开分析视图） |
| `group_by` 维度集合 | `BREAKDOWN_DIMENSIONS` 的键 |
| `filter` 维度集合 | `MetricFilters` 的字段 |

最后一条独立参考查询是关键：如果视图定义本身写错了，前面几条比对都可能一起错，
只有「用另一条路径重新算一遍」才能发现。

### 4.4 「受支持」与「已实现」是两回事

`metrics.yaml` 里的 `supported_operations` 是**业务陈述**（这个指标在业务上支持对比、
贡献拆解等操作）；当前**代码实现了什么**由 `eda.metrics.operations.
IMPLEMENTED_ANALYSIS_OPERATIONS` 声明，目前只有 `total` 和 `breakdown`。

请求一个尚未实现的操作会抛 `UnsupportedOperationError`，而不是静默给出近似结果。
`report --show-definitions` 会分别打印「已实现操作」和「待实现」。

### 4.5 语义配置的信任边界

- 配置是**随代码受控维护的可信资产**，走代码评审，不接收用户上传；
- 加载只走 `yaml.safe_load`，无法构造任意 Python 对象；
- 没有任何命令行参数或环境变量可以把加载器指向别的目录（有测试断言）；
- 运行时代码不写入 `semantic/`（有测试断言）；
- 列名进入 SQL 前有两道独立闸门：schema 层限制为 `^[a-z][a-z0-9_]*$`，
  交叉校验要求它必须是所声明基表视图的列。

---

## 5. 安全边界（贯穿所有阶段）

### 5.1 绝不执行模型生成的代码

模型只允许输出两种东西：**结构化 JSON**（分析计划）和**解释文字**。
阶段 2 之后连 SQL 都由代码编译，模型不再直接产出执行用 SQL（自由 Text-to-SQL 基线除外，
它同样必须过安全执行器）。代码里没有 `eval` / `exec` / `compile` / `subprocess`，
`tests/test_project_constraints.py::test_no_dynamic_code_execution` 在语法树上逐文件强制。

### 5.2 SQL 只有一条执行通道

`eda/db.py` 是整个项目里唯一出现 `sqlite3.connect` 的模块，并由
`test_sqlite_connect_lives_only_in_eda_db` 守住。阶段 2 会在它之上加一层
`eda/sql/executor.py`：

- 用 **SQLGlot 解析成 AST** 后判断语句类型，而不是看字符串前缀或写正则
  （`SELECT` 前缀可以被 CTE、注释、多语句、`PRAGMA` 绕过）；
- 必须单语句；只允许 `SELECT`（含 `WITH ... SELECT`）；
- 表名必须落在白名单内，且**不包含状态库**；
- 强制行数上限与执行超时；
- 参数一律绑定，绝不把值拼进 SQL 字符串（阶段 1.1 的 `build_core_metrics_sql`
  已经是这个写法，并有测试断言 SQL 文本里不含字面值）。

数据库连接自身也是只读的双保险：`mode=ro` URI（SQLite 层面拒绝写，且不会创建文件）
加 `PRAGMA query_only = ON`。测试验证即使 `query_only` 被关掉，`mode=ro` 仍然生效。

### 5.3 两个数据库彻底分开

| 数据库 | 写入者 | 模型可见性 |
|---|---|---|
| `data/business.db` | 只有 `python -m eda.data.build_db` | 只读查询，表结构对模型可见 |
| `data/checkpoints.db` | 只有 LangGraph（阶段 4） | **完全不可见**，不在表白名单里，生成的 SQL 无法触达 |

### 5.4 SQL 可执行 ≠ 答案正确

这是两件事，项目里分别处理：

- **可执行性**由安全执行器保证（阶段 2）；
- **业务正确性**由「手工 fixture + 人工推导的期望值」保证（阶段 1 已落地），
  阶段 6 的独立保留集评测再在此基础上判分；
- **结论与证据一致性**由阶段 5 的证据校验负责（防止「数字对、话说错」）。

评测数据的三层划分与保留集纪律见 [`evaluation/README.md`](../evaluation/README.md)。

期望答案只存在于 `tests/data/expected_fixture_metrics.json`，
运行时代码与语义配置里都没有硬编码的题目、期望 SQL 或期望答案；
`test_expected_answers_are_not_baked_into_runtime_code` 与
`test_semantic_config_contains_no_fixture_answers` 会从期望文件里读出所有大额数字，
反向扫描 `eda/**.py` 与 `semantic/*.yaml`。

### 5.5 密钥与费用

API Key、Base URL、模型名全部来自 `eda/config.py`（pydantic-settings 读 `.env`）。
Key 用 `SecretStr` 包装，打印 settings 或日志都只会看到掩码。
`.env.example` 只含占位值，有测试断言这一点。

真实模型调用必须由人显式触发：自动测试套件默认不选中 `live_llm` 标记，
`ENABLE_LIVE_LLM_TESTS` 默认 `false`，因此 `pytest` 不会产生任何外部调用或费用。

---

## 6. 模块划分

```
semantic/                  # 版本化语义层（受控资产，业务定义权威来源）
  metrics.yaml
  dimensions.yaml
  relationships.yaml

evaluation/
  README.md                # 评测划分规范（保留集纪律）

eda/
  __init__.py
  config.py                # Settings（.env）；key/base_url/model 都在这里
  db.py                    # 唯一的 sqlite3 出入口：只读连接 + 执行原语
  semantic/
    models.py              # 语义层 Pydantic schema + 交叉引用校验
    loader.py              # safe_load + 版本校验（唯一加载入口）
  domain/
    enums.py               # 状态 / 地区 / 类别 / 渠道 的受控词表（DDL 也用同一批值）
    models.py              # Pydantic 行模型 + Dataset（含引用完整性校验）
  data/
    schema.sql             # DDL：STRICT 表、CHECK、主外键、3 个视图
    schema.py              # DDL 读取 + 期望对象清单
    fixtures/*.csv         # 手工小数据（可人工复核）
    fixture_dataset.py     # 读取并校验手工数据
    generator.py           # 固定种子 / 固定日期窗口的演示数据生成器
    loader.py              # 写库 + 建库后自检
    build_db.py            # CLI：建库，默认拒绝覆盖
  metrics/
    operations.py          # 封闭的计算操作集合（sum / count_distinct / ratio）
    definitions.py         # 语义层的编译产物：渲染 SQL 文本 + 绑定参数
    core.py                # 核心指标与单维度拆分的计算
    report.py              # 只读命令行报表（人工核对用，无 LLM）
```

**定义、编译、执行三者分离**是这里的关键：YAML 负责定义，`operations.py` +
`definitions.py` 负责编译成 SQL 文本，`eda/db.py`（阶段 2 后是安全执行器）负责执行。
好处是阶段 2 的 AnalysisPlan 编译器可以直接复用同一套语义与同一组操作，
而不是再拼一遍公式。

---

## 7. 后续阶段的落点（尚未实现）

| 阶段 | 新增模块 | 要点 |
|---|---|---|
| 2 | `eda/plan/`、`eda/sql/validator.py`、`eda/sql/executor.py` | AnalysisPlan schema 与校验、确定性 SQL 编译器、SQLGlot AST 校验、表白名单、行数/超时上限、对抗测试 |
| 3 | `eda/llm/client.py`、`eda/graph/` | DeepSeek 客户端（配置驱动）、LangGraph 单轮流程、口径澄清、有限错误处理 |
| 4 | `eda/graph/checkpoint.py`、`eda/plan/multistep.py` | 有限多步对比与贡献拆解（数值分解，非因果）、checkpoint 持久化、`thread_id` 会话隔离 |
| 5 | `app/streamlit_app.py`、`eda/viz/`、`eda/audit/` | 结论证据校验、受控确定性图表、运行记录与追溯 |
| 6 | `evaluation/dev/`、`evaluation/heldout/`、`evaluation/runs/` | 对照实验（语义层 vs 自由 Text-to-SQL 基线）、独立保留集评测、离线 CI、发布检查 |

---

## 8. 技术选型与版本

Python 计划写的是 3.11；本机没有 3.11，实际使用 **3.12.6**（LangGraph 1.2.x 要求
`>=3.10`，其余依赖均支持 3.12）。`pyproject.toml` 声明 `requires-python = ">=3.11,<3.14"`。

实际安装并测试过的版本记录在 `requirements.txt`（直接依赖）、`requirements.lock.txt`
（完整解析结果，`pip freeze` 格式）与 `docs/progress.md`。

DeepSeek 的模型名与 Base URL 取自官方文档（https://api-docs.deepseek.com/ ），
不靠记忆猜测；当前文档给出的模型是 `deepseek-flash` 与 `deepseek-v4-pro`，
Base URL 为 `https://api.deepseek.com`（OpenAI 兼容格式）。**尚未经过真实调用验证。**

不引入需求之外的东西：没有微服务、没有 Redis、没有向量数据库、没有多 Agent 框架、
没有额外的 ORM。语义层是三份 YAML 加一个 Pydantic 加载器，不是一个新框架。
