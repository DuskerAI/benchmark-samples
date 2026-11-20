#!/bin/bash
set -euo pipefail

# Repair 6: stats.c median — off-by-one for even-length arrays
# Repair 7: stats.c stddev — apply Bessel's correction (n to n-1)
cat > /tmp/fix_stats.py << 'PYEOF'
with open("/app/src/stats.c") as f:
    content = f.read()

content = content.replace(
    "median = (sorted[n / 2] + sorted[n / 2 + 1]) / 2.0;",
    "median = (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0;"
)

content = content.replace(
    "return sqrt(sum_sq / n);",
    "return sqrt(sum_sq / (n - 1));"
)

with open("/app/src/stats.c", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_stats.py

# Repair 9: json_output.c — rewrite with proper format specifiers and escaping
cat > /app/src/json_output.c << 'CEOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "json_output.h"

char* json_escape_string(const char* str) {
    int len = strlen(str);
    int extra = 0;
    for (int i = 0; i < len; i++) {
        if (str[i] == '"' || str[i] == '\\') extra++;
    }
    char* out = (char*)malloc(len + extra + 1);
    int j = 0;
    for (int i = 0; i < len; i++) {
        if (str[i] == '"' || str[i] == '\\') {
            out[j++] = '\\';
        }
        out[j++] = str[i];
    }
    out[j] = '\0';
    return out;
}

void json_print_stats(const CsvData* csv, const ColumnStats* stats_arr, int num_stats_cols, const int* stat_col_indices) {
    printf("{\n");
    printf("  \"row_count\": %d,\n", csv->num_rows);
    printf("  \"columns\": {\n");
    for (int i = 0; i < num_stats_cols; i++) {
        int col = stat_col_indices[i];
        char* escaped = json_escape_string(csv->headers[col]);
        printf("    \"%s\": {\n", escaped);
        printf("      \"mean\": %.6f,\n", stats_arr[i].mean);
        printf("      \"median\": %.6f,\n", stats_arr[i].median);
        printf("      \"stddev\": %.6f,\n", stats_arr[i].stddev);
        printf("      \"min\": %.6f,\n", stats_arr[i].min);
        printf("      \"max\": %.6f,\n", stats_arr[i].max);
        printf("      \"p25\": %.6f,\n", stats_arr[i].p25);
        printf("      \"p75\": %.6f\n", stats_arr[i].p75);
        printf("    }%s\n", (i < num_stats_cols - 1) ? "," : "");
        free(escaped);
    }
    printf("  }\n");
    printf("}\n");
}

void json_print_regression(const char* x_col, const char* y_col, double slope, double intercept, double r_squared) {
    char* x_esc = json_escape_string(x_col);
    char* y_esc = json_escape_string(y_col);
    printf("{\n");
    printf("  \"regression\": {\n");
    printf("    \"x_column\": \"%s\",\n", x_esc);
    printf("    \"y_column\": \"%s\",\n", y_esc);
    printf("    \"slope\": %.6f,\n", slope);
    printf("    \"intercept\": %.6f,\n", intercept);
    printf("    \"r_squared\": %.6f\n", r_squared);
    printf("  }\n");
    printf("}\n");
    free(x_esc);
    free(y_esc);
}

void json_print_full(const CsvData* csv, const ColumnStats* stats_arr, int num_stats_cols, const int* stat_col_indices,
                     const char* x_col, const char* y_col, double slope, double intercept, double r_squared) {
    printf("{\n");
    printf("  \"row_count\": %d,\n", csv->num_rows);
    printf("  \"columns\": {\n");
    for (int i = 0; i < num_stats_cols; i++) {
        int col = stat_col_indices[i];
        char* escaped = json_escape_string(csv->headers[col]);
        printf("    \"%s\": {\n", escaped);
        printf("      \"mean\": %.6f,\n", stats_arr[i].mean);
        printf("      \"median\": %.6f,\n", stats_arr[i].median);
        printf("      \"stddev\": %.6f,\n", stats_arr[i].stddev);
        printf("      \"min\": %.6f,\n", stats_arr[i].min);
        printf("      \"max\": %.6f,\n", stats_arr[i].max);
        printf("      \"p25\": %.6f,\n", stats_arr[i].p25);
        printf("      \"p75\": %.6f\n", stats_arr[i].p75);
        printf("    }%s\n", (i < num_stats_cols - 1) ? "," : "");
        free(escaped);
    }
    printf("  },\n");
    char* x_esc = json_escape_string(x_col);
    char* y_esc = json_escape_string(y_col);
    printf("  \"regression\": {\n");
    printf("    \"x_column\": \"%s\",\n", x_esc);
    printf("    \"y_column\": \"%s\",\n", y_esc);
    printf("    \"slope\": %.6f,\n", slope);
    printf("    \"intercept\": %.6f,\n", intercept);
    printf("    \"r_squared\": %.6f\n", r_squared);
    printf("  }\n");
    printf("}\n");
    free(x_esc);
    free(y_esc);
}
CEOF

# Repair 10: csv_parser.c — remove extra fgets that skips first data row
cat > /tmp/fix_csv.py << 'PYEOF'
with open("/app/src/csv_parser.c") as f:
    content = f.read()

content = content.replace(
    """    if (!fgets(line, sizeof(line), fp)) {
        fclose(fp);
        free(csv);
        return NULL;
    }

    int row = 0;""",
    """    int row = 0;"""
)

with open("/app/src/csv_parser.c", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_csv.py

# Repair 11: server/api.js — key mismatch ("std" vs "stddev")
cat > /tmp/fix_api_key.py << 'PYEOF'
with open("/app/server/api.js") as f:
    content = f.read()

content = content.replace("std: stats.std", "stddev: stats.stddev")

with open("/app/server/api.js", "w") as f:
    f.write(content)
PYEOF
python3 /tmp/fix_api_key.py

echo "Milestone 2 solved"
