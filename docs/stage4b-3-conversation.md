# 阶段 4B-3：比较分析会话、恢复与 CLI

在 `feat/comparative-analysis`、HEAD `5daf3c4` 上增量实现。复用阶段 4A
的 ConversationService、同一 LangGraph、SqliteSaver 和 thread_lock；复用
4B-1 的 PeriodSpec、ComparativeAnalysisPlan 和 Decimal 计算，以及 4B-2 的
确定性编译、结果校验、证据和安全查询入口。没有第二套会话协议或 checkpoint 数据库。

## 规划协议

扩展原 TurnDecision，`prompt_version=conversation-plan-v2`。三个互斥状态：

- apply：status、必需 intent、可选 patch。
- clarify：status、非空 missing、可选 intent/patch；只返回固定澄清文案。
- refuse：status、refusal_category；只返回固定拒绝文案，无候选计划。

intent 仍为 new/refine/clarify_reply。patch.set 允许原单期间字段及
compare/mom/contribution、current_period、baseline_period；patch.clear 和
clear_filters 沿用显式清除语义。省略表示继承，显式 null 不表示清除。
JSON 重复键、额外字段、类型强转、状态字段冲突、多维度数组、未批准指标/筛选均拒绝。
实例递归重验，model_construct 的额外字段和显式字段集合不能被静默丢弃；
model_copy 注入也须重新校验。协议没有 SQL、物理对象、公式、步骤、查询次数、
数值答案、evidence_id/query_id 或自由解释文字入口。

模型每轮最多调用两次。第一次 JSON/结构/业务计划校验失败，可携带固定
invalid_plan 再规划一次；不回传原始响应或异常。澄清、拒绝、模型服务错误、
期限耗尽、执行错误、截断或安全拒绝均终止，不进入查询或模型重试循环。

## 日期与继承

比较解析器支持明确的 `YYYY年`、`YYYY年M月`。compare/contribution 必须给出
两个同粒度完整日历期间；代码将较早期间置为 baseline、较晚期间置为 current。
相同期间、粒度混用、未来或未结束期间、日区间、方向重设和模糊时间进入澄清。
模型若提供期间，必须与从本轮原文生成的完整 PeriodSpec 相等，不能换方向或标签。

mom 只接受一个完整自然月，baseline 由 previous_month 生成，模型不得提供它。
跨年和闰年沿用标准日历与 4B-1 校验。参考日期来自首次 thread 创建时的固定值，
不读取系统当前日期；未到月末的“本月环比”不会被当成完整月。最终计划必须经过
validate_as_of 和确定性编译的再次校验。完整日历期间并不证明数据覆盖无缺口。

- refine 从最近成功 confirmed 继承指标、两个期间和筛选；clarify_reply 续接对应 pending。
- 比较后贡献下钻只允许改 operation 和一个明确请求的 dimension_id，保留原指标、
  两期和筛选。“改按类别看贡献”替换原地区维度，不合并两个视角。
- 非可加指标或未批准贡献维度不查询；同时要求多个维度、合并视角、模型自动下钻
  或未经请求换维度均拒绝或澄清。“继续”不能自动重跑贡献下钻。
- 不从 total/breakdown 凭空造出第二个期间；跨分析类型不能继承另一类型的期间。
- 无效/模糊日期清除新草稿中的期间，不能回退旧时间执行。
- --new-topic 把本轮规划起点置空并强制 new。成功替换 confirmed；澄清保存新 pending
  并清除旧 confirmed；执行/模型失败保留轮次开始前 confirmed/pending。
- inherited/changed/cleared 由代码比较业务字段生成；筛选差异表示为 filters.region 等。

Fake 是有限示例语法，复用原单期间语法识别指标和筛选，未知表达澄清。
Fake 不读取数据库答案或标准答案；Real 也必须通过同一合并和日期校验。

## 图与统一预算

实际单期间路径保持：

```text
begin → plan → merge → execute → finalize
```

比较路径：

```text
begin → plan → merge → prepare_analysis
  → execute_step → check_step  （compare/mom 共 2 次；contribution 共 4 次）
  → calculate → finalize
```

