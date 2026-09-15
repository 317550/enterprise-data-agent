# 阶段 5：确定性展示与本地证据界面

## 范围与恢复现场

2026-09-15 从 feat/evidence-visualization-ui、HEAD 5247c44 的未提交现场增量续做。
完整读取 stage5-full-instructions.txt 和现场所有修改/未跟踪文件；恢复说明覆盖首次开工的干净检查。
未操作 backup-stage5-interrupted stash，未覆盖或重建现场文件。未改阶段 4B 公共服务/CLI、
原图、业务公式或阶段二安全执行。未调用真实模型，未提交、推送、切换分支或进入阶段 6。

## ChartSpec 与确定性图表

Pydantic strict=True、extra=forbid、frozen=True、revalidate_instances=always；
递归导出模型实例后重验，防止 model_construct/model_copy 隐藏额外字段或非法值。

只允许 chart_type、title、x_field、y_field、series_field、x_kind、y_unit、
sort_direction、source_kind、completeness。类型为 bar/line；标题、单位和坐标名使用封闭词表。
坐标只允许 category/period、value/change，可选 series；source_kind 为 single/comparison/contribution。
SQL、物理表列、表达式、Python、回调、URL、HTML、颜色表达式、自由 Vega 配置及未知字段均无法通过。
validate_chart 还拒绝不存在的坐标、空数据或 completeness=false。

- total：KPI 与表格，无图。
- breakdown/ranking：完整分类结果为柱图；top_n 导致非完整总体时仅保留表格。
- compare：两期间柱图。
- mom：按 PeriodSpec.start_date 排序的两点折线，明确仅两个期间。
- contribution：同一个 region 或 category 的正负变化柱图，保留 hidden_dimension_count/hidden_net_change。
- 空、失败、incomplete_result、truncated 或非完整总体不画总体图。

业务金额默认分，不改底层结果。表格和 KPI 复用已有精确 Decimal 字符串及两位比例显示；
仅 Plotly 的视觉坐标使用浮点。负值、超过 100% 的贡献保留，并提示抵消效应。
基数为零的变化率和总变化为零的贡献率为“无定义”。不产生长期趋势、hypothesis 或因果解释。

## ViewModel 与 SQL 边界

支持 success、clarification_required、refused、model_error、plan_validation_error、
execution_error、conversation_error。成功展示中文指标/操作、计划与期间、两期值、变化量/率、
贡献率显示字段、原查询行、completeness、calendar_period_complete、data_coverage_verified，
并显式提示“未验证数据库期间内部无缺失”。finding_type 仅 observation/decomposition。

技术元数据包含 request_id、三种 version、model_name、model_call_count、query_count、node_path、
elapsed_ms，成功时还有 inherited/changed/cleared。服务调用前失败也分配安全 request_id、
版本和零调用计数；不伪造服务已执行。UI 总耗时包含审计调用；审计中的耗时截至写入前。
warnings 使用固定文案，不回显服务自由文本。错误只显示固定安全信息；不展示异常、原始响应、Key 或数据库路径。

仅已有单期间 AnalysisResult.execution.sql 可在折叠技术详情显示。comparative 不暴露 SQL，
只显示已返回执行计划与 evidence_id → query_id；展示不重查、不重编译、不修改阶段 4B 输出。

## Streamlit 状态与防重复

默认 Fake、reference_date=2024-12-31；固定 fixture、ui-checkpoints.db、ui-audit.jsonl，
无文件、URL、SQL、代码或路径输入入口。配置只监听 127.0.0.1，并关闭 usage stats。
real 仅显式选择且环境 Key 存在后进入原适配器；不使用 .env Key。本阶段无真实模型调用。

服务仅在 st.form_submit_button 的明确提交分支执行一次。processing 阻止处理中提交，
last_response 先保存再展示；普通 rerun、展开详情与图表渲染不调用服务。
session_state 保存 thread/provider/reference_date/messages/last_response/last_submission_id/processing。
不使用 st.cache_data 存响应，不把 session_state 写日志。

