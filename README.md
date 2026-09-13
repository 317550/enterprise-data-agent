# Enterprise Data Agent

基于**受控业务语义层**的**只读**经营数据分析 Agent：自然语言问题 →
结构化分析计划 → 确定性 SQL 编译 → 安全校验与只读执行 → 结果与证据，
当前支持单轮规划，多轮追问与会话恢复尚未实现。

> **这是一个本地、可复现、可测试的作品集项目，不声称生产就绪。**
> 数据是虚构的电商经营数据，不含任何真实个人信息。
> 核心金额指标叫**「有效订单成交额（简化口径）」**：只统计 `paid` / `completed`
> 订单的明细金额，不扣退款，**不是财务净收入，也不等同于标准 gross revenue**。
> 详见 [`docs/metrics.md`](docs/metrics.md)。

自由 Text-to-SQL 在本项目中**只作为评测基线**，不是默认执行路径。

**当前进度：阶段 1、1.1、2 与阶段 3 离线实现已完成**。阶段三新增
PlannerDecision → AnalysisPlan 单轮规划；默认 Fake 模型，无联网，真实规划
需显式 `--provider real`。模型不能生成 SQL、访问数据库或解释数值。
未包含 LangGraph 与 Web 界面，真实模型烟雾验证待用户手工执行。
协议、时间规则、预算和命令见[阶段三说明](docs/stage3-planning.md)。
完整分阶段状态与逐项追踪表见 [`docs/progress.md`](docs/progress.md)。

---

## 技术选型

| 用途 | 选型 | 实际安装版本 |
|---|---|---|
| 运行时 | CPython | **3.12.6**（计划写的是 3.11，本机没有 3.11；LangGraph 1.2.x 要求 `>=3.10`） |
| 编排 | langgraph | 1.2.11 |
| 状态持久化 | langgraph-checkpoint-sqlite | 3.1.1 |
| 数据校验 / 配置 | pydantic / pydantic-settings | 2.13.5 / 2.15.0 |
| 语义层配置 | PyYAML | 6.0.3 |
| SQL 解析校验 | sqlglot | 30.18.0 |
| 数据库 | SQLite（标准库 `sqlite3`） | SQLite 3.45.3（STRICT 表需 >= 3.37，建库时会检查） |
| 模型 API | openai SDK（指向 DeepSeek 的 OpenAI 兼容端点） | 3.13.0 |
| 数据处理 / 图表 | pandas / plotly | 3.0.5 / 7.0.0 |
| 界面 | streamlit | 1.63.0 |
| 测试 | pytest | 9.1.1 |

### 已验证的环境

以上版本是在下面这个环境里**实际安装并跑过测试**的，不是规划值：

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11（10.0.22631） |
| Shell | PowerShell / CMD |
| 解释器 | `.venv\Scripts\python.exe`，CPython 3.12.6 |
| SQLite | 3.45.3（CPython 3.12.6 自带） |
| `pip check` | `No broken requirements found.` |
| `pytest -q` | **488 passed**（阶段三 Fake 离线验证后；原阶段二基线 374 项保留） |

其他平台、其他 Python 版本（含计划里的 3.11）**均未验证**。

### 两个依赖文件的分工

| 文件 | 实际格式 | 用途 |
|---|---|---|
| `requirements.txt` | 手工维护的**直接依赖**清单，`==` 固定版本 | 只列本项目直接 import 的包，简短可评审；间接依赖交给 pip 解析 |
| `requirements.lock.txt` | `pip freeze` 输出：纯 `name==version`，**无 hash**，**不是** pip-tools / uv 锁文件 | 完整环境快照（含间接依赖）。平台相关：内含 `pyarrow`、`sqlite-vec` 等 win_amd64 wheel，在 CPython 3.12.6 / Windows 上生成 |

因为锁文件是 `pip freeze` 格式（无 hash、无 `--require-hashes`），与它匹配的安装命令
就是普通的 `pip install -r`：