begin 的拒绝直接进入 finalize；plan 服务错误直接 finalize；merge 的澄清、拒绝、
最终校验错误进入 finalize，唯一修复边是 merge → plan。准备或执行失败立即
finalize；check_step 仅在存在下一步且查询预算允许时返回 execute_step，否则进入
calculate 或 finalize。calculate 只调用 4B-1 Decimal 计算；finalize 是唯一 END 出口。

执行循环最多 4 次（最多 3 次返回 execute_step），compare/mom 最多 2 次。
成功比较路径 10 个节点，贡献路径 14 个节点；一次修复再增加 plan/merge 两个节点，
全图最多 16 次业务节点执行。服务 recursion_limit=20 只作额外护栏。

每轮默认总期限 30 秒，可用 ConversationService(timeout_seconds=...) 调整为有限正值。
TurnContext 在进入图前创建一个固定 deadline；规划、合并和所有查询共同消耗它。
Real transport 收到剩余时间并收紧已有超时；比较 prepare 使用同一个绝对 deadline，
每次子查询 timeout 取阶段二上限与剩余期限的较小值。单期间也收紧查询 timeout。

模型上限 2 次，单期间查询上限 1 次，compare/mom 2 次，contribution 4 次。
查询调用前计数，失败也消耗一次；第三步失败不执行第四步，不自动重试 SQL。
预算属于 Runtime.context，绝不落入 checkpoint。

4B-2 的 run_comparative_analysis 公开签名、输出和错误约定保持兼容；内部提取
prepare_analysis/execute_step/check_step/calculate/render_result。公共 runner 和图
调用同一套原语，_check 原校验保留；没有复制公式、SQL 编译或数据库执行逻辑。

## checkpoint 与临时状态

state_version 从 conversation-v1 升为 **conversation-v2**，semantic_version 保持
**1.1.0**。旧 state_version、旧 semantic_version、损坏结构明确返回
incompatible_checkpoint，不静默迁移。请使用新的 checkpoint 文件或 thread；
旧文件保留，不自动删除。恢复后接收新问题，不重放中断前的模型或查询。
上次 turn_status=in_progress 时返回 recovered=true；普通进程重启沿用已确认状态，
不因此把 recovered 置为 true。

持久化业务白名单：state_version、semantic_version、reference_date、confirmed、
confirmed_type、pending、turn_count、turn_status。confirmed_type 明确区分
single/comparative；confirmed 为相应完整业务计划。pending 仅含 analysis_type、
values、missing、intent；草稿只保存有界业务字段，已提供期间的顺序、粒度、
环比关系、未来边界和贡献可加性也须合法。

单期间业务字段是 metric_id、operation、dimension_id、start_date、end_date、
top_n、order_by、sort_direction、filters。比较业务字段是 metric_id、operation、
current_period、baseline_period、dimension_id、top_n、sort_direction、filters。
PeriodSpec 只含 start_date/end_date/label/granularity/is_complete。
filters 仅含通过语义验证的 dimension_id/op/value；允许保存恢复需要的筛选值。
SqliteSaver 另有框架 checkpoint ID、父版本和调度通道元数据。

Runtime.context 临时字段为 question、db_path、model、limits、request_id、
model_call_count、raw_response、candidate、result、recovered、new_topic、base、dates、
decision、analysis、outcome、error_code、refusal_reason、repair_error、execution_count、
timeout_seconds、clock、deadline、node_path、comparative、comparison、contribution。
comparative 包含编译步骤、预算、当前索引、待验结果、临时证据和已验结果。
这些对象、问题、prompt、原始响应、SQL、绑定、数值结果、证据映射、Key 和内部异常
均不得持久化。日志不记录问题、筛选值、prompt、SQL、结果或 Key。

沿用原 OS thread_lock，覆盖读取、规划、执行及同步保存；同 thread 忙时立即拒绝，
不同 thread 隔离。没有新增连接旁路或 checkpoint 管理逻辑。

## 输出与证据

成功返回 request_id、status、analysis_operation、semantic_version、state_version、
prompt_version、model_name、reference_date、model_call_count、query_count、node_path、
baseline_period、current_period、comparison、可选 contribution、evidence、warnings、
inherited/changed/cleared、recovered 和 completeness。finding_type 为 observation
或 decomposition，不输出 hypothesis；询问原因时固定返回“无法判断原因”。

