#!/bin/bash
set -euo pipefail

cd /app/task_file
cat > src/replay.c <<'EOF'
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ITEMS 1024

typedef struct { unsigned long addr, size; } Block;
typedef struct { char id[32], tag[65]; unsigned long gen, addr, size, align; } Item;
typedef struct { char key[32], value[96]; } Field;

static char arena_name[32];
static unsigned long arena_size, page_size, quarantine_limit;
static unsigned long applied, rejected, high_water;
static Block free_list[MAX_ITEMS];
static size_t free_count;
static Item live[MAX_ITEMS], queue[MAX_ITEMS], history[MAX_ITEMS];
static size_t live_count, queue_count, history_count;

static bool token_ok(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n > 31) return false;
    for (size_t i = 0; i < n; i++) if (!(isalnum((unsigned char)s[i]) || s[i] == '_' || s[i] == '.' || s[i] == ':' || s[i] == '-')) return false;
    return true;
}

static bool hex_ok(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n > 64) return false;
    for (size_t i = 0; i < n; i++) if (!isxdigit((unsigned char)s[i])) return false;
    return true;
}

static bool parse_uint(const char *s, unsigned long *out, bool allow_zero) {
    if (!s || !*s) return false;
    unsigned long v = 0;
    for (const char *p = s; *p; p++) {
        if (!isdigit((unsigned char)*p)) return false;
        unsigned long d = (unsigned long)(*p - '0');
        if (v > (UINT64_MAX - d) / 10) return false;
        v = v * 10 + d;
    }
    if (!allow_zero && v == 0) return false;
    *out = v;
    return true;
}

static unsigned long align_up(unsigned long v, unsigned long a) { return (v + a - 1) / a * a; }

static void copy_text(char *dst, size_t cap, const char *src) {
    size_t i = 0;
    if (cap == 0) return;
    while (i + 1 < cap && src[i]) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}

static int cmp_block(const void *a, const void *b) {
    const Block *x = a, *y = b;
    if (x->addr == y->addr) return 0;
    return x->addr < y->addr ? -1 : 1;
}

static int cmp_item(const void *a, const void *b) {
    const Item *x = a, *y = b;
    if (x->addr != y->addr) return x->addr < y->addr ? -1 : 1;
    return strcmp(x->id, y->id);
}

static void coalesce_free(void) {
    qsort(free_list, free_count, sizeof(free_list[0]), cmp_block);
    size_t out = 0;
    for (size_t i = 0; i < free_count; i++) {
        if (free_list[i].size == 0) continue;
        if (out && free_list[out - 1].addr + free_list[out - 1].size >= free_list[i].addr) {
            unsigned long end = free_list[i].addr + free_list[i].size;
            unsigned long old = free_list[out - 1].addr + free_list[out - 1].size;
            if (end > old) free_list[out - 1].size = end - free_list[out - 1].addr;
        } else {
            free_list[out++] = free_list[i];
        }
    }
    free_count = out;
}

static int live_index(const char *id) {
    for (size_t i = 0; i < live_count; i++) if (strcmp(live[i].id, id) == 0) return (int)i;
    return -1;
}

static unsigned long last_generation(const char *id) {
    unsigned long g = 0;
    for (size_t i = 0; i < history_count; i++) if (strcmp(history[i].id, id) == 0 && history[i].gen > g) g = history[i].gen;
    return g;
}

static void remember_generation(const char *id, unsigned long gen) {
    for (size_t i = 0; i < history_count; i++) if (strcmp(history[i].id, id) == 0) { history[i].gen = gen; return; }
    if (history_count < MAX_ITEMS) { copy_text(history[history_count].id, sizeof(history[history_count].id), id); history[history_count].gen = gen; history_count++; }
}

static bool add_free(unsigned long addr, unsigned long size) {
    if (size == 0 || free_count >= MAX_ITEMS) return false;
    free_list[free_count++] = (Block){addr, size};
    coalesce_free();
    return true;
}

static void release_queue(void) {
    while (queue_count > quarantine_limit) {
        Item item = queue[0];
        memmove(queue, queue + 1, (queue_count - 1) * sizeof(queue[0]));
        queue_count--;
        add_free(item.addr, item.size);
    }
}

static bool take_block(unsigned long size, unsigned long align, unsigned long *addr) {
    coalesce_free();
    for (size_t i = 0; i < free_count; i++) {
        unsigned long range_start = free_list[i].addr;
        unsigned long range_end = range_start + free_list[i].size;
        unsigned long p = align_up(range_start, align);
        while (p + size <= range_end) {
            unsigned long page0 = p / page_size;
            unsigned long page1 = (p + size - 1) / page_size;
            if (page0 == page1) {
                *addr = p;
                Block old = free_list[i];
                memmove(free_list + i, free_list + i + 1, (free_count - i - 1) * sizeof(free_list[0]));
                free_count--;
                unsigned long before = p - old.addr;
                unsigned long after_addr = p + size;
                unsigned long after = old.addr + old.size - after_addr;
                if (before && free_count < MAX_ITEMS) free_list[free_count++] = (Block){old.addr, before};
                if (after && free_count < MAX_ITEMS) free_list[free_count++] = (Block){after_addr, after};
                coalesce_free();
                return true;
            }
            p = align_up((page0 + 1) * page_size, align);
        }
    }
    return false;
}

