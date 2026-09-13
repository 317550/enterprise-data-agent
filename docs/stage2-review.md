# 阶段二独立代码审查与定向修复

审查日期：2026-09-13。分支：`feat/secure-query-executor`。
范围只涉及 AnalysisPlan、语义校验、确定性编译、SQLGlot、SQLite authorizer、
受限只读执行、结构化结果与旧报表适配。未使用网络、LLM 或子代理。

## 基线与调用链

独立执行了 branch / status / diff stat / pip check / pytest / diff check。
首次沙箱内 pytest 因 Windows 临时目录及缓存权限失败，不能作为产品测试结果；
获得执行权限后原样重跑 `python -m pytest -q`：311 passed in 6.05s，exit 0。
pip check 无损坏依赖，diff check 无空白错误。开始时有 11 个已跟踪改动文件，
以及计划、SQL、查询模块、示例和六个阶段二测试文件未跟踪；保留这些用户改动。

实际路径：`eda.query.cli.main` → `run_analysis_plan` → `parse_analysis_plan`
→ `compile_plan` → `execute_readonly_query` → `eda.db.connect_readonly`
→ `execute_on_connection` → `validate_sql` → authorizer / SQLite 限额 / progress
handler → 逐行读取 → ExecutionResult → AnalysisResult。
旧 `compute_core_metrics` / `compute_breakdown` 同样经 execute_on_connection。

原实现正确保留：extra=forbid、语义词表与合法维度兼容校验、绑定参数、
COUNT(DISTINCT order_id)、订单发生地区、整数分、零分母 NULL、稳定并列排序、
分类/商品“订单平均贡献额”、mode=ro / query_only、直接 close 观察、行数额外
探测、超预算行排除、top_n 与执行器截断区分。独立参考查询、人工 fixture
标准答案与阶段一/1.1 测试全部保留。

## 已确认问题与修复

1. **高：空 dbname 的无条件 main 映射不能证明安全。** 不限定数据库前缀的
   temp / attached 批准同名表 COUNT(*) 也会获得空 dbname。现在安装策略前
   检查 database_list，只有 main-only 连接的 column='' / dbname=None
   可兼容；其余缺失数据库身份的 READ 默认拒绝。安装后 DDL / ATTACH 被拒绝。
2. **中：计划边界不够严格。** fromisoformat 接受紧凑日期、ISO 周日期，
   但 SQL 使用文本日期比较；top_n 接受 bool / 字符串 / 整数浮点；过滤值没有
   独立长度上限；传入已有模型或 model_construct/model_copy 可绕过完整校验。
   现要求 YYYY-MM-DD、严格整数 1..100、单值最多 128 字符，并在解析及编译
   入口重新校验模型导出内容。旧 MetricFilters 同步收紧日期格式。
3. **中：AST 分析边界存在遗漏。** Unicode casefold 不等于 SQLite ASCII
   大小写语义；嵌套 WITH 的 recursive 标志、CTE 显式列重命名、重复 CTE
   以及列的数据库限定需要明确拒绝。现逐层检查，节点采用精确类型白名单。
   实测 100/300/800 层括号曾泄出 RecursionError，现返回固定 unsupported_sql。
4. **中：错误与结果回显输入。** Pydantic/SQLGlot 原异常、CLI 文件路径可进入
   对外消息，结构化 filters 携带绑定值。现固定错误文案，过滤元数据只保留
   dimension_id / op；无效 UTF-8 计划文件也分类处理。
5. **中：生命周期与限制。** 只读工厂初始化失败可能泄漏连接；旧连接限额被
   持久修改；回调安装不在清理范围；字符预算误用为 SQLite 字节预算；正无穷
   timeout 可禁用期限。现初始化失败关闭、finally 清理并恢复限额、UTF-8
   字节预算匹配、拒绝非有限 timeout。明确独占连接且无既有回调的接口约束。
6. **中：错误分类补足。** 优先识别 SQLite 基础错误码；execute/fetch 均分类，
   超大整数或非法编码绑定返回 invalid_params；资源错误不再因碰巧超过时间
   被覆盖成 timeout。缺库、损坏库、绑定错误、真实中断和恢复均有测试。
