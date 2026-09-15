# 数据模型与指标口径（v1）

**口径的权威来源是 `semantic/` 下的三份 YAML**（阶段 1.1 起）。本文件是这些配置的
人类可读说明与人工推导记录：两者不一致时，以 YAML 为准并修正本文件，
同时必须让 `tests/test_semantic_consistency.py` 全绿。

| 配置文件 | 内容 |
|---|---|
| `semantic/metrics.yaml` | 指标定义（口径、过滤、允许维度、零分母策略） |
| `semantic/dimensions.yaml` | 维度定义（来源字段、允许取值、允许操作、不可加声明） |
| `semantic/relationships.yaml` | 表粒度与主键、关联键与基数、重复累计注意事项、分析视图 |

三份文件都带 `schema_version`（当前 `1.1.0`）。

金额单位统一为**整数「分」**。数据库中不存在任何浮点金额列，所以求和是精确的。

---

## 1. 数据表、粒度与主外键

数据为虚构电商经营数据，不含任何真实个人信息。

| 表 | 记录粒度（一行代表什么） | 主键 | 外键 |
|---|---|---|---|
| `customers` | 一个注册客户 | `customer_id` | 无 |
| `products` | 一个可售商品（SKU） | `product_id` | 无 |
| `orders` | 一张订单（订单头） | `order_id` | `customer_id → customers.customer_id` |
| `order_items` | 一张订单中的一个商品行 | `order_item_id` | `order_id → orders.order_id`，`product_id → products.product_id` |

`order_items` 上有 `UNIQUE (order_id, product_id)`：同一商品在同一订单内只会出现一行，
不会被拆成两行。

这四张表、它们的列清单、主键与外键都在 `semantic/relationships.yaml` 中声明，
并由测试与真实数据库的 `PRAGMA table_info` / `PRAGMA foreign_key_list` 逐项比对。

### 1.1 字段说明

**customers**（脱敏）

| 字段 | 含义 |
|---|---|
| `customer_id` | 代理主键（伪匿名，如 `C0001`） |
| `signup_date` | 注册日期，ISO `YYYY-MM-DD` |
| `signup_region` | **注册时**所属大区。**不用于地区业绩统计** |
| `signup_channel` | 注册渠道：`web` / `app` / `mini_program`。不用于渠道业绩统计 |
| `customer_tier` | 分层标签：`new` / `regular` / `vip` |

隐私约定：不存储、也不生成姓名、手机号、邮箱、详细地址、证件号、生日。
大区是粗粒度标签（`华东` / `华北` / `华南` / `华中` / `西南`），不是地理坐标。

**products**

| 字段 | 含义 |
|---|---|
| `product_id` | 代理主键 |
| `product_name` | 虚构商品名 |
| `category` | 商品类别（`手机数码` / `家用电器` / `服饰鞋包` / `食品生鲜` / `美妆个护` / `家居日用`） |
| `list_price_cents` | **目录价**（分）。不是成交价，任何成交额计算都不得使用该列 |
| `is_active` | 0/1，是否在售 |

**orders**

| 字段 | 含义 |
|---|---|
| `order_id` | 代理主键 |
| `customer_id` | 下单客户 |
| `order_date` | 订单发生日期（时间轴就用这一列） |
| `status` | `pending` / `paid` / `completed` / `cancelled` / `refunded` |
| `order_region` | **订单发生时**的地区。地区报表必须用它，而不是 `customers.signup_region`，这样客户迁移不会改写历史 |
| `order_channel` | **订单发生时**的下单渠道 |

`semantic/dimensions.yaml` 中 `region` 维度显式声明了
`forbidden_source_fields: [signup_region]`，并有测试确认没有任何维度取自注册字段。

**order_items**

| 字段 | 含义 |
|---|---|
| `order_item_id` | 代理主键（如 `O00000001-1`） |
| `order_id` | 所属订单 |
| `product_id` | 商品 |
| `quantity` | 数量，`> 0` |
| `unit_price_cents` | **成交时的单价**（分），`>= 0` |

