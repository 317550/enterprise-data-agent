# 阶段 4A：严格多轮规划与本地恢复

阶段 4A 在原单轮接口之外新增 `eda.conversation.service.ConversationService` 与
`python -m eda.conversation.cli`。原 `eda.agent.service.run_question` 和单轮 CLI
保持兼容。每轮只执行一个经过完整校验的 AnalysisPlan，执行路径仍是阶段二的
`run_analysis_plan`；没有新增 SQL 生成、数据库发现或执行通道。

本次从 `feat/conversation-state` 的未提交现场继续，先验证原有 13 项合并测试，
再增量完成合并、最小图、checkpoint、恢复、并发和 CLI。原有本地文件保留并延续。
定向收口前再次复现 554 passed 基线，在原实现上拆分节点、增加真实多轮适配器
和确定性 `--new-topic`，没有重建项目或进入阶段 4B。

## 严格协议与合并

`TurnDecision.status` 仅允许 `apply`、`clarify`、`refuse`。字段按状态互斥，
额外字段、显式 null、重复 JSON 键、重复清除和冲突修改均拒绝。
`clarify` 必须提供非空 `missing`，使用封闭的业务缺失字段集合；
`refuse` 只携带受控拒绝类别，不携带计划。

意图决定唯一的合并来源：

- `new`：从空计划开始，不继承旧指标、日期、拆分、排名或筛选。
- `refine`：只继承最近成功查询的 `confirmed`；无历史则澄清。
- `clarify_reply`：只续接独立的 `pending` 草稿；无草稿则澄清。
  意图尚未明确的草稿不能通过一次空回复变成可执行计划，必须明确 new/refine。

`patch.set` 中省略字段表示继承，提供字段表示替换，不接受 null。
`set.filters` **替换整个筛选列表**；`clear_filters` 只清除列出的 region/category。
`patch.clear` 显式清除字段；清除 `dimension_id` 同时切换为 total，并清除
top_n、order_by、sort_direction。清除必需字段会进入澄清。
清除 top_n 不改变已有拆分与排序；breakdown 缺失排序时由 AnalysisPlan 填充默认值。

本轮明确日期由 `resolve_dates` 决定，模型日期必须与之相同；本轮未提供日期时
模型不得编造日期。无效或歧义日期会清除候选中的旧日期并澄清，不回退执行历史日期。
参考日期首次创建 thread 时固定；重启时不传参数则继续使用保存值，显式传入不同值
会返回 `reference_date_mismatch`，需要使用另一个 thread。

草稿也限制指标、维度、日期、筛选的形状和批准值。执行前仍须通过完整 AnalysisPlan
的字段组合与语义校验。返回的 `inherited`、`changed`、`cleared` 由代码计算，
筛选差异按 `filters.region` 等叶字段展示，不采用模型的解释文字。

## 最小图与状态边界

图结构为 `START → begin → plan → merge → execute → finalize → END`，
共五个业务节点，使用 `durability="sync"`。

- begin：写入 in_progress，复用既有范围预检和日期解析，准备本轮继承起点。
- plan：每次进入只调用一次 PlannerModel；调用计数始终不超过两次。
- merge：校验 TurnDecision，处理明确新话题意图，执行纯合并与完整 AnalysisPlan 校验。
- execute：最多调用一次既有 run_analysis_plan，不执行第二次规划或查询修复。
- finalize：只生成确定性响应，按成功/澄清结果更新 confirmed/pending，并写 completed。

条件边为 begin → plan/finalize（预检拒绝），plan → merge/finalize（模型错误），
merge → execute/finalize（澄清、拒绝或最终计划错误）。首次无效计划可经
merge → plan 修复一次；第二次仍无效则只能 finalize。execute 的 success 与
execution_error 两条条件均进入 finalize。finalize 唯一出口是 END。
正常成功路径五个节点，一次修复成功路径七次节点执行；服务另设 recursion_limit=10。
实际 stream 节点路径、调用计数、执行计数及中间节点无状态写入均有定向测试。

实际终止路径（以下均从 START 开始、在 finalize 后到 END）：

- success / execution_error：begin → plan → merge → execute → finalize。
- clarification_required：begin → plan → merge → finalize。
- refused：预检拒绝为 begin → finalize；模型拒绝为 begin → plan → merge → finalize。
- model_error：begin → plan → finalize。
- plan_validation_error：begin → plan → merge → plan → merge → finalize。

