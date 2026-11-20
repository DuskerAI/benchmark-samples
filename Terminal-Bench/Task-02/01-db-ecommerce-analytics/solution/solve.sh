#!/bin/bash
set -e

mkdir -p /tmp/orc
cd /tmp/orc

cat > go.mod << 'EOF'
module oracle

go 1.22

require github.com/marcboeker/go-duckdb v1.6.3
EOF

cat > main.go << 'GOEOF'
package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"

	_ "github.com/marcboeker/go-duckdb"
)

const dataDir = "/app/input_data"
const outDir  = "/app/output_data"

func must(err error) {
	if err != nil { log.Fatal(err) }
}

// sql replaces {TABLE} tokens with full CSV paths.
func q(tmpl string) string {
	r := strings.NewReplacer(
		"{ORDERS}",    dataDir+"/orders.csv",
		"{CUSTOMERS}", dataDir+"/customers.csv",
		"{PRODUCTS}",  dataDir+"/products.csv",
		"{SUPPLIERS}", dataDir+"/suppliers.csv",
		"{RETURNS}",   dataDir+"/returns.csv",
	)
	return r.Replace(tmpl)
}

func runQuery(db *sql.DB, tmpl string) []map[string]interface{} {
	rows, err := db.Query(q(tmpl))
	must(err)
	defer rows.Close()
	cols, _ := rows.Columns()
	var result []map[string]interface{}
	for rows.Next() {
		vals := make([]interface{}, len(cols))
		ptrs := make([]interface{}, len(cols))
		for i := range vals { ptrs[i] = &vals[i] }
		must(rows.Scan(ptrs...))
		row := make(map[string]interface{})
		for i, c := range cols { row[c] = vals[i] }
		result = append(result, row)
	}
	must(rows.Err())
	return result
}

func writeJSON(name string, data []map[string]interface{}) {
	b, err := json.MarshalIndent(data, "", "  ")
	must(err)
	must(os.WriteFile(outDir+"/"+name, b, 0644))
	fmt.Println("Written:", name)
}