行金额 `line_amount_cents` **不落库**，始终由视图派生为 `quantity * unit_price_cents`，
因此不可能与明细数据不一致。

---

## 2. 订单状态语义

| 状态 | 是否计入 v1 成交额 | 说明 |
|---|---|---|
| `paid` | ✅ 计入 | 已付款 |
| `completed` | ✅ 计入 | 已完成 |
| `pending` | ❌ 整单排除 | 未付款 |
| `cancelled` | ❌ 整单排除 | 已取消，从未付款 |
| `refunded` | ❌ 整单排除 | 见下方说明 |

关于 `refunded` 的简化处理：v1 **不处理部分退款，也不做净额冲销**。项目里没有退款金额表，
所以我们不会从 `paid`/`completed` 的金额中减去退款，而是直接把当前状态为 `refunded`
的订单整单排除。这是一个简化，会同时高估（未冲销部分退款，因为不存在部分退款数据）
和低估（整单排除了已发生的付款）真实收入。

**因此该指标只能叫「有效订单成交额（简化口径）」。它不是财务净收入，
也不等同于标准会计口径的 gross revenue。**

---

## 3. v1 指标定义

所有指标的规范基表是视图 `v_revenue_lines`（订单明细粒度，已按状态过滤）。

指标的 `id` 是稳定标识符，`name_zh` 是展示名称。代码里通过
`eda.metrics.resolve_metric()` 可以用 ID、历史 ID 或中文同义词查到同一个定义。

| 指标 ID | 展示名称 | 单位 | 聚合方式 | 可加 |
|---|---|---|---|---|
| `effective_order_gmv_cents` | 有效订单成交额（简化口径） | 分 | `sum(line_amount_cents)` | ✅ |
| `valid_order_count` | 有效订单数 | 单 | `count_distinct(order_id)` | ❌（见 4.1） |
| `aov_cents` | 客单价 | 分/单 | `ratio(成交额 ÷ 有效订单数)` | ❌ |
| `item_quantity` | 有效销量 | 件 | `sum(quantity)` | ✅ |
| `distinct_customers` | 下单客户数 | 人 | `count_distinct(customer_id)` | ❌ |

> **历史标识符**：阶段 1 把成交额指标叫 `revenue_cents`。阶段 1.1 把它重命名为
> `effective_order_gmv_cents`，并在 `semantic/metrics.yaml` 里用
> `legacy_ids: ["revenue_cents"]` 显式保留旧名，这样阶段 1 的回归测试与期望值文件
> 无需改动。`CoreMetrics.revenue_cents` 也保留为只读别名属性。

### 3.1 有效订单成交额（简化口径）

```sql
SELECT COALESCE(SUM(line_amount_cents), 0) AS effective_order_gmv_cents
FROM v_revenue_lines
WHERE order_date >= :start_date AND order_date <= :end_date;
```

即：状态为 `paid` / `completed` 的订单，其**订单明细金额之和**
（`SUM(quantity * unit_price_cents)`）。

注意事项：
- 使用成交单价 `unit_price_cents`，**不用**目录价 `list_price_cents`；
- 不处理部分退款，不做净额冲销（见第 2 节）；
- `pending` / `cancelled` / `refunded` 整单排除；
- 按类别拆分时金额可加（金额按明细行归属，互斥）。

### 3.2 有效订单数

```sql
SELECT COUNT(DISTINCT order_id) AS valid_order_count
FROM v_revenue_lines
WHERE order_date >= :start_date AND order_date <= :end_date;
```

- 必须 `COUNT(DISTINCT order_id)`。在明细粒度上 `COUNT(*)` 会把多行订单重复计数；
- 定义为「状态有效且至少有一行明细」的订单。本项目的数据生成保证每单至少一行明细
  （`Dataset` 校验器强制），所以这一点不会造成差异；
- **按类别或商品拆分时不可加**，见 4.1。

### 3.3 客单价

```
aov_cents = effective_order_gmv_cents / valid_order_count
```