若首次合并校验失败，在以上后续路径的 plan 前多一次 plan → merge 尝试；
成功修复的完整路径为 begin → plan → merge → plan → merge → execute → finalize。
只有尚未形成终止结果的第一次校验失败走修复边；最终计划错误不会再返回 plan。
无效 CLI 参数、无效 thread 或 thread_busy 在进入图之前返回 conversation_error。

持久化图只有一个 `session` 通道，包含：

- state_version、semantic_version、reference_date；
- 至多一个已确认业务计划和一个有界草稿；
- 有上限的 turn_count，以及 completed/in_progress 标记。

业务字段白名单为 `state_version, semantic_version, reference_date, confirmed,
pending, turn_count, turn_status`。confirmed 为完整 AnalysisPlan；pending 仅有
`values, missing, intent`。二者的业务计划字段限于 `metric_id, operation,
dimension_id, start_date, end_date, top_n, order_by, sort_direction, filters`；
filters 仅有经过语义校验的 `dimension_id, op, value`。
框架另保存 checkpoint ID、版本、父记录、线程与调度元数据；不是额外业务记忆。

问题原文、request_id、模型实例、数据库路径、执行限制、调用预算、原始模型响应、
本轮合并候选和完整查询结果都在 `Runtime.context`，不进入 checkpoint。
临时字段完整清单为 `question, db_path, model, limits, request_id, model_call_count,
raw_response, candidate, result, recovered, new_topic, base, dates, decision,
analysis, outcome, error_code, refusal_reason, repair_error, execution_count`。
图内部的输入/调度通道也只含上述状态或调度标记。查询结果仅随本次调用返回；
重启不会从磁盘恢复旧结果，也不保存对话消息列表。
经用户确认，允许 confirmed/pending 保存通过语义校验的业务筛选值，以支持
跨进程继承；筛选值不写入日志，也不持久化原始请求/响应或完整 prompt 副本。

