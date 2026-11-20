#include "dax.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static void usage(void) {
    fprintf(stderr, "usage: daxadmin --media capable|incapable --plan FILE --out DIR\n");
}

int main(int argc, char **argv) {
    const char *plan_path = NULL, *out = NULL, *media = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--plan")) plan_path = argv[i + 1];
        else if (!strcmp(argv[i], "--out")) out = argv[i + 1];
        else if (!strcmp(argv[i], "--media")) media = argv[i + 1];
        else { usage(); return 2; }
    }
    if (!plan_path || !out || !media || (strcmp(media, "capable") && strcmp(media, "incapable"))) {
        usage(); return 2;
    }
    FILE *plan = fopen(plan_path, "r");
    if (!plan) { perror("plan"); return 2; }
    if (mkdir(out, 0777) && errno != EEXIST) { perror("out"); fclose(plan); return 2; }
    char audit_path[512], state_path[512];
    if (snprintf(audit_path, sizeof audit_path, "%s/audit.tsv", out) >= (int)sizeof audit_path ||
        snprintf(state_path, sizeof state_path, "%s/state.tsv", out) >= (int)sizeof state_path) {
        fclose(plan); return 2;
    }
    FILE *audit = fopen(audit_path, "w");
    if (!audit) { perror("audit"); fclose(plan); return 2; }
    State state;
    state_init(&state, !strcmp(media, "capable"));
    int ok = execute_plan(&state, plan, audit);
    fclose(plan);
    fclose(audit);
    if (!ok || !write_state(&state, state_path)) return 2;
    return 0;
}
