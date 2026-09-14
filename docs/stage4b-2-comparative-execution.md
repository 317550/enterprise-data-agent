# 阶段 4B-2：确定性执行、结果检查与证据链

入口为 `eda.query.comparative.run_comparative_analysis`。本阶段保持语义版本
1.1.0，复用 4B-1 业务协议与 Decimal 计算，仅新增核心执行模块和离线测试。

## 调用链与执行计划

`ComparativeAnalysisPlan → compile_comparative_plan → ComparativeExecutionPlan
→ run_analysis_plan → 原 compiler / SQLGlot / authorizer / 只读执行器
→ AnalysisResult 完整性检查 → calculate_comparison / calculate_contribution
→ ComparativeAnalysisResult`。

执行步骤包含 step_id、evidence_id、role、analysis_plan。严格类型、冻结实例、
extra=forbid；已有实例递归导出后重新验证，不能借 model_construct/model_copy
绕过。执行计划重新比较代码确定性生成的完整步骤，拒绝自定义顺序、数量或子计划。
核心服务只接收业务计划，不接受外部提供的执行步骤或 evidence/query ID。

- compare、mom：恰好 baseline_total → current_total，均为 total。
- contribution：恰好 baseline_total → current_total → baseline_breakdown
  → current_breakdown。breakdown 仅携带已验证的单一维度。
- 所有子计划重新经过原 AnalysisPlan 校验，指标、筛选一致，仅期间及操作不同。
  contribution 的 top_n 不进入查询；没有新增 SQL 或连接入口。

## 预算与期限

每个请求只创建一个 ComparativeExecutionBudget，保存固定的
`deadline = 请求入口 monotonic() + timeout_seconds` 和可变 query_count。
总期限必须有限且为正，默认 30 秒。compare/mom 上限 2，contribution 上限 4；
调用者可通过 max_queries 收紧上限，不能扩大业务计划上限。

每次调用前检查剩余时间与预算；即将调用 runner 时 query_count 加一。
成功、抛异常、返回不合格结果均消耗一次；未开始的查询不计数。
第三步失败返回 query_count=3，停止后续步骤，不重试、不调用模型修复。
各次阶段二 ExecutionLimits.timeout_seconds 取原上限和请求剩余时间的较小值。
返回及异常出口再次检查同一个 deadline，计算结束也检查；超时结果不会被采纳。
预算耗尽为 budget_exhausted，时间耗尽为 timeout。

沿用阶段二进程内合作式期限，不是 OS 强制终止：无法杀死不合作的注入 runner，
也不能保证连接建立或编译在硬实时期限内中断；返回后仍会拒绝逾期结果。
runner 和 clock 是可信基础设施的测试注入点，不来自模型输入。

## 每步校验与稳定错误

对返回 AnalysisResult 递归导出并严格重验，检查：

- execution.status=ok、无执行错误；query_id 非空、与 execution.query_id 一致，
  且与此前查询不同。
- semantic_version、metric_id、指标名、unit/unit_code、operation、起止日期、
  dimension_id 和可见 filters 元数据精确匹配子计划和语义定义。
- execution 与 completeness 均未截断，is_complete_population=true，未排名截取。
- total 列严格为 metric_value，恰好一行且 dimension_value=None。
- breakdown 列严格为 dimension_value、metric_value，维度值非空、长度有界、
  不重复且属于语义允许值；row_count 与实际行数一致。
- 数值必须是有限 int/float，拒绝 bool、NaN、Infinity、异常形状。

truncated=true 或非完整总体统一返回 incomplete_result。其余稳定分类包括
invalid_plan、invalid_limits、execution_error、missing_query_id、metadata_mismatch、
invalid_result_shape、undefined_metric、reconciliation_failed。错误不回传 SQL、
数据库路径、绑定值或内部异常文本。失败返回 comparison/contribution=None，
总体及展示完整性为 false，只保留失败前已验证的证据。

空 total 行集是形状错误，NULL 指标返回 undefined_metric，不转为 0。
空 breakdown 仅在完整分项与独立总量核对成功时可接受；非零总量配空 breakdown
为 reconciliation_failed。原语义 SUM 的空集结果是 SQL 明确定义的 0，保持该口径。
零基期变化率为 null；总变化为零时贡献率为 null。

阶段二结果主动脱敏 filters.value，因此只能核对返回的 dimension_id/op。
本阶段保留代码实际派发的已验证子计划（含筛选值）作为本次返回的证据。
无法仅凭脱敏结果证明恶意 runner 实际使用了哪些绑定值；默认 runner 为可信原服务。
筛选值不会写入错误消息、checkpoint 或审计日志。

## 计算、展示和 JSON

比较与贡献均调用 4B-1 函数，不复制公式。贡献先核对两期完整分项和分别等于
独立总量、变化量之和等于总变化，再排序、应用 top_n。失败不返回贡献率或排名。
每份贡献只有一个 dimension_id，地区与类别是独立分析，不合并贡献率。

top_n 仅影响展示，返回 hidden_dimension_count、hidden_net_change。
证据的 row_count 仍是完整查询行数；is_complete_population 描述已计算总体，
display_is_complete_population 在隐藏分项时为 false，并附带展示子集提示。

