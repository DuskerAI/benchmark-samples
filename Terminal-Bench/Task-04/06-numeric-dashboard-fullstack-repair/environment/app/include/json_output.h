#ifndef JSON_OUTPUT_H
#define JSON_OUTPUT_H

#include "csv_parser.h"
#include "stats.h"

void json_print_stats(const CsvData* csv, const ColumnStats* stats_arr, int num_stats_cols, const int* stat_col_indices);
void json_print_regression(const char* x_col, const char* y_col, double slope, double intercept, double r_squared);
void json_print_full(const CsvData* csv, const ColumnStats* stats_arr, int num_stats_cols, const int* stat_col_indices,
                     const char* x_col, const char* y_col, double slope, double intercept, double r_squared);
char* json_escape_string(const char* str);

#endif