- **分母为 0 时返回空值（`None` / SQL `NULL`），并附带一条说明文字，绝不返回 0。**
  说明文字声明在 `semantic/metrics.yaml` 的
  `aov_cents.zero_denominator_policy.note_zh`，代码里的
  `eda.metrics.core.AOV_UNDEFINED_NOTE` 直接读取它，两者不可能不一致；
- 核心总量报表在 Python 中对两个精确整数做除法；阶段二计划与拆分 SQL 使用
  `CASE` 保护零分母并通过 `1.0 * 分子 / 分母` 明确做浮点除法，避免整除。
  金额求和与去重计数保持整数，比率展示使用浮点结果；
- 计算过程不做四舍五入，只有展示时才格式化为两位小数的「元」；
- 按 `region` / `channel` / `month` / `date` 拆分时，每张订单只属于一个维度值，
  报表继续展示为**「客单价」**；
- 按 `category` / `product` 拆分时，分子只包含该类别或商品的成交金额，
  分母是「包含该类别或商品的去重订单数」。这个结果不是整张订单通常意义上的客单价，
  而是该类别或商品对相关订单的**「订单平均贡献额」**，也不是商品平均单价。

展示名不是 `report.py` 的维度特判，而是受控语义元数据：
`semantic/dimensions.yaml` 的 `aov_display_name_zh`。Pydantic 只允许
`客单价` / `订单平均贡献额` 两个值，不允许任意表达式；报表按维度直接读取该字段。
这个修正只改变表头，不改变 `aov_cents` 的公式或数值。

### 3.4 时间区间的包含规则

时间轴统一使用 `orders.order_date`。区间是**闭区间**：`order_date` 恰好等于
`start_date` 或恰好等于 `end_date` 的订单都会被计入。

`semantic/metrics.yaml` 中每个指标都声明 `date_range_inclusive: both`，
`tests/test_semantic_consistency.py::test_date_range_includes_both_endpoints`
用 fixture 的边界日期（2024-01-05 有订单、2024-01-06 没有）验证这一点。

---

## 4. 多表关联时如何避免重复计算

这是本项目最容易出错的地方。规则同时写在 `semantic/relationships.yaml` 的
`double_count_note_zh` 字段里，供 Agent 在后续阶段读取。

**粒度阶梯**

```
orders (1 张订单 1 行)
   └── order_items (1 张订单 N 行)  ← v_order_lines / v_revenue_lines 的粒度
           └── products (按主键 1:1 关联，不会放大行数)
```

**三条硬规则**

1. **`fan_out` 只描述正向连接对左侧输入行的影响。**
   对一条 `from_table LEFT JOIN to_table` 关系：
   - `many_to_one` / `one_to_one` 必须是 `fan_out: none`；
   - `one_to_many` 必须是 `fan_out: duplicates_left`。

   因此 `order_items LEFT JOIN orders` 是 `many_to_one`，不会复制左侧
   `order_items` 行，`order_items__orders` 正确标记为 `fan_out: none`。
   但是一张订单本来就对应多条明细：把订单头字段投影到这些**既有**明细行后，
   同一个订单级数值会重复出现。这是粒度转换造成的重复，不是正向连接放大左侧行。
   本项目通过「订单头上根本没有金额列」从结构上消除了直接重复求和的错误；
   未来若订单头增加金额列，把它带到明细粒度后也不能直接 `SUM`。
2. **订单数一律 `COUNT(DISTINCT order_id)`。** 明细粒度上的 `COUNT(*)` 得到的是明细行数。
   在手工 fixture 里这两个数分别是 9 和 5，测试
   `test_orders_are_counted_once_regardless_of_line_count` 专门守住这一点。
3. **当前三个正向关系都不会放大左侧行。** `orders → customers`、
   `order_items → orders`、`order_items → products` 都是 `many_to_one`，
   目标列是目标表主键，因此每条左侧行至多匹配一条右侧行，均标记为 `fan_out: none`。
   关联前后 `SUM(line_amount_cents)` 必须完全相等；
   有两个测试分别验证。配置校验还会拒绝「声明为 `many_to_one` 但没有关联到目标主键」
   的关系，因为那会静默放大行数。

### 4.1 类别订单数不是可加指标

