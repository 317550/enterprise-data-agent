# 架构设计

本项目是一个**本地可复现、可测试的作品集项目**，不声称生产就绪。

**当前项目定位（阶段三更新）**：基于受控业务语义层的单轮经营分析 Agent。
阶段三采用接口驱动的规划服务，不引入 LangGraph 或第二次模型解释。当前实现以
[阶段三协议](stage3-planning.md) 为准，模型不接触 SQL 或数据库。

不是「把自然语言翻译成 SQL」，而是「把自然语言映射到受控的业务语义，再由确定性代码
编译成 SQL」。这个区别决定了后面所有设计。

---

## 1. 默认执行路径

核心指标查询的默认路径是：

```
自然语言问题
   → PlannerDecision（ready / clarify / refuse，最多一次计划修复）
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
| 3 | 自然语言 → 受控 AnalysisPlan 单轮规划、澄清/拒绝、一次计划修复 |
| 4 | 有限多步对比与贡献拆解、多轮追问与持久化 |
| 5 | 结论证据校验、受控图表、Streamlit 界面与运行记录 |
| 6 | 对照实验、独立保留集评测、离线 CI 与发布检查 |

阶段 1、1.1、2 与阶段三离线实现已完成；真实模型验证待手工执行。逐项状态见
[`docs/progress.md`](progress.md) 的追踪表。

---

## 3. 当前阶段三形态

```text
自然语言问题 + 显式参考日期
  → 只读范围预检 / 确定性日期窗口
  → PlannerModel（默认 Fake；显式 real 才联网）
  → PlannerDecision 严格校验
      clarify / refuse → 安全问题或拒绝文案（不执行）
      无效输出 → 只反馈脱敏错误类别，最多修复一次
      ready → AnalysisPlan 完整校验与日期核对
  → run_analysis_plan（复用阶段二全部防线）
  → AnalysisResult + 确定性说明 / 完整性警告
