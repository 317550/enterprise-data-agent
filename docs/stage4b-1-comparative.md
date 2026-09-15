# 阶段 4B-1：比较分析基础

仅实现严格业务模型、版本化语义能力和纯 Decimal 计算；未接入查询、图、
checkpoint 状态结构、CLI、自然语言规划或真实模型。阶段二执行器仍只执行
原 AnalysisPlan 的 total/breakdown，未把比较能力标记为可查询执行。

## 模型和期间

`eda.plan.comparative.PeriodSpec` 要求 start_date、end_date、label、granularity、
is_complete。严格类型、禁止额外字段；日期只能为 YYYY-MM-DD。一个期间只能
覆盖一个完整自然月或自然年，is_complete 必须为 true。标签仅为展示文字，
不参与期间推断。季度、周、任意区间不在协议中。

`ComparativeAnalysisPlan` 只接受 canonical metric_id，operation 为 compare/mom/
contribution，current_period、baseline_period、可选单一 dimension_id、filters、
top_n、sort_direction。compare/contribution 必须明确两期，粒度相同，基期严格
早于本期且不重叠。mom 只接受月，代码生成紧邻上一个完整自然月；调用方若提供
基期，必须与计算结果相符。跨年及闰年使用标准日历计算。

模型表示日历边界完整性；`validate_as_of(plan, reference_date)` 进一步核对
截至参考日期期间是否已经结束。参考日期按包含当天处理；月末之前的“本月”
不能通过此入口成为完整环比。不推断自然语言，也不把未结束月份默认为完整。

dimension_id 与 top_n 仅 contribution 可用。sort_direction 默认 desc，贡献按
带符号变化量排序，同值按 dimension_value 升序。filters 复用封闭 eq/in 词汇，
只允许语义批准的筛选维度和值，拒绝重复筛选。Python 输入使用严格 tuple，
JSON 数组显式转 tuple；不对字符串、数字、布尔值做宽松转换。

直接验证与 `parse_comparative_plan` 均递归导出已有模型后重新校验，覆盖
model_construct/model_copy 中的非法字段值和嵌套实例。SQL、表名、列名、表达式、
公式及多个贡献维度均没有协议入口。

## 语义版本和可加性

沿用现有版本规则，三份 YAML 的 schema_version 与代码支持版本从 1.0.0 同步
升到 1.1.0；Session.semantic_version 继续采用该版本。4A 的 1.0.0 checkpoint
返回 incompatible_checkpoint，不自动迁移、不改写旧状态。

所有五个指标声明 compare 和 mom。成交额、销量支持 region/category 贡献；
有效订单数支持 region 贡献；客单价与去重客户数不支持 contribution。

新增必填 additive_dimensions 和 contribution_dimensions。既有 additive 表示
指标整体可加属性；additive_dimensions 明确具体互斥划分，允许有效订单数在
地区可加、类别不可加。加载时交叉检查 allowed_dimensions、维度 non_additive_metrics
及 group_by 能力，拒绝冲突配置。运行时通过 is_additive_over 查配置，无指标名称
特判。地区和类别是独立视角，每份结果只携带一个 dimension_id，不能跨视角相加。

## 纯计算约定

`eda.metrics.comparative.calculate_comparison(current, baseline)`：

- absolute_change = current - baseline。
- change_rate = absolute_change / baseline；baseline=0 返回 None。
- current=0、baseline 非 0 时变化率为 -1，中文展示为 -100.00%。

只接收有限 Decimal，拒绝 float、NaN、Infinity。输入限制为最多 90 位系数、
小数指数不低于 -30、最高有效位不超过 60；在独立 128 位十进制上下文中计算。
current、baseline 和变化量保留精确十进制值；循环小数比例按 12 位小数存储，
不是无限精度有理数。所有比例和展示统一 ROUND_HALF_UP，不受调用方 Decimal
上下文影响。display_zh 与数值字段分离，展示两位小数，百分数乘 100，None 显示
“无定义”；值沿用调用方指标单位，不隐式把分换成元。

`calculate_contribution(plan, current, baseline, current_total=..., baseline_total=...)`
接收每期 dimension_value 到 Decimal 的映射及独立总值；不查数据库。按维度值
对齐，单边缺失补 0，最多对齐 10000 个维度值。

- dimension_change = current - baseline。
- contribution_rate = dimension_change / total_change。
- total_change=0 时贡献率 None；允许负贡献率及超过 100%。
- 核对完整 sum(dimension_change) == total_change，并额外核对两期分项和分别
  等于各自总值，避免两期相同偏差恰好抵消。

核对失败返回 reconciliation_failed，保留全部分项、不生成贡献率、不应用 top_n。
核对成功后才排序和截取 top_n，返回 hidden_dimension_count 和 hidden_net_change，
总变化和各项贡献率的分母始终采用完整数据。输入为空且两期总值均为 0 可成功核对。

## 离线验证

修改前基线：601 passed in 30.36s。首轮定向暴露 JSON 数组与严格 tuple 的兼容
问题；修复后比较模型/纯计算、语义配置/一致性、checkpoint 五文件定向：
233 passed in 6.62s。首次完整回归为 9 failed、702 passed，9 项均命中原单轮测试
硬编码的旧语义版本断言；同步为 1.1.0 后，最终完整回归 **711 passed in 30.34s**。
原 601 项保留，新增 107 项比较模型/计算用例、1 项旧语义 checkpoint 拒绝用例，
另有新增两个 Python 文件自动纳入既有导入/动态执行守护参数化测试。

测试命令使用项目 `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`。
首次基线在沙箱内因 pytest 临时目录权限产生 268 passed、333 errors；授权在沙箱外
重跑后复现 601 passed。全部测试为离线 Fake/mock 与临时 fixture 库；新增纯计算
测试无需数据库。git diff --check 通过。未 commit、push、merge、tag，未创建或切换分支。

## 最终修改文件和 Git 状态

分支 feat/comparative-analysis。9 个已跟踪文件修改，5 个新文件未跟踪；均未暂存。

- M：docs/metrics.md、docs/progress.md。
- M：eda/plan/__init__.py、eda/semantic/models.py。
- M：semantic/dimensions.yaml、semantic/metrics.yaml、semantic/relationships.yaml。
- M：tests/test_agent_planning.py、tests/test_conversation_checkpoint.py。
- ??：docs/stage4b-1-comparative.md。
- ??：eda/metrics/comparative.py、eda/plan/comparative.py。
- ??：tests/test_comparative_calculation.py、tests/test_comparative_plan.py。

eda/sql、eda/query、eda/conversation、eda/agent 及原 eda/plan/models.py 无修改。

## 后续输出契约衔接

最终输出契约修复继续复用本阶段 calculate_comparison、calculate_contribution 与
display_decimal（ROUND_HALF_UP、两位百分比、无定义显示“无定义”）。纯计算 dataclass
及 display_zh 行为不变。4B-2 的对外模型增加明确值字段和比例展示值，保留旧字段；
详见 [4B-2 输出契约](stage4b-2-comparative-execution.md)。