一张订单可以横跨多个类别。因此按 `category`（或 `product`）拆分时：

- 各类别的**成交额相加 = 总成交额**（金额按行归属，互斥）；
- 各类别的**订单数相加 > 总订单数**（每个类别统计的是「包含该类别商品的订单数」，
  订单被重复计入）。

fixture 中总有效订单 5 单，但按类别的订单数相加为 9。这不是 bug，是定义决定的。

这条规则在 `semantic/dimensions.yaml` 里声明为：

```yaml
  - id: category
    non_additive_metrics: ["valid_order_count", "distinct_customers"]
    non_additive_note_zh: >-
      按商品类别拆分时，一个订单可能横跨多个类别：…
```

`compute_breakdown` 在按 `category` / `product` 拆分时会自动附带这条说明——
说明文字取自配置，不是在 Python 里另写一遍。

按 `month` / `date` / `region` / `channel` 拆分则是互斥划分，两个总数都必须加得上。

**去重指标在任何维度上都不可加**：同一客户可能在多个月份、多个地区下单，
所以 `distinct_customers` 在所有维度上都被标记为不可加。

---

## 5. 视图

| 视图 | 粒度 | 用途 |
|---|---|---|
| `v_order_lines` | 订单明细行，**全部状态** | 状态结构分析（如取消率）。**不要**用它算成交额 |
| `v_revenue_lines` | 订单明细行，仅 `paid`/`completed` | 所有 v1 指标的规范基表 |
| `v_revenue_orders` | 订单（仅 `paid`/`completed`） | 需要订单级金额时用（分布、分位数） |

三个视图的列清单、来源表、状态过滤都在 `semantic/relationships.yaml` 的
`analysis_views` 中声明，并与真实数据库比对。

`v_order_lines` 额外提供 `order_month`（`substr(order_date,1,7)`，形如 `2024-06`），
按月分析直接用它，不必在查询里重复写字符串截取。

---

## 6. 手工 fixture 与人工复核

fixture 数据在 `eda/data/fixtures/*.csv`：4 个客户、5 个商品、8 张订单、12 行明细。
规模小到可以用纸笔完整复核。人工复核结果存放在
`tests/data/expected_fixture_metrics.json`（**测试资产，不是运行时代码**）。

> 阶段 1.1 **没有改动** fixture 数据，也没有改动期望值文件中的任何数字。
> 期望值文件仍使用 `revenue_cents` 作为键名，这是阶段 1 的历史标识符，
> 通过 `legacy_ids` 与别名属性保持可用。

### 6.1 计入成交额的订单与明细

| 订单 | 状态 | 日期 | 地区 | 渠道 | 明细（数量 × 成交单价 = 行金额，分） | 订单金额 |
|---|---|---|---|---|---|---|
| O001 | paid | 2024-01-05 | 华东 | web | P002 手机数码 2×9900=19800；P005 食品生鲜 3×4500=13500 | 33300 |
| O002 | completed | 2024-01-12 | 华北 | app | P001 手机数码 1×189900=189900 | 189900 |
| O004 | completed | 2024-02-02 | 华东 | web | P004 服饰鞋包 2×15900=31800；P005 食品生鲜 1×4900=4900；P002 手机数码 1×9500=9500 | 46200 |
| O007 | paid | 2024-02-20 | 西南 | app | P005 食品生鲜 10×4200=42000 | 42000 |
| O008 | completed | 2024-02-28 | 华东 | web | P003 家用电器 1×125000=125000；P004 服饰鞋包 1×18800=18800 | 143800 |

### 6.2 被排除的订单

| 订单 | 状态 | 明细金额（分） | 排除理由 |
|---|---|---|---|
| O003 | cancelled | 129900 | 已取消 |
| O005 | pending | 199900 | 未付款 |
| O006 | refunded | 119900 | 退款订单整单排除（v1 不做净额冲销） |

排除金额合计 `129900 + 199900 + 119900 = 449700`。

### 6.3 全窗口（2024-01-01 ~ 2024-12-31，闭区间）核心指标

