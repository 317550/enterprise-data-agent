# 数据模型与指标口径（v1）

本文件是**口径的唯一权威来源**。代码中的 `eda/metrics/definitions.py` 与本文件一一对应；
两者不一致时，以本文件为准并修正代码。

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

### 1.1 字段说明

**customers**（脱敏）

| 字段 | 含义 |
|---|---|
| `customer_id` | 代理主键（伪匿名，如 `C0001`） |
| `signup_date` | 注册日期，ISO `YYYY-MM-DD` |
| `signup_region` | **注册时**所属大区 |
| `signup_channel` | 注册渠道：`web` / `app` / `mini_program` |
| `customer_tier` | 分层标签：`new` / `regular` / `vip` |

隐私约定：不存储、也不生成姓名、手机号、邮箱、详细地址、证件号、生日。
大区是粗粒度标签（`华东` / `华北` / `华南` / `华中` / `西南`），不是地理坐标。

**products**

| 字段 | 含义 |
|---|---|
| `product_id` | 代理主键 |
| `product_name` | 虚构商品名 |
| `category` | 商品类别（`手机数码` / `家用电器` / `服饰鞋包` / `食品生鲜` / `美妆个护` / `家居日用`） |
| `list_price_cents` | **目录价**（分）。不是成交价，任何营业额计算都不得使用该列 |
| `is_active` | 0/1，是否在售 |

**orders**

| 字段 | 含义 |
|---|---|
| `order_id` | 代理主键 |
| `customer_id` | 下单客户 |
| `order_date` | 订单发生日期（时间轴就用这一列） |
| `status` | `pending` / `paid` / `completed` / `cancelled` / `refunded` |
| `order_region` | **订单发生时**的地区。地区报表必须用它，而不是 `customers.signup_region`，这样客户迁移不会改写历史 |
| `order_channel` | 下单渠道 |

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

| 状态 | 是否计入 v1 营业额 | 说明 |
|---|---|---|
| `paid` | ✅ 计入 | 已付款 |
| `completed` | ✅ 计入 | 已完成 |
| `pending` | ❌ 排除 | 未付款 |
| `cancelled` | ❌ 排除 | 已取消，从未付款 |
| `refunded` | ❌ 整单排除 | 见下方说明 |

关于 `refunded` 的简化处理：v1 **不做净额冲销**。项目里没有退款金额表，所以我们不会从
`paid`/`completed` 的金额中减去退款，而是直接把当前状态为 `refunded` 的订单整单排除。
这是一个简化，会同时高估（未冲销部分退款，因为不存在部分退款数据）和低估
（整单排除了已发生的付款）真实收入。

**因此该指标只能叫「营业额（简化口径 / gross）」，不能叫「净收入 / net revenue」。**

---

## 3. v1 指标定义

所有指标的规范基表是视图 `v_revenue_lines`（订单明细粒度，已按状态过滤）。

### 3.1 营业额（`revenue_cents`，单位：分）

```sql
SELECT COALESCE(SUM(line_amount_cents), 0) AS revenue_cents
FROM v_revenue_lines
WHERE order_date BETWEEN :start_date AND :end_date;
```

即：状态为 `paid` / `completed` 的订单，其**订单明细金额之和**
（`SUM(quantity * unit_price_cents)`）。

注意事项：
- 使用成交单价 `unit_price_cents`，**不用**目录价 `list_price_cents`；
- 不扣退款，是 gross 不是 net（见第 2 节）；
- `pending` / `cancelled` / `refunded` 整单排除。

### 3.2 有效订单量（`valid_order_count`，单位：单）

```sql
SELECT COUNT(DISTINCT order_id) AS valid_order_count
FROM v_revenue_lines
WHERE order_date BETWEEN :start_date AND :end_date;
```

- 必须 `COUNT(DISTINCT order_id)`。在明细粒度上 `COUNT(*)` 会把多行订单重复计数；
- 定义为「状态有效且至少有一行明细」的订单。本项目的数据生成保证每单至少一行明细
  （`Dataset` 校验器强制），所以这一点不会造成差异。

### 3.3 客单价（`aov_cents`，单位：分/单）

```
aov_cents = revenue_cents / valid_order_count
```

- **分母为 0 时返回空值（`None` / SQL `NULL`），并附带一条说明文字，绝不返回 0。**
  说明文字见 `eda.metrics.core.AOV_UNDEFINED_NOTE`；
- 除法在 Python 中用两个精确整数完成，避免 SQL 里意外发生整除；
- 计算过程不做四舍五入，只有展示时才格式化为两位小数的「元」。

### 3.4 辅助指标