每项 evidence 引用实际安全查询返回的 query_id，角色固定为 baseline_total、
current_total、baseline_breakdown、current_breakdown。失败会话响应不携带候选、
SQL、绑定值、路径、原始输出、内部异常或部分证据。4B-2 公共接口仍保留其原来的
失败前已验证证据行为，未改变该接口契约。

truncated/非完整总体停止计算。贡献先核对完整分项，再用 top_n 截取展示，
hidden_dimension_count/hidden_net_change 由原计算函数生成。零基期 change_rate=null，
总变化为零时 contribution_rate=null；CLI 只省略无值的顶层输出，不丢弃嵌套数值 null。
Decimal JSON 保持字符串。calendar_period_complete=true 与 data_coverage_verified=false
分别表示日历完整与尚未验证的数据覆盖，不得混用。

## 离线跨进程 CLI

下面每一条都是独立进程，默认 provider=fake。首次使用新状态文件；同 thread 后续
调用从该文件恢复。两次 --new-topic 分别展示年份比较和独立月份环比。

```powershell
.venv\Scripts\python.exe -m eda.conversation.cli "对比2024年和2023年的有效订单成交额。" --thread demo4b3 --db data\fixture.db --checkpoint-db data\demo4b3.db --reference-date 2024-12-31 --new-topic
.venv\Scripts\python.exe -m eda.conversation.cli "查看2024年2月成交额环比。" --thread demo4b3 --db data\fixture.db --checkpoint-db data\demo4b3.db --new-topic
.venv\Scripts\python.exe -m eda.conversation.cli "按地区看变化贡献。" --thread demo4b3 --db data\fixture.db --checkpoint-db data\demo4b3.db
.venv\Scripts\python.exe -m eda.conversation.cli "改按类别看贡献。" --thread demo4b3 --db data\fixture.db --checkpoint-db data\demo4b3.db
.venv\Scripts\python.exe -m eda.conversation.cli "2024年订单数" --thread demo4b3 --db data\fixture.db --checkpoint-db data\demo4b3.db --new-topic
```

已实际执行的五进程演示全部 success/exit 0，查询数依次 2、2、4、4、1，模型调用均 1。
年份比较为 2023-01-01 至 2023-12-31（0）与 2024-01-01 至 2024-12-31（455200），
change_rate=null。环比及后两次贡献均为 2024-01-01 至 2024-01-31（223200）与
2024-02-01 至 2024-02-29（232000），absolute_change="8800"，
change_rate="0.039426523297"。最后 total 无比较期间继承。

中断前已执行的地区贡献 evidence_id → query_id 示例（每次查询重新生成）：

- baseline_total → e67a2b6e742d43b1a2dcf8e0954cf969
- current_total → c2844e7428cf4e468d6dbd95fa80220a
- baseline_breakdown → 9cb89c9e0fb945f6aa06bf0dc33a6512
- current_breakdown → f7cb5686548e41a2b029c4c8c116977b

fixture 地区贡献 top_n=1 时只展示华东 +156700，隐藏 2 个维度、合计 -147900；
完整总变化仍为 8800。自动化测试另以 seed=17、83、地区/类别两个视角直接遍历
生成数据核算独立参考，不调用生产比较计算函数获得期望值。

## Real：用户手工验收已完成三个受控场景

以下结果来自用户提供的手工验收记录，本次文档更新没有重新调用模型，
不保存 Key、完整问题、完整 prompt、原始模型响应、SQL 或绑定值。

1. deepseek-flash 真实年份比较成功：operation=compare；baseline=2023 年，值 0；
   current=2024 年，值 455200；absolute_change=455200；change_rate=null；
   model_call_count=1，query_count=2，exit_code=0。
2. 同一 thread/checkpoint 地区贡献下钻成功：dimension_id=region；
   华东 223300、华北 189900、西南 42000，分项合计 455200；
   query_count=4，model_call_count=1，exit_code=0。
3. 同一 thread/checkpoint 改按类别贡献成功：dimension_id=category；
   手机数码 219200、家用电器 125000、食品生鲜 60400、服饰鞋包 50600，
   分项合计 455200；query_count=4，model_call_count=1，exit_code=0。

