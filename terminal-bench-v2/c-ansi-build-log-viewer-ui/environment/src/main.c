#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "logview.h"

int main(int argc, char **argv) {
    if (argc < 2 || argc > 4) {
        fprintf(stderr, "usage: %s <log> [query] [export]\n", argv[0]);
        return 2;
    }
    int exp = argc == 4 && strcmp(argv[3], "export") == 0;
    char *json = render_log_json(argv[1], argc >= 3 ? argv[2] : "", exp);
    if (!json) return 1;
    puts(json);
    free(json);
    return 0;
}
