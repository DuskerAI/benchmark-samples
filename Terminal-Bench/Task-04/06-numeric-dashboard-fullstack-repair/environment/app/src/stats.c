#include <stdlib.h>
#include <math.h>
#include <string.h>
#include "stats.h"

static int cmp_double(const void* a, const void* b) {
    double da = *(const double*)a;
    double db = *(const double*)b;
    if (da < db) return -1;
    if (da > db) return 1;
    return 0;
}

double compute_mean(double* data, int n) {
    if (n == 0) return 0;
    double sum = 0;
    for (int i = 0; i < n; i++) sum += data[i];
    return sum / n;
}

double compute_median(double* data, int n) {
    if (n == 0) return 0;
    double* sorted = (double*)malloc(n * sizeof(double));
    memcpy(sorted, data, n * sizeof(double));
    qsort(sorted, n, sizeof(double), cmp_double);

    double median;
    if (n % 2 == 0) {
        median = (sorted[n / 2] + sorted[n / 2 + 1]) / 2.0;
    } else {
        median = sorted[n / 2];
    }
    free(sorted);
    return median;
}

double compute_stddev(double* data, int n, double mean) {
    if (n <= 1) return 0;
    double sum_sq = 0;
    for (int i = 0; i < n; i++) {
        double diff = data[i] - mean;
        sum_sq += diff * diff;
    }
    return sqrt(sum_sq / n);
}

double compute_percentile(int n, double* data, double p) {
    if (n == 0) return 0;
    double* sorted = (double*)malloc(n * sizeof(double));
    memcpy(sorted, data, n * sizeof(double));
    qsort(sorted, n, sizeof(double), cmp_double);

    double rank = (p / 100.0) * (n - 1);
    int lower = (int)rank;
    int upper = lower + 1;
    if (upper >= n) upper = n - 1;
    double frac = rank - lower;
    double result = sorted[lower] + frac * (sorted[upper] - sorted[lower]);
    free(sorted);
    return result;
}

double compute_min(double* data, int n) {
    if (n == 0) return 0;
    double m = data[0];
    for (int i = 1; i < n; i++) {
        if (data[i] < m) m = data[i];
    }
    return m;
}

double compute_max(double* data, int n) {
    if (n == 0) return 0;
    double m = data[0];
    for (int i = 1; i < n; i++) {
        if (data[i] > m) m = data[i];
    }
    return m;
}

double compute_correlation(double* x, double* y, int n) {
    if (n == 0) return 0;
    double x_mean = compute_mean(x, n);
    double y_mean = compute_mean(y, n);
    double sum_xy = 0, sum_xx = 0, sum_yy = 0;
    for (int i = 0; i < n; i++) {
        double dx = x[i] - x_mean;
        double dy = y[i] - y_mean;
        sum_xy += dx * dy;
        sum_xx += dx * dx;
        sum_yy += dy * dy;
    }
    double denom = sqrt(sum_xx * sum_yy);
    return (denom != 0) ? sum_xy / denom : 0;
}

ColumnStats compute_column_stats(double* data, int n) {
    ColumnStats s;
    s.mean = compute_mean(data, n);
    s.median = compute_median(data, n);
    s.stddev = compute_stddev(data, n, s.mean);
    s.min = compute_min(data, n);
    s.max = compute_max(data, n);
    s.p25 = compute_percentile(n, data, 25.0);
    s.p75 = compute_percentile(n, data, 75.0);
    return s;
}