```cmd
:: 复现完全一致的已验证环境
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt

:: 或者只装直接依赖，让 pip 自行解析间接依赖
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 改动依赖后重新生成锁文件并自检
.venv\Scripts\python.exe -m pip freeze > requirements.lock.txt
.venv\Scripts\python.exe -m pip check
```

---

## 快速开始（Windows CMD）

```cmd
cd C:\Users\lenovo\Desktop\agent-learning\enterprise_data_agent

:: 1) 创建虚拟环境并安装依赖（已有 .venv 可跳过）
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt

:: 2) 复制配置模板（当前阶段用不到 API Key，可以先留占位值）
copy .env.example .env

:: 3) 建库：演示数据（固定种子，可复现）。已存在则默认拒绝覆盖
.venv\Scripts\python.exe -m eda.data.build_db --dataset demo

:: 4) 建库：手工小数据（可人工复核）
.venv\Scripts\python.exe -m eda.data.build_db --dataset fixture

:: 5) 依赖自检与测试
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pytest -q

:: 6) 只读报表（人工核对口径，不走 LLM）
.venv\Scripts\python.exe -m eda.metrics.report --db data\fixture.db --show-definitions
.venv\Scripts\python.exe -m eda.metrics.report --start 2024-06-01 --end 2024-06-30 --by region

:: 7) 阶段 2：用结构化 AnalysisPlan 跑安全查询闭环（不走 LLM）
.venv\Scripts\python.exe -m eda.query.cli --plan examples\plans\fixture_gmv_total.json --db data\fixture.db
.venv\Scripts\python.exe -m eda.query.cli --plan examples\plans\fixture_gmv_by_category.json --db data\fixture.db
```

> 控制台中文乱码时，先执行 `chcp 65001`。

### 建库脚本的覆盖保护

目标文件已存在时**默认拒绝并退出码 2**，不会删除任何数据：

```cmd
.venv\Scripts\python.exe -m eda.data.build_db --dataset demo
echo %ERRORLEVEL%
:: -> 2，且 data\business.db 原样保留
```

确实要重建时必须显式加 `--force-overwrite`；即便如此，新库也是先写到临时文件、
自检通过后才原子替换，失败不会把你现有的库弄坏。

---

## 业务语义层

业务定义的权威来源是仓库根目录下的三份 YAML，它们**随代码受控维护**
（走代码评审，不接收用户上传）：

| 文件 | 内容 |
|---|---|
| `semantic/metrics.yaml` | 指标：ID、中文名、同义词、单位、基表与粒度、受支持的计算操作、输入字段、过滤口径、允许维度、零分母策略 |
| `semantic/dimensions.yaml` | 维度：ID、来源字段、类型、允许取值、允许操作（`group_by` / `filter`）、不可加声明 |
| `semantic/relationships.yaml` | 表粒度与主键、表间关联键与基数、重复累计注意事项、三个分析视图 |

三份文件都带 `schema_version`（当前 `1.0.0`），由 `eda/semantic/` 用 Pydantic 加载与
交叉校验。**Python 只实现一组封闭的计算操作**（`eda/metrics/operations.py` 里的
`sum` / `count_distinct` / `ratio` 三个手写函数），没有表达式执行器，不用 `eval` / `exec`。

配置与代码的口径一致性由 `tests/test_semantic_consistency.py` 强制：声明的列、主键、
外键、状态过滤会与真实数据库的 `PRAGMA` 结果比对，指标总量还会与一条**手写的独立参考
查询**（直接查基表、绕开视图）对账。

拆分报表的比率表头也来自受控维度元数据：按 `region` / `channel` / `month` / `date`
展示「客单价」，按 `category` / `product` 展示「订单平均贡献额」。后两者的公式是
「该类别或商品成交金额 ÷ 包含它的去重订单数」，不是整张订单通常意义上的客单价。
`aov_display_name_zh` 由 Pydantic 限制为这两个固定名称，不接受任意表达式。

