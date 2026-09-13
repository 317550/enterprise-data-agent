# 进度记录

分阶段实施，每阶段完成后停下来交付、复核，再进入下一阶段。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 项目骨架、数据模型、指标口径、可复现模拟数据 | ✅ 已完成（2026-09-13） |
| 2 | 只读 SQL 安全执行器及对抗测试 | ⬜ 未开始 |
| 3 | LangGraph 单轮分析流程及有限错误修复 | ⬜ 未开始 |
| 4 | 多轮追问、持久化和会话隔离 | ⬜ 未开始 |
| 5 | Streamlit 演示、确定性图表和结果追溯 | ⬜ 未开始 |
| 6 | 独立评测、文档、依赖与发布检查 | ⬜ 未开始 |

---

## 环境事实（实际执行结果，非计划）

| 项目 | 实际情况 |
|---|---|
| 操作系统 | Windows 11 (10.0.22631)，PowerShell |
| 计划的 Python | 3.11 |
| **实际的 Python** | **3.12.6**（`py -3.12`）。本机只有 3.12 和 Anaconda 的 3.10.9，**没有安装 3.11**；我没有擅自安装新的 Python 解释器 |
| 虚拟环境 | `.venv`（项目内） |
| SQLite | 3.45.3（随 CPython 3.12.6），满足 STRICT 表所需的 >= 3.37 |
| 依赖安装 | 全部成功，实际版本见 `requirements.txt` / `requirements.lock.txt` |
| Git | 仓库已初始化在 `main` 分支，**无任何提交**。按约束未执行任何 commit / push / 建远端仓库 |

### 选 3.12 而不是 3.11 的依据

- `langgraph 1.2.11` 的 `requires_python` 是 `>=3.10`（取自 PyPI 元数据），3.12 在范围内；
- 其余依赖（pydantic 2.13.5 / sqlglot 30.18.0 / pandas 3.0.5 / plotly 7.0.0 /
  streamlit 1.63.0 / openai 3.13.0 / pytest 9.1.1）均已在 3.12.6 上安装并跑通测试；
- `pyproject.toml` 声明 `requires-python = ">=3.11,<3.14"`，所以你之后装了 3.11 也能直接用；
- 代码里用了 `enum.StrEnum`，这是 3.11+ 特性，与声明一致。

**如果你希望严格使用 3.11**，需要你自己安装 3.11 后重建虚拟环境（我不会擅自装解释器）：

```cmd
py -3.11 -m venv .venv311
.venv311\Scripts\python.exe -m pip install -r requirements.txt
.venv311\Scripts\python.exe -m pytest -q
```

### DeepSeek 接口核对

已查阅官方文档 https://api-docs.deepseek.com/ （2026-09-13）：

- Base URL（OpenAI 兼容）：`https://api.deepseek.com`
- 当前模型名：`deepseek-flash`、`deepseek-v4-pro`
  （旧名 `deepseek-v4-flash` 仍被接受但对应模型已下线；文档未再列出 `deepseek-chat`）
- 调用方式：OpenAI SDK 指定 `base_url` 即可

这些值全部写在 `.env.example` 与 `eda/config.py` 的默认值里，**没有硬编码进业务逻辑**。
阶段 1 没有发起任何模型调用，因此上述模型名**尚未经过真实调用验证**。

---

## 阶段 1 交付内容

### 新增文件