三次成功调用均使用 prompt_version=conversation-plan-v2、state_version=conversation-v2、
semantic_version=1.1.0、reference_date=2024-12-31、timeout_seconds=60。
第一次成功响应 recovered=true，表示此前 in_progress 状态被恢复后处理新输入，
不表示结果重放；后两轮 recovered=false。

真实调用前曾发生 DNS 间歇性解析超时。用户将 WLAN DNS 从 114.114.114.114 改为
119.29.29.29、223.5.5.5 后，连续三次 HTTPS 探测成功。这里只记录本次环境故障和
处理过程，不据此声称 DeepSeek 夜间停服，也不推断 DeepSeek 的全局可用性。
Key 仅通过进程环境变量临时传入，验证后已清除；本记录不包含凭据或请求/响应原文。

真实验证仅覆盖上述三个受控场景，不覆盖全部模型行为或服务运行条件。
calendar_period_complete=true 不代表 data_coverage_verified=true；
不宣称系统不存在所有未知安全缺陷。

### 手工超时后的最小修复

用户报告两次新 thread/checkpoint 均在 begin → plan → finalize 返回
model_error/model_timeout，模型调用 1、查询 0、exit 4。此前离线诊断未调用 DeepSeek；后续成功验收见上文。
Settings.llm_timeout_seconds 默认 60.0，环境变量为 LLM_TIMEOUT_SECONDS（配置名不区分
大小写，现有 Settings 支持环境及 .env）；本地诊断读取值为 60.0。
ConversationService.timeout_seconds 默认 30.0。原 CLI 无参创建 RealConversationModel，
由其继承的初始化读取 Settings；创建 ConversationService 时未传 timeout_seconds。

图创建绝对 deadline = 起始单调时钟 + 总期限，规划传入 remaining_seconds；
Real 最终传给 HTTPSConnection 的 timeout = min(LLM_TIMEOUT_SECONDS, remaining_seconds)。
因此原默认传输限制略小于 30 秒。它是 socket 阻塞操作超时，图另在节点边界检查统一
总期限，属于合作式期限，并非强制中断所有操作的硬实时计时器。

空历史、相同参考日期的离线上下文测量：阶段三 6626 字符 / 8529 UTF-8 字节，
conversation-plan-v2 为 7529 字符 / 8908 字节；字节增加约 4.4%。这不含用户问题及
HTTP 包装，不能据此把两次超时归因于 Prompt 大小；服务端响应耗时尚无实测证据。

最小修复仅为会话 CLI 增加 --timeout-seconds，有限数值范围 [1, 120]，默认 30；
直接传入 ConversationService 的统一总期限。保留 Real 两期限取较小值，不自动覆盖
用户较短的 LLM 配置。需延长到 60 秒时通过现有配置机制同时协调两者：

```powershell
$env:LLM_TIMEOUT_SECONDS = "60"
.venv\Scripts\python.exe -m eda.conversation.cli "对比2024年和2023年的有效订单成交额。" --thread real4b3-timeout60 --db data\fixture.db --checkpoint-db data\real4b3-timeout60.db --reference-date 2024-12-31 --provider real --new-topic --timeout-seconds 60
```

Key 仍由用户在进程环境中安全配置，以上为不含 Key 的手工示例；用户后续已完成上文三轮验收。若设置更长总期限，
也应显式检查 LLM_TIMEOUT_SECONDS；例如配置 15 秒时，即使 CLI 设 60，传输仍最多 15 秒。
超时保持固定 model_timeout，不重试，不进入 SQL，不记录 Key、问题、Prompt 或响应。

阶段 4B-3 的三轮用户手工真实验收已成功。RealConversationModel 复用共享 HTTPSConnection transport，
不从 .env 取 Key，仅取进程 DEEPSEEK_API_KEY；无 SDK 自动重试，无重定向跟随。
保留响应上限并增加请求字节上限，剩余期限收紧传输超时。自动测试仅使用 mock。
以下命令在用户自行安全配置进程环境后手工执行，不应把 Key 写进命令或文档：

```powershell
.venv\Scripts\python.exe -m eda.conversation.cli "对比2024年和2023年的有效订单成交额。" --thread real4b3 --db data\fixture.db --checkpoint-db data\real4b3.db --reference-date 2024-12-31 --provider real --new-topic
.venv\Scripts\python.exe -m eda.conversation.cli "按地区看变化贡献。" --thread real4b3 --db data\fixture.db --checkpoint-db data\real4b3.db --provider real
.venv\Scripts\python.exe -m eda.conversation.cli "改按类别看贡献。" --thread real4b3 --db data\fixture.db --checkpoint-db data\real4b3.db --provider real
```

