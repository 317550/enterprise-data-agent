# 阶段三：单轮自然语言规划

本阶段覆盖旧计划中的 LangGraph / SQL 修复设想，只实现自然语言到受控
AnalysisPlan 的单轮规划。未增加自由 Text-to-SQL、期间比较、预测、因果解释、
图表、会话状态或多步 Agent。

## 调用链与协议

`eda.agent.cli` → `run_question` → 请求/日期预检 → `PlannerModel.plan`
→ `parse_decision` → PlannerDecision / AnalysisPlan 完整校验 →
`run_analysis_plan` → 原 `compile_plan` / SQLGlot / authorizer / 只读执行器
→ 原 AnalysisResult → 确定性状态、警告和说明。阶段二的安全代码保持不变。

PlannerDecision 为 extra=forbid 的 Pydantic 模型；仅允许互斥的三种形状：

- ready：只有 status 与完整 plan。
- clarify：只有 status 与一条 clarification。
- refuse：只有 status、refusal_category、refusal_reason。

即使无关字段的值是 null，也拒绝多余字段。已有模型实例、model_construct
或 model_copy 也先导出再完整校验。重复 JSON 键、无效 JSON、SQL / table /
column / expression / join / where / order_by_sql 等额外字段均被拒绝。
plan 仍使用阶段二 AnalysisPlan；不复制指标公式、SQL 编译器或安全策略。

第一版澄清文案为六条封闭短问题（指标、日期、无效日期、单维度、过滤、排名数量），
拒绝类别为 write_request / unauthorized_request / unsupported_analysis，
各对应固定安全文案。这是有意限制：不直接展示模型自由文本，避免泄漏或伪造解释。

## 时间与语义上下文

analysis_reference_date 可通过环境配置或 CLI --reference-date 指定，默认
2024-12-31，绝不从当前系统日期推导。配置支持 .env，真实模型 API Key 除外。

- 今年：参考年份 1 月 1 日至参考日期；本月：月初至参考日期。
- 去年：上一完整日历年；上个月：上一完整日历月，覆盖跨年及闰年。
- 明确年份、月份：整个日历年或月，不静默裁剪。
- 明确 YYYY-MM-DD 或中文年月日：单日或完整的两个端点。
- 无效日期、紧凑日期、非规范 ISO 日期、不完整端点和其他无法唯一解析的时间：澄清。

第一版采用上述保守日期语法。季度、周、“最近一段时间”、省略年份的跨月区间
等均不猜测。模型计划日期必须与已确定窗口完全一致，不能静默纠正不存在日期。
当前年/月未结束、明确请求末日超出参考日期时附警告；不推断数据库数据完整。

模型上下文仅包含语义配置派生的指标 ID/名称/同义词/业务说明、已实现操作、
允许维度、维度名称/允许操作/取值、版本、参考日期、日期规则及 Decision Schema。
业务说明剔除物理字段，完全不传表、视图、列清单、数据库路径、数据行或标准答案。
关系配置由既有语义加载器校验使用，不向模型暴露物理 JOIN 或发现能力。

## 模型隔离与预算

核心服务只依赖 PlannerModel 接口，所有自动化规划测试使用 FakePlannerModel。
Fake 支持脚本化返回/异常和一小组离线中文演示语法；它不持有答案、不读数据库。
未知过滤词或表达方式保守澄清，不把未理解条件静默删除。它不是通用自然语言模型。

RealPlannerModel 是单独的 HTTPS JSON 适配器，调用配置中的 DeepSeek
chat-completions 接口。不使用 SDK 自动重试，无工具调用、重定向或第二次解释。
只接受 HTTPS；Key 仅取进程环境 DEEPSEEK_API_KEY，不读取 .env 中的 Key。
timeout/max_tokens/model/base_url 等复用已有 Settings。未添加外部服务或依赖。
输入问题长度最多 2048 字符；模型文本输出最多 16384 字符，HTTP 响应也有读取上限。

请求最多两次模型调用：初次规划 + 一次无效 JSON/结构/计划修复。
修复上下文只增加 invalid_json / invalid_structure / date_mismatch 类别；
不附原输出、验证错误详情、SQL 或绑定值。超时、服务异常、澄清、拒绝、
阶段二安全拒绝或资源错误都终止，不重试。第二次校验失败返回
plan_validation_error / plan_generation_failed。修复期间超时仍计入第二次调用。
显式写操作、越权或不支持的请求可由预检零调用拒绝；预检是保守预算防护，
不是替代语义校验和执行器的通用安全分类器。

每次请求的审计记录仅包括 request_id、model_call_count、prompt_version、
semantic_version、model_name、reference_date、status、error_code、
validation_errors；仅成功后才带 query_id。logger 只写这份记录，不记录
问题、完整 prompt、模型原始输出、计划或过滤值。默认 CLI 不输出这些 INFO 日志。

success 应用户协议携带完整已验证 AnalysisPlan（包含本次请求的过滤值）和
AnalysisResult，这是给请求者的业务返回，不作为审计日志保存。澄清、拒绝和错误
只带对应安全消息与请求元数据，没有 plan、AnalysisResult 或 query_id。
零分母保留 JSON null 和说明；空分组成功；排名子集/截断结果不宣称完整总体，
不再求总体或调用模型解释。日期区间直接展示在成功响应顶层。

