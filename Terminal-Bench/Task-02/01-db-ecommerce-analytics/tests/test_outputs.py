"""
test_outputs.py — verifies agent output against DuckDB ground truth
computed fresh from the same CSV data at test time.
"""
import json
import os
import duckdb
import pytest

OUTPUT = "/app/output_data"
DATA   = "/app/input_data"

# ─── shared DuckDB connection ─────────────────────────────────────
@pytest.fixture(scope="session")
def con():
    return duckdb.connect()

def load_json(filename):
    path = os.path.join(OUTPUT, filename)
    assert os.path.exists(path), f"Missing output file: {path}"
    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, list), f"{filename} must be a JSON array"
    return data

def approx_eq(a, b, tol=0.02):
    """Values within tol (absolute) are considered equal."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


# ════════════════════════════════════════════════════════════════════
# 1. top_customers.json
# ════════════════════════════════════════════════════════════════════
class TestTopCustomers:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH valid_returns AS (
                SELECT
                    o.customer_id,
                    SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price) AS return_value,
                    COUNT(*) AS return_count
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
                GROUP BY o.customer_id
            ),
            customer_gross AS (
                SELECT
                    c.customer_id,
                    c.name,
                    ROUND(SUM(o.quantity * o.unit_price), 2) AS gross_spend,
                    COUNT(o.order_id) AS order_count
                FROM read_csv_auto('{DATA}/orders.csv') o
                JOIN read_csv_auto('{DATA}/customers.csv') c ON o.customer_id = c.customer_id
                GROUP BY c.customer_id, c.name
            )
            SELECT
                cg.customer_id,
                cg.name,
                cg.gross_spend,
                ROUND(COALESCE(vr.return_value, 0), 2) AS return_value,
                COALESCE(vr.return_count, 0) AS return_count,
                ROUND(cg.gross_spend - COALESCE(vr.return_value, 0), 2) AS net_spend,
                cg.order_count,
                ROUND((cg.gross_spend - COALESCE(vr.return_value, 0)) / cg.order_count, 2) AS avg_order_value
            FROM customer_gross cg
            LEFT JOIN valid_returns vr ON cg.customer_id = vr.customer_id
            ORDER BY net_spend DESC
            LIMIT 15
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify top_customers.json exists and is a valid JSON array."""
        load_json("top_customers.json")

    def test_exactly_15_rows(self):
        """Verify exactly 15 customer rows are returned."""
        data = load_json("top_customers.json")
        assert len(data) == 15, f"Expected 15 rows, got {len(data)}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("top_customers.json")
        required = {"customer_id", "name", "net_spend", "gross_spend", "return_value", "return_count", "order_count", "avg_order_value"}
        for row in data:
            assert required <= set(row.keys()), f"Missing fields in: {row}"

    def test_sorted_by_net_spend_desc(self):
        """Verify output rows are ordered by net_spend descending."""
        data = load_json("top_customers.json")
        spends = [r["net_spend"] for r in data]
        assert spends == sorted(spends, reverse=True), "Not sorted by net_spend DESC"

    def test_top_customer_matches_expected(self, expected):
        """Verify the top customer matches the DuckDB ground truth."""
        data = load_json("top_customers.json")
        exp_id    = expected[0][0]
        exp_spend = float(expected[0][5])  # net_spend is index 5
        actual_ids   = [r["customer_id"] for r in data]
        actual_spends = [float(r["net_spend"]) for r in data]
        assert exp_id in actual_ids, f"Expected top customer {exp_id} not in output"
        idx = actual_ids.index(exp_id)
        assert approx_eq(actual_spends[idx], exp_spend, tol=0.05),             f"Top customer net_spend mismatch: {actual_spends[idx]} vs {exp_spend}"

    def test_all_customer_ids_match_expected(self, expected):
        """Verify the set of 15 customer IDs matches the expected top-15."""
        data = load_json("top_customers.json")
        exp_ids = {r[0] for r in expected}
        act_ids = {r["customer_id"] for r in data}
        assert exp_ids == act_ids, f"Customer ID sets differ. Extra: {act_ids - exp_ids}, Missing: {exp_ids - act_ids}"

    def test_avg_order_value_consistent(self):
        """Verify avg_order_value equals net_spend / order_count for each row."""
        data = load_json("top_customers.json")
        for row in data:
            computed = round(row["net_spend"] / row["order_count"], 2)
            assert approx_eq(row["avg_order_value"], computed, tol=0.02),                 f"avg_order_value {row['avg_order_value']} inconsistent with net_spend/count={computed}"

    def test_net_spend_equals_gross_minus_returns(self):
        """Verify net_spend equals gross_spend minus return_value."""
        data = load_json("top_customers.json")
        for row in data:
            computed = round(row["gross_spend"] - row["return_value"], 2)
            assert approx_eq(row["net_spend"], computed, tol=0.02),                 f"net_spend {row['net_spend']} != gross_spend - return_value = {computed}"


