# Enterprise Data Agent

基于**受控业务语义层**的**只读**经营数据分析 Agent：自然语言问题 →
结构化分析计划 → 确定性 SQL 编译 → 安全校验与只读执行 → 结果与证据，
当前支持单轮规划、多轮追问、本地会话恢复，以及阶段 4B-3 的期间比较、环比与单维度贡献下钻。

> **这是一个本地、可复现、可测试的作品集项目，不声称生产就绪。**
> 数据是虚构的电商经营数据，不含任何真实个人信息。
> 核心金额指标叫**「有效订单成交额（简化口径）」**：只统计 `paid` / `completed`
> 订单的明细金额，不扣退款，**不是财务净收入，也不等同于标准 gross revenue**。
> 详见 [`docs/metrics.md`](docs/metrics.md)。

自由 Text-to-SQL 在本项目中**只作为评测基线**，不是默认执行路径。

**当前进度：阶段 1、1.1、2、3、4A、4B-1/2/3 与阶段 5 本地 Fake 界面已完成**。阶段三新增
PlannerDecision → AnalysisPlan 单轮规划；默认 Fake 模型，无联网，真实规划
需显式 `--provider real`。模型不能生成 SQL、访问数据库或解释数值。
阶段 4A 新增最小 LangGraph、独立 SQLite checkpoint、thread 隔离、澄清续接、
程序重启恢复和并发拒绝策略；多轮 CLI 默认 Fake，支持显式 `--provider real`
及确定性 `--new-topic`。阶段 4B-1/2/3 增加严格比较业务计划、确定性 Decimal
计算、两步比较/四步贡献执行，以及同一会话中的继承和恢复。阶段 5 已增加本地 Streamlit 界面、确定性展示和隐私审计，
阶段 4B-3 已由用户手工完成 deepseek-flash 年份比较、同一 thread/checkpoint 的地区贡献及类别贡献三轮真实验证，均成功。
协议、时间规则、预算和命令见[阶段三说明](docs/stage3-planning.md)。
多轮协议、恢复边界和 CLI 示例见[阶段 4A 说明](docs/stage4a-conversation.md)。
比较协议、实际图路径、预算、checkpoint v2 与 Fake/Real 命令见
[阶段 4B-3 说明](docs/stage4b-3-conversation.md)。语义版本保持 1.1.0；旧 conversation-v1
checkpoint 明确拒绝，不自动迁移，请为本阶段使用新的状态文件。
会话 CLI 可用 `--timeout-seconds 60` 指定本轮统一期限（1–120 秒，默认仍为 30）。
Real 传输超时取 `LLM_TIMEOUT_SECONDS`（默认 60）与剩余总期限的较小值；
手工验证前可设置 `$env:LLM_TIMEOUT_SECONDS = "60"`，避免已有较短配置提前截断。
完整命令、离线超时诊断及用户反馈的真实验收结果见阶段 4B-3 文档；不增加重试或模型调用预算。
三轮均使用 60 秒期限、conversation-plan-v2 / conversation-v2 / semantic 1.1.0，
参考日期 2024-12-31；年份总变化及两种贡献分项合计均为 455200。
验证前的间歇性 DNS 解析超时在调整 WLAN DNS 后排查通过，不能据此推断服务商停服。
Key 仅临时通过进程环境变量传入，验证后已清除。真实验证仅覆盖上述三个受控场景；
日历完整不等于数据覆盖已验证，也不证明全局可用性或不存在所有未知安全缺陷。
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
| `pytest -q -p no:cacheprovider` | **978 passed**（阶段 5 最终恢复回归；保留原 921 项） |

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

此报表入口仍仅执行 `total` / `breakdown`，会分别列出该入口的「已实现操作」与「待实现」。
`compare`、`mom` 与 `contribution` 通过比较执行接口及多轮会话 CLI 使用。

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
  eda/                     # 应用代码（默认 Fake；显式 real 才联网）
    config.py              # Settings：base_url / model / 路径；真实 Key 仅来自进程环境
    db.py                  # 唯一的 sqlite3 出入口；只读连接 mode=ro + query_only
    semantic/              # 语义层 schema 与加载器（safe_load + 版本校验）
    domain/                # 受控词表 + Pydantic 行模型
    data/                  # DDL、手工 fixture、演示数据生成器、建库 CLI
    metrics/               # 封闭计算操作、语义层编译产物、指标计算、只读报表 CLI
    plan/                  # AnalysisPlan：业务请求是否合法
    sql/                   # 确定性编译、SQLGlot 校验、authorizer、执行策略
    query/                 # 计划闭环服务与薄 CLI
    agent/                 # 原单轮规划接口与共享 HTTPS 传输
    conversation/          # 严格多轮协议、五节点图、恢复/锁、Fake/real 与 CLI
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

### 比较与贡献输出契约

比较输出包含 baseline_value、current_value、absolute_change、change_rate、
change_rate_display、两期 PeriodSpec 和 evidence_ids；贡献明细包含 dimension_value、
两期值、absolute_change、contribution_rate、contribution_rate_display 和 evidence_ids。
旧 baseline/current/dimension_change 字段兼容保留。Decimal JSON 为精确字符串，
比例 12 位，展示为 ROUND_HALF_UP 两位百分比；无定义为 null / “无定义”。
例如 fixture 月份比较 223200 → 232000，变化 8800，比例 "0.039426523297"，显示 "3.94%"。
公共执行接口、会话服务和 CLI 共用该输出；详情见 [4B-2](docs/stage4b-2-comparative-execution.md)。

## 阶段 5：本地证据界面

在现有源码目录、已安装锁文件依赖且 fixture 已存在时启动（不要重建已有数据库）：

```powershell
.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

打开 http://127.0.0.1:8501，默认 Fake，可离线演示。依次提交“查看2024年2月成交额环比”、
“按地区看变化贡献”、“改按类别看贡献”；勾选“新话题”后提交“2024年订单数”。
金额保留分，表格保留 Decimal 精确字符串；图表不解释原因。
刷新后输入原 thread_id 恢复业务状态，再提交新问题；不会恢复旧结果或消息历史。
完成后在启动终端按 Ctrl+C。

业务库固定为 data/fixture.db，独立状态为 data/ui-checkpoints.db，审计为 data/ui-audit.jsonl，
均被现有 /data/ 忽略规则覆盖。界面不接受路径、URL 或上传文件。
仅明确提交才调用服务；普通 rerun 和展开技术详情不会调用模型或重复审计。
real 必须主动选择，Key 只来自进程环境；本阶段只验证 Fake/mock，未调用真实模型。

Streamlit 1.63.0、Pandas 3.0.5、Plotly 7.0.0 与原约束一致，未升级。
setuptools/wheel 是离线构建验证用开发依赖。eda.viz 与 eda.audit 已加入显式包清单；
wheel 安装后仓库外包导入由测试验证。应用仍从源码启动，根目录语义 YAML 的完整分发未在本阶段扩展。
设计、测试和限制见[阶段 5 验收](docs/stage5-evidence-ui.md)。
