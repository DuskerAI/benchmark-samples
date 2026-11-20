#ifndef STATS_H
#define STATS_H

typedef struct {
    double mean;
    double median;
    double stddev;
    double min;
    double max;
    double p25;
    double p75;
} ColumnStats;

double compute_mean(double* data, int n);
double compute_median(double* data, int n);
double compute_stddev(double* data, int n, double mean);
double compute_percentile(double* data, int n, double p);
double compute_min(double* data, int n);
double compute_max(double* data, int n);
double compute_correlation(double* x, double* y, int n);
ColumnStats compute_column_stats(double* data, int n);

#endif