# ════════════════════════════════════════════════════════════════════
# 2. monthly_revenue.json
# ════════════════════════════════════════════════════════════════════
class TestMonthlyRevenue:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH monthly_gross AS (
                SELECT
                    printf('%04d-%02d', year(order_date), month(order_date)) AS month,
                    ROUND(SUM(quantity * unit_price), 2) AS gross_revenue,
                    COUNT(order_id) AS order_count
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY month ORDER BY month
            ),
            monthly_returns AS (
                SELECT
                    printf('%04d-%02d', year(r.return_date), month(r.return_date)) AS month,
                    ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2) AS return_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
                GROUP BY month
            ),
            combined AS (
                SELECT
                    mg.month, mg.gross_revenue, mg.order_count,
                    COALESCE(mr.return_value, 0) AS return_value,
                    ROUND(mg.gross_revenue - COALESCE(mr.return_value, 0), 2) AS net_revenue,
                    ROW_NUMBER() OVER (ORDER BY mg.month) AS rn
                FROM monthly_gross mg
                LEFT JOIN monthly_returns mr ON mg.month = mr.month
            )
            SELECT
                month, gross_revenue, order_count, return_value, net_revenue,
                CASE WHEN rn >= 3 THEN
                    ROUND(AVG(net_revenue) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)
                ELSE NULL END AS rolling_3m_avg_net_revenue,
                ROUND((net_revenue - LAG(net_revenue) OVER (ORDER BY month)) / LAG(net_revenue) OVER (ORDER BY month) * 100, 2) AS mom_growth_pct
            FROM combined
            ORDER BY month
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify monthly_revenue.json exists and is a valid JSON array."""
        load_json("monthly_revenue.json")

    def test_correct_row_count(self, expected):
        """Verify the number of months matches the number of distinct order months."""
        data = load_json("monthly_revenue.json")
        assert len(data) == len(expected),             f"Expected {len(expected)} months, got {len(data)}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("monthly_revenue.json")
        required = {"month", "gross_revenue", "order_count", "return_value", "net_revenue",
                    "rolling_3m_avg_net_revenue", "mom_growth_pct"}
        for row in data:
            assert required <= set(row.keys()), f"Missing fields: {row}"

    def test_month_format(self):
        """Verify every month value is in YYYY-MM format."""
        data = load_json("monthly_revenue.json")
        import re
        for row in data:
            assert re.match(r"^\d{4}-\d{2}$", row["month"]),                 f"month not in YYYY-MM format: {row['month']}"

    def test_sorted_by_month_asc(self):
        """Verify rows are ordered chronologically by month ascending."""
        data = load_json("monthly_revenue.json")
        months = [r["month"] for r in data]
        assert months == sorted(months), "Not sorted by month ASC"

    def test_first_month_has_null_growth(self):
        """Verify mom_growth_pct is null for the first month (no prior month)."""
        data = load_json("monthly_revenue.json")
        assert data[0]["mom_growth_pct"] is None,             f"First month mom_growth_pct should be null, got {data[0]['mom_growth_pct']}"

    def test_first_two_months_null_rolling_avg(self):
        """Verify rolling_3m_avg_net_revenue is null for the first two months."""
        data = load_json("monthly_revenue.json")
        assert data[0]["rolling_3m_avg_net_revenue"] is None,             f"Month 1 rolling_3m_avg_net_revenue should be null, got {data[0]['rolling_3m_avg_net_revenue']}"
        assert data[1]["rolling_3m_avg_net_revenue"] is None,             f"Month 2 rolling_3m_avg_net_revenue should be null, got {data[1]['rolling_3m_avg_net_revenue']}"

    def test_third_month_has_rolling_avg(self):
        """Verify rolling_3m_avg_net_revenue is non-null starting from month 3."""
        data = load_json("monthly_revenue.json")
        assert data[2]["rolling_3m_avg_net_revenue"] is not None,             "Month 3 rolling_3m_avg_net_revenue should not be null"

    def test_gross_revenue_values_match(self, expected):
        """Verify gross_revenue matches DuckDB ground truth for each month."""
        data = load_json("monthly_revenue.json")
        by_month = {r["month"]: r for r in data}
        for exp in expected:
            m = exp[0]
            assert m in by_month, f"Month {m} missing from output"
            assert approx_eq(by_month[m]["gross_revenue"], float(exp[1])),                 f"gross_revenue mismatch for {m}: {by_month[m]['gross_revenue']} vs {exp[1]}"

    def test_net_revenue_values_match(self, expected):
        """Verify net_revenue matches DuckDB ground truth for each month."""
        data = load_json("monthly_revenue.json")
        by_month = {r["month"]: r for r in data}
        for exp in expected:
            m = exp[0]
            assert approx_eq(by_month[m]["net_revenue"], float(exp[4])),                 f"net_revenue mismatch for {m}: {by_month[m]['net_revenue']} vs {exp[4]}"

    def test_net_revenue_equals_gross_minus_returns(self):
        """Verify net_revenue equals gross_revenue minus return_value."""
        data = load_json("monthly_revenue.json")
        for row in data:
            computed = round(row["gross_revenue"] - row["return_value"], 2)
            assert approx_eq(row["net_revenue"], computed, tol=0.02),                 f"net_revenue inconsistency for {row['month']}: {row['net_revenue']} vs {computed}"

    def test_mom_growth_based_on_net_revenue(self, expected):
        """Verify mom_growth_pct matches expected percentage change in net_revenue."""
        data = load_json("monthly_revenue.json")
        by_month = {r["month"]: r for r in data}
        for exp in expected[1:]:
            m, exp_growth = exp[0], exp[6]
            if exp_growth is None:
                continue
            act_growth = by_month[m]["mom_growth_pct"]
            assert act_growth is not None, f"mom_growth_pct null for {m}"
            assert approx_eq(act_growth, float(exp_growth), tol=0.1),                 f"MoM growth mismatch for {m}: {act_growth} vs {exp_growth}"

    def test_rolling_avg_values_match(self, expected):
        """Verify rolling_3m_avg_net_revenue matches the 3-month window average."""
        data = load_json("monthly_revenue.json")
        by_month = {r["month"]: r for r in data}
        for exp in expected[2:]:  # skip first 2
            m = exp[0]
            exp_rolling = exp[5]
            act_rolling = by_month[m]["rolling_3m_avg_net_revenue"]
            assert act_rolling is not None, f"rolling_3m_avg_net_revenue null for {m}"
            assert approx_eq(act_rolling, float(exp_rolling), tol=1.0),                 f"Rolling avg mismatch for {m}: {act_rolling} vs {exp_rolling}"


# ════════════════════════════════════════════════════════════════════
# 3. category_stats.json
# ════════════════════════════════════════════════════════════════════
class TestCategoryStats:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH valid_returns AS (
                SELECT
                    o.product_id,
                    o.order_id,
                    LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
            ),
            cat_returns AS (
                SELECT
                    p.category,
                    ROUND(SUM(vr.refund_value), 2) AS return_value,
                    COUNT(DISTINCT vr.order_id) AS return_orders
                FROM valid_returns vr
                JOIN read_csv_auto('{DATA}/products.csv') p ON vr.product_id = p.product_id
                GROUP BY p.category
            ),
            cat_gross AS (
                SELECT
                    p.category,
                    ROUND(SUM(o.quantity * o.unit_price), 2) AS total_revenue,
                    SUM(o.quantity) AS total_units,
                    COUNT(DISTINCT o.product_id) AS unique_products_sold,
                    COUNT(o.order_id) AS total_orders
                FROM read_csv_auto('{DATA}/orders.csv') o
                JOIN read_csv_auto('{DATA}/products.csv') p ON o.product_id = p.product_id
                GROUP BY p.category
            )
            SELECT
                cg.category, cg.total_revenue, cg.total_units, cg.unique_products_sold,
                COALESCE(cr.return_value, 0) AS return_value,
                ROUND(cg.total_revenue - COALESCE(cr.return_value, 0), 2) AS net_revenue,
                ROUND(COALESCE(cr.return_orders, 0) * 100.0 / cg.total_orders, 2) AS return_rate_pct
            FROM cat_gross cg
            LEFT JOIN cat_returns cr ON cg.category = cr.category
            ORDER BY cg.total_revenue DESC
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify category_stats.json exists and is a valid JSON array."""
        load_json("category_stats.json")

    def test_all_categories_present(self, expected):
        """Verify all product categories appear in the output."""
        data = load_json("category_stats.json")
        exp_cats = {r[0] for r in expected}
        act_cats = {r["category"] for r in data}
        assert exp_cats == act_cats, f"Category mismatch: {exp_cats} vs {act_cats}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("category_stats.json")
        required = {"category", "total_revenue", "total_units", "unique_products_sold",
                    "return_value", "net_revenue", "return_rate_pct"}
        for row in data:
            assert required <= set(row.keys())

    def test_sorted_by_revenue_desc(self):
        """Verify rows are ordered by total_revenue descending."""
        data = load_json("category_stats.json")
        revs = [r["total_revenue"] for r in data]
        assert revs == sorted(revs, reverse=True)

    def test_values_match(self, expected):
        """Verify all numeric metrics match DuckDB ground truth per category."""
        data = load_json("category_stats.json")
        by_cat = {r["category"]: r for r in data}
        for exp in expected:
            cat = exp[0]
            assert approx_eq(by_cat[cat]["total_revenue"], float(exp[1])),                 f"total_revenue mismatch for {cat}"
            assert int(by_cat[cat]["total_units"]) == int(exp[2]),                 f"total_units mismatch for {cat}"
            assert int(by_cat[cat]["unique_products_sold"]) == int(exp[3]),                 f"unique_products_sold mismatch for {cat}"
            assert approx_eq(by_cat[cat]["return_value"], float(exp[4]), tol=0.05),                 f"return_value mismatch for {cat}"
            assert approx_eq(by_cat[cat]["net_revenue"], float(exp[5]), tol=0.05),                 f"net_revenue mismatch for {cat}"
            assert approx_eq(by_cat[cat]["return_rate_pct"], float(exp[6]), tol=0.05),                 f"return_rate_pct mismatch for {cat}"

    def test_net_revenue_equals_gross_minus_returns(self):
        """Verify net_revenue equals total_revenue minus return_value."""
        data = load_json("category_stats.json")
        for row in data:
            computed = round(row["total_revenue"] - row["return_value"], 2)
            assert approx_eq(row["net_revenue"], computed, tol=0.02),                 f"net_revenue inconsistency for {row['category']}: {row['net_revenue']} vs {computed}"

    def test_return_rate_pct_bounds(self):
        """Verify return_rate_pct is between 0 and 100 for all categories."""
        data = load_json("category_stats.json")
        for row in data:
            assert 0.0 <= row["return_rate_pct"] <= 100.0,                 f"return_rate_pct out of bounds for {row['category']}: {row['return_rate_pct']}"


