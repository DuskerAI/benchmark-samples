#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "json_output.h"

char* json_escape_string(const char* str) {
    int len = strlen(str);
    char* out = (char*)malloc(len + 1);
    strcpy(out, str);
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
        printf("      \"mean\": %d,\n", (int)stats_arr[i].mean);
        printf("      \"median\": %d,\n", (int)stats_arr[i].median);
        printf("      \"stddev\": %d,\n", (int)stats_arr[i].stddev);
        printf("      \"min\": %d,\n", (int)stats_arr[i].min);
        printf("      \"max\": %d,\n", (int)stats_arr[i].max);
        printf("      \"p25\": %d,\n", (int)stats_arr[i].p25);
        printf("      \"p75\": %d\n", (int)stats_arr[i].p75);
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
    printf("    \"slope\": %d,\n", (int)slope);
    printf("    \"intercept\": %d,\n", (int)intercept);
    printf("    \"r_squared\": %d\n", (int)r_squared);
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
        printf("      \"mean\": %d,\n", (int)stats_arr[i].mean);
        printf("      \"median\": %d,\n", (int)stats_arr[i].median);
        printf("      \"stddev\": %d,\n", (int)stats_arr[i].stddev);
        printf("      \"min\": %d,\n", (int)stats_arr[i].min);
        printf("      \"max\": %d,\n", (int)stats_arr[i].max);
        printf("      \"p25\": %d,\n", (int)stats_arr[i].p25);
        printf("      \"p75\": %d\n", (int)stats_arr[i].p75);
        printf("    },\n");
        free(escaped);
    }
    printf("  },\n");
    char* x_esc = json_escape_string(x_col);
    char* y_esc = json_escape_string(y_col);
    printf("  \"regression\": {\n");
    printf("    \"x_column\": \"%s\",\n", x_esc);
    printf("    \"y_column\": \"%s\",\n", y_esc);
    printf("    \"slope\": %d,\n", (int)slope);
    printf("    \"intercept\": %d,\n", (int)intercept);
    printf("    \"r_squared\": %d\n", (int)r_squared);
    printf("  }\n");
    printf("}\n");
    free(x_esc);
    free(y_esc);
}
