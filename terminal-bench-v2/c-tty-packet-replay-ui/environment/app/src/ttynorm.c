#include "replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: ttynorm <capture> [--query q] [--seek ms] [--session id] [--channel id] [--limit n]\n");
        return 2;
    }
    const char *path = argv[1];
    const char *query = "";
    const char *session = "";
    const char *channel = "";
    long seek = -1;
    int limit = 256;
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--query") == 0 && i + 1 < argc) query = argv[++i];
        else if (strcmp(argv[i], "--seek") == 0 && i + 1 < argc) seek = strtol(argv[++i], NULL, 10);
        else if (strcmp(argv[i], "--session") == 0 && i + 1 < argc) session = argv[++i];
        else if (strcmp(argv[i], "--channel") == 0 && i + 1 < argc) channel = argv[++i];
        else if (strcmp(argv[i], "--limit") == 0 && i + 1 < argc) limit = atoi(argv[++i]);
        else {
            fprintf(stderr, "unknown option\n");
            return 2;
        }
    }
    char err[256] = {0};
    char *json = render_capture_json(path, query, seek, session, channel, limit, err, sizeof err);
    if (!json) {
        fprintf(stderr, "%s\n", err[0] ? err : "render failed");
        return 1;
    }
    fputs(json, stdout);
    free(json);
    return 0;
}