Python 内部使用 Decimal；所有 Decimal JSON 字段使用字符串，None 使用 null，
query_count/row_count 等计数仍为 JSON 整数。接受阶段二数字时使用
Decimal(str(value))，保持整数（包括超过 2^53 的整数）及收到的十进制表示。
上游 SQL 客单价可能已是浮点近似，转换不能恢复丢失精度；结果 warnings 明确该边界。
比例沿用 4B-1 的 12 位小数、ROUND_HALF_UP 和独立 128 位上下文。
测试验证 model_dump(mode="json") 与 model_dump_json 一致、整数不失真、
0.3 与 0.1 的 Decimal 差为字符串 "0.2"。

## 证据示例与人工核算

一次真实只读 fixture 查询的 evidence_id → query_id 如下；query_id 每次运行重新生成：

- baseline_total → 1df223287bfa40abaee3c469b63124f8
- current_total → 79f6ae5a7bda40aaa7c2e73e07e9a280
- baseline_breakdown → 78137c9d2f3f42b3b4bf2d9b740dc251
- current_breakdown → 7fb2485c345c4204a5f925afc0fc6f98

比较记录固定引用两份 total 证据；每条贡献固定引用两份 breakdown 证据。
每份证据携带对应子计划、role、row_count、completeness=true、truncated=false。
未经校验或超时返回的结果不进入证据链。

2024 年 1 月到 2 月，有效成交额单位为分：

- 基期：33,300（华东）+189,900（华北）=223,200。
- 本期：190,000（华东）+42,000（西南）=232,000。
- 总变化 8,800，变化率 JSON 为 "0.039426523297"。
- 华东 +156,700、西南 +42,000、华北 −189,900，合计 +8,800。
- top_n=1 只展示华东；hidden_dimension_count=2，hidden_net_change=−147,900。

独立参考直接遍历 Dataset 订单与明细，筛选 paid/completed 并以整数乘加核算，
不调用运行时比较函数。生成配置：80 单、15 客户、2024-01-01 至 2024-02-29，
其余沿用 DemoDataSpec 默认值；地区和类别两个视角均通过查询结果逐项校验。

- seed=17：基期 4,221,330，本期 3,350,955，变化 −870,375 分。
  地区变化：华东 −43,485、华中 −71,560、华北 −1,146,310、华南 −9,900、西南 +400,880。
- seed=83：基期 4,717,190，本期 3,947,230，变化 −769,960 分。
  地区变化：华东 +827,760、华中 −982,790、华北 +157,660、华南 −550,170、西南 −222,420。

这些参考数值仅在测试和文档中，未进入运行时代码。

## 数据覆盖和一致性边界

calendar_period_complete 描述完整日历月/年，data_coverage_verified 固定 false。
没有 MIN/MAX、覆盖查询或新增函数白名单，不能声称数据库无缺日、无漏数。
没有跨查询共享事务/快照：原服务每步打开独立只读连接；应对稳定数据源执行。
贡献核对可发现部分跨步不一致，但不能证明比较总量来自同一数据库快照。
本阶段不接 LangGraph、checkpoint、多轮协议、CLI、Fake/Real Planner 或自然语言规划。

## 验证与文件范围

恢复后先直接完整读取四个未跟踪实现文件，确认分支 feat/comparative-analysis，
4B-1 提交 30bc355，重跑三个比较模块得到 190 passed in 3.98s。
增量未删除或放宽测试；依照新要求将不完整错误精确断言统一为 incomplete_result。
增量定向为 199 passed in 4.54s；新增模块测试安装网络连接阻断 fixture。

新增文件：eda/plan/comparative_execution.py、eda/query/comparative.py、
eda/query/comparative_models.py、tests/test_comparative_execution.py 和本文档。
原有跟踪文件、711 项基线测试、fixture CSV、标准答案、语义配置、阶段二安全代码、
阶段 4A 及依赖均保持不变。没有操作 stash、暂存、提交或切换分支。

数据库核对基线（UTC）：

- business.db SHA256：323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029；
  mtime：2026-09-13T08:49:27.0360631Z。
- fixture.db SHA256：77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773；
  mtime：2026-09-13T08:49:30.2846596Z。

最终验证：

- `.venv\Scripts\python.exe -m pip check`：No broken requirements found。
- `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`：
  806 passed in 69.57s。原 711 项保留，新增执行测试 92 项，
  新增三个生产模块自动纳入已有守护测试再增加 3 项。
- `git diff --check` 通过；另直接检查全部五个未跟踪文件，UTF-8 解码、
  Python AST 解析及行尾空白检查均通过。新增生产代码无数据库连接/执行旁路。
- 两个数据库的 SHA256 和 UTC mtime 与上述基线完全相同。
- 对 HEAD 的跟踪文件差异为空；`.env`、数据库、WAL/SHM、锁文件没有进入 Git。
- 最终 `git status --short` 仅下列五项，均未暂存；分支保持 feat/comparative-analysis：

```text
?? docs/stage4b-2-comparative-execution.md
?? eda/plan/comparative_execution.py
?? eda/query/comparative.py
?? eda/query/comparative_models.py
?? tests/test_comparative_execution.py
```

未访问网络、调用真实模型、升级依赖、操作备份 stash、commit/push/merge/tag，
未进入 4B-3。工作停在本阶段交付状态。