static int split_fields(char *line, char *rec, Field *fields, size_t *count) {
    *count = 0;
    char *save = NULL;
    char *tok = strtok_r(line, " ", &save);
    if (!tok) return 0;
    snprintf(rec, 32, "%s", tok);
    while ((tok = strtok_r(NULL, " ", &save))) {
        char *eq = strchr(tok, '=');
        if (!eq || eq == tok || !eq[1]) return -1;
        *eq = '\0';
        for (size_t i = 0; i < *count; i++) if (strcmp(fields[i].key, tok) == 0) return -1;
        if (*count >= 16 || strlen(tok) >= sizeof(fields[0].key) || strlen(eq + 1) >= sizeof(fields[0].value)) return -1;
        snprintf(fields[*count].key, sizeof(fields[*count].key), "%s", tok);
        snprintf(fields[*count].value, sizeof(fields[*count].value), "%s", eq + 1);
        (*count)++;
    }
    return 1;
}

static const char *field(Field *fields, size_t count, const char *key) {
    for (size_t i = 0; i < count; i++) if (strcmp(fields[i].key, key) == 0) return fields[i].value;
    return NULL;
}

static bool only_fields(Field *fields, size_t count, const char **keys, size_t n) {
    if (count != n) return false;
    for (size_t i = 0; i < n; i++) if (!field(fields, count, keys[i])) return false;
    return true;
}

static void replay_line(char *line) {
    while (*line == ' ' || *line == '\t') line++;
    if (*line == '\0' || *line == '#') return;
    char rec[32];
    Field fields[16];
    size_t count = 0;
    int ok = split_fields(line, rec, fields, &count);
    if (ok <= 0) { rejected++; return; }
    static bool have_arena = false;
    if (strcmp(rec, "ARENA") == 0 && !have_arena) {
        const char *keys[] = {"name", "size", "page", "quarantine"};
        unsigned long sz, pg, q;
        if (!only_fields(fields, count, keys, 4) || !token_ok(field(fields, count, "name")) ||
            !parse_uint(field(fields, count, "size"), &sz, false) || !parse_uint(field(fields, count, "page"), &pg, false) ||
            !parse_uint(field(fields, count, "quarantine"), &q, true) || sz % pg != 0) {
            rejected++;
            return;
        }
        copy_text(arena_name, sizeof(arena_name), field(fields, count, "name"));
        arena_size = sz; page_size = pg; quarantine_limit = q;
        free_count = live_count = queue_count = history_count = 0;
        free_list[free_count++] = (Block){0, arena_size};
        have_arena = true;
        return;
    }
    if (!have_arena) { rejected++; return; }
    if (strcmp(rec, "ALLOC") == 0) {
        const char *keys[] = {"id", "gen", "size", "align", "tag"};
        const char *id = field(fields, count, "id");
        const char *tag = field(fields, count, "tag");
        unsigned long gen, size, align, addr;
        if (!only_fields(fields, count, keys, 5) || !token_ok(id) || !hex_ok(tag) ||
            !parse_uint(field(fields, count, "gen"), &gen, false) || !parse_uint(field(fields, count, "size"), &size, false) ||
            !parse_uint(field(fields, count, "align"), &align, false) || (align & (align - 1)) || align > 64 ||
            size > page_size || live_index(id) >= 0 || gen != last_generation(id) + 1 || live_count >= MAX_ITEMS || !take_block(size, align, &addr)) {
            rejected++;
            return;
        }
        Item item = {0};
        copy_text(item.id, sizeof(item.id), id); copy_text(item.tag, sizeof(item.tag), tag);
        item.gen = gen; item.addr = addr; item.size = size; item.align = align;
        live[live_count++] = item;
        remember_generation(id, gen);
        if (addr + size > high_water) high_water = addr + size;
        applied++;
        return;
    }
    if (strcmp(rec, "FREE") == 0) {
        const char *keys[] = {"id", "gen", "tag"};
        const char *id = field(fields, count, "id");
        const char *tag = field(fields, count, "tag");
        unsigned long gen;
        int idx;
        if (!only_fields(fields, count, keys, 3) || !token_ok(id) || !parse_uint(field(fields, count, "gen"), &gen, false) ||
            (idx = live_index(id)) < 0 || live[idx].gen != gen || strcmp(live[idx].tag, tag) != 0 || queue_count >= MAX_ITEMS) {
            rejected++;
            return;
        }
        Item item = live[idx];
        memmove(live + idx, live + idx + 1, (live_count - (size_t)idx - 1) * sizeof(live[0]));
        live_count--;
        queue[queue_count++] = item;
        release_queue();
        applied++;
        return;
    }
    rejected++;
}

