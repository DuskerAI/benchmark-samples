#include "dax.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const State *sort_state;

static int by_path(const void *a, const void *b) {
    size_t ia = *(const size_t *)a, ib = *(const size_t *)b;
    return strcmp(sort_state->nodes[ia].path, sort_state->nodes[ib].path);
}

int write_state(const State *s, const char *path) {
    FILE *out = fopen(path, "w");
    if (!out) return 0;
    size_t order[MAX_NODES];
    for (size_t i = 0; i < s->node_count; i++) order[i] = i;
    sort_state = s;
    qsort(order, s->node_count, sizeof order[0], by_path);
    fprintf(out, "path\ttype\tfs_xflag_dax\ts_dax\tin_core\topens\n");
    for (size_t j = 0; j < s->node_count; j++) {
        const Node *n = &s->nodes[order[j]];
        fprintf(out, "%s\t%s\t%d\t", n->path, n->type == NODE_DIR ? "dir" : "file", n->flag);
        if (n->type == NODE_FILE && n->resident) fprintf(out, "%d", n->active);
        else fputc('-', out);
        fprintf(out, "\t%d\t%u\n", n->resident, n->opens);
    }
    int ok = !ferror(out) && fclose(out) == 0;
    return ok;
}