```

模型只负责规划，不负责计算或解释结果，不具备 SQL 或数据库能力。
执行错误是终止状态，绝不返回模型修复。每请求最多两次模型调用。
完整接口、命令和测试范围见 [stage3-planning.md](stage3-planning.md)。
多轮持久化与后续编排仍未实现，也没有提前建设其框架。

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
`test_sqlite_connect_lives_only_in_eda_db` 守住。阶段 2 已在它之上加上执行策略
`eda/sql/executor.py`：执行器**不得**自己 `connect`，只调用 `connect_readonly`。

三个防护边界分开，不混在一个类里：

1. **AnalysisPlan**（`eda/plan/models.py`）：业务请求是否合法——指标、维度、操作、
   日期闭区间、过滤词表。禁止传入 SQL / 表名 / 表达式。
2. **SQLGlot validator**（`eda/sql/validator.py`）：SQL 结构与对象访问是否合法。
   用 AST 判断语句类型，而不是看字符串前缀或写正则（`SELECT` 前缀可以被 CTE、
   注释、多语句、`PRAGMA` 绕过）。必须单条 SELECT；允许非递归 CTE、INNER/LEFT JOIN
   **仅 `JOIN ... ON`**、`COUNT(*)`；拒绝 `JOIN ... USING`、`SELECT *`、递归 CTE、
   集合运算、窗口函数、写操作、DDL、`ATTACH` / `PRAGMA`、系统表、未批准函数、
   未限定的歧义列、CTE 遮蔽物理表。CTE / 表名 / 别名按 SQLite 语义做
   ASCII 大小写折叠比较，因此 `WITH Orders` 与 `WITH ORDERS` 同样不得遮蔽 `orders`。
   Unicode 字母不按 Python casefold 等同为 ASCII。未识别节点、深层递归解析、
   CTE 显式列重命名、重复 CTE 名与派生表默认拒绝；嵌套 SELECT 逐层校验。
3. **SQLite authorizer**（`eda/sql/authorizer.py`）：执行层最后防护。默认拒绝，
   只放行 `SQLITE_SELECT`、**`main` 库**上批准关系/列的 `SQLITE_READ`、批准函数。
   `temp` 与 ATTACH 库的 READ 一律拒绝。`COUNT(*)` 时 SQLite 可能把 `dbname` 留空，
   只有安装策略前通过受信 `PRAGMA database_list` 确认连接仅含 main，且
   READ 的 column 为空字符串、dbname 为 None 时，才允许按 main 检查。
   已有 temp 或 ATTACH 时拒绝这种不确定来源的读取，即便它实际来自主库。
   安装后 ATTACH 与 DDL 均被拒绝，连接须由调用者独占。拒绝用 `SQLITE_DENY`，
   不用 `SQLITE_IGNORE`。真实回调与附加库集成证据见 `stage2-review.md`。

另外：

- 编译器只输出参数化 SQL，标识符来自语义配置，过滤值绑定为 `:filter_0` 等；
- 排名就是 `breakdown + order_by + top_n`，按最终指标排序，`NULL` 用 `NULLS LAST`；
- 行数 / 返回字节 / SQL 长度 / 单值字节 / 执行超时是**进程内尽力而为**，不是 OS 沙箱，
  也不是 `sqlite3.connect(..., timeout=)`（那只是锁等待）。
  `sql_max_value_bytes` / `SQLITE_LIMIT_LENGTH` 按**字节**计，不是字符数，
  也可能约束 SQLite 内部编码后的整行长度，不能理解为只约束单个展示值；
- 空结果是成功；`top_n` 与执行器截断分开标记，二者都不能当作完整总体再汇总。

`max_result_bytes` 计每行值数组经 `json.dumps(ensure_ascii=False)` 后的 UTF-8
字节数之和（包括该行方括号、分隔符与转义）；不包括列名、SQL、元数据、外层
JSON 包装或 pretty-print 空白。超预算行不进入结果。这不是最终输出文件的总字节上限。
SQL 文本先按 Python 字符数检查；SQLite 的字节上限使用最多四倍字符预算。
时间限制使用 monotonic deadline 和 progress handler，不使用 LIMIT 充当超时。

`execute_on_connection` 要求调用者独占连接且没有既有 authorizer/progress handler；
Python sqlite3 无法读取旧回调，因此接口不承诺恢复未知旧回调。执行结束清理本次
cursor 和回调，并恢复原 SQLite 长度限制。内部新建连接在成功/失败时均关闭，
连接工厂初始化失败也关闭。旧报表无法表达执行器截断时直接报 resource_limit。
结构化查询保留过滤维度/op 元数据，省略原始过滤值；固定错误文案不回显路径或绑定值。

数据库连接自身也是只读的双保险：正确编码的 `mode=ro` URI（SQLite 层面拒绝写，
且不会为不存在的路径创建空库）加 `PRAGMA query_only = ON`，并关闭扩展加载。
测试验证即使 `query_only` 被关掉，`mode=ro` 仍然生效。

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

阶段三所有自动化测试使用 Fake，不访问网络。真实适配器只在 CLI 显式选择
`--provider real` 时启用，Key 仅来自进程环境。最多两次规划调用；原 live_llm
开关保留但不负责本阶段 CLI 的授权，默认 fake 不会因环境变量存在而联网。

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
    core.py                # 核心指标与单维度拆分；经小型适配走统一执行器
    report.py              # 只读命令行报表（人工核对用，无 LLM）
  plan/
    models.py              # AnalysisPlan：业务请求是否合法
  sql/
    compiler.py            # AnalysisPlan → 参数化 SQL（不执行）
    catalog.py             # 从语义层发布批准的表/列/函数
    validator.py           # SQLGlot AST：结构与对象访问
    authorizer.py          # SQLite 执行层默认拒绝
    executor.py            # 执行策略：校验 → authorizer → 资源限制
  query/
    service.py             # 计划闭环：parse → compile → execute → 结构化结果
    cli.py                 # 薄 CLI：读 JSON 计划
```

**定义、编译、执行三者分离**是这里的关键：YAML 负责定义，`operations.py` +
`definitions.py` / `eda/sql/compiler.py` 负责编译成 SQL 文本，`eda/sql/executor.py`
负责执行策略，真正的 `sqlite3.connect` 仍只在 `eda/db.py`。AnalysisPlan 编译器
复用同一套语义表达式，没有再写一份公式。

---

## 7. 后续阶段的落点（尚未实现）

| 阶段 | 新增模块 | 要点 |
|---|---|---|
| 2（已完成） | `eda/plan/`、`eda/sql/`、`eda/query/` | AnalysisPlan schema 与校验、确定性 SQL 编译器、SQLGlot AST 校验、SQLite authorizer、行数/超时上限、对抗测试 |
| 3（离线实现完成） | `eda/agent/` | PlannerDecision、Fake/真实规划适配器、严格日期和计划校验、最多两次模型调用；真实烟雾验证待执行 |
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
