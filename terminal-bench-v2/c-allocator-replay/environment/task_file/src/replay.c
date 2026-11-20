#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Block { unsigned long addr, size; } Block;
typedef struct Live { char id[32], tag[65]; unsigned long gen, addr, size, align; } Live;

static Block free_list[512];
static size_t free_count;
static Live live_list[512];
static size_t live_count;
static Live quarantine[512];
static size_t quarantine_count;
static char arena_name[32] = "";
static unsigned long arena_size, page_size, quarantine_limit, applied, rejected, high_water;

static unsigned long align_up(unsigned long v, unsigned long a) { return (v + a - 1) / a * a; }

static int find_live(const char *id) {
    for (size_t i = 0; i < live_count; i++) if (strcmp(live_list[i].id, id) == 0) return (int)i;
    return -1;
}

static void add_free(unsigned long addr, unsigned long size) {
    if (size == 0 || free_count >= 512) return;
    free_list[free_count++] = (Block){addr, size};
}

static void sort_free(void) {
    for (size_t i = 0; i < free_count; i++) for (size_t j = i + 1; j < free_count; j++) if (free_list[j].addr < free_list[i].addr) { Block t = free_list[i]; free_list[i] = free_list[j]; free_list[j] = t; }
}

static void coalesce(void) {
    sort_free();
    size_t out = 0;
    for (size_t i = 0; i < free_count; i++) {
        if (out && free_list[out - 1].addr + free_list[out - 1].size >= free_list[i].addr) {
            unsigned long end = free_list[i].addr + free_list[i].size;
            if (end > free_list[out - 1].addr + free_list[out - 1].size) free_list[out - 1].size = end - free_list[out - 1].addr;
        } else {
            free_list[out++] = free_list[i];
        }
    }
    free_count = out;
}

static bool take_block(unsigned long size, unsigned long align, unsigned long *addr) {
    sort_free();
    for (size_t i = 0; i < free_count; i++) {
        unsigned long start = align_up(free_list[i].addr, align);
        if (start + size > free_list[i].addr + free_list[i].size) continue;
        *addr = start;
        unsigned long before = start - free_list[i].addr;
        unsigned long after_addr = start + size;
        unsigned long after = free_list[i].addr + free_list[i].size - after_addr;
        if (before && after) {
            free_list[i].size = before;
            add_free(after_addr, after);
        } else if (before) {
            free_list[i].size = before;
        } else if (after) {
            free_list[i].addr = after_addr;
            free_list[i].size = after;
        } else {
            memmove(&free_list[i], &free_list[i + 1], (free_count - i - 1) * sizeof(free_list[0]));
            free_count--;
        }
        return true;
    }
    return false;
}

static void release_one(void) {
    if (!quarantine_count) return;
    add_free(quarantine[0].addr, quarantine[0].size);
    memmove(&quarantine[0], &quarantine[1], (quarantine_count - 1) * sizeof(quarantine[0]));
    quarantine_count--;
    coalesce();
}

static void parse_line(char *line) {
    char id[32], tag[65];
    unsigned long gen, size, align;
    if (line[0] == '\0' || line[0] == '#') return;
    if (sscanf(line, "ARENA name=%31s size=%lu page=%lu quarantine=%lu", arena_name, &arena_size, &page_size, &quarantine_limit) == 4) {
        free_count = live_count = quarantine_count = 0;
        add_free(0, arena_size);
        return;
    }
    if (sscanf(line, "ALLOC id=%31s gen=%lu size=%lu align=%lu tag=%64s", id, &gen, &size, &align, tag) == 5) {
        if (find_live(id) >= 0 || !size || !align || (align & (align - 1)) || align > 64) { rejected++; return; }
        unsigned long addr = 0;
        if (!take_block(size, align, &addr)) { rejected++; return; }
        Live l = {0};
        snprintf(l.id, sizeof l.id, "%s", id); snprintf(l.tag, sizeof l.tag, "%s", tag);
        l.gen = gen; l.addr = addr; l.size = size; l.align = align;
        live_list[live_count++] = l;
        if (addr + size > high_water) high_water = addr + size;
        applied++;
        return;
    }
    if (sscanf(line, "FREE id=%31s gen=%lu tag=%64s", id, &gen, tag) == 3) {
        int idx = find_live(id);
        if (idx < 0) { rejected++; return; }
        Live l = live_list[idx];
        memmove(&live_list[idx], &live_list[idx + 1], (live_count - (size_t)idx - 1) * sizeof(live_list[0]));
        live_count--;
        quarantine[quarantine_count++] = l;
        while (quarantine_count > quarantine_limit) release_one();
        applied++;
        return;
    }
    rejected++;
}

static int cmp_live(const void *a, const void *b) {
    const Live *x = a, *y = b;
    if (x->addr != y->addr) return x->addr < y->addr ? -1 : 1;
    return strcmp(x->id, y->id);
}

static void write_json(const char *path, bool ignored_tail) {
    qsort(live_list, live_count, sizeof(live_list[0]), cmp_live);
    sort_free();
    FILE *f = fopen(path, "w");
    if (!f) exit(3);
    fprintf(f, "{\n  \"arena\": {\"name\": \"%s\", \"size\": %lu, \"page_size\": %lu, \"quarantine_limit\": %lu},\n", arena_name, arena_size, page_size, quarantine_limit);
    fprintf(f, "  \"applied\": %lu,\n  \"rejected\": %lu,\n  \"ignored_tail\": %s,\n", applied, rejected, ignored_tail ? "true" : "false");
    fprintf(f, "  \"live\": [");
    for (size_t i = 0; i < live_count; i++) fprintf(f, "%s{\"id\": \"%s\", \"gen\": %lu, \"tag\": \"%s\", \"addr\": %lu, \"size\": %lu, \"align\": %lu}", i ? ", " : "", live_list[i].id, live_list[i].gen, live_list[i].tag, live_list[i].addr, live_list[i].size, live_list[i].align);
    fprintf(f, "],\n  \"free\": [");
    for (size_t i = 0; i < free_count; i++) fprintf(f, "%s{\"addr\": %lu, \"size\": %lu}", i ? ", " : "", free_list[i].addr, free_list[i].size);
    fprintf(f, "],\n  \"quarantine\": [");
    for (size_t i = 0; i < quarantine_count; i++) fprintf(f, "%s{\"id\": \"%s\", \"gen\": %lu, \"tag\": \"%s\", \"addr\": %lu, \"size\": %lu}", i ? ", " : "", quarantine[i].id, quarantine[i].gen, quarantine[i].tag, quarantine[i].addr, quarantine[i].size);
    fprintf(f, "],\n  \"high_water\": %lu,\n  \"digest\": \"%016lx\"\n}\n", high_water, (unsigned long)(high_water ^ rejected ^ applied));
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    char buf[512];
    bool ignored_tail = false;
    while (fgets(buf, sizeof buf, f)) {
        size_t n = strlen(buf);
        if (n && buf[n - 1] == '\n') buf[n - 1] = '\0'; else ignored_tail = true;
        parse_line(buf);
    }
    fclose(f);
    write_json(argv[2], ignored_tail);
    return 0;
}
