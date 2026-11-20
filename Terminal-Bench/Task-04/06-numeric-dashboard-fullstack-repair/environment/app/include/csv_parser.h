#ifndef CSV_PARSER_H
#define CSV_PARSER_H

#define MAX_COLUMNS 20
#define MAX_ROWS 1000
#define MAX_FIELD_LEN 128
#define MAX_LINE_LEN 4096

typedef struct {
    char headers[MAX_COLUMNS][MAX_FIELD_LEN];
    double data[MAX_ROWS][MAX_COLUMNS];
    int num_rows;
    int num_cols;
} CsvData;

CsvData* csv_parse(const char* filepath);
void csv_free(CsvData* csv);
int csv_col_index(const CsvData* csv, const char* name);

#endif
