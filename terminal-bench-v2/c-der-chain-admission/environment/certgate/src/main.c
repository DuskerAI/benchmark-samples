#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* Minimal entry point for the certificate evidence gate. */
int main(int argc, char **argv) {
    const char *out = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--out") == 0) out = argv[i + 1];
    }
    if (argc != 7 || out == NULL) {
        return 1;
    }
    if (mkdir(out, 0777) != 0 && errno != EEXIST) return 1;
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/decision.json", out) >= (int)sizeof(path)) return 1;
    FILE *f = fopen(path, "w");
    if (!f) return 1;
    fputs("{\"decision\":\"REJECT\",\"code\":\"UNSUPPORTED\",\"certificates\":0,\"leaf\":\"\"}\n", f);
    fclose(f);
    if (snprintf(path, sizeof(path), "%s/trace.tsv", out) >= (int)sizeof(path)) return 1;
    f = fopen(path, "w");
    if (!f) return 1;
    fputs("index\tsubject\tissuer\trole\ttime\tsignature\tconstraints\n", f);
    fclose(f);
    return 2;
}
