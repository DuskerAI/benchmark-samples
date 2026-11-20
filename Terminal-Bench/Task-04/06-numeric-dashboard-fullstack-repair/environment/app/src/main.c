#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "csv_parser.h"
#include "stats.h"
#include "json_output.h"

static void print_help(void) {
    printf("StatEngine - Statistical computation engine\n");
    printf("Usage: statengine --file <path> [--mode stats|regression|full] [--help]\n\n");
    printf("Options:\n");
    printf("  --file <path>   Path to CSV data file\n");
    printf("  --mode <mode>   Computation mode: stats, regression, full (default: full)\n");
    printf("  --help          Show this help message\n\n");
    printf("Modes:\n");
    printf("  stats       Compute descriptive statistics per numeric column\n");
    printf("  regression  Compute linear regression between first two numeric columns\n");
    printf("  full        Compute both statistics and regression\n");
}

int main(int argc, char* argv[]) {
    const char* filepath = NULL;
    const char* mode = "full";

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            print_help();
            return 0;
        } else if (strcmp(argv[i], "--file") == 0 && i + 1 < argc) {
            filepath = argv[++i];
        } else if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            mode = argv[++i];
        }
    }

    if (!filepath) {
        fprintf(stderr, "Error: --file argument required\n");
        print_help();
        return 1;
    }

    CsvData* csv = csv_parse(filepath);
    if (!csv) {
        fprintf(stderr, "Error: failed to parse CSV file '%s'\n", filepath);
        return 1;
    }

    int stat_col_indices[MAX_COLUMNS];
    int num_stats_cols = 0;
    for (int i = 0; i < csv->num_cols; i++) {
        if (strcmp(csv->headers[i], "id") != 0) {
            stat_col_indices[num_stats_cols++] = i;
        }
    }

    ColumnStats stats_arr[MAX_COLUMNS];
    for (int i = 0; i < num_stats_cols; i++) {
        int col = stat_col_indices[i];
        double col_data[MAX_ROWS];
        for (int r = 0; r < csv->num_rows; r++) {
            col_data[r] = csv->data[r][col];
        }
        stats_arr[i] = compute_column_stats(col_data, csv->num_rows);
    }

    if (strcmp(mode, "stats") == 0) {
        json_print_stats(csv, stats_arr, num_stats_cols, stat_col_indices);
    } else if (strcmp(mode, "regression") == 0) {
        if (num_stats_cols < 2) {
            fprintf(stderr, "Error: need at least 2 numeric columns for regression\n");
            csv_free(csv);
            return 1;
        }
        int xi = stat_col_indices[0];
        int yi = stat_col_indices[1];
        double x_data[MAX_ROWS], y_data[MAX_ROWS];
        for (int r = 0; r < csv->num_rows; r++) {
            x_data[r] = csv->data[r][xi];
            y_data[r] = csv->data[r][yi];
        }
        double x_mean = compute_mean(x_data, csv->num_rows);
        double y_mean = compute_mean(y_data, csv->num_rows);
        double num = 0, den = 0;
        for (int r = 0; r < csv->num_rows; r++) {
            num += (x_data[r] - x_mean) * (y_data[r] - y_mean);
            den += (x_data[r] - x_mean) * (x_data[r] - x_mean);
        }
        double slope = (den != 0) ? num / den : 0;
        double intercept = y_mean - slope * x_mean;
        double ss_res = 0, ss_tot = 0;
        for (int r = 0; r < csv->num_rows; r++) {
            double pred = slope * x_data[r] + intercept;
            ss_res += (y_data[r] - pred) * (y_data[r] - pred);
            ss_tot += (y_data[r] - y_mean) * (y_data[r] - y_mean);
        }
        double r_squared = (ss_tot != 0) ? 1.0 - ss_res / ss_tot : 0;
        json_print_regression(csv->headers[xi], csv->headers[yi], slope, intercept, r_squared);
    } else if (strcmp(mode, "full") == 0) {
        double x_data[MAX_ROWS], y_data[MAX_ROWS];
        int xi = stat_col_indices[0];
        int yi = stat_col_indices[1];
        for (int r = 0; r < csv->num_rows; r++) {
            x_data[r] = csv->data[r][xi];
            y_data[r] = csv->data[r][yi];
        }
        double x_mean = compute_mean(x_data, csv->num_rows);
        double y_mean = compute_mean(y_data, csv->num_rows);
        double num = 0, den = 0;
        for (int r = 0; r < csv->num_rows; r++) {
            num += (x_data[r] - x_mean) * (y_data[r] - y_mean);
            den += (x_data[r] - x_mean) * (x_data[r] - x_mean);
        }
        double slope = (den != 0) ? num / den : 0;
        double intercept = y_mean - slope * x_mean;
        double ss_res = 0, ss_tot = 0;
        for (int r = 0; r < csv->num_rows; r++) {
            double pred = slope * x_data[r] + intercept;
            ss_res += (y_data[r] - pred) * (y_data[r] - pred);
            ss_tot += (y_data[r] - y_mean) * (y_data[r] - y_mean);
        }
        double r_squared = (ss_tot != 0) ? 1.0 - ss_res / ss_tot : 0;
        json_print_full(csv, stats_arr, num_stats_cols, stat_col_indices,
                        csv->headers[xi], csv->headers[yi], slope, intercept, r_squared);
    } else {
        fprintf(stderr, "Error: unknown mode '%s'\n", mode);
        csv_free(csv);
        return 1;
    }

    csv_free(csv);
    return 0;
}
