-- ---------------------------------------------------------------------------
-- Enterprise Data Agent -- business data schema (fictional e-commerce).
--
-- Conventions
--   * Money is ALWAYS an integer number of cents (分). No REAL columns.
--   * Dates are ISO 'YYYY-MM-DD' TEXT, checked by GLOB.
--   * STRICT tables: SQLite enforces declared column types (needs SQLite>=3.37).
--   * PRAGMA foreign_keys is a per-connection setting and is turned on by
--     eda/db.py, not here.
--
-- Grain of each table is documented next to the table.
-- ---------------------------------------------------------------------------

-- Grain: one row per registered customer.
-- Privacy: pseudonymous ids only -- no name / phone / e-mail / address.
CREATE TABLE customers (
    customer_id    TEXT NOT NULL PRIMARY KEY,
    signup_date    TEXT NOT NULL CHECK (signup_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    signup_region  TEXT NOT NULL CHECK (signup_region IN ('华东','华北','华南','华中','西南')),
    signup_channel TEXT NOT NULL CHECK (signup_channel IN ('web','app','mini_program')),
    customer_tier  TEXT NOT NULL CHECK (customer_tier IN ('new','regular','vip'))
) STRICT;

-- Grain: one row per sellable product (SKU).
-- list_price_cents is the catalogue price, NOT the transacted price.
CREATE TABLE products (
    product_id       TEXT NOT NULL PRIMARY KEY,
    product_name     TEXT NOT NULL,
    category         TEXT NOT NULL CHECK (category IN ('手机数码','家用电器','服饰鞋包','食品生鲜','美妆个护','家居日用')),
    list_price_cents INTEGER NOT NULL CHECK (list_price_cents > 0),
    is_active        INTEGER NOT NULL CHECK (is_active IN (0, 1))
) STRICT;

-- Grain: one row per order (order header).
-- order_region is the region AT THE TIME OF THE ORDER; it may differ from
-- customers.signup_region. Regional reporting must use this column.
CREATE TABLE orders (
    order_id      TEXT NOT NULL PRIMARY KEY,
    customer_id   TEXT NOT NULL REFERENCES customers(customer_id),
    order_date    TEXT NOT NULL CHECK (order_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    status        TEXT NOT NULL CHECK (status IN ('pending','paid','completed','cancelled','refunded')),
    order_region  TEXT NOT NULL CHECK (order_region IN ('华东','华北','华南','华中','西南')),
    order_channel TEXT NOT NULL CHECK (order_channel IN ('web','app','mini_program'))
) STRICT;

-- Grain: one row per (order, product) line.
-- unit_price_cents is the price actually charged for this line at order time.
-- The line amount is NOT stored; it is derived as quantity * unit_price_cents
-- by the views below so it can never become stale.
CREATE TABLE order_items (
    order_item_id    TEXT NOT NULL PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES orders(order_id),
    product_id       TEXT NOT NULL REFERENCES products(product_id),
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    UNIQUE (order_id, product_id)
) STRICT;

CREATE INDEX idx_orders_date        ON orders (order_date);
CREATE INDEX idx_orders_status_date ON orders (status, order_date);
CREATE INDEX idx_orders_region      ON orders (order_region);
CREATE INDEX idx_orders_customer    ON orders (customer_id);
CREATE INDEX idx_items_order        ON order_items (order_id);
CREATE INDEX idx_items_product      ON order_items (product_id);

-- ---------------------------------------------------------------------------
-- Views. These exist to make the correct join path the easy path and to stop
-- fan-out double counting (see docs/metrics.md).
-- ---------------------------------------------------------------------------

-- Grain: one row per order line, ALL statuses. Use for status-mix analysis
-- (e.g. cancellation rate), never for revenue.
CREATE VIEW v_order_lines AS
SELECT
    oi.order_item_id                        AS order_item_id,
    o.order_id                              AS order_id,
    o.order_date                            AS order_date,
    substr(o.order_date, 1, 7)              AS order_month,
    o.status                                AS status,
    o.order_region                          AS order_region,
    o.order_channel                         AS order_channel,
    o.customer_id                           AS customer_id,
    oi.product_id                           AS product_id,
    p.product_name                          AS product_name,
    p.category                              AS category,
    oi.quantity                             AS quantity,
    oi.unit_price_cents                     AS unit_price_cents,
    oi.quantity * oi.unit_price_cents       AS line_amount_cents
FROM order_items AS oi
JOIN orders      AS o ON o.order_id   = oi.order_id
JOIN products    AS p ON p.product_id = oi.product_id;

-- Grain: one row per order line, revenue statuses only.
-- CANONICAL BASE for the v1 revenue metric. Because products joins 1:1 on the
-- primary key, this join cannot duplicate a line.
--   revenue      -> SUM(line_amount_cents)
--   order count  -> COUNT(DISTINCT order_id)      <- never COUNT(*)
CREATE VIEW v_revenue_lines AS
SELECT *
FROM v_order_lines
WHERE status IN ('paid', 'completed');

-- Grain: one row per revenue-status order.
-- Use when you need order-level amounts (distributions, percentiles) without
-- worrying about line fan-out.
CREATE VIEW v_revenue_orders AS
SELECT
    order_id                    AS order_id,
    order_date                  AS order_date,
    order_month                 AS order_month,
    status                      AS status,
    order_region                AS order_region,
    order_channel               AS order_channel,
    customer_id                 AS customer_id,
    SUM(line_amount_cents)      AS order_amount_cents,
    SUM(quantity)               AS item_quantity,
    COUNT(*)                    AS line_count
FROM v_revenue_lines
GROUP BY order_id, order_date, order_month, status, order_region, order_channel, customer_id;