```
有效订单成交额 = 33300 + 189900 + 46200 + 42000 + 143800 = 455200 分 = 4552.00 元
有效订单数     = 5 单                （明细行数为 9，务必区分）
客单价         = 455200 / 5 = 91040 分 = 910.40 元
有效销量       = (2+3) + 1 + (2+1+1) + 10 + (1+1) = 22 件
下单客户数     = C001, C002, C003, C004 = 4 人
全状态金额     = 455200 + 449700 = 904900 分   （成交额 + 排除金额 = 全量，无金额丢失）
```

### 6.4 分维度核对

| 维度 | 取值 | 成交额（分） | 有效订单数 |
|---|---|---|---|
| 月份 | 2024-01 | 223200 | 2 |
| 月份 | 2024-02 | 232000 | 3 |
| 地区 | 华东 | 223300 | 3 |
| 地区 | 华北 | 189900 | 1 |
| 地区 | 西南 | 42000 | 1 |
| 类别 | 手机数码 | 219200 | 3 |
| 类别 | 家用电器 | 125000 | 1 |
| 类别 | 食品生鲜 | 60400 | 3 |
| 类别 | 服饰鞋包 | 50600 | 2 |
| 渠道 | app | 231900 | 2 |
| 渠道 | web | 223300 | 3 |

校验：月份、地区、渠道三种拆分的成交额与订单数都分别加总为 `455200` 和 `5`；
类别拆分成交额同样加总为 `455200`，但订单数加总为 `9`（见 4.1）。

O007 是刻意设计的：客户 C003 注册地区是 `华南`，但这张订单的 `order_region` 是 `西南`。
所以按地区统计时 `华南` 为 0，`西南` 为 42000 —— 用错列会立刻出现差异。
演示数据上还有一个专门的测试
（`test_region_metrics_would_differ_if_signup_region_were_used`）证明这条规则是有效的。

> 修改任何 fixture CSV，都必须重做本节的手工推导并同步更新
> `tests/data/expected_fixture_metrics.json`。不允许反过来「按代码输出改期望值」。

---

## 7. 演示数据（种子固定）

`eda/data/generator.py` 按固定种子与固定日期窗口生成演示数据，默认
`seed=20240101`、窗口 `2024-01-01 ~ 2024-12-31`，默认 400 客户 / 30 商品 / 3000 订单。

- **不依赖机器当前日期。** 生成器从不调用 `date.today()` / `datetime.now()`，
  有测试在语法树上强制这一点；
- **整数运算。** 成交单价 = `list_price_cents * 折扣百分比 // 100`，没有浮点舍入，
  所以跨平台结果一致；
- 覆盖 12 个月、5 个大区、6 个商品类别、5 种订单状态；含有月度季节性（6 月、11 月偏高）；
- 约 9% 的订单 `order_region` 与客户 `signup_region` 不同，用来暴露「用错地区列」的问题。

**关于「全年覆盖」的准确说法**：上面说的是数据**覆盖了** 2024 全年 12 个月，
**不能据此认为每一天都有数据**。按日期拆分只会返回有有效订单的日期，
不会为没有订单的日期补 0 行；需要完整日历序列必须在应用层补齐，当前未实现。
`test_date_breakdown_only_returns_days_that_have_orders` 把这个行为固定下来。

演示数据只用于展示与压力测试，**不作为答案正确性的判据**。业务答案的正确性判据只有
手工 fixture 与人工推导，以及独立参考计算（见
[`evaluation/README.md`](../evaluation/README.md)）。

## 阶段 4B-1 比较能力

所有指标支持 compare/mom 的业务计算语义；贡献能力由必填 contribution_dimensions 和 additive_dimensions 控制。成交额、销量允许地区或类别贡献；有效订单数只允许地区贡献；客单价、去重客户数不允许贡献相加。地区与类别各自独立，不能合并求和。阶段二查询执行能力仍为 total/breakdown。

语义版本 1.1.0 不兼容旧 1.0.0 checkpoint，不做静默迁移。字段、公式、舍入与期间规则详见 [阶段 4B-1](stage4b-1-comparative.md)。