退出码沿用 0/2/3/4/5/6/7：成功、澄清、拒绝、模型错误、计划校验错误、执行错误、
会话错误。新增比较错误仅用固定 code，例如 timeout、budget_exhausted、incomplete_result、
metadata_mismatch、invalid_result_shape、undefined_metric、reconciliation_failed。

## 验证记录与边界

初次开发基线 806 passed in 67.18s；中断前完整回归为 888 passed、1 failed。
2026-09-15 从恢复的 14 个变更文件续做，分支/HEAD 符合、diff check 通过；
恢复现场九文件定向 384 passed、1 failed in 99.55s。唯一失败是新增测试直接比较
checkpoint JSON 列表和模型 tuple；已改为按 Session 校验后比较完整业务状态，并增加
轮次、pending 和 in_progress 断言。旧 4A 精确节点集合仅更新为实际扩展后的完整集合，
原路径、预算、隔离、安全和结果断言保留，还增加返回路径与查询计数断言。

最终九文件定向 **393 passed in 99.67s**；完整离线回归
**897 passed in 118.48s**（exit 0），保留原 806 项，增加 91 项。
`.venv\Scripts\python.exe -m pip check` 返回 `No broken requirements found.`。
`git diff --check` 通过；所有新增及修改 Python/Markdown 文件均严格 UTF-8 解码，
Python AST 解析成功，逐行无尾随空白。原测试函数全部保留。

再次实际运行五个独立 Fake CLI 进程（thread=stage4b3-final-resume，
checkpoint=data/stage4b3-final.db），全部 success/exit 0，查询数 2/2/4/4/1，
模型调用均 1，数值与上文一致。第三、四个进程从持久化业务状态继承期间，
分别使用 region/category；最后 new-topic 单期间结果 comparison=null。
此处 recovered=false 表示没有恢复“中断中的轮次”，不表示没有读取历史 confirmed。
最终地区贡献的实际 evidence_id → query_id：

- baseline_total → 60813c3311044036a3131934e3ace68b
- current_total → 66f05a50d4af45b29598abf00e517449
- baseline_breakdown → 72ad3c80de7d47afa1f5866f855fce7d
- current_breakdown → efc51902be26407d85b7fea2ef814abc

数据库 SHA256 与 UTC mtime 均保持基线值：

- business.db：`323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029`；
  `2026-09-13T08:49:27.0360631Z`。
- fixture.db：`77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773`；
  `2026-09-13T08:49:30.2846596Z`。

fixture CSV、标准答案、语义配置、阶段二实现和依赖文件无变更；安装版本满足
pyproject 与 requirements 的约束。默认 Fake 的断网由子进程 socket 禁用测试覆盖。
DEEPSEEK_API_KEY 环境变量不存在；Git 只跟踪既有 `.env.example`，没有 `.env`、
运行时数据库、WAL/SHM 或锁文件；新 checkpoint 受现有 ignore 规则保护。


边界：自然语言日期与 Fake 为有限中文语法，未知表达澄清，不支持任意日期区间、
隐式同比、季度或自由因果分析。期限是进程内合作式限制，不能强制杀死不合作的
注入 runner；不提供硬实时保证。每步仍独立打开只读连接，无跨步一致快照；
应对稳定数据源使用。数据库覆盖未验证；上游 SQL 比率可能已有浮点近似，
Decimal 转换不能恢复丢失精度。checkpoint 提供业务状态恢复，不保证旧结果重放，
无跨版本迁移、历史清理或多机锁。真实模型已验证上文三个受控场景；
其他场景、长期及全局服务可用性未由本次验证覆盖。

停止于阶段 4B-3；没有图表、Streamlit、多 Agent、自由 SQL/Python 或后续评测。

## 修改清单与最终 Git 状态

共 19 个文件：16 个已跟踪修改、3 个新增（包含后续最小超时修复）。运行时演示文件被忽略。
分支保持 `feat/comparative-analysis`，HEAD 保持 `5daf3c4`；未操作备份 stash。