# ════════════════════════════════════════════════════════════════════
# 4. supplier_share.json
# ════════════════════════════════════════════════════════════════════
class TestSupplierShare:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH return_by_supplier AS (
                SELECT
                    p.supplier_id,
                    ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2) AS return_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
                JOIN read_csv_auto('{DATA}/products.csv') p ON o.product_id = p.product_id
                GROUP BY p.supplier_id
            ),
            supplier_rev AS (
                SELECT
                    s.supplier_id, s.name AS supplier_name, s.country,
                    ROUND(SUM(o.quantity * o.unit_price), 2) AS total_revenue
                FROM read_csv_auto('{DATA}/orders.csv') o
                JOIN read_csv_auto('{DATA}/products.csv') p ON o.product_id = p.product_id
                JOIN read_csv_auto('{DATA}/suppliers.csv') s ON p.supplier_id = s.supplier_id
                GROUP BY s.supplier_id, s.name, s.country
            )
            SELECT
                sr.supplier_id, sr.supplier_name, sr.country, sr.total_revenue,
                COALESCE(rbs.return_value, 0) AS return_value,
                ROUND(sr.total_revenue - COALESCE(rbs.return_value, 0), 2) AS net_revenue,
                ROUND((sr.total_revenue - COALESCE(rbs.return_value, 0)) /
                      SUM(sr.total_revenue - COALESCE(rbs.return_value, 0)) OVER () * 100, 2) AS share_pct
            FROM supplier_rev sr
            LEFT JOIN return_by_supplier rbs ON sr.supplier_id = rbs.supplier_id
            ORDER BY net_revenue DESC
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify supplier_share.json exists and is a valid JSON array."""
        load_json("supplier_share.json")

    def test_all_suppliers_present(self, expected):
        """Verify all 5 suppliers appear in the output."""
        data = load_json("supplier_share.json")
        assert len(data) == len(expected), f"Expected {len(expected)} suppliers, got {len(data)}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("supplier_share.json")
        required = {"supplier_id", "supplier_name", "country", "total_revenue",
                    "return_value", "net_revenue", "share_pct"}
        for row in data:
            assert required <= set(row.keys())

    def test_share_pcts_sum_to_100(self):
        """Verify share_pct values sum to 100 across all suppliers."""
        data = load_json("supplier_share.json")
        total = sum(r["share_pct"] for r in data)
        assert abs(total - 100.0) < 0.1, f"share_pct sum {total} != 100"

    def test_sorted_by_net_revenue_desc(self):
        """Verify rows are ordered by net_revenue descending."""
        data = load_json("supplier_share.json")
        revs = [r["net_revenue"] for r in data]
        assert revs == sorted(revs, reverse=True), "Not sorted by net_revenue DESC"

    def test_values_match(self, expected):
        """Verify all numeric metrics match DuckDB ground truth per supplier."""
        data = load_json("supplier_share.json")
        by_id = {r["supplier_id"]: r for r in data}
        for exp in expected:
            sid = exp[0]
            assert sid in by_id, f"supplier_id {sid} missing"
            assert approx_eq(by_id[sid]["total_revenue"], float(exp[3])),                 f"total_revenue mismatch for supplier {sid}"
            assert approx_eq(by_id[sid]["return_value"], float(exp[4]), tol=0.05),                 f"return_value mismatch for supplier {sid}"
            assert approx_eq(by_id[sid]["net_revenue"], float(exp[5]), tol=0.05),                 f"net_revenue mismatch for supplier {sid}"
            assert approx_eq(by_id[sid]["share_pct"], float(exp[6]), tol=0.05),                 f"share_pct mismatch for supplier {sid}"

    def test_net_revenue_equals_gross_minus_returns(self):
        """Verify net_revenue equals total_revenue minus return_value."""
        data = load_json("supplier_share.json")
        for row in data:
            computed = round(row["total_revenue"] - row["return_value"], 2)
            assert approx_eq(row["net_revenue"], computed, tol=0.02),                 f"net_revenue inconsistency for supplier {row['supplier_id']}: {row['net_revenue']} vs {computed}"


# ════════════════════════════════════════════════════════════════════
# 5. customer_cohorts.json
# ════════════════════════════════════════════════════════════════════
class TestCustomerCohorts:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH first_orders AS (
                SELECT customer_id,
                       printf('%04d-%02d', year(MIN(order_date)), month(MIN(order_date))) AS cohort_month
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY customer_id
            ),
            cohort_sizes AS (
                SELECT cohort_month, COUNT(*) AS cohort_size
                FROM first_orders GROUP BY cohort_month
            ),
            next_month_activity AS (
                SELECT DISTINCT fo.cohort_month, fo.customer_id
                FROM first_orders fo
                JOIN read_csv_auto('{DATA}/orders.csv') o ON fo.customer_id = o.customer_id
                WHERE printf('%04d-%02d', year(o.order_date), month(o.order_date))
                    = printf('%04d-%02d',
                        year(strptime(fo.cohort_month,'%Y-%m') + INTERVAL 1 MONTH),
                        month(strptime(fo.cohort_month,'%Y-%m') + INTERVAL 1 MONTH))
            ),
            retained AS (
                SELECT cohort_month, COUNT(*) AS retained_month2
                FROM next_month_activity GROUP BY cohort_month
            ),
            last_cohort AS (SELECT MAX(cohort_month) AS last_month FROM cohort_sizes)
            SELECT cs.cohort_month, cs.cohort_size,
                   COALESCE(r.retained_month2, 0) AS retained_month2,
                   ROUND(COALESCE(r.retained_month2,0)*100.0/cs.cohort_size, 2) AS retention_rate_pct
            FROM cohort_sizes cs
            LEFT JOIN retained r ON cs.cohort_month = r.cohort_month
            CROSS JOIN last_cohort lc
            WHERE cs.cohort_size >= 5 AND cs.cohort_month < lc.last_month
            ORDER BY cs.cohort_month
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify customer_cohorts.json exists and is a valid JSON array."""
        load_json("customer_cohorts.json")

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("customer_cohorts.json")
        required = {"cohort_month", "cohort_size", "retained_month2", "retention_rate_pct"}
        for row in data:
            assert required <= set(row.keys())

    def test_correct_row_count(self, expected):
        """Verify the number of cohort rows matches expected after size and month filters."""
        data = load_json("customer_cohorts.json")
        assert len(data) == len(expected),             f"Expected {len(expected)} cohort rows, got {len(data)}"

    def test_sorted_by_cohort_month(self):
        """Verify rows are ordered by cohort_month ascending."""
        data = load_json("customer_cohorts.json")
        months = [r["cohort_month"] for r in data]
        assert months == sorted(months)

    def test_cohort_sizes_match(self, expected):
        """Verify cohort_size matches the number of customers with their first order in that month."""
        data = load_json("customer_cohorts.json")
        by_month = {r["cohort_month"]: r for r in data}
        for exp in expected:
            cm = exp[0]
            assert cm in by_month, f"Cohort month {cm} missing"
            assert int(by_month[cm]["cohort_size"]) == int(exp[1]),                 f"cohort_size mismatch for {cm}: {by_month[cm]['cohort_size']} vs {exp[1]}"

    def test_retained_counts_match(self, expected):
        """Verify retained_month2 matches the DuckDB ground truth per cohort."""
        data = load_json("customer_cohorts.json")
        by_month = {r["cohort_month"]: r for r in data}
        for exp in expected:
            cm = exp[0]
            assert int(by_month[cm]["retained_month2"]) == int(exp[2]),                 f"retained_month2 mismatch for {cm}: {by_month[cm]['retained_month2']} vs {exp[2]}"

    def test_retention_rate_computed_correctly(self):
        """Verify retention_rate_pct equals retained_month2 / cohort_size * 100."""
        data = load_json("customer_cohorts.json")
        for row in data:
            expected_rate = round(row["retained_month2"] / row["cohort_size"] * 100, 2)
            assert approx_eq(row["retention_rate_pct"], expected_rate, tol=0.02),                 f"retention_rate_pct {row['retention_rate_pct']} != {expected_rate} for {row['cohort_month']}"

    def test_last_month_excluded(self, expected, con):
        """Verify the final calendar month in the dataset is not in the output."""
        data = load_json("customer_cohorts.json")
        last_month = con.execute(
            f"SELECT MAX(printf('%04d-%02d', year(order_date),"
            f" month(order_date))) FROM read_csv_auto('{DATA}/orders.csv')"
        ).fetchone()[0]
        actual_months = {r["cohort_month"] for r in data}
        assert last_month not in actual_months,             f"Last calendar month {last_month} should be excluded from customer_cohorts.json"