7. **中：旧报表丢失执行器截断状态。** 旧返回模型无法表达截断，仍可求分组
   总额；现直接抛 resource_limit，报表 CLI 输出稳定分类错误。
8. **低：结果说明不准确。** dimension_value 排序的 top_n 仍被称为“按指标值”；
   改为“按请求排序”。文档明确 SQL 与 Python 的 AOV 计算位置、返回字节预算
   边界和 SQLite LENGTH 可约束编码整行；指标公式和 fixture 数值未变。

## 真实 SQLite 回调证据

本机 CPython 3.12.6 / SQLite 3.45.3；下列为真实 READ 回调的
`(table, column, dbname)`，不是通过手工调用 _authorize 推定：

- `SELECT COUNT(*) FROM orders`：`('orders', '', None)`。
- `SELECT order_id FROM orders`：`('orders', 'order_id', 'main')`。
- `SELECT COUNT(*) FROM main.orders`：`('orders', '', 'main')`。
- `SELECT COUNT(*) FROM temp.orders`：`('orders', '', 'temp')`。
- `SELECT COUNT(*) FROM aux.orders`：`('orders', '', 'aux')`。
- temp 或 aux 中的 orders 经不带前缀的名称解析：`('orders', '', None)`。
- 查询批准视图会触发 main 中 v_revenue_lines / v_order_lines 和
  orders / order_items / products 的列读取，正常放行。

已 ATTACH 数据库测试先经 eda.db 的受信测试连接创建辅助内存对象，再装
authorizer 后尝试真实 READ，直接记录 SQLITE_DENY。生产入口仍 mode=ro。
同名 temp 表经 AST 合法 SQL 到执行器后返回 unauthorized，证明不是 AST 提前挡住。

## 修复与测试对应

新增 `tests/test_stage2_review.py`，63 个展开用例。主要对应：

- 计划：`test_noncanonical_dates_rejected`、`test_top_n_is_strict_integer`、
  `test_filter_value_length_is_bounded`、`test_constructed_or_copied_plan_cannot_bypass_compiler`。
- AST：`test_uncertain_or_unsupported_ast_is_rejected`、
  `test_cte_alias_column_case_matches_sqlite`、
  `test_deep_sql_is_safely_rejected_and_connection_recovers`。
- authorizer：`test_real_count_star_callback_and_view_expansion`、
  `test_real_foreign_database_read_is_denied`、
  `test_unqualified_foreign_count_has_no_database_and_is_denied`、
  `test_executor_classifies_real_temp_shadow_denial`、
  `test_authorizer_without_ast_denies_unapproved_actions`。
- 错误、真实超时及恢复：`test_invalid_bindings_and_recovery`、
  `test_real_sqlite_progress_interrupt_in_both_phases`、`test_corrupt_database_is_safe_error`。
  超时测试推进测试时钟并调用生产 progress callback，实际捕获 SQLite
  SQLITE_INTERRUPT，分别确认发生于 execute 和 fetchone；原有真实重 JOIN 超时保留。
- 关闭、字节限制及恢复：`test_engine_paths_close_and_restore_limits`、
  `test_readonly_factory_closes_if_configuration_fails`、
  `test_utf8_byte_budget_and_limit_restoration`、`test_deadline_must_be_finite_positive`。
- 脱敏与结果：`test_validator_errors_hide_parser_and_identifier_details`、
  `test_cli_plan_errors_never_leak_path_or_value`、
  `test_filter_metadata_is_redacted_and_dimension_ranking_is_accurate`。
- 旧报表：`test_legacy_filters_also_reject_noncanonical_dates`、
  `test_legacy_report_refuses_executor_truncation`、`test_legacy_report_cli_handles_executor_error`。

既有测试没有删除或放宽：合法维度但指标不允许的测试原本就真实有效；
_authorize 空 dbname 断言从 OK 收紧为 DENY；旧 fetch 中断包装器仅让受信
database_list 预检使用真实 cursor；编译器注入测试仅增加测试内受控词表项，
保留“SQL 无 DROP、绑定值原样”的原断言，并补非法词表的编译入口拒绝测试。
没有改 fixture CSV、期望 JSON 或独立参考计算。