func main() {
	must(os.MkdirAll(outDir, 0755))
	db, err := sql.Open("duckdb", "")
	must(err)
	defer db.Close()

	// 1. top_customers.json
	writeJSON("top_customers.json", runQuery(db, `
		WITH valid_returns AS (
			SELECT o.customer_id,
			       SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price) AS return_value,
			       COUNT(*) AS return_count
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
			GROUP BY o.customer_id
		),
		customer_gross AS (
			SELECT c.customer_id, c.name,
			       ROUND(SUM(o.quantity * o.unit_price), 2) AS gross_spend,
			       COUNT(o.order_id) AS order_count
			FROM read_csv_auto('{ORDERS}') o
			JOIN read_csv_auto('{CUSTOMERS}') c ON o.customer_id = c.customer_id
			GROUP BY c.customer_id, c.name
		)
		SELECT cg.customer_id, cg.name, cg.gross_spend,
		       ROUND(COALESCE(vr.return_value, 0), 2) AS return_value,
		       COALESCE(vr.return_count, 0) AS return_count,
		       ROUND(cg.gross_spend - COALESCE(vr.return_value, 0), 2) AS net_spend,
		       cg.order_count,
		       ROUND((cg.gross_spend - COALESCE(vr.return_value, 0)) / cg.order_count, 2) AS avg_order_value
		FROM customer_gross cg
		LEFT JOIN valid_returns vr ON cg.customer_id = vr.customer_id
		ORDER BY net_spend DESC LIMIT 15
	`))

	// 2. monthly_revenue.json
	writeJSON("monthly_revenue.json", runQuery(db, `
		WITH monthly_gross AS (
			SELECT printf('%04d-%02d', year(order_date), month(order_date)) AS month,
			       ROUND(SUM(quantity * unit_price), 2) AS gross_revenue,
			       COUNT(order_id) AS order_count
			FROM read_csv_auto('{ORDERS}')
			GROUP BY month ORDER BY month
		),
		monthly_returns AS (
			SELECT printf('%04d-%02d', year(r.return_date), month(r.return_date)) AS month,
			       ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2) AS return_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
			GROUP BY month
		),
		combined AS (
			SELECT mg.month, mg.gross_revenue, mg.order_count,
			       COALESCE(mr.return_value, 0) AS return_value,
			       ROUND(mg.gross_revenue - COALESCE(mr.return_value, 0), 2) AS net_revenue,
			       ROW_NUMBER() OVER (ORDER BY mg.month) AS rn
			FROM monthly_gross mg
			LEFT JOIN monthly_returns mr ON mg.month = mr.month
		)
		SELECT month, gross_revenue, order_count, return_value, net_revenue,
		       CASE WHEN rn >= 3 THEN
		           ROUND(AVG(net_revenue) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)
		       ELSE NULL END AS rolling_3m_avg_net_revenue,
		       ROUND(
		           (net_revenue - LAG(net_revenue) OVER (ORDER BY month))
		           / LAG(net_revenue) OVER (ORDER BY month) * 100,
		       2) AS mom_growth_pct
		FROM combined ORDER BY month
	`))

	// 3. category_stats.json
	writeJSON("category_stats.json", runQuery(db, `
		WITH valid_returns AS (
			SELECT o.product_id, o.order_id,
			       LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
		),
		cat_returns AS (
			SELECT p.category,
			       ROUND(SUM(vr.refund_value), 2) AS return_value,
			       COUNT(DISTINCT vr.order_id) AS return_orders
			FROM valid_returns vr
			JOIN read_csv_auto('{PRODUCTS}') p ON vr.product_id = p.product_id
			GROUP BY p.category
		),
		cat_gross AS (
			SELECT p.category,
			       ROUND(SUM(o.quantity * o.unit_price), 2) AS total_revenue,
			       SUM(o.quantity) AS total_units,
			       COUNT(DISTINCT o.product_id) AS unique_products_sold,
			       COUNT(o.order_id) AS total_orders
			FROM read_csv_auto('{ORDERS}') o
			JOIN read_csv_auto('{PRODUCTS}') p ON o.product_id = p.product_id
			GROUP BY p.category
		)
		SELECT cg.category, cg.total_revenue, cg.total_units, cg.unique_products_sold,
		       COALESCE(cr.return_value, 0) AS return_value,
		       ROUND(cg.total_revenue - COALESCE(cr.return_value, 0), 2) AS net_revenue,
		       ROUND(COALESCE(cr.return_orders, 0) * 100.0 / cg.total_orders, 2) AS return_rate_pct
		FROM cat_gross cg
		LEFT JOIN cat_returns cr ON cg.category = cr.category
		ORDER BY cg.total_revenue DESC
	`))

	// 4. supplier_share.json
	writeJSON("supplier_share.json", runQuery(db, `
		WITH return_by_supplier AS (
			SELECT p.supplier_id,
			       ROUND(SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price), 2) AS return_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
			JOIN read_csv_auto('{PRODUCTS}') p ON o.product_id = p.product_id
			GROUP BY p.supplier_id
		),
		supplier_rev AS (
			SELECT s.supplier_id, s.name AS supplier_name, s.country,
			       ROUND(SUM(o.quantity * o.unit_price), 2) AS total_revenue
			FROM read_csv_auto('{ORDERS}') o
			JOIN read_csv_auto('{PRODUCTS}') p ON o.product_id = p.product_id
			JOIN read_csv_auto('{SUPPLIERS}') s ON p.supplier_id = s.supplier_id
			GROUP BY s.supplier_id, s.name, s.country
		)
		SELECT sr.supplier_id, sr.supplier_name, sr.country, sr.total_revenue,
		       COALESCE(rbs.return_value, 0) AS return_value,
		       ROUND(sr.total_revenue - COALESCE(rbs.return_value, 0), 2) AS net_revenue,
		       ROUND((sr.total_revenue - COALESCE(rbs.return_value, 0)) /
		             SUM(sr.total_revenue - COALESCE(rbs.return_value, 0)) OVER () * 100, 2) AS share_pct
		FROM supplier_rev sr
		LEFT JOIN return_by_supplier rbs ON sr.supplier_id = rbs.supplier_id
		ORDER BY net_revenue DESC
	`))

	// 5. customer_cohorts.json
	writeJSON("customer_cohorts.json", runQuery(db, `
		WITH first_orders AS (
			SELECT customer_id,
			       printf('%04d-%02d', year(MIN(order_date)), month(MIN(order_date))) AS cohort_month
			FROM read_csv_auto('{ORDERS}')
			GROUP BY customer_id
		),
		cohort_sizes AS (
			SELECT cohort_month, COUNT(*) AS cohort_size
			FROM first_orders GROUP BY cohort_month
		),
		next_month_activity AS (
			SELECT DISTINCT fo.cohort_month, fo.customer_id
			FROM first_orders fo
			JOIN read_csv_auto('{ORDERS}') o ON fo.customer_id = o.customer_id
			WHERE printf('%04d-%02d', year(o.order_date), month(o.order_date))
			      = printf('%04d-%02d',
			          year(strptime(fo.cohort_month, '%Y-%m') + INTERVAL 1 MONTH),
			          month(strptime(fo.cohort_month, '%Y-%m') + INTERVAL 1 MONTH))
		),
		retained AS (
			SELECT cohort_month, COUNT(*) AS retained_month2
			FROM next_month_activity GROUP BY cohort_month
		),
		last_cohort AS (
			SELECT MAX(cohort_month) AS last_month FROM cohort_sizes
		)
		SELECT cs.cohort_month, cs.cohort_size,
		       COALESCE(r.retained_month2, 0) AS retained_month2,
		       ROUND(COALESCE(r.retained_month2, 0) * 100.0 / cs.cohort_size, 2) AS retention_rate_pct
		FROM cohort_sizes cs
		LEFT JOIN retained r ON cs.cohort_month = r.cohort_month
		CROSS JOIN last_cohort lc
		WHERE cs.cohort_size >= 5 AND cs.cohort_month < lc.last_month
		ORDER BY cs.cohort_month
	`))

	// 6. product_performance.json
	writeJSON("product_performance.json", runQuery(db, `
		WITH valid_returns AS (
			SELECT r.order_id, o.product_id,
			       LEAST(r.quantity_returned, o.quantity) AS qty_returned,
			       LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
		),
		return_by_product AS (
			SELECT product_id,
			       ROUND(SUM(refund_value), 2) AS return_value,
			       COUNT(*) AS return_order_count
			FROM valid_returns GROUP BY product_id
		),
		product_sales AS (
			SELECT o.product_id, COUNT(o.order_id) AS total_orders,
			       SUM(o.quantity) AS net_quantity_sold,
			       ROUND(SUM(o.quantity * o.unit_price), 2) AS gross_revenue
			FROM read_csv_auto('{ORDERS}') o GROUP BY o.product_id
		),
		product_net AS (
			SELECT ps.product_id, p.name AS product_name, p.category, s.name AS supplier_name,
			       ps.total_orders, ps.net_quantity_sold, ps.gross_revenue,
			       COALESCE(rbp.return_value, 0) AS return_value,
			       ROUND(ps.gross_revenue - COALESCE(rbp.return_value, 0), 2) AS net_revenue,
			       ROUND(COALESCE(rbp.return_order_count, 0) * 100.0 / ps.total_orders, 2) AS return_rate_pct
			FROM product_sales ps
			JOIN read_csv_auto('{PRODUCTS}') p ON ps.product_id = p.product_id
			JOIN read_csv_auto('{SUPPLIERS}') s ON p.supplier_id = s.supplier_id
			LEFT JOIN return_by_product rbp ON ps.product_id = rbp.product_id
		),
		total_net AS (SELECT SUM(net_revenue) AS total FROM product_net),
		ranked AS (
			SELECT pn.*,
			       ROUND(pn.net_revenue / t.total * 100, 2) AS revenue_share_pct,
			       ROUND(SUM(pn.net_revenue) OVER (ORDER BY pn.net_revenue DESC
			             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / t.total * 100, 2) AS cumulative_revenue_pct
			FROM product_net pn CROSS JOIN total_net t
			ORDER BY pn.net_revenue DESC
		)
		SELECT product_id, product_name, category, supplier_name,
		       total_orders, net_quantity_sold, gross_revenue, return_value, net_revenue,
		       return_rate_pct, revenue_share_pct, cumulative_revenue_pct,
		       CASE WHEN cumulative_revenue_pct <= 70 THEN 'A'
		            WHEN cumulative_revenue_pct <= 90 THEN 'B'
		            ELSE 'C' END AS abc_class
		FROM ranked ORDER BY net_revenue DESC
	`))

	// 7. customer_segments.json
	writeJSON("customer_segments.json", runQuery(db, `
		WITH customer_returns AS (
			SELECT o.customer_id,
			       SUM(LEAST(r.quantity_returned, o.quantity) * o.unit_price) AS return_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
			GROUP BY o.customer_id
		),
		customer_orders AS (
			SELECT customer_id, MAX(order_date) AS last_order_date,
			       COUNT(order_id) AS order_count,
			       SUM(quantity * unit_price) AS gross_spend
			FROM read_csv_auto('{ORDERS}') GROUP BY customer_id
		),
		ref_date AS (SELECT MAX(last_order_date) AS ref FROM customer_orders),
		customer_metrics AS (
			SELECT co.customer_id,
			       CAST((SELECT ref FROM ref_date) - co.last_order_date AS INTEGER) AS recency_days,
			       co.order_count,
			       ROUND(co.gross_spend - COALESCE(cr.return_value, 0), 2) AS net_spend
			FROM customer_orders co
			LEFT JOIN customer_returns cr ON co.customer_id = cr.customer_id
		),
		rfm AS (
			SELECT customer_id, recency_days, order_count, net_spend,
			       NTILE(4) OVER (ORDER BY recency_days DESC, customer_id ASC) AS r_score,
			       NTILE(4) OVER (ORDER BY order_count ASC,   customer_id ASC) AS f_score,
			       NTILE(4) OVER (ORDER BY net_spend ASC,     customer_id ASC) AS m_score
			FROM customer_metrics
		),
		segmented AS (
			SELECT *, r_score + f_score + m_score AS rfm_total,
			       CASE WHEN r_score + f_score + m_score >= 10 THEN 'Champions'
			            WHEN r_score + f_score + m_score >= 7  THEN 'Loyal Customers'
			            WHEN r_score + f_score + m_score >= 4  THEN 'At Risk'
			            ELSE 'Lost' END AS segment
			FROM rfm
		),
		grand_total AS (SELECT SUM(net_spend) AS total FROM segmented)
		SELECT segment,
		       COUNT(*) AS customer_count,
		       ROUND(AVG(recency_days), 1) AS avg_recency_days,
		       ROUND(AVG(order_count), 2) AS avg_order_count,
		       ROUND(AVG(net_spend), 2) AS avg_net_spend,
		       ROUND(SUM(net_spend), 2) AS total_net_spend,
		       ROUND(SUM(net_spend) / (SELECT total FROM grand_total) * 100, 2) AS revenue_share_pct
		FROM segmented GROUP BY segment ORDER BY total_net_spend DESC
	`))

	// 8. price_cohort_performance.json
	writeJSON("price_cohort_performance.json", runQuery(db, `
		WITH product_median_price AS (
			SELECT product_id, MEDIAN(unit_price) AS median_price
			FROM read_csv_auto('{ORDERS}') GROUP BY product_id
		),
		product_tiers AS (
			SELECT product_id, NTILE(3) OVER (ORDER BY median_price ASC) AS tier
			FROM product_median_price
		),
		valid_returns AS (
			SELECT o.product_id, o.order_id,
			       LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
		),
		return_by_product AS (
			SELECT product_id,
			       ROUND(SUM(refund_value), 2) AS return_value,
			       COUNT(*) AS return_order_count
			FROM valid_returns GROUP BY product_id
		),
		sales_by_product AS (
			SELECT product_id, COUNT(order_id) AS total_orders,
			       ROUND(SUM(quantity * unit_price), 2) AS gross_revenue
			FROM read_csv_auto('{ORDERS}') GROUP BY product_id
		),
		product_data AS (
			SELECT pt.tier,
			       CASE pt.tier WHEN 1 THEN 'Budget' WHEN 2 THEN 'Mid-range' ELSE 'Premium' END AS tier_name,
			       sp.total_orders, sp.gross_revenue,
			       COALESCE(rbp.return_value, 0) AS return_value,
			       COALESCE(rbp.return_order_count, 0) AS return_order_count
			FROM product_tiers pt
			JOIN sales_by_product sp ON pt.product_id = sp.product_id
			LEFT JOIN return_by_product rbp ON pt.product_id = rbp.product_id
		)
		SELECT tier, tier_name,
		       COUNT(*) AS product_count,
		       SUM(total_orders) AS total_orders,
		       ROUND(SUM(gross_revenue), 2) AS gross_revenue,
		       ROUND(SUM(return_value), 2) AS return_value,
		       ROUND(SUM(gross_revenue) - SUM(return_value), 2) AS net_revenue,
		       ROUND(SUM(return_order_count) * 100.0 / SUM(total_orders), 2) AS return_rate_pct,
		       ROUND((SUM(gross_revenue) - SUM(return_value)) / COUNT(*), 2) AS avg_net_revenue_per_product
		FROM product_data GROUP BY tier, tier_name ORDER BY tier ASC
	`))

	// 9. cohort_revenue_trend.json
	writeJSON("cohort_revenue_trend.json", runQuery(db, `
		WITH first_orders AS (
			SELECT customer_id,
			       printf('%04d-%02d', year(MIN(order_date)), month(MIN(order_date))) AS cohort_month
			FROM read_csv_auto('{ORDERS}')
			GROUP BY customer_id
		),
		valid_returns AS (
			SELECT o.order_id,
			       LEAST(r.quantity_returned, o.quantity) * o.unit_price AS refund_value
			FROM read_csv_auto('{RETURNS}') r
			JOIN read_csv_auto('{ORDERS}') o ON r.order_id = o.order_id
		),
		order_data AS (
			SELECT o.order_id, o.customer_id,
			       printf('%04d-%02d', year(o.order_date), month(o.order_date)) AS activity_month,
			       o.quantity * o.unit_price AS gross_amount,
			       COALESCE(vr.refund_value, 0) AS refund_amount
			FROM read_csv_auto('{ORDERS}') o
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
	`))

	fmt.Println("All 9 output files written.")
}
GOEOF

GONOSUMDB="*" go build -mod=mod -o /tmp/orc/solve .
/tmp/orc/solve
