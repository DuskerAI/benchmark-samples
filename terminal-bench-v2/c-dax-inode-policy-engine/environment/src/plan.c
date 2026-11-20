#define _POSIX_C_SOURCE 200809L
#include "dax.h"
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static int clean_path(const char *p) {
    size_t n = strlen(p);
    if (n < 1 || n >= MAX_PATH || p[0] != '/') return 0;
    if (n > 1 && p[n - 1] == '/') return 0;
    return !strstr(p, "//") && !strstr(p, "/./") && !strstr(p, "/../");
}

static void result(FILE *out, unsigned seq, const char *op, const char *status) {
    fprintf(out, "%u\t%s\t%s\n", seq, op, status);
}

int execute_plan(State *s, FILE *plan, FILE *audit) {
    char *line = NULL;
    size_t cap = 0;
    unsigned seq = 0;
    fprintf(audit, "seq\top\tresult\n");
    while (getline(&line, &cap, plan) >= 0) {
        char *save = NULL, *op, *a, *b, *extra;
        line[strcspn(line, "\r\n")] = '\0';
        char *start = line;
        while (isspace((unsigned char)*start)) start++;
        if (!*start || *start == '#') continue;
        seq++;
        op = strtok_r(start, " \t", &save);
        a = strtok_r(NULL, " \t", &save);
        b = strtok_r(NULL, " \t", &save);
        extra = strtok_r(NULL, " \t", &save);
        const char *status = "FORMAT";
        if (!strcmp(op, "MKDIR") || !strcmp(op, "CREATE")) {
            if (a && !b && clean_path(a)) {
                int rc = node_add(s, a, !strcmp(op, "MKDIR") ? NODE_DIR : NODE_FILE);
                status = rc == 0 ? "OK" : rc == -2 ? "EXISTS" : "PARENT";
            }
        } else if (!strcmp(op, "SETFLAG")) {
            if (a && b && !extra && clean_path(a) && (!strcmp(b, "on") || !strcmp(b, "off")))
                status = set_flag(s, a, !strcmp(b, "on"));
        } else if (!strcmp(op, "OPEN")) {
            if (a && b && !extra && clean_path(a) && strlen(b) < MAX_NAME)
                status = open_node(s, a, b);
        } else if (!strcmp(op, "CLOSE")) {
            if (a && !b) status = close_handle(s, a);
        } else if (!strcmp(op, "EVICT")) {
            if (a && !b && clean_path(a)) status = evict_node(s, a);
        } else if (!strcmp(op, "REMOUNT")) {
            MountMode mode;
            if (a && !b && parse_mode(a, &mode)) { change_mount(s, mode); status = "OK"; }
        }
        result(audit, seq, op, status);
    }
    free(line);
    return !ferror(plan) && !ferror(audit);
}