| 指标 | 定义 |
|---|---|
| `item_quantity` | `SUM(quantity)`，有效销量（件） |
| `distinct_customers` | `COUNT(DISTINCT customer_id)`，下单客户数（人） |

---

## 4. 多表关联时如何避免重复计算

这是本项目最容易出错的地方，规则如下。

**粒度阶梯**

```
orders (1 张订单 1 行)
   └── order_items (1 张订单 N 行)  ← v_order_lines / v_revenue_lines 的粒度
           └── products (按主键 1:1 关联，不会放大行数)
```

**三条硬规则**

1. **金额只在明细粒度上求和。** 先 `orders JOIN order_items` 再对订单头上的某个列求和，
   会把订单级数值重复 N 次。本项目通过「订单头上根本没有金额列」从结构上消除了这个错误。
2. **订单数一律 `COUNT(DISTINCT order_id)`。** 明细粒度上的 `COUNT(*)` 得到的是明细行数。
   在手工 fixture 里这两个数分别是 9 和 5，测试 `test_orders_are_counted_once_regardless_of_line_count`
   专门守住这一点。
3. **关联维表（`products`、`customers`）不会改变金额。** 它们都是按主键 1:1 关联，
   所以关联前后 `SUM(line_amount_cents)` 必须完全相等；有两个测试分别验证。

**按类别拆分的特殊注意**

一张订单可以横跨多个类别。因此按 `category` 拆分时：

- 各类别的**营业额相加 = 总营业额**（金额按行归属，互斥）；
- 各类别的**订单数相加 > 总订单数**（每个类别统计的是「包含该类别商品的订单数」，订单被重复计入）。

fixture 中总有效订单 5 单，但按类别的订单数相加为 9。这不是 bug，是定义决定的；
`compute_breakdown` 在按 `category` / `product` 拆分时会自动附带这条说明。
按 `month` / `region` / `channel` 拆分则是互斥划分，两个总数都必须加得上。

**「营业额除以订单数」不等于「订单金额的平均值」？** 在本项目的口径下两者相等，
因为分子分母用的是同一批订单。但只要加了类别筛选，分子变成「该类别的金额」，
分母变成「包含该类别的订单数」，此时的客单价含义是「该类别对订单的平均贡献额」，
不是该类别商品的平均单价。

---

## 5. 视图

| 视图 | 粒度 | 用途 |
|---|---|---|
| `v_order_lines` | 订单明细行，**全部状态** | 状态结构分析（如取消率）。**不要**用它算营业额 |
| `v_revenue_lines` | 订单明细行，仅 `paid`/`completed` | 所有 v1 指标的规范基表 |
| `v_revenue_orders` | 订单（仅 `paid`/`completed`） | 需要订单级金额时用（分布、分位数） |

`v_order_lines` 额外提供 `order_month`（`substr(order_date,1,7)`，形如 `2024-06`），
按月分析直接用它，不必在查询里重复写字符串截取。

---

## 6. 手工 fixture 与人工复核

fixture 数据在 `eda/data/fixtures/*.csv`：4 个客户、5 个商品、8 张订单、12 行明细。
规模小到可以用纸笔完整复核。人工复核结果存放在
`tests/data/expected_fixture_metrics.json`（**测试资产，不是运行时代码**）。

### 6.1 计入营业额的订单与明细

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

### 6.3 全窗口（2024-01-01 ~ 2024-12-31）核心指标

```
营业额        = 33300 + 189900 + 46200 + 42000 + 143800 = 455200 分 = 4552.00 元
有效订单量    = 5 单                （明细行数为 9，务必区分）
客单价        = 455200 / 5 = 91040 分 = 910.40 元
有效销量      = (2+3) + 1 + (2+1+1) + 10 + (1+1) = 22 件
下单客户数    = C001, C002, C003, C004 = 4 人
全状态金额    = 455200 + 449700 = 904900 分   （营业额 + 排除金额 = 全量，无金额丢失）
```

### 6.4 分维度核对

| 维度 | 取值 | 营业额（分） | 有效订单数 |
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

校验：月份、地区、渠道三种拆分的营业额与订单数都分别加总为 `455200` 和 `5`；
类别拆分营业额同样加总为 `455200`，但订单数加总为 `9`（见第 4 节）。

O007 是刻意设计的：客户 C003 注册地区是 `华南`，但这张订单的 `order_region` 是 `西南`。
所以按地区统计时 `华南` 为 0，`西南` 为 42000 —— 用错列会立刻出现差异。

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

演示数据只用于展示与压力测试，**不作为答案正确性的判据**。业务答案的正确性判据只有
手工 fixture 与人工推导。