static void fnv_update(uint64_t *h, const char *s) {
    while (*s) { *h ^= (unsigned char)*s++; *h *= 0x100000001b3ULL; }
}

static void fnv_ulong(uint64_t *h, unsigned long v) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%lu", v);
    fnv_update(h, buf);
}

static uint64_t digest_value(bool ignored_tail) {
    uint64_t h = 0xcbf29ce484222325ULL;
    fnv_update(&h, "A "); fnv_update(&h, arena_name); fnv_update(&h, " "); fnv_ulong(&h, arena_size); fnv_update(&h, " "); fnv_ulong(&h, page_size); fnv_update(&h, " "); fnv_ulong(&h, quarantine_limit); fnv_update(&h, "\n");
    for (size_t i = 0; i < live_count; i++) { fnv_update(&h, "L "); fnv_update(&h, live[i].id); fnv_update(&h, " "); fnv_ulong(&h, live[i].gen); fnv_update(&h, " "); fnv_update(&h, live[i].tag); fnv_update(&h, " "); fnv_ulong(&h, live[i].addr); fnv_update(&h, " "); fnv_ulong(&h, live[i].size); fnv_update(&h, " "); fnv_ulong(&h, live[i].align); fnv_update(&h, "\n"); }
    for (size_t i = 0; i < free_count; i++) { fnv_update(&h, "F "); fnv_ulong(&h, free_list[i].addr); fnv_update(&h, " "); fnv_ulong(&h, free_list[i].size); fnv_update(&h, "\n"); }
    for (size_t i = 0; i < queue_count; i++) { fnv_update(&h, "Q "); fnv_update(&h, queue[i].id); fnv_update(&h, " "); fnv_ulong(&h, queue[i].gen); fnv_update(&h, " "); fnv_update(&h, queue[i].tag); fnv_update(&h, " "); fnv_ulong(&h, queue[i].addr); fnv_update(&h, " "); fnv_ulong(&h, queue[i].size); fnv_update(&h, "\n"); }
    fnv_update(&h, "H "); fnv_ulong(&h, high_water); fnv_update(&h, " R "); fnv_ulong(&h, rejected); fnv_update(&h, " T "); fnv_update(&h, ignored_tail ? "1\n" : "0\n");
    return h;
}

static void write_report(const char *path, bool ignored_tail) {
    qsort(live, live_count, sizeof(live[0]), cmp_item);
    coalesce_free();
    FILE *f = fopen(path, "w");
    if (!f) exit(3);
    fprintf(f, "{\n  \"arena\": {\"name\": \"%s\", \"size\": %lu, \"page_size\": %lu, \"quarantine_limit\": %lu},\n", arena_name, arena_size, page_size, quarantine_limit);
    fprintf(f, "  \"applied\": %lu,\n  \"rejected\": %lu,\n  \"ignored_tail\": %s,\n", applied, rejected, ignored_tail ? "true" : "false");
    fprintf(f, "  \"live\": [");
    for (size_t i = 0; i < live_count; i++) fprintf(f, "%s{\"id\": \"%s\", \"gen\": %lu, \"tag\": \"%s\", \"addr\": %lu, \"size\": %lu, \"align\": %lu}", i ? ", " : "", live[i].id, live[i].gen, live[i].tag, live[i].addr, live[i].size, live[i].align);
    fprintf(f, "],\n  \"free\": [");
    for (size_t i = 0; i < free_count; i++) fprintf(f, "%s{\"addr\": %lu, \"size\": %lu}", i ? ", " : "", free_list[i].addr, free_list[i].size);
    fprintf(f, "],\n  \"quarantine\": [");
    for (size_t i = 0; i < queue_count; i++) fprintf(f, "%s{\"id\": \"%s\", \"gen\": %lu, \"tag\": \"%s\", \"addr\": %lu, \"size\": %lu}", i ? ", " : "", queue[i].id, queue[i].gen, queue[i].tag, queue[i].addr, queue[i].size);
    fprintf(f, "],\n  \"high_water\": %lu,\n  \"digest\": \"%016llx\"\n}\n", high_water, (unsigned long long)digest_value(ignored_tail));
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    char buf[1024];
    bool ignored_tail = false;
    while (fgets(buf, sizeof(buf), f)) {
        size_t n = strlen(buf);
        if (n && buf[n - 1] == '\n') buf[n - 1] = '\0'; else { ignored_tail = true; break; }
        replay_line(buf);
    }
    fclose(f);
    write_report(argv[2], ignored_tail);
    return 0;
}
EOF
make clean
make