# ════════════════════════════════════════════════════════════════════
# 6. product_performance.json
# ════════════════════════════════════════════════════════════════════
class TestProductPerformance:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH valid_returns AS (
                SELECT
                    r.order_id, o.product_id,
                    LEAST(r.quantity_returned, o.quantity) AS qty_returned,
                    LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
            ),
            return_by_product AS (
                SELECT product_id, ROUND(SUM(refund_value), 2) AS return_value, COUNT(*) AS return_order_count
                FROM valid_returns GROUP BY product_id
            ),
            product_sales AS (
                SELECT o.product_id, COUNT(o.order_id) AS total_orders,
                       SUM(o.quantity) AS net_quantity_sold,
                       ROUND(SUM(o.quantity * o.unit_price), 2) AS gross_revenue
                FROM read_csv_auto('{DATA}/orders.csv') o GROUP BY o.product_id
            ),
            product_net AS (
                SELECT
                    ps.product_id, p.name AS product_name, p.category, s.name AS supplier_name,
                    ps.total_orders, ps.net_quantity_sold, ps.gross_revenue,
                    COALESCE(rbp.return_value, 0) AS return_value,
                    ROUND(ps.gross_revenue - COALESCE(rbp.return_value, 0), 2) AS net_revenue,
                    ROUND(COALESCE(rbp.return_order_count, 0) * 100.0 / ps.total_orders, 2) AS return_rate_pct
                FROM product_sales ps
                JOIN read_csv_auto('{DATA}/products.csv') p ON ps.product_id = p.product_id
                JOIN read_csv_auto('{DATA}/suppliers.csv') s ON p.supplier_id = s.supplier_id
                LEFT JOIN return_by_product rbp ON ps.product_id = rbp.product_id
            ),
            total_net AS (SELECT SUM(net_revenue) AS total FROM product_net),
            ranked AS (
                SELECT pn.*,
                    ROUND(pn.net_revenue / t.total * 100, 2) AS revenue_share_pct,
                    ROUND(SUM(pn.net_revenue) OVER (ORDER BY pn.net_revenue DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / t.total * 100, 2) AS cumulative_revenue_pct
                FROM product_net pn CROSS JOIN total_net t ORDER BY pn.net_revenue DESC
            )
            SELECT product_id, product_name, category, supplier_name, total_orders,
                   net_quantity_sold, gross_revenue, return_value, net_revenue, return_rate_pct,
                   revenue_share_pct, cumulative_revenue_pct,
                   CASE WHEN cumulative_revenue_pct <= 70 THEN 'A'
                        WHEN cumulative_revenue_pct <= 90 THEN 'B'
                        ELSE 'C' END AS abc_class
            FROM ranked ORDER BY net_revenue DESC
        """).fetchall()
        return rows

    def test_file_exists(self):
        """Verify product_performance.json exists and is a valid JSON array."""
        load_json("product_performance.json")

    def test_all_50_products_present(self):
        """Verify all 50 products (IDs 1-50) appear in the output."""
        data = load_json("product_performance.json")
        assert len(data) == 50, f"Expected 50 products, got {len(data)}"
        ids = {r["product_id"] for r in data}
        assert ids == set(range(1, 51)), f"Product IDs mismatch: missing {set(range(1,51)) - ids}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("product_performance.json")
        required = {"product_id", "product_name", "category", "supplier_name",
                    "total_orders", "net_quantity_sold", "gross_revenue", "return_value",
                    "net_revenue", "return_rate_pct", "revenue_share_pct",
                    "cumulative_revenue_pct", "abc_class"}
        for row in data:
            assert required <= set(row.keys()), f"Missing fields in: {set(required) - set(row.keys())}"

    def test_sorted_by_net_revenue_desc(self):
        """Verify rows are ordered by net_revenue descending."""
        data = load_json("product_performance.json")
        revs = [r["net_revenue"] for r in data]
        assert revs == sorted(revs, reverse=True), "Not sorted by net_revenue DESC"

    def test_abc_classes_valid(self):
        """Verify every abc_class value is one of 'A', 'B', or 'C'."""
        data = load_json("product_performance.json")
        valid = {"A", "B", "C"}
        for row in data:
            assert row["abc_class"] in valid, f"Invalid abc_class: {row['abc_class']}"

    def test_cumulative_revenue_pct_increases(self):
        """Verify cumulative_revenue_pct is monotonically non-decreasing."""
        data = load_json("product_performance.json")
        cumulatives = [r["cumulative_revenue_pct"] for r in data]
        for i in range(1, len(cumulatives)):
            assert cumulatives[i] >= cumulatives[i-1],                 f"cumulative_revenue_pct not monotonically increasing at position {i}: {cumulatives[i-1]} -> {cumulatives[i]}"

    def test_revenue_share_pct_sums_to_100(self):
        """Verify revenue_share_pct values sum to 100 across all products."""
        data = load_json("product_performance.json")
        total = sum(r["revenue_share_pct"] for r in data)
        assert abs(total - 100.0) < 0.15, f"revenue_share_pct sum {total} != 100"

    def test_abc_a_class_count(self):
        """Verify the number of A-class products falls within the expected Pareto range."""
        data = load_json("product_performance.json")
        a_count = sum(1 for r in data if r["abc_class"] == "A")
        # Expect 20-27 A-class products (cumulative <= 70%)
        assert 18 <= a_count <= 28, f"A-class count {a_count} outside expected range 18-28"

    def test_return_rate_pct_bounds(self):
        """Verify return_rate_pct is between 0 and 100 for all products."""
        data = load_json("product_performance.json")
        for row in data:
            assert 0.0 <= row["return_rate_pct"] <= 100.0,                 f"return_rate_pct out of bounds for product {row['product_id']}: {row['return_rate_pct']}"

    def test_net_revenue_values_match(self, expected):
        """Verify net_revenue matches the DuckDB ground truth for the top 5 products."""
        data = load_json("product_performance.json")
        by_id = {r["product_id"]: r for r in data}
        for exp in expected[:5]:  # top 5 products by net_revenue
            pid = exp[0]
            assert pid in by_id, f"product_id {pid} missing"
            assert approx_eq(by_id[pid]["net_revenue"], float(exp[8]), tol=0.05),                 f"net_revenue mismatch for product {pid}: {by_id[pid]['net_revenue']} vs {exp[8]}"

    def test_abc_classification_logic(self, expected):
        """Verify A class products are at cumulative <= 70%, B <= 90%, C > 90%."""
        data = load_json("product_performance.json")
        for row in data:
            c = row["cumulative_revenue_pct"]
            cls = row["abc_class"]
            if cls == "A":
                assert c <= 70.5, f"A-class product {row['product_id']} has cumulative {c} > 70"
            elif cls == "B":
                assert c <= 90.5, f"B-class product {row['product_id']} has cumulative {c} > 90"

    def test_net_revenue_equals_gross_minus_returns(self):
        """Verify net_revenue equals gross_revenue minus return_value."""
        data = load_json("product_performance.json")
        for row in data:
            computed = round(row["gross_revenue"] - row["return_value"], 2)
            assert approx_eq(row["net_revenue"], computed, tol=0.02),                 f"net_revenue inconsistency for product {row['product_id']}: {row['net_revenue']} vs {computed}"


# ════════════════════════════════════════════════════════════════════
# 7. customer_segments.json
# ════════════════════════════════════════════════════════════════════
class TestCustomerSegments:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH customer_returns AS (
                SELECT
                    o.customer_id,
                    SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price) AS return_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
                GROUP BY o.customer_id
            ),
            customer_orders AS (
                SELECT
                    customer_id,
                    MAX(order_date)            AS last_order_date,
                    COUNT(order_id)            AS order_count,
                    SUM(quantity * unit_price) AS gross_spend
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY customer_id
            ),
            ref_date AS (
                SELECT MAX(last_order_date) AS ref FROM customer_orders
            ),
            customer_metrics AS (
                SELECT
                    co.customer_id,
                    CAST((SELECT ref FROM ref_date) - co.last_order_date AS INTEGER) AS recency_days,
                    co.order_count,
                    ROUND(co.gross_spend - COALESCE(cr.return_value, 0), 2) AS net_spend
                FROM customer_orders co
                LEFT JOIN customer_returns cr ON co.customer_id = cr.customer_id
            ),
            rfm AS (
                SELECT
                    customer_id, recency_days, order_count, net_spend,
                    NTILE(4) OVER (ORDER BY recency_days DESC, customer_id ASC) AS r_score,
                    NTILE(4) OVER (ORDER BY order_count ASC,   customer_id ASC) AS f_score,
                    NTILE(4) OVER (ORDER BY net_spend ASC,     customer_id ASC) AS m_score
                FROM customer_metrics
            ),
            segmented AS (
                SELECT *,
                    r_score + f_score + m_score AS rfm_total,
                    CASE
                        WHEN r_score + f_score + m_score >= 10 THEN 'Champions'
                        WHEN r_score + f_score + m_score >= 7  THEN 'Loyal Customers'
                        WHEN r_score + f_score + m_score >= 4  THEN 'At Risk'
                        ELSE 'Lost'
                    END AS segment
                FROM rfm
            ),
            grand_total AS (SELECT SUM(net_spend) AS total FROM segmented)
            SELECT
                segment,
                COUNT(*)                                                          AS customer_count,
                ROUND(AVG(recency_days), 1)                                       AS avg_recency_days,
                ROUND(AVG(order_count), 2)                                        AS avg_order_count,
                ROUND(AVG(net_spend), 2)                                          AS avg_net_spend,
                ROUND(SUM(net_spend), 2)                                          AS total_net_spend,
                ROUND(SUM(net_spend) / (SELECT total FROM grand_total) * 100, 2)  AS revenue_share_pct
            FROM segmented
            GROUP BY segment
            ORDER BY total_net_spend DESC
        """).fetchall()
        cols = ["segment","customer_count","avg_recency_days","avg_order_count",
                "avg_net_spend","total_net_spend","revenue_share_pct"]
        return [dict(zip(cols, r)) for r in rows]

    def test_file_exists(self):
        """Verify customer_segments.json exists and is a JSON array."""
        load_json("customer_segments.json")

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("customer_segments.json")
        required = {"segment","customer_count","avg_recency_days","avg_order_count",
                    "avg_net_spend","total_net_spend","revenue_share_pct"}
        for row in data:
            assert required <= set(row.keys()), f"Missing fields: {required - set(row.keys())}"

    def test_valid_segments(self):
        """Verify exactly the four expected segment names are present."""
        data = load_json("customer_segments.json")
        expected_segs = {"Champions", "Loyal Customers", "At Risk", "Lost"}
        actual_segs = {r["segment"] for r in data}
        assert actual_segs == expected_segs, f"Segments mismatch: got {actual_segs}"

    def test_sorted_by_total_net_spend_desc(self):
        """Verify rows are ordered by total_net_spend descending."""
        data = load_json("customer_segments.json")
        spends = [r["total_net_spend"] for r in data]
        assert spends == sorted(spends, reverse=True), "Not sorted by total_net_spend DESC"

    def test_all_customers_accounted(self):
        """Verify the sum of customer_count across all segments equals 1000."""
        data = load_json("customer_segments.json")
        total = sum(r["customer_count"] for r in data)
        assert total == 1000, f"Expected 1000 total customers, got {total}"

    def test_revenue_share_sums_to_100(self):
        """Verify revenue_share_pct values sum to approximately 100."""
        data = load_json("customer_segments.json")
        total = sum(r["revenue_share_pct"] for r in data)
        assert approx_eq(total, 100.0, tol=0.1), f"revenue_share_pct sums to {total}, expected ~100"

    def test_customer_counts_match(self, expected):
        """Verify customer_count per segment matches DuckDB ground truth."""
        data = load_json("customer_segments.json")
        exp_map = {r["segment"]: r["customer_count"] for r in expected}
        for row in data:
            seg = row["segment"]
            assert row["customer_count"] == exp_map[seg], \
                f"Segment '{seg}': expected {exp_map[seg]} customers, got {row['customer_count']}"

    def test_avg_recency_days_match(self, expected):
        """Verify avg_recency_days per segment matches DuckDB ground truth."""
        data = load_json("customer_segments.json")
        exp_map = {r["segment"]: r["avg_recency_days"] for r in expected}
        for row in data:
            seg = row["segment"]
            assert approx_eq(row["avg_recency_days"], exp_map[seg], tol=1.0), \
                f"Segment '{seg}': avg_recency_days {row['avg_recency_days']} != expected {exp_map[seg]}"

    def test_total_net_spend_match(self, expected):
        """Verify total_net_spend per segment matches DuckDB ground truth."""
        data = load_json("customer_segments.json")
        exp_map = {r["segment"]: r["total_net_spend"] for r in expected}
        for row in data:
            seg = row["segment"]
            assert approx_eq(row["total_net_spend"], exp_map[seg], tol=1.0), \
                f"Segment '{seg}': total_net_spend {row['total_net_spend']} != expected {exp_map[seg]}"