查看当前口径：

```cmd
.venv\Scripts\python.exe -m eda.metrics.report --db data\fixture.db --show-definitions
```

输出会分别列出每个指标的「已实现操作」与「待实现」——配置里声明「业务上支持」的操作
（如 `compare`、`contribution`）当前**代码尚未实现**，请求时会明确报错，而不是给近似结果。

---

## 目录结构

```
enterprise_data_agent/
  semantic/                # 版本化语义层（业务定义权威来源，受控资产）
    metrics.yaml
    dimensions.yaml
    relationships.yaml
  evaluation/
    README.md              # 评测划分规范：fixture / 开发集 / 保留集与保留集纪律
  eda/                     # 应用代码（当前阶段完全离线）
    config.py              # Settings：API key / base_url / model / 路径，全部来自 .env
    db.py                  # 唯一的 sqlite3 出入口；只读连接 mode=ro + query_only
    semantic/              # 语义层 schema 与加载器（safe_load + 版本校验）
    domain/                # 受控词表 + Pydantic 行模型
    data/                  # DDL、手工 fixture、演示数据生成器、建库 CLI
    metrics/               # 封闭计算操作、语义层编译产物、指标计算、只读报表 CLI
    plan/                  # AnalysisPlan：业务请求是否合法
    sql/                   # 确定性编译、SQLGlot 校验、authorizer、执行策略
    query/                 # 计划闭环服务与薄 CLI
  examples/plans/          # 手工可跑的 AnalysisPlan JSON
  tests/                   # pytest；不访问网络，不调用 LLM
    data/expected_fixture_metrics.json   # 人工推导的期望值（测试资产）
  docs/
    architecture.md        # 架构、默认执行路径与安全边界
    metrics.md             # 数据模型与指标口径说明 + 人工推导记录
    progress.md            # 分阶段进度、需求追踪表、已验证/未验证清单
  data/                    # 生成的数据库（已 gitignore）
```

---

## 不可违反的约束（已在代码与测试中落实）

1. 不执行模型生成的 Python / shell / 任意代码；无 `eval` / `exec` / `compile` / `subprocess`
   —— 由 `tests/test_project_constraints.py` 在语法树上逐文件强制。
   语义配置同样是纯声明式，不含任何可执行表达式（有测试断言）。
2. SQL 只有一条连接通道：`eda/db.py` 是唯一出现 `sqlite3.connect` 的模块（有测试守住）。
   分析查询的执行策略在 `eda/sql/executor.py`，它只调用该受控只读工厂。
3. 不靠 `SELECT` 前缀或正则判断 SQL 安全性；用 SQLGlot 解析 AST，再用 SQLite
   authorizer 做执行层默认拒绝。资源限制是进程内尽力而为，不是 OS 沙箱。
4. SQL 可执行不等于业务答案正确：正确性由手工 fixture 与人工推导的期望值判定。
5. 运行时代码与语义配置中都没有硬编码的题目、期望 SQL 或期望答案（有测试反向扫描）。
6. 不为了通过测试而删弱测试、改答案或吞异常。
7. 不自动提交、推送、建远端仓库、合并 PR 或发标签。
8. 不读取、打印、提交真实密钥；`.env.example` 只含占位值（有测试断言）；
   Key 用 `SecretStr` 包装，日志里只会看到掩码。
9. 网络或权限受阻会如实说明，不宣称未执行的测试通过。
10. 不引入需求外的框架、微服务、Redis、向量数据库或多 Agent 系统。
11. 贡献拆解只是数值分解，**不得表述为因果推断**。

**费用控制**：自动化测试全部离线。阶段三真实调用只能手工传 `--provider real`，
Key 仅从进程环境 `DEEPSEEK_API_KEY` 读取，`.env` 中的 Key 不启用真实适配器。
每请求最多两次模型调用（规划 + 一次计划修复），没有 SQL 修复或解释调用。
