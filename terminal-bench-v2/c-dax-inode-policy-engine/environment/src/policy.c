#include "dax.h"
#include <string.h>

int policy_active(const State *s, const Node *n) {
    if (n->type != NODE_FILE || !s->capable) return 0;
    if (s->mode == MODE_ALWAYS) return 1;
    if (s->mode == MODE_NEVER) return 0;
    return n->flag;
}

int parse_mode(const char *word, MountMode *mode) {
    if (!strcmp(word, "inode") || !strcmp(word, "default")) *mode = MODE_INODE;
    else if (!strcmp(word, "always")) *mode = MODE_ALWAYS;
    else if (!strcmp(word, "never")) *mode = MODE_NEVER;
    else return 0;
    return 1;
}

void change_mount(State *s, MountMode mode) {
    s->mode = mode;
}