# ════════════════════════════════════════════════════════════════════
# 8. price_cohort_performance.json
# ════════════════════════════════════════════════════════════════════
class TestPriceCohortPerformance:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH product_median_price AS (
                SELECT
                    product_id,
                    MEDIAN(unit_price) AS median_price
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY product_id
            ),
            product_tiers AS (
                SELECT
                    product_id,
                    NTILE(3) OVER (ORDER BY median_price ASC) AS tier
                FROM product_median_price
            ),
            valid_returns AS (
                SELECT
                    o.product_id,
                    o.order_id,
                    LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
            ),
            return_by_product AS (
                SELECT
                    product_id,
                    ROUND(SUM(refund_value), 2) AS return_value,
                    COUNT(*) AS return_order_count
                FROM valid_returns
                GROUP BY product_id
            ),
            sales_by_product AS (
                SELECT
                    product_id,
                    COUNT(order_id) AS total_orders,
                    ROUND(SUM(quantity * unit_price), 2) AS gross_revenue
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY product_id
            ),
            product_data AS (
                SELECT
                    pt.tier,
                    CASE pt.tier WHEN 1 THEN 'Budget' WHEN 2 THEN 'Mid-range' ELSE 'Premium' END AS tier_name,
                    sp.total_orders,
                    sp.gross_revenue,
                    COALESCE(rbp.return_value, 0) AS return_value,
                    COALESCE(rbp.return_order_count, 0) AS return_order_count
                FROM product_tiers pt
                JOIN sales_by_product sp ON pt.product_id = sp.product_id
                LEFT JOIN return_by_product rbp ON pt.product_id = rbp.product_id
            )
            SELECT
                tier,
                tier_name,
                COUNT(*) AS product_count,
                SUM(total_orders) AS total_orders,
                ROUND(SUM(gross_revenue), 2) AS gross_revenue,
                ROUND(SUM(return_value), 2) AS return_value,
                ROUND(SUM(gross_revenue) - SUM(return_value), 2) AS net_revenue,
                ROUND(SUM(return_order_count) * 100.0 / SUM(total_orders), 2) AS return_rate_pct,
                ROUND((SUM(gross_revenue) - SUM(return_value)) / COUNT(*), 2) AS avg_net_revenue_per_product
            FROM product_data
            GROUP BY tier, tier_name
            ORDER BY tier ASC
        """).fetchall()
        cols = ["tier","tier_name","product_count","total_orders","gross_revenue",
                "return_value","net_revenue","return_rate_pct","avg_net_revenue_per_product"]
        return [dict(zip(cols, r)) for r in rows]

    def test_file_exists(self):
        """Verify price_cohort_performance.json exists and is a JSON array."""
        load_json("price_cohort_performance.json")

    def test_exactly_three_tiers(self):
        """Verify exactly 3 rows (one per tier)."""
        data = load_json("price_cohort_performance.json")
        assert len(data) == 3, f"Expected 3 tier rows, got {len(data)}"

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("price_cohort_performance.json")
        required = {"tier","tier_name","product_count","total_orders","gross_revenue",
                    "return_value","net_revenue","return_rate_pct","avg_net_revenue_per_product"}
        for row in data:
            assert required <= set(row.keys()), f"Missing fields: {required - set(row.keys())}"

    def test_valid_tier_names(self):
        """Verify tier names are exactly Budget, Mid-range, Premium."""
        data = load_json("price_cohort_performance.json")
        names = {r["tier_name"] for r in data}
        assert names == {"Budget", "Mid-range", "Premium"}, f"Unexpected tier names: {names}"

    def test_sorted_by_tier_asc(self):
        """Verify rows are ordered by tier ascending (1, 2, 3)."""
        data = load_json("price_cohort_performance.json")
        tiers = [r["tier"] for r in data]
        assert tiers == sorted(tiers), f"Not sorted by tier ASC: {tiers}"

    def test_product_counts_sum_to_50(self):
        """Verify product_count totals exactly 50 across all tiers."""
        data = load_json("price_cohort_performance.json")
        total = sum(r["product_count"] for r in data)
        assert total == 50, f"Expected 50 total products, got {total}"

    def test_product_counts_match(self, expected):
        """Verify product_count per tier matches DuckDB ground truth (catches wrong price source)."""
        data = load_json("price_cohort_performance.json")
        exp_map = {r["tier"]: r["product_count"] for r in expected}
        for row in data:
            t = row["tier"]
            assert row["product_count"] == exp_map[t], \
                f"Tier {t}: expected {exp_map[t]} products, got {row['product_count']}"

    def test_net_revenue_match(self, expected):
        """Verify net_revenue per tier matches DuckDB ground truth."""
        data = load_json("price_cohort_performance.json")
        exp_map = {r["tier"]: r["net_revenue"] for r in expected}
        for row in data:
            t = row["tier"]
            assert approx_eq(row["net_revenue"], exp_map[t], tol=1.0), \
                f"Tier {t}: net_revenue {row['net_revenue']} != expected {exp_map[t]}"

    def test_return_rate_pct_bounds(self):
        """Verify return_rate_pct is between 0 and 100 for all tiers."""
        data = load_json("price_cohort_performance.json")
        for row in data:
            assert 0.0 <= row["return_rate_pct"] <= 100.0, \
                f"return_rate_pct out of bounds for tier {row['tier']}: {row['return_rate_pct']}"

    def test_net_revenue_equals_gross_minus_returns(self):
        """Verify net_revenue == gross_revenue - return_value."""
        data = load_json("price_cohort_performance.json")
        for row in data:
            computed = round(row["gross_revenue"] - row["return_value"], 2)
            assert approx_eq(row["net_revenue"], computed, tol=0.02), \
                f"Tier {row['tier']}: net_revenue {row['net_revenue']} != gross - returns ({computed})"

    def test_avg_net_revenue_per_product_consistent(self):
        """Verify avg_net_revenue_per_product == net_revenue / product_count."""
        data = load_json("price_cohort_performance.json")
        for row in data:
            computed = round(row["net_revenue"] / row["product_count"], 2)
            assert approx_eq(row["avg_net_revenue_per_product"], computed, tol=0.02), \
                f"Tier {row['tier']}: avg_net_revenue_per_product {row['avg_net_revenue_per_product']} != {computed}"


# ════════════════════════════════════════════════════════════════════
# 9. cohort_revenue_trend.json
# ════════════════════════════════════════════════════════════════════
class TestCohortRevenueTrend:
    @pytest.fixture(scope="class")
    def expected(self, con):
        rows = con.execute(f"""
            WITH first_orders AS (
                SELECT customer_id,
                       printf('%04d-%02d', year(MIN(order_date)), month(MIN(order_date))) AS cohort_month
                FROM read_csv_auto('{DATA}/orders.csv')
                GROUP BY customer_id
            ),
            valid_returns AS (
                SELECT o.order_id,
                       LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
                FROM read_csv_auto('{DATA}/returns.csv') r
                JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
            ),
            order_data AS (
                SELECT o.order_id, o.customer_id,
                       printf('%04d-%02d', year(o.order_date), month(o.order_date)) AS activity_month,
                       o.quantity * o.unit_price AS gross_amount,
                       COALESCE(vr.refund_value, 0) AS refund_amount
                FROM read_csv_auto('{DATA}/orders.csv') o
                LEFT JOIN valid_returns vr ON o.order_id = vr.order_id
            ),
            cohort_activity AS (
                SELECT fo.cohort_month,
                       od.activity_month,
                       COUNT(DISTINCT od.customer_id) AS active_customers,
                       ROUND(SUM(od.gross_amount), 2) AS gross_revenue,
                       ROUND(SUM(od.gross_amount - od.refund_amount), 2) AS net_revenue
                FROM order_data od
                JOIN first_orders fo ON od.customer_id = fo.customer_id
                WHERE od.activity_month >= fo.cohort_month
                GROUP BY fo.cohort_month, od.activity_month
            )
            SELECT cohort_month, activity_month,
                   CAST(
                       (YEAR(strptime(activity_month, '%Y-%m')) - YEAR(strptime(cohort_month, '%Y-%m'))) * 12 +
                       (MONTH(strptime(activity_month, '%Y-%m')) - MONTH(strptime(cohort_month, '%Y-%m')))
                   AS INTEGER) AS months_since_acquisition,
                   active_customers, gross_revenue, net_revenue
            FROM cohort_activity
            ORDER BY cohort_month ASC, activity_month ASC
        """).fetchall()
        cols = ["cohort_month", "activity_month", "months_since_acquisition",
                "active_customers", "gross_revenue", "net_revenue"]
        return [dict(zip(cols, r)) for r in rows]

    def test_file_exists(self):
        """Verify cohort_revenue_trend.json exists and is a valid JSON array."""
        load_json("cohort_revenue_trend.json")

    def test_required_fields(self):
        """Verify all required fields are present in every row."""
        data = load_json("cohort_revenue_trend.json")
        required = {"cohort_month", "activity_month", "months_since_acquisition",
                    "active_customers", "gross_revenue", "net_revenue"}
        for row in data:
            assert required <= set(row.keys()), \
                f"Missing fields: {required - set(row.keys())}"

    def test_correct_row_count(self, expected):
        """Verify the number of rows matches expected (only active cohort-month pairs)."""
        data = load_json("cohort_revenue_trend.json")
        assert len(data) == len(expected), \
            f"Expected {len(expected)} rows, got {len(data)}"

    def test_sorted_correctly(self):
        """Verify rows are ordered by cohort_month ASC then activity_month ASC."""
        data = load_json("cohort_revenue_trend.json")
        keys = [(r["cohort_month"], r["activity_month"]) for r in data]
        assert keys == sorted(keys), "Not sorted by cohort_month ASC, activity_month ASC"

    def test_month_format(self):
        """Verify cohort_month and activity_month are in YYYY-MM format."""
        import re
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            assert re.match(r"^\d{4}-\d{2}$", row["cohort_month"]), \
                f"cohort_month not in YYYY-MM format: {row['cohort_month']}"
            assert re.match(r"^\d{4}-\d{2}$", row["activity_month"]), \
                f"activity_month not in YYYY-MM format: {row['activity_month']}"

    def test_activity_month_ge_cohort_month(self):
        """Verify activity_month is always >= cohort_month (no retroactive attribution)."""
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            assert row["activity_month"] >= row["cohort_month"], \
                f"activity_month {row['activity_month']} < cohort_month {row['cohort_month']}"

    def test_months_since_acquisition_correct(self):
        """Verify months_since_acquisition equals the integer calendar-month gap."""
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            cy, cm = int(row["cohort_month"][:4]), int(row["cohort_month"][5:])
            ay, am = int(row["activity_month"][:4]), int(row["activity_month"][5:])
            expected_gap = (ay - cy) * 12 + (am - cm)
            assert row["months_since_acquisition"] == expected_gap, \
                f"months_since_acquisition {row['months_since_acquisition']} != {expected_gap} " \
                f"for cohort={row['cohort_month']}, activity={row['activity_month']}"

    def test_zero_months_for_acquisition_month(self):
        """Verify months_since_acquisition is 0 when cohort_month equals activity_month."""
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            if row["cohort_month"] == row["activity_month"]:
                assert row["months_since_acquisition"] == 0, \
                    f"months_since_acquisition should be 0 for acquisition month: {row}"

    def test_active_customers_positive(self):
        """Verify active_customers is always >= 1 (rows only exist when there is activity)."""
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            assert row["active_customers"] >= 1, \
                f"active_customers must be >= 1, got {row['active_customers']}"

    def test_net_revenue_le_gross_revenue(self):
        """Verify net_revenue <= gross_revenue for every row (returns reduce revenue)."""
        data = load_json("cohort_revenue_trend.json")
        for row in data:
            assert row["net_revenue"] <= row["gross_revenue"] + 0.02, \
                f"net_revenue {row['net_revenue']} > gross_revenue {row['gross_revenue']} " \
                f"for cohort={row['cohort_month']}, activity={row['activity_month']}"

    def test_active_customers_match(self, expected):
        """Verify active_customers matches DuckDB ground truth for each cohort-month pair."""
        data = load_json("cohort_revenue_trend.json")
        by_key = {(r["cohort_month"], r["activity_month"]): r for r in data}
        for exp in expected:
            key = (exp["cohort_month"], exp["activity_month"])
            assert key in by_key, \
                f"Missing row for cohort={exp['cohort_month']}, activity={exp['activity_month']}"
            assert int(by_key[key]["active_customers"]) == int(exp["active_customers"]), \
                f"active_customers mismatch for {key}: " \
                f"{by_key[key]['active_customers']} vs {exp['active_customers']}"

    def test_net_revenue_values_match(self, expected):
        """Verify net_revenue matches DuckDB ground truth for each cohort-month pair."""
        data = load_json("cohort_revenue_trend.json")
        by_key = {(r["cohort_month"], r["activity_month"]): r for r in data}
        for exp in expected:
            key = (exp["cohort_month"], exp["activity_month"])
            assert approx_eq(by_key[key]["net_revenue"], float(exp["net_revenue"]), tol=0.05), \
                f"net_revenue mismatch for {key}: " \
                f"{by_key[key]['net_revenue']} vs {exp['net_revenue']}"

    def test_gross_revenue_values_match(self, expected):
        """Verify gross_revenue matches DuckDB ground truth for each cohort-month pair."""
        data = load_json("cohort_revenue_trend.json")
        by_key = {(r["cohort_month"], r["activity_month"]): r for r in data}
        for exp in expected:
            key = (exp["cohort_month"], exp["activity_month"])
            assert approx_eq(by_key[key]["gross_revenue"], float(exp["gross_revenue"]), tol=0.05), \
                f"gross_revenue mismatch for {key}: " \
                f"{by_key[key]['gross_revenue']} vs {exp['gross_revenue']}"

    def test_no_duplicate_cohort_activity_pairs(self):
        """Verify each (cohort_month, activity_month) pair appears exactly once."""
        data = load_json("cohort_revenue_trend.json")
        keys = [(r["cohort_month"], r["activity_month"]) for r in data]
        assert len(keys) == len(set(keys)), \
            "Duplicate (cohort_month, activity_month) pairs found"


# ════════════════════════════════════════════════════════════════════
# Data Quality: orphaned returns + quantity cap
# ════════════════════════════════════════════════════════════════════
class TestDataQuality:
    def test_orphaned_returns_excluded(self, con):
        """Verify returns referencing non-existent order_ids are excluded.

        returns.csv contains 10 rows with order_ids 99991–100000 that do not
        exist in orders.csv.  A correct implementation JOINs (not LEFT JOINs)
        returns to orders, so these rows are silently dropped.  If they were
        included they would inflate the total refund figure.
        """
        data = load_json("category_stats.json")
        actual_total_returns = round(sum(float(r["return_value"]) for r in data), 2)

        # Ground truth: inner-join excludes the 10 orphaned rows
        expected_total = float(con.execute(f"""
            SELECT ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2)
            FROM read_csv_auto('{DATA}/returns.csv') r
            JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
        """).fetchone()[0])

        assert approx_eq(actual_total_returns, expected_total, tol=1.0), (
            f"Total return_value across all categories is {actual_total_returns} "
            f"but expected {expected_total}. "
            "Returns with non-existent order_ids (99991–100000) may have been included."
        )

    def test_quantity_returned_capped_at_order_quantity(self, con):
        """Verify quantity_returned is capped at the original order quantity.

        15 rows in returns.csv have quantity_returned > the matching order's
        quantity.  Refund must be MIN(quantity_returned, original_quantity) *
        unit_price, not quantity_returned * unit_price.
        """
        data = load_json("category_stats.json")
        actual_total_returns = round(sum(float(r["return_value"]) for r in data), 2)

        capped = float(con.execute(f"""
            SELECT ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2)
            FROM read_csv_auto('{DATA}/returns.csv') r
            JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
        """).fetchone()[0])

        uncapped = float(con.execute(f"""
            SELECT ROUND(SUM(r.quantity_returned * o.unit_price), 2)
            FROM read_csv_auto('{DATA}/returns.csv') r
            JOIN read_csv_auto('{DATA}/orders.csv') o ON r.order_id = o.order_id
        """).fetchone()[0])

        assert approx_eq(actual_total_returns, capped, tol=1.0), (
            f"Total return_value {actual_total_returns} does not match the capped "
            f"value {capped} (uncapped would be {uncapped}). "
            "Use MIN(quantity_returned, original_quantity) when computing refund amounts."
        )


# ════════════════════════════════════════════════════════════════════
# Go implementation check
# ════════════════════════════════════════════════════════════════════
class TestGoImplementation:
    def test_go_source_exists(self):
        """Verify the solution is implemented in Go.

        The task explicitly requires a Go program.  At least one .go source
        file must remain on disk after the program finishes.
        """
        import glob as _glob
        go_files = (
            _glob.glob('/app/**/*.go', recursive=True) +
            _glob.glob('/tmp/**/*.go', recursive=True) +
            _glob.glob('/root/**/*.go', recursive=True)
        )
        assert len(go_files) > 0, (
            "No .go source files found under /app/, /tmp/, or /root/. "
            "This task requires a Go implementation. "
            "Write a Go program and leave the source file(s) on disk."
        )
