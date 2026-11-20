#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "stats.h"

static void trim_newline(char* str) {
    int len = strlen(str);
    while (len > 0 && (str[len - 1] == '\n' || str[len - 1] == '\r')) {
        str[--len] = '\0';
    }
}

static int parse_header(const char* line, char headers[][MAX_FIELD_LEN], int max_cols) {
    char buf[MAX_LINE_LEN];
    strncpy(buf, line, MAX_LINE_LEN - 1);
    buf[MAX_LINE_LEN - 1] = '\0';
    trim_newline(buf);

    int col = 0;
    char* token = strtok(buf, ",");
    while (token && col < max_cols) {
        while (*token == ' ') token++;
        strncpy(headers[col], token, MAX_FIELD_LEN - 1);
        headers[col][MAX_FIELD_LEN - 1] = '\0';
        col++;
        token = strtok(NULL, ",");
    }
    return col;
}

CsvData* csv_parse(const char* filepath) {
    FILE* fp = fopen(filepath, "r");
    if (!fp) return NULL;

    CsvData* csv = (CsvData*)calloc(1, sizeof(CsvData));
    if (!csv) { fclose(fp); return NULL; }

    char line[MAX_LINE_LEN];

    if (!fgets(line, sizeof(line), fp)) {
        fclose(fp);
        free(csv);
        return NULL;
    }
    csv->num_cols = parse_header(line, csv->headers, MAX_COLUMNS);

    if (!fgets(line, sizeof(line), fp)) {
        fclose(fp);
        free(csv);
        return NULL;
    }

    int row = 0;
    while (fgets(line, sizeof(line), fp) && row < MAX_ROWS) {
        trim_newline(line);
        if (strlen(line) == 0) continue;

        char* token = strtok(line, ",");
        int col = 0;
        while (token && col < csv->num_cols) {
            csv->data[row][col] = atof(token);
            col++;
            token = strtok(NULL, ",");
        }
        row++;
    }

    csv->num_rows = row;
    fclose(fp);
    return csv;
}

void csv_free(CsvData* csv) {
    if (csv) free(csv);
}

int csv_col_index(const CsvData* csv, const char* name) {
    for (int i = 0; i < csv->num_cols; i++) {
        if (strcmp(csv->headers[i], name) == 0) return i;
    }
    return -1;
}
