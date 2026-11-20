#include "dax.h"
#include <string.h>

void state_init(State *s, int capable) {
    memset(s, 0, sizeof *s);
    s->capable = capable;
    s->mode = MODE_INODE;
    strcpy(s->nodes[0].path, "/");
    s->nodes[0].type = NODE_DIR;
    s->nodes[0].resident = 1;
    s->node_count = 1;
}

int node_find(const State *s, const char *path) {
    for (size_t i = 0; i < s->node_count; i++)
        if (!strcmp(s->nodes[i].path, path)) return (int)i;
    return -1;
}

static int parent_index(const State *s, const char *path) {
    char parent[MAX_PATH];
    size_t n = strlen(path);
    if (n < 2 || n >= sizeof parent || path[0] != '/' || path[n - 1] == '/') return -1;
    memcpy(parent, path, n + 1);
    char *slash = strrchr(parent, '/');
    if (slash == parent) parent[1] = '\0'; else *slash = '\0';
    int p = node_find(s, parent);
    return p >= 0 && s->nodes[p].type == NODE_DIR ? p : -1;
}

int node_add(State *s, const char *path, NodeType type) {
    if (node_find(s, path) >= 0) return -2;
    int parent = parent_index(s, path);
    if (parent < 0 || s->node_count == MAX_NODES) return -1;
    Node *n = &s->nodes[s->node_count++];
    memset(n, 0, sizeof *n);
    strcpy(n->path, path);
    n->type = type;
    n->flag = s->nodes[parent].flag;
    n->resident = 1;
    n->active = policy_active(s, n);
    return 0;
}

const char *set_flag(State *s, const char *path, int value) {
    int i = node_find(s, path);
    if (i < 0) return "MISSING";
    s->nodes[i].flag = value;
    return "OK";
}

const char *open_node(State *s, const char *path, const char *handle) {
    for (size_t h = 0; h < MAX_HANDLES; h++)
        if (s->handles[h].used && !strcmp(s->handles[h].name, handle)) return "EXISTS";
    int i = node_find(s, path);
    if (i < 0) return "MISSING";
    if (s->nodes[i].type != NODE_FILE) return "NOT_FILE";
    size_t slot;
    for (slot = 0; slot < MAX_HANDLES && s->handles[slot].used; slot++);
    if (slot == MAX_HANDLES) return "LIMIT";
    if (!s->nodes[i].resident) {
        s->nodes[i].resident = 1;
        s->nodes[i].active = policy_active(s, &s->nodes[i]);
    }
    s->handles[slot].used = 1;
    s->handles[slot].node = i;
    strcpy(s->handles[slot].name, handle);
    s->nodes[i].opens++;
    return "OK";
}

const char *close_handle(State *s, const char *handle) {
    for (size_t h = 0; h < MAX_HANDLES; h++) {
        if (s->handles[h].used && !strcmp(s->handles[h].name, handle)) {
            s->nodes[s->handles[h].node].opens--;
            s->handles[h].used = 0;
            return "OK";
        }
    }
    return "HANDLE";
}

const char *evict_node(State *s, const char *path) {
    int i = node_find(s, path);
    if (i < 0) return "MISSING";
    if (s->nodes[i].opens) return "BUSY";
    s->nodes[i].resident = 0;
    s->nodes[i].active = 0;
    return "OK";
}
