#ifndef DAX_H
#define DAX_H

#include <stddef.h>
#include <stdio.h>

#define MAX_NODES 512
#define MAX_HANDLES 512
#define MAX_PATH 192
#define MAX_NAME 64

typedef enum { NODE_DIR, NODE_FILE } NodeType;
typedef enum { MODE_INODE, MODE_ALWAYS, MODE_NEVER } MountMode;

typedef struct {
    char path[MAX_PATH];
    NodeType type;
    int flag;
    int resident;
    int active;
    unsigned opens;
} Node;

typedef struct {
    char name[MAX_NAME];
    int node;
    int used;
} Handle;

typedef struct {
    Node nodes[MAX_NODES];
    size_t node_count;
    Handle handles[MAX_HANDLES];
    MountMode mode;
    int capable;
} State;

void state_init(State *s, int capable);
int node_find(const State *s, const char *path);
int node_add(State *s, const char *path, NodeType type);
const char *set_flag(State *s, const char *path, int value);
const char *open_node(State *s, const char *path, const char *handle);
const char *close_handle(State *s, const char *handle);
const char *evict_node(State *s, const char *path);
void change_mount(State *s, MountMode mode);
int policy_active(const State *s, const Node *n);
int parse_mode(const char *word, MountMode *mode);
int execute_plan(State *s, FILE *plan, FILE *audit);
int write_state(const State *s, const char *path);

#endif