```text
 M README.md
 M docs/architecture.md
 M docs/progress.md
 M eda/agent/real.py
 M eda/conversation/cli.py
 M eda/conversation/context.py
 M eda/conversation/fake.py
 M eda/conversation/graph.py
 M eda/conversation/merge.py
 M eda/conversation/models.py
 M eda/conversation/real.py
 M eda/conversation/service.py
 M eda/query/comparative.py
 M tests/test_conversation_cli.py
 M tests/test_conversation_graph.py
 M tests/test_conversation_real.py
?? docs/stage4b-3-conversation.md
?? eda/conversation/comparative.py
?? tests/test_conversation_comparative.py
```

## 超时修复验收补记

本轮只增量修改 eda/conversation/cli.py、tests/test_conversation_cli.py、
tests/test_conversation_real.py、README.md 和本文件，保留其余阶段 4B-3 现场。
新增 13 项离线测试；第一次定向 52 passed / 1 failed，失败是新测试 mock 的函数名
误写，修正为 run_analysis_plan 后定向 **53 passed in 44.55s**。
覆盖默认/60 秒统一 deadline、非法范围与非有限数值、环境配置与剩余期限取最小、
固定超时返回、零查询、无重试及日志脱敏。未更改规划协议、安全校验或查询执行逻辑。

最终完整离线回归 **910 passed in 113.12s**（exit 0），原 897 项保留，新增 13 项。
pip check 返回 No broken requirements found；git diff --check 及 UTF-8/AST/尾随空白检查通过。
数据库 SHA256/mtime 保持上述基线；该离线超时修复当时未调用真实模型，
未操作 stash、提交或切换分支。后续用户真实验收结果见 Real 节。

## 阶段 4B 最终输出契约修复

输出缺口位于 4B-2 的共享结果模型：4B-1 已有 Decimal 计算和 display_decimal，
4B-2 原先仅输出 current/baseline、dimension_change 及原始比例，4B-3 直接沿用。
现在 comparison 增加 baseline_value、current_value、change_rate_display、
baseline_period、current_period，保留 absolute_change、change_rate、evidence_ids
及旧 current/baseline。外层 baseline_period/current_period 也继续保留。
贡献 rows 每条增加 baseline_value、current_value、absolute_change、
contribution_rate_display，保留 dimension_value、contribution_rate、evidence_ids
及旧 current/baseline/dimension_change。

兼容字段由共享结果模型从既有精确字段确定性派生，展示仅调用已有 display_decimal；
没有复制计算公式。比例保持 12 位 Decimal，JSON 精确值为字符串；展示使用
ROUND_HALF_UP 两位百分比。无定义比例保持 null，展示字符串为“无定义”。
模型不生成展示值。run_comparative_analysis、ConversationService 与 Fake CLI
使用同一结果模型，三条输出路径一致；checkpoint 不保存这些结果字段。

离线 fixture 比较示例（comparison 中的部分字段）：

```json
{"baseline_value":"223200","current_value":"232000","absolute_change":"8800","change_rate":"0.039426523297","change_rate_display":"3.94%","baseline":"223200","current":"232000","evidence_ids":["baseline_total","current_total"]}
```

同一对象内 baseline_period 为 2024-01-01 至 2024-01-31，current_period 为
2024-02-01 至 2024-02-29，含完整 PeriodSpec 元数据，与外层期间一致。
零基期年份比较仍为 baseline_value="0"、current_value="455200"、
change_rate=null、change_rate_display="无定义"。
离线合成贡献用例验证 0.500000000000 → 50.00%、-0.500000000000 → -50.00%、
1.500000000000 → 150.00%；总变化为零时 null → 无定义。

本次未改模型协议、图结构、查询预算、SQL 编译器、安全执行器、语义公式或
checkpoint 白名单；失败响应不增加结果字段。没有调用真实模型或修改项目运行时数据库。

最终离线回归：**921 passed in 110.81s**（exit 0），保留原 910 项、新增 11 项。
pip check 返回 No broken requirements found；git diff --check 通过，新增测试文件
UTF-8/AST/尾随空白检查通过。测试使用新专用临时目录，项目运行时数据库未修改或删除。
未调用真实模型，未提交、推送、操作 stash 或切换分支。停止于本次输出契约修复。