新建生成合法随机 thread，清当前浏览器展示，不删 checkpoint；恢复先 validate_thread/inspect，
只恢复业务状态，下一次提交处理新输入，不 invoke(None)，不声称恢复旧结果/完整聊天历史。
连接重建可丢失 session_state，跨浏览器恢复依靠原 checkpoint，跨进程互斥依靠原 thread_lock。

## 审计白名单与隔离

严格 AuditRecord 仅保存 run_id、created_at、thread_digest（SHA256）、request_id、status、
analysis_operation、prompt_version、state_version、semantic_version、model_name、model_call_count、
query_count、node_path、elapsed_ms、error_code、completeness、evidence ID/query ID 映射。
不保存原 thread_id、问题、消息、筛选值、prompt、原始响应、SQL/绑定、路径、结果、贡献明细、Key 或异常。

独立 JSONL；路径 resolve/samefile 检查传入业务与 checkpoint、配置业务与状态路径、固定保护库；
拒绝硬链接、外来文件内容；schema 完整校验后写。原非阻塞 OS 文件锁保护检查与追加，
request_id 或 run_id 已存在即不追加。8 MiB 容量闸门，有界争用时返回安全 warning，分析结果保持不变。
JSON 回读只把白名单 node_path 数组还原 tuple 后严格重验；无 SQLite 执行旁路。

## 测试与验收记录

恢复前记录（来自用户恢复说明）：阶段 5/项目约束定向 123 passed；wheel 仓库外导入、
审计幂等、跨浏览器状态与 rerun 已验证；普通 total、年份 compare、地区贡献浏览器冒烟通过。
未重复运行该 123 项集合。

恢复新增修复：服务前错误的追踪元数据、UI 含审计耗时；新增两项离线测试。
相关定向：5 passed, 44 deselected in 16.10s。首次沙箱执行因新临时目录 WinError 5 失败，
随后在沙箱外以另一个全新目录执行通过；没有读取或删除任何旧 pytest 临时目录。

阶段 5 测试覆盖 ChartSpec 拒绝/实例绕过、图表规则、ViewModel 精确值与脱敏、审计白名单/别名/
幂等/并发、AppTest 表单与 rerun/新建/恢复/错误/图表，以及真实离线 wheel 构建安装后仓库外 import。
自动化使用 Fake/mock，socket 连接被测试阻断；wheel 使用 --no-index、--no-deps、--no-build-isolation。

## Fake 手工演示