## CLI

默认离线命令（可显式加 --provider fake）：

```cmd
.venv\Scripts\python.exe -X utf8 -m eda.agent.cli "2024年各地区有效订单成交额是多少？" --db data/fixture.db --reference-date 2024-12-31
```

退出码：success=0、clarification_required=2、refused=3、model_error=4、
plan_validation_error=5、execution_error=6。非法 CLI 参数或本地配置输出固定
invalid_input JSON；--help 为普通命令行帮助。JSON 字段形状稳定，request_id /
query_id / elapsed_ms 按实际请求生成。

真实验证仅供用户手工执行。先在当前终端安全设置 DEEPSEEK_API_KEY，并核实
DEEPSEEK_MODEL / DEEPSEEK_BASE_URL，再执行这一条命令：

```cmd
.venv\Scripts\python.exe -X utf8 -m eda.agent.cli "2024年各地区有效订单成交额是多少？" --provider real --db data/fixture.db --reference-date 2024-12-31
```

本次开发未运行该真实命令。接口/模型可用性、真实鉴权和真实模型规划质量未验证；
不能以 Fake 测试宣称远程服务验证成功。缺失进程环境 Key 时返回固定配置错误。

烟雾验收核对：decision_status=ready、status=success；plan 仅批准字段；
日期为 2024-01-01 至 2024-12-31；SQL 与 compile_plan(plan).sql 一致；
model_call_count 为 1 或 2；含 query_id、semantic_version=1.0.0、
prompt_version=nl-plan-v1。独立 fixture 地区金额为华东 223300、华北 189900、
西南 42000 分，按指标降序。真实响应不得反写为自动化测试标准答案。

## 测试与交付范围

开始时分支 feat/nl-analysis-planning，工作区干净；main 已含阶段二合并提交
74ba962，其阶段二提交为 98e3575。原样基线 374 passed in 7.32s。

新增 eda/agent/{__init__,models,dates,context,planner,fake,service,real,cli}.py
及 tests/test_agent_planning.py、本说明文件。
修改 eda/config.py、pyproject.toml、.env.example、README.md、
docs/architecture.md、docs/progress.md。没有修改原测试、阶段二模块、语义公式、
fixture CSV、标准答案或业务数据库。

新增测试包括：真实阶段二 fixture 总量/地区/类别排名对账，指标同义词，固定
参考日期，非法日期与歧义，多维度及过滤澄清，零调用拒绝，Decision 字段互斥，
恶意额外字段和构造模型，合法维度但指标不兼容，一次修复成功/预算终止，
超时/服务异常，真实资源超限/执行器截断，空结果/零分母，脱敏日志和 CLI，
默认 fake 与显式 provider 分支，原始资产 SHA256/mtime。每条编排路径核对调用数。
测试内禁止 socket.create_connection 与 HTTPSConnection.connect；真实 provider
选项测试也替换为 Fake，未进行远程调用。

最终实际运行：

- `.venv\Scripts\python.exe -m pip check`：No broken requirements found，exit 0。
- `.venv\Scripts\python.exe -m pytest -q`：488 passed in 7.87s，exit 0。
  原有 374 项不删不改；新增 105 项规划测试，另有 9 项由新 Python 模块自动展开
  的既有代码约束检查。真实模型次数为零。
- `git diff --check`：exit 0，仅有 Windows 换行转换提示。
- Fake CLI 手工示例：status=success、decision_status=ready、model_call_count=1，
  地区金额华东 223300 / 华北 189900 / 西南 42000 分，查询日期 2024-01-01
  至 2024-12-31，包含请求/查询 ID、semantic_version=1.0.0、prompt_version=nl-plan-v1。
  truncated=false、is_complete_population=true。SQL 与阶段二编译器一致。
- `git ls-files .env` 无输出；Git 状态不含 .env 或密钥。
- `git diff -- tests/data eda/data/fixtures eda/plan eda/sql eda/query eda/db.py semantic`
  无输出，原有安全实现、fixture、标准答案、语义配置保持原样。

受保护数据库前后 SHA256 / UTC mtime 完全相同：

- business.db：`323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029`；
  `2026-09-13T08:49:27.0360631Z`。
- fixture.db：`77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773`；
  `2026-09-13T08:49:30.2846596Z`。

最终 `git status --short`：

```text
 M .env.example
 M README.md
 M docs/architecture.md
 M docs/progress.md
 M eda/config.py
 M pyproject.toml
?? docs/stage3-planning.md
?? eda/agent/
?? tests/test_agent_planning.py
```

本次没有 commit、push、merge、tag、创建/切换分支或进入阶段四。

## 已知边界

不支持任意自然语言时间表达或任意筛选；Fake 为可审查演示语法，未知内容会澄清。
真实模型可能给出语义上合法但未准确理解问题的指标/维度；本阶段的强校验验证
允许范围和确定性日期，不能证明通用自然语言意图等价。新增真实能力须单独评测。
真实 HTTPS timeout 为连接/读取等待预算，不是进程硬实时；模型 token 上限与
至多两次调用限制不等于硬费用上限。基础业务数据库仍须由受控建库路径产生。
项目仍从源码运行，打包、其他平台和远程模型验证未完成；不引入 LangGraph、
多轮持久化、期间比较、贡献拆解、图表、预测或阶段四工作。