LangGraph 的状态持久化与 Runtime 用法参照
[官方 persistence 文档](https://docs.langchain.com/oss/python/langgraph/persistence) 和
[Runtime 接口](https://reference.langchain.com/python/langgraph/runtime/Runtime)，
并核对本机安装版本：LangGraph 1.2.11、SQLite checkpointer 3.1.1、checkpoint core 4.2.0。
pyproject.toml 的最低版本收紧到已验证的 1.2.11 / 3.1.1，仍保留 major 上界；
requirements.txt 的固定版本与 requirements.lock.txt 一致。core 是间接依赖，
由 lock 固定为 4.2.0；三个已安装版本均满足声明。本次没有更新依赖或重写锁文件。

## 独立数据库、失败与恢复

checkpoint 默认保存到 `data/checkpoints.db`，由 SqliteSaver 管理。
业务库继续由 `eda.db.connect_readonly` 打开；两类连接不共享。
打开 checkpoint 前，先拒绝当前/配置业务库、fixture 库的相同路径及文件别名，
并只读检查已有文件：含其他业务表或视图的数据库不会交给可写 saver。
checkpoint 本身不支持硬链接别名，以免同一文件产生不同锁身份。
默认 data 目录，以及 .db/.sqlite/.sqlite3 数据库、其 WAL/SHM/journal 和对应
.locks 目录均被 gitignore 覆盖，并用 git check-ignore 实测。

模型、验证或执行失败，以及拒绝请求，均保留之前的 confirmed/pending；
失败候选不会成为下轮历史。此时“继续/refine”仍指向上次成功计划，
“clarify_reply”仍指向原草稿。成功会替换 confirmed 并清除 pending。
明确 new 请求进入澄清时会清除旧 confirmed，保存新话题的独立草稿。
new 请求失败则整轮不改变既有业务计划；调用方可再次明确发送新话题。

CLI `--new-topic`（API `run(..., new_topic=True)`）是确定性入口：本轮传给模型
的 confirmed/pending 均为空，merge 由代码强制 new 意图。即使模型猜测 refine
或 clarify_reply，也不会继承旧条件；new 已明确时移除多余 intent/history 澄清项，
其余缺失字段继续经过合并校验。成功替换 confirmed；澄清清除旧 confirmed 并
保存新草稿；任何失败或拒绝仍保留轮次开始前的业务状态。原自然语言 new 意图保留。

进程重启后按 thread_id 读取最新状态，重新校验状态版本、语义版本和业务字段。
不支持静默迁移不兼容状态。若上轮停在 in_progress，则下一次请求返回
`recovered=true`，从保存的业务状态处理**新输入**。不会调用 `invoke(None)`
重放旧模型请求或执行，也不会继承旧预算。若查询已完成但完成状态尚未落盘，
该次结果可能丢失；这里提供业务状态恢复，不提供 exactly-once 查询/结果重放。

每个 checkpoint 的业务状态有界；SQLite 历史记录与 thread 数仍会随使用增长。
本阶段不提供历史清理、跨版本迁移、网络文件系统部署或多机协调。

## 同一 thread 的并发策略

采用**忙时立即拒绝**：按 checkpoint 规范路径与 thread_id 获取本机 OS 文件锁，
覆盖读取最新状态、模型规划、查询到同步 checkpoint 落盘。
同一 thread 的并发请求返回 `thread_busy`，不调用模型、不修改业务状态。
不同 thread 可独立运行，SQLite 仅在短暂写入期间协调访问。
同一进程的不同服务实例和本机不同进程遵循相同锁协议。

进程终止后 OS 自动释放锁。锁文件放在 `<checkpoint文件名>.locks/`，
文件名使用 thread_id 摘要。锁文件保留，不在运行中删除，避免锁身份分裂。
thread_id 限制为 1–128 个 ASCII 字母、数字、下划线、点或连字符，首字符为字母或数字。
锁只保护通过本服务发起的调用；直接调用底层图/saver 不属于公开并发接口。

## 多轮 CLI 与真实适配器

CLI 每次处理一轮，相同 `--thread` 可跨进程继续。`--provider fake/real` 默认 fake。
下面是离线演示，首次使用 --new-topic，后续不加该标志即可续接：

```cmd
.venv\Scripts\python.exe -m eda.conversation.cli "订单数" --thread demo --db data\fixture.db --provider fake --new-topic
.venv\Scripts\python.exe -m eda.conversation.cli "2024年" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "改成成交额" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "按地区" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "只看华东" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "取消地区筛选" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "取消拆分" --thread demo --db data\fixture.db
.venv\Scripts\python.exe -m eda.conversation.cli "客单价" --thread demo --db data\fixture.db --new-topic
```

可用 `--checkpoint-db` 指定独立状态库。Fake 是有限、可审查的语法：支持完整单轮问题、
指标替换、日期回复、按维度拆分、前 N、升降序、只看批准筛选值、显式取消和新话题。
未知内容进入澄清，不能视为通用自然语言理解。模型接口仍为 `plan(question, context)`，
但多轮 context 和输出协议不同，真实多轮请使用 RealConversationModel。

该适配器只复用阶段三 RealPlannerModel 的单次 `_request_json` HTTPS 传输，
不使用 PlannerDecision。传输仍为标准库 HTTPSConnection，无 SDK、无自动重试；
保留 HTTPS、禁止 URL 用户信息/query/fragment、超时、响应字节上限、工具调用拒绝
及错误脱敏约束。真实输出必须通过严格 TurnDecision 校验，非法输出交给图的一次
计划修复预算处理。模型不能生成 SQL。
4A.1 最终审查补齐 URL 解析、端口校验、空用户信息、HTTPS 连接构造和关闭阶段的
脱敏边界：配置问题为 model_configuration_error，连接生命周期异常为
model_unavailable（请求超时仍为 model_timeout），不回显内部异常或响应正文。

prompt 从受控的参考日期、语义层、confirmed/pending 重新构建；不转发调用者的
任意规则、原始聊天记录、SQL、数据库结果或其他 context 扩展键。
修复只反馈 invalid_plan，不回传原始输出。Key 只读取进程环境变量
DEEPSEEK_API_KEY，不读取 Settings 中由 .env 加载的 Key；缺失时返回
model_error / model_configuration_error（退出码 4），不会建立 HTTPS 连接。

以下真实多轮命令**仅供用户手工运行，本次没有执行**。先在当前进程环境中安全设置
DEEPSEEK_API_KEY，不要将 Key 写入命令记录或文档：

```cmd
.venv\Scripts\python.exe -m eda.conversation.cli "订单数" --thread real-demo --db data\fixture.db --provider real --new-topic
.venv\Scripts\python.exe -m eda.conversation.cli "2024年" --thread real-demo --db data\fixture.db --provider real
.venv\Scripts\python.exe -m eda.conversation.cli "改成成交额" --thread real-demo --db data\fixture.db --provider real
```

预期第一轮澄清日期，第二轮成功，第三轮继承日期并替换指标。模型与远端协议可用性、
自然语言理解效果仍待手工烟雾验证；单轮入口和默认 provider 行为保持兼容。

JSON 返回状态为 success/clarification_required/refused/model_error/
plan_validation_error/execution_error，对应退出码 0/2/3/4/5/6。
澄清返回受控 `missing` 字段；会话输入、存储或并发错误返回 conversation_error，
退出码 7，并携带脱敏 error_code。

## 验证与范围

五个定向测试文件覆盖协议与合并、实际节点路径、SQLite 恢复、并发、CLI 及
真实适配器的 mock transport；另运行原单轮 test_agent_planning.py 保证兼容。
收口前基线复现 554 passed in 57.18s；收口后定向加单轮兼容测试
203 passed in 47.50s（其中多轮 98 项、单轮 105 项）。
命令为 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`。
系统 Python 的 pytest 版本过旧，测试使用项目虚拟环境；Windows 临时目录权限
问题通过已授权的沙箱外测试执行解决。pip check 与 git diff --check 通过。
跨进程测试实际终止在模型调用中等待的 Fake 子进程，再由新服务恢复。
所有查询测试使用临时目录生成的 fixture 库；不改动仓库 fixture 或标准答案。
原有导入守护仅对 conversation/graph.py、conversation/checkpoint.py 的 langgraph
导入增加精确许可，阶段二 SQL 与数据层守护保持原样。

不进入阶段 4B：期间对比、贡献拆解、图表、Streamlit、多 Agent、自由 Text-to-SQL
均未实现。单轮及多轮真实模型烟雾测试仍需另行执行；本次测试全部离线。

## 4A.1 最终审查记录

本次最终验证结果（2026-09-14）：初始五文件定向 98 passed in 47.98s；
修复对应 mock 与单轮兼容测试 134 passed in 6.25s；最终五文件定向
**103 passed in 46.48s**；之后唯一一次完整回归 **601 passed in 62.41s**。
原 596 项保留，新增 5 项用于已复现的 HTTPS 异常边界；没有重复扩充业务测试。
pip check：No broken requirements found；git diff --check：exit 0（仅 Windows
换行提示）。测试全部使用 Fake/mock，未调用真实模型。

实际审查包含全部未跟踪 conversation 源码、五个测试文件与本说明，
不是仅根据 git diff --stat 中的七个已跟踪文件判断范围。
本轮代码修复限于共享 HTTPS 传输的异常边界；增加 5 项已先复现失败的回归用例，
并在既有默认 CLI 测试中增加 socket 断网断言，未删除或放宽任何测试。

pyproject.toml 既有改动包含依赖下限和包列表：LangGraph >=1.2.11、SQLite saver
>=3.1.1，与 requirements.txt 及 requirements.lock.txt 已有的精确固定版本一致；
core 4.2.0 为间接锁定依赖。没有新的解析版本需要同步，两个 requirements 文件无需
产生人为改动。追加 eda.conversation 包也不改变依赖解析。

受保护数据库的 SHA256 和 UTC mtime 在审查前后核对：

- business.db：`323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029`；
  `2026-09-13T08:49:27.0360631Z`。
- fixture.db：`77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773`；
  `2026-09-13T08:49:30.2846596Z`。

Git 可见文件不含 .env、数据库、WAL/SHM/journal 或锁文件；凭据扫描只命中既有
脱敏测试常量与示例占位值。fixture CSV、标准答案、语义配置和阶段二代码无 diff。
checkpoint 白名单、瞬态材料不落盘、默认 CLI 不联网均由临时库/Fake/mock 测试验证。

当前工作区完整修改清单（包括未跟踪文件）：

- `eda/conversation/__init__.py`、`models.py`、`merge.py`、`context.py`、`graph.py`、
  `checkpoint.py`、`service.py`、`fake.py`、`real.py`、`cli.py`。
- `tests/test_conversation_merge.py`、`test_conversation_graph.py`、
  `test_conversation_checkpoint.py`、`test_conversation_cli.py`、`test_conversation_real.py`。
- `eda/agent/real.py`、`tests/test_project_constraints.py`、`pyproject.toml`、`.gitignore`。
- `README.md`、`docs/architecture.md`、`docs/progress.md`、`docs/stage4a-conversation.md`。

此次最终审查新增的修改仅为共享 HTTPS 异常边界、对应回归用例、既有默认 CLI
测试的断网断言及文档修订；其余阶段 4A 修改从当前现场保留，未重新生成。