```powershell
.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

浏览器访问 http://127.0.0.1:8501。月度环比“查看2024年2月成交额环比”显示
223200 → 232000 分，变化 8800，3.94%，两点折线；“按地区看变化贡献”后
“改按类别看贡献”切换成功，地区/类别未合并。类别变化依次为 125000、50600、33400、-200200。
Get-NetTCPConnection 实测监听仅 127.0.0.1:8501。

刷新后新浏览器状态无旧结果，恢复原 thread 后提交地区贡献成功。
展开技术详情后经 Main menu → Rerun，request_id 保持不变，model_call_count/query_count=1/4；
审计前后均 9 行，SHA256 同为 e007f5753a7669dbe21c7dcb1e7fe4785259764fb3605485fce9fafc630d5fed。
勾选新话题提交“成交额”得到 clarification_required 和固定补充提示；继续新话题提交
“2024年订单数”得到 success、合计 5 单，不继承之前贡献操作。
单期间技术详情可见已有执行 SQL；页面未见 Key、数据库路径、traceback 或原始模型响应。
同 request_id 不重复审计由离线用例直接覆盖；浏览器普通 rerun 另以审计文件字节不变验证。

## 依赖与打包

Python 3.12.6；Streamlit 1.63.0（AppTest 可用）、Pandas 3.0.5、Plotly 7.0.0。
三者原已直接声明且满足 pyproject 范围，与两个 requirements pins 一致，未重复添加或升级。
中断前补齐开发构建依赖 setuptools 75.8.0、wheel 0.45.1；显式 packages 加 eda.viz/eda.audit，
均有 __init__.py。app/streamlit_app.py 保持源码入口。

## 资产与已知限制

- business.db SHA256：323F4BB4CDFDF95533386181BFE4B0E1C2DB258198BB7C718F8E7B220CEB9029；
  UTC mtime：2026-09-13T08:49:27.0360631Z。
- fixture.db SHA256：77D88B1D8E9903DC3584A1736269491F75BF867608F5CCB44F215F99396A9773；
  UTC mtime：2026-09-13T08:49:30.2846596Z。

恢复时实测与原基线相同。fixture CSV、标准答案、语义配置、阶段二安全实现不修改。
已有 /data/ 忽略规则覆盖 UI 状态、JSONL、WAL/SHM 与锁；.env 不进入 Git。

限制：仅本地作品集；未验证真实模型、其他平台或公网部署。审计为有界本地文件，未提供轮转，
争用/容量/损坏失败只给 warning。thread 摘要是 SHA256 假名标识，不是加密。
wheel 仅验证新增包的分发与导入；源码外完整运行仍受根目录 semantic YAML 未打包的既有限制。
checkpoint 继续保存阶段 4B 已验证业务计划及恢复必需的受控筛选值；因此不宣称满足附件
“任何筛选值不得进入 checkpoint”的字面要求。本次遵循原 checkpoint/业务状态复用约束，
没有新增原始文本、响应、SQL、结果存储，也未重写历史持久化协议。

## 最终完整回归

仅执行一次最终完整回归，使用全新专用目录：

```powershell
$finalTemp = Join-Path $env:TEMP ("eda-stage5-final-resume-" + [guid]::NewGuid().ToString("N"))
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider "--basetemp=$finalTemp"
```

实际 basetemp：C:\Users\lenovo\AppData\Local\Temp\eda-stage5-final-resume-d4f7ddd86d664570a0c42fde3dcd698a。
结果 **978 passed in 162.62s (0:02:42)**，exit 0。
原 921 项保留，新增阶段 5 49 项及模块展开约束 8 项；AppTest、断网、幂等、真实 wheel
构建安装与仓库外包导入均包含在此次通过的回归中。未重复完整回归，未访问或删除旧临时目录。
浏览器测试后使用 Ctrl+C 停止 Streamlit，实测 8501 无监听。

## 修改文件清单（相对 HEAD 5247c44）

既有文件最小修改：

- README.md
- docs/architecture.md
- docs/progress.md
- pyproject.toml
- requirements.txt
- requirements.lock.txt

阶段 5 新文件（包含中断前未提交现场）：

- .streamlit/config.toml
- app/streamlit_app.py
- eda/viz/__init__.py
- eda/viz/models.py
- eda/viz/spec.py
- eda/viz/transform.py
- eda/viz/runtime.py
- eda/audit/__init__.py
- eda/audit/models.py
- eda/audit/store.py
- tests/test_stage5.py
- docs/stage5-evidence-ui.md

本次恢复新增生产修复仅在 runtime.py；在 test_stage5.py 增加对应两项测试，其余现场实现保留。

## 收尾核对

pip check：No broken requirements found（exit 0）。git diff --check 通过；新增 Python
UTF-8/AST/尾随空白检查通过。两个 requirements 的所有 pins 与安装版本一致，所有声明范围满足。
最终再次实测两个业务库 SHA256 与 UTC mtime 均与上文基线完全相同。
受保护业务/语义/fixture/标准答案/安全/会话源码 git diff 为空；Git 跟踪列表无 .env、
运行时数据库、JSONL 或锁，既有忽略规则覆盖独立 UI 文件。

最终 git status --short：

```text
 M README.md
 M docs/architecture.md
 M docs/progress.md
 M pyproject.toml
 M requirements.lock.txt
 M requirements.txt
?? .streamlit/
?? app/
?? docs/stage5-evidence-ui.md
?? eda/audit/
?? eda/viz/
?? tests/test_stage5.py
```

保留未提交工作区，停止于阶段 5。未操作备份 stash，未 commit/push/merge/tag/切换分支。