## 本次文件变更

新增：`tests/test_stage2_review.py`、`docs/stage2-review.md`。

修改：`eda/plan/models.py`；`eda/sql/compiler.py`、`authorizer.py`、`catalog.py`、
`validator.py`、`executor.py`；`eda/query/cli.py`、`service.py`；`eda/db.py`；
`eda/metrics/core.py`、`definitions.py`、`report.py`；
`tests/test_sql_compiler.py`、`tests/test_sql_executor.py`；
`README.md`、`docs/architecture.md`、`docs/metrics.md`、`docs/progress.md`；
`semantic/metrics.yaml`（仅 AOV 文案）。其余 Git 状态项为开始时已有用户工作。

## 最终验证

- pip check：No broken requirements found，exit 0。
- 完整 pytest：374 passed in 7.28s，exit 0。基线 311 + 新增 63，原测试全部保留。
- git diff --check：exit 0；Git 仅提示 Windows CRLF 转换，不是空白错误。
- 两个指定 CLI 示例：exit 0，semantic_version=1.0.0，status=ok。
  total 返回 1 行，metric_value=455200 分；category 返回 4 行，依次为
  手机数码 219200、家用电器 125000、食品生鲜 60400、服饰鞋包 50600 分。
  两者均 truncated=false、truncation_reason=null、is_complete_population=true。

受保护数据库前后 SHA256 与 UTC mtime 完全相同：

- business.db：`323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029`；
  mtime `2026-09-13T08:49:27.0360631Z`。
- fixture.db：`77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773`；
  mtime `2026-09-13T08:49:30.2846596Z`。

## 支持范围、明确不处理与剩余边界

支持 SQLite 方言单条受控 SELECT、批准表/视图及列、绑定值、现有算术/
条件表达式、SUM/COUNT/COALESCE/SUBSTR、COUNT(*)、COUNT(DISTINCT column)、
GROUP/ORDER/LIMIT、显式 INNER/LEFT JOIN ON、可分析的非递归 CTE。
注释或字符串内分号不误判为多语句。

拒绝多语句、写入、DDL、PRAGMA、ATTACH/DETACH、系统表、未知函数、SELECT *、
递归 CTE、集合运算、窗口、JOIN USING/NATURAL/CROSS/RIGHT/FULL、派生表、
数据库限定引用、未知 AST、过深嵌套、CTE 遮蔽、重复 CTE 和 CTE 列重命名。
未扩大 SQL 支持范围。

明确不处理：触发器/递归视图的通用分析、用户可修改 schema 或 UDF 的威胁模型、
恶意外部数据库、跨平台/其他 SQLite 版本完整验证、OS 级隔离、硬实时/硬内存
保证、后续阶段功能及打包发布。进程内限额是尽力防护，锁等待与解析不提供
硬截止；无全局内存隔离。已有 temp/attached 连接上的不确定 COUNT 会保守拒绝，
需要主库列读取或使用新的主库只读连接。调用者旧回调无法自动恢复，接口明确禁止
共用或预装回调；非业务输入的恶意 Python 自定义连接/参数对象不在受控模型内。

在上述源码运行、受控业务数据库与本机验证范围内，达到阶段二提交标准。
没有 commit、push、merge、tag、分支切换或阶段三工作。

## 最终 git status --short

以下包含本次修复与开始时已有的用户未提交改动，不能全部归因为本次审查：

```text
 M .env.example
 M README.md
 M docs/architecture.md
 M docs/metrics.md
 M docs/progress.md
 M eda/config.py
 M eda/db.py
 M eda/metrics/core.py
 M eda/metrics/definitions.py
 M eda/metrics/report.py
 M pyproject.toml
 M semantic/metrics.yaml
 M tests/test_config_and_definitions.py
?? docs/stage2-review.md
?? eda/plan/
?? eda/query/
?? eda/sql/
?? examples/
?? tests/test_analysis_plan.py
?? tests/test_query_cli.py
?? tests/test_query_service.py
?? tests/test_sql_compiler.py
?? tests/test_sql_executor.py
?? tests/test_sql_validator.py
?? tests/test_stage2_review.py
```
