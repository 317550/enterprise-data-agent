# 架构设计

本项目是一个**本地可复现、可测试的作品集项目**，不声称生产就绪。

目标：自然语言问题 → 指标与时间范围解析 → SQL 生成 → 安全校验 → 只读执行 →
有限错误修复 → 结果解释与图表，并支持多轮追问与会话恢复。

---

## 1. 目标形态（阶段 6 完成后）

```
                 ┌─────────────────────────────────────────────┐
   用户提问  ──▶ │  Streamlit UI（阶段 5）                      │
                 └───────────────────┬─────────────────────────┘
                                     ▼
                 ┌─────────────────────────────────────────────┐
                 │  LangGraph 图（阶段 3 / 4）                  │
                 │                                             │
                 │  parse_intent → build_sql → validate_sql    │
                 │        ▲                       │            │
                 │        │                       ▼            │
                 │   repair_sql ◀── 失败 ──  execute_sql        │
                 │   (次数上限)                   │            │
                 │                                ▼            │
                 │                          explain_result     │
                 └───────┬──────────────────────┬──────────────┘
                         │                      │
              LLM 调用（DeepSeek）        唯一 SQL 执行通道
                         │                      │
                         ▼                      ▼
            ┌────────────────────┐   ┌──────────────────────────┐
            │ eda/llm（阶段 3）   │   │ eda/sql/executor（阶段 2）│
            │ 只读配置里的        │   │ SQLGlot 解析 + AST 校验   │
            │ key/base_url/model │   │ 白名单表 + 行数/超时上限  │
            └────────────────────┘   └───────────┬──────────────┘
                                                 ▼
                                     ┌──────────────────────┐
                                     │ eda/db.py            │
                                     │ sqlite3 唯一出入口    │
                                     └───┬──────────────┬───┘
                                         │              │
                          business.db（只读 mode=ro）   checkpoints.db
                                                        （仅应用写入，
                                                          模型不可见）
```

---

## 2. 安全边界（贯穿所有阶段）

### 2.1 绝不执行模型生成的代码

模型只允许输出两种东西：**结构化 JSON**（意图、指标、时间范围）和 **SQL 文本**。
不存在 Python / shell 代码生成路径，代码里没有 `eval` / `exec` / `compile` /
`subprocess`。`tests/test_project_constraints.py::test_no_dynamic_code_execution`
在语法树上逐文件强制这一点。

### 2.2 SQL 只有一条执行通道

`eda/db.py` 是整个项目里唯一出现 `sqlite3.connect` 的模块，并由
`test_sqlite_connect_lives_only_in_eda_db` 守住。阶段 2 会在它之上加一层
`eda/sql/executor.py`：

- 用 **SQLGlot 解析成 AST** 后判断语句类型，而不是看字符串前缀或写正则
  （`SELECT` 前缀可以被 CTE、注释、多语句、`PRAGMA` 绕过）；
- 必须单语句；只允许 `SELECT`（含 `WITH ... SELECT`）；
- 表名必须落在白名单内，且**不包含状态库**；
- 强制行数上限与执行超时；
- 参数一律绑定，绝不把值拼进 SQL 字符串（阶段 1 的
  `build_core_metrics_sql` 已经是这个写法，并有测试断言 SQL 文本里不含字面值）。

数据库连接自身也是只读的双保险：`mode=ro` URI（SQLite 层面拒绝写，且不会创建文件）
加 `PRAGMA query_only = ON`。测试验证即使 `query_only` 被关掉，`mode=ro` 仍然生效。

### 2.3 两个数据库彻底分开

| 数据库 | 写入者 | 模型可见性 |
|---|---|---|
| `data/business.db` | 只有 `python -m eda.data.build_db` | 只读查询，表结构对模型可见 |
| `data/checkpoints.db` | 只有 LangGraph（阶段 4） | **完全不可见**，不在表白名单里，生成的 SQL 无法触达 |

### 2.4 SQL 可执行 ≠ 答案正确

这是两件事，项目里分别处理：

- **可执行性**由安全执行器保证（阶段 2）；
- **业务正确性**由「手工 fixture + 人工推导的期望值」保证（阶段 1 已落地），
  阶段 6 的独立评测再在此基础上判分。

期望答案只存在于 `tests/data/expected_fixture_metrics.json`，
运行时代码里没有任何硬编码的题目、期望 SQL 或期望答案；
`test_expected_answers_are_not_baked_into_runtime_code` 会从期望文件里读出所有大额数字，
反向扫描 `eda/**.py` 的字面量，防止有人把答案抄进代码。

### 2.5 密钥与费用

API Key、Base URL、模型名全部来自 `eda/config.py`（pydantic-settings 读 `.env`）。
Key 用 `SecretStr` 包装，打印 settings 或日志都只会看到掩码。
`.env.example` 只含占位值，有测试断言这一点。

真实模型调用必须由人显式触发：自动测试套件默认不选中 `live_llm` 标记，
`ENABLE_LIVE_LLM_TESTS` 默认 `false`，因此 `pytest` 不会产生任何外部调用或费用。

---

## 3. 模块划分

```
eda/
  __init__.py
  config.py                # Settings（.env）；key/base_url/model 都在这里
  db.py                    # 唯一的 sqlite3 出入口：只读连接 + 执行原语
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
    definitions.py         # 指标口径 + SQL 文本（只产 SQL，不执行）
    core.py                # 核心指标与分维度拆分的计算
    report.py              # 只读命令行报表（人工核对用，无 LLM）
```

**定义与执行分离**是这里的关键设计：`definitions.py` 只生成 SQL 文本和绑定参数，
执行交给 `eda/db.py`（阶段 2 后交给安全执行器）。好处是阶段 3 的 Agent 可以复用
阶段 1 测试已经验证过的同一份 SQL，而不是自己再拼一遍。

---

## 4. 后续阶段的落点（尚未实现）

| 阶段 | 新增模块 | 要点 |
|---|---|---|
| 2 | `eda/sql/validator.py`、`eda/sql/executor.py` | SQLGlot AST 校验、表白名单、行数/超时上限、对抗测试 |
| 3 | `eda/llm/client.py`、`eda/graph/` | DeepSeek 客户端（配置驱动）、LangGraph 单轮流程、有限次 SQL 修复 |
| 4 | `eda/graph/checkpoint.py` | `langgraph-checkpoint-sqlite` 持久化、`thread_id` 会话隔离、多轮追问上下文 |
| 5 | `app/streamlit_app.py`、`eda/viz/` | 确定性图表（Plotly）、展示实际执行的 SQL 与口径以便追溯 |
| 6 | `eval/` | 独立评测集（题目与期望值不进运行时代码）、依赖与发布检查 |

---

## 5. 技术选型与版本

Python 官方要求的是 3.11；本机没有 3.11，实际使用 **3.12.6**（LangGraph 1.2.x 要求
`>=3.10`，其余依赖均支持 3.12）。`pyproject.toml` 声明 `requires-python = ">=3.11,<3.14"`。

实际安装并测试过的版本记录在 `requirements.lock.txt` 与 `docs/progress.md`。
DeepSeek 的模型名与 Base URL 取自官方文档（https://api-docs.deepseek.com/ ），
不靠记忆猜测；当前文档给出的模型是 `deepseek-flash` 与 `deepseek-v4-pro`，
Base URL 为 `https://api.deepseek.com`（OpenAI 兼容格式）。

不引入需求之外的东西：没有微服务、没有 Redis、没有向量数据库、没有多 Agent 框架、
没有额外的 ORM。
