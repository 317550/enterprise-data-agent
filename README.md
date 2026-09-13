# Enterprise Data Agent

基于 LangGraph 的**只读**经营数据分析 Agent：自然语言问题 → 指标与时间范围解析 →
SQL 生成 → 安全校验 → 只读执行 → 有限错误修复 → 结果解释与图表，支持多轮追问与会话恢复。

> **这是一个本地、可复现、可测试的作品集项目，不声称生产就绪。**
> 数据是虚构的电商经营数据，不含任何真实个人信息。
> 「营业额」使用的是简化口径（不扣退款），不能当作净收入使用，详见
> [`docs/metrics.md`](docs/metrics.md)。

**当前进度：阶段 1 已完成**（项目骨架、数据模型、指标口径、可复现模拟数据）。
阶段 1 不调用任何 LLM，也不包含 LangGraph 与 Web 界面。
完整分阶段状态见 [`docs/progress.md`](docs/progress.md)。

---

## 技术选型

| 用途 | 选型 | 实际安装版本 |
|---|---|---|
| 运行时 | CPython | **3.12.6**（计划写的是 3.11，本机没有 3.11；LangGraph 1.2.x 要求 `>=3.10`） |
| 编排 | langgraph | 1.2.11 |
| 状态持久化 | langgraph-checkpoint-sqlite | 3.1.1 |
| 数据校验 / 配置 | pydantic / pydantic-settings | 2.13.5 / 2.15.0 |
| SQL 解析校验 | sqlglot | 30.18.0 |
| 数据库 | SQLite（标准库 `sqlite3`） | SQLite 3.45.3（STRICT 表需 >= 3.37，建库时会检查） |
| 模型 API | openai SDK（指向 DeepSeek 的 OpenAI 兼容端点） | 3.13.0 |
| 数据处理 / 图表 | pandas / plotly | 3.0.5 / 7.0.0 |
| 界面 | streamlit | 1.63.0 |
| 测试 | pytest | 9.1.1 |

依赖范围见 `pyproject.toml`，精确固定版本见 `requirements.txt`，
完整解析结果见 `requirements.lock.txt`。

---

## 快速开始（Windows CMD）

```cmd
cd C:\Users\lenovo\Desktop\agent-learning\enterprise_data_agent

:: 1) 创建虚拟环境并安装依赖
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2) 复制配置模板（阶段 1 用不到 API Key，可以先留占位值）
copy .env.example .env

:: 3) 建库：演示数据（固定种子，可复现）
.venv\Scripts\python.exe -m eda.data.build_db --dataset demo

:: 4) 建库：手工小数据（可人工复核）
.venv\Scripts\python.exe -m eda.data.build_db --dataset fixture

:: 5) 跑测试
.venv\Scripts\python.exe -m pytest -q

:: 6) 只读报表（人工核对口径，不走 LLM）
.venv\Scripts\python.exe -m eda.metrics.report --db data\fixture.db --show-definitions
.venv\Scripts\python.exe -m eda.metrics.report --start 2024-06-01 --end 2024-06-30 --by region
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

## 目录结构

```
enterprise_data_agent/
  eda/                     # 应用代码（阶段 1 完全离线）
    config.py              # Settings：API key / base_url / model / 路径，全部来自 .env
    db.py                  # 唯一的 sqlite3 出入口；只读连接 mode=ro + query_only
    domain/                # 受控词表 + Pydantic 行模型
    data/                  # DDL、手工 fixture、演示数据生成器、建库 CLI
    metrics/               # 指标口径、计算、只读报表 CLI
  tests/                   # pytest；不访问网络，不调用 LLM
    data/expected_fixture_metrics.json   # 人工推导的期望值（测试资产）
  docs/
    architecture.md        # 架构与安全边界
    metrics.md             # 数据模型与指标口径（口径的权威来源）
    progress.md            # 分阶段进度、已验证/未验证清单
  data/                    # 生成的数据库（已 gitignore）
```

---

## 不可违反的约束（已在代码与测试中落实）

1. 不执行模型生成的 Python / shell / 任意代码；无 `eval` / `exec` / `compile` / `subprocess`
   —— 由 `tests/test_project_constraints.py` 在语法树上逐文件强制。
2. SQL 只有一条执行通道：`eda/db.py` 是唯一出现 `sqlite3.connect` 的模块（有测试守住），
   阶段 2 会在其上加安全执行器。
3. 不靠 `SELECT` 前缀或正则判断 SQL 安全性；阶段 2 使用 SQLGlot 解析 AST。
4. SQL 可执行不等于业务答案正确：正确性由手工 fixture 与人工推导的期望值判定。
5. 运行时代码中没有硬编码的题目、期望 SQL 或期望答案。
6. 不为了通过测试而删弱测试、改答案或吞异常。
7. 不自动提交、推送、建远端仓库、合并 PR 或发标签。
8. 不读取、打印、提交真实密钥；`.env.example` 只含占位值（有测试断言）；
   Key 用 `SecretStr` 包装，日志里只会看到掩码。
9. 网络或权限受阻会如实说明，不宣称未执行的测试通过。
10. 不引入需求外的框架、微服务、Redis、向量数据库或多 Agent 系统。

**费用控制**：`pytest` 默认不产生任何外部调用。真实 DeepSeek 调用要等阶段 3，
并且必须由你手动执行带 `live_llm` 标记的测试，同时在 `.env` 中把
`ENABLE_LIVE_LLM_TESTS` 设为 `true`。