```
.gitignore
.env.example
README.md
pyproject.toml
requirements.txt
requirements.lock.txt
docs/architecture.md
docs/metrics.md
docs/progress.md
eda/__init__.py
eda/config.py
eda/db.py
eda/domain/__init__.py
eda/domain/enums.py
eda/domain/models.py
eda/data/__init__.py
eda/data/schema.sql
eda/data/schema.py
eda/data/fixtures/customers.csv
eda/data/fixtures/products.csv
eda/data/fixtures/orders.csv
eda/data/fixtures/order_items.csv
eda/data/fixture_dataset.py
eda/data/generator.py
eda/data/loader.py
eda/data/build_db.py
eda/metrics/__init__.py
eda/metrics/definitions.py
eda/metrics/core.py
eda/metrics/report.py
tests/conftest.py
tests/data/expected_fixture_metrics.json
tests/test_schema_constraints.py
tests/test_fixture_metrics.py
tests/test_generator.py
tests/test_build_db.py
tests/test_report_cli.py
tests/test_config_and_definitions.py
tests/test_project_constraints.py
```

（修改文件：无。阶段 1 开始时工作目录为空。）

### 入口

| 入口 | 作用 |
|---|---|
| `python -m eda.data.build_db` | 建库。`--dataset demo/fixture`、`--db`、`--seed`、`--start-date`、`--end-date`、`--orders`、`--customers`、`--force-overwrite` |
| `python -m eda.metrics.report` | 只读报表，人工核对口径。`--db`、`--start`、`--end`、`--region`、`--category`、`--by`、`--limit`、`--show-definitions` |
| `python -m pytest` | 测试。不访问网络，不调用 LLM |

### 实际执行结果

```
.venv\Scripts\python.exe -m pytest -q
-> 100 passed in 4.36s          （最终一次为 106 项，含 report CLI 测试）

.venv\Scripts\python.exe -m eda.data.build_db --dataset demo
-> customers 400 / products 30 / orders 3000 / order_items 5486，exit 0

.venv\Scripts\python.exe -m eda.data.build_db --dataset demo     （第二次）
-> "target database already exists ... Refusing to touch it"，exit 2，原库未被修改

.venv\Scripts\python.exe -m eda.data.build_db --dataset fixture
-> customers 4 / products 5 / orders 8 / order_items 12，exit 0

.venv\Scripts\python.exe -m eda.metrics.report --db data\fixture.db --by category
-> 营业额 4,552.00 元（455200 分）/ 有效订单 5 单 / 客单价 910.40 元
   与 docs/metrics.md 第 6 节的手工推导完全一致
```

开发过程中有 2 个测试先失败后修正，记录在此以免误解为「一次写对」：
`test_generator_never_reads_the_system_clock` 和
`test_expected_answers_are_not_baked_into_runtime_code` 最初用纯文本匹配源码，
把文档字符串里的说明文字误判成违规。修法是把检查改成在**语法树**上做（更精确、更严格），
而不是放宽断言。

---

## 阶段 1 尚未实现的内容

- SQL 安全执行器（SQLGlot AST 校验、表白名单、行数/超时上限）—— 阶段 2；
- 任何 LLM 调用、意图解析、SQL 生成、错误修复 —— 阶段 3；
- LangGraph 图、checkpoint 持久化、多轮追问、会话隔离 —— 阶段 4；
- Streamlit 界面与图表 —— 阶段 5；
- 独立评测集与发布检查 —— 阶段 6；
- `美妆个护`、`家居日用` 两个类别在手工 fixture 里没有数据（演示数据里有），
  fixture 故意保持最小规模；
- 退款金额表、部分退款、净收入口径（v1 明确不做）。

## 阶段 1 尚未验证的内容

- **DeepSeek 的模型名、鉴权与响应格式没有经过真实调用验证**，只核对了官方文档；
- Python 3.11 上没有跑过（本机无 3.11），只在 3.12.6 上验证；
- 只在 Windows + SQLite 3.45.3 上验证，未在 Linux/macOS 或其他 SQLite 版本上验证；
  跨平台一致性依赖「整数运算 + 固定种子」的设计，但没有实际跨平台执行过；
- LangGraph / Streamlit / Plotly / pandas / sqlglot **只验证了能安装**，其 API 用法
  在阶段 1 完全没有使用，因此没有验证；
- 未做性能压测（演示数据 3000 单规模下查询是毫秒级，但没有正式测量）。
