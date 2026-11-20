#!/bin/bash
set -euo pipefail

cd /app/task_file
cat > src/replay.c <<'SRC'
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ITEMS 4096
#define MAX_LINE 1048576
#define NAME_MAX_LEN 64
#define FNV_OFFSET UINT64_C(0xcbf29ce484222325)
#define FNV_PRIME UINT64_C(0x100000001b3)

typedef struct { char name[NAME_MAX_LEN]; unsigned char *data; size_t size; } FileImg;
typedef struct { char name[NAME_MAX_LEN]; unsigned char *data; unsigned char *present; size_t size; size_t npages; } MapImg;
typedef struct { int id; char op[12]; char file[NAME_MAX_LEN]; char map[NAME_MAX_LEN]; size_t off; size_t mapoff; size_t len; int link; } Sub;
typedef struct { int id; uint32_t pos; int res; int order; } Cq;
typedef struct { int id; char op[12]; int res; size_t bytes; } Done;

static FileImg files[MAX_ITEMS];
static MapImg maps[MAX_ITEMS];
static Sub subs[MAX_ITEMS];
static Cq cqs[MAX_ITEMS];
static Done done[MAX_ITEMS];
static int nfiles, nmaps, nsubs, ncqs, ndone;
static uint32_t init_head, ring_entries = 1;
static size_t page_size = 1;
static int have_init;

static char *field(char **keys, char **vals, int n, const char *key) { for (int i = 0; i < n; i++) if (strcmp(keys[i], key) == 0) return vals[i]; return NULL; }
static int name_ok(const char *s) { if (!s || !*s || strlen(s) >= NAME_MAX_LEN) return 0; for (const unsigned char *p = (const unsigned char *)s; *p; p++) if (!isalnum(*p) && *p != '_' && *p != '-' && *p != '.') return 0; return 1; }
static int ref_ok(const char *s) { return s && (strcmp(s, "-") == 0 || name_ok(s)); }
static int parse_u32(const char *s, uint32_t *out) { char *end = NULL; errno = 0; unsigned long v = strtoul(s ? s : "", &end, 10); if (errno || !end || *end || v > 0xffffffffUL) return 0; *out = (uint32_t)v; return 1; }
static int parse_size(const char *s, size_t *out) { char *end = NULL; errno = 0; unsigned long long v = strtoull(s ? s : "", &end, 10); if (errno || !end || *end) return 0; *out = (size_t)v; return 1; }
static int parse_pos_int(const char *s, int *out) { char *end = NULL; errno = 0; long v = strtol(s ? s : "", &end, 10); if (errno || !end || *end || v <= 0 || v > 2147483647L) return 0; *out = (int)v; return 1; }
static int parse_int(const char *s, int *out) { char *end = NULL; errno = 0; long v = strtol(s ? s : "", &end, 10); if (errno || !end || *end || v < -2147483647L - 1 || v > 2147483647L) return 0; *out = (int)v; return 1; }
static int pow2_size(size_t x) { return x && ((x & (x - 1)) == 0); }
static int hexval(int c) { if (c >= '0' && c <= '9') return c - '0'; if (c >= 'a' && c <= 'f') return c - 'a' + 10; if (c >= 'A' && c <= 'F') return c - 'A' + 10; return -1; }
static int parse_hex(const char *s, unsigned char **out, size_t *len) { size_t n = strlen(s ? s : ""); if (n % 2) return 0; unsigned char *buf = calloc(n / 2 ? n / 2 : 1, 1); if (!buf) return 0; for (size_t i = 0; i < n; i += 2) { int a = hexval((unsigned char)s[i]); int b = hexval((unsigned char)s[i + 1]); if (a < 0 || b < 0) { free(buf); return 0; } buf[i / 2] = (unsigned char)((a << 4) | b); } *out = buf; *len = n / 2; return 1; }
static FileImg *find_file(const char *name) { for (int i = 0; i < nfiles; i++) if (strcmp(files[i].name, name) == 0) return &files[i]; return NULL; }
static MapImg *find_map(const char *name) { for (int i = 0; i < nmaps; i++) if (strcmp(maps[i].name, name) == 0) return &maps[i]; return NULL; }
static Sub *find_sub(int id) { for (int i = 0; i < nsubs; i++) if (subs[i].id == id) return &subs[i]; return NULL; }

static int parse_present(unsigned char *present, size_t npages, const char *s) {
    char tmp[MAX_LINE];
    snprintf(tmp, sizeof(tmp), "%s", s ? s : "");
    if (!tmp[0]) return 1;
    for (char *p = strtok(tmp, ","); p; p = strtok(NULL, ",")) {
        if (!*p) return 0;
        char *dash = strchr(p, '-');
        size_t lo = 0, hi = 0;
        if (dash) { *dash = 0; if (!parse_size(p, &lo) || !parse_size(dash + 1, &hi) || hi < lo) return 0; }
        else { if (!parse_size(p, &lo)) return 0; hi = lo; }
        for (size_t idx = lo; idx <= hi && idx < npages; idx++) present[idx] = 1;
    }
    return 1;
}

static int split(char *line, char **keys, char **vals, int max) {
    int n = 0;
    for (char *tok = strtok(line, " \t\r\n"); tok && n < max; tok = strtok(NULL, " \t\r\n")) {
        char *eq = strchr(tok, '=');
        if (!eq || eq == tok) return -1;
        *eq = 0; keys[n] = tok; vals[n] = eq + 1; n++;
    }
    return n;
}

static void load_trace(const char *path) {
    FILE *fp = fopen(path, "r");
    if (!fp) exit(2);
    char line[MAX_LINE];
    int order = 0;
    while (fgets(line, sizeof(line), fp)) {
        char raw[MAX_LINE];
        snprintf(raw, sizeof(raw), "%s", line);
        char *rec = strtok(raw, " \t\r\n");
        if (!rec || rec[0] == '#') { order++; continue; }
        char rest[MAX_LINE];
        char *after = line + strlen(rec);
        snprintf(rest, sizeof(rest), "%s", after);
        char *keys[64], *vals[64];
        int nf = split(rest, keys, vals, 64);
        if (nf < 0) { order++; continue; }
        if (strcmp(rec, "INIT") == 0) {
            if (!have_init) {
                size_t entries = 0, page = 0;
                uint32_t head = 0;
                if (parse_size(field(keys, vals, nf, "entries"), &entries) && parse_size(field(keys, vals, nf, "page"), &page) && parse_u32(field(keys, vals, nf, "head"), &head) && pow2_size(entries) && entries <= 0xffffffffULL && page > 0) {
                    have_init = 1; ring_entries = (uint32_t)entries; page_size = page; init_head = head;
                }
            }
        } else if (!have_init) {
            order++; continue;
        } else if (strcmp(rec, "FILE") == 0) {
            char *name = field(keys, vals, nf, "name");
            unsigned char *buf = NULL; size_t len = 0;
            if (name_ok(name) && parse_hex(field(keys, vals, nf, "hex"), &buf, &len)) {
                FileImg *dst = find_file(name);
                if (!dst) { if (nfiles >= MAX_ITEMS) { free(buf); order++; continue; } dst = &files[nfiles++]; }
                else free(dst->data);
                snprintf(dst->name, sizeof(dst->name), "%s", name); dst->data = buf; dst->size = len;
            }
        } else if (strcmp(rec, "MAP") == 0) {
            char *name = field(keys, vals, nf, "name");
            size_t size = 0, len = 0;
            unsigned char *buf = NULL;
            if (name_ok(name) && parse_size(field(keys, vals, nf, "size"), &size) && parse_hex(field(keys, vals, nf, "data"), &buf, &len) && len == size) {
                size_t npages = page_size ? (size + page_size - 1) / page_size : 0;
                unsigned char *present = calloc(npages ? npages : 1, 1);
                if (!present) exit(10);
                if (!parse_present(present, npages, field(keys, vals, nf, "present"))) { free(buf); free(present); order++; continue; }
                MapImg *dst = find_map(name);
                if (!dst) { if (nmaps >= MAX_ITEMS) { free(buf); free(present); order++; continue; } dst = &maps[nmaps++]; }
                else { free(dst->data); free(dst->present); }
                snprintf(dst->name, sizeof(dst->name), "%s", name); dst->data = buf; dst->present = present; dst->size = size; dst->npages = npages;
            }
        } else if (strcmp(rec, "SUB") == 0) {
            Sub s; memset(&s, 0, sizeof(s));
            char *op = field(keys, vals, nf, "op"); char *file = field(keys, vals, nf, "file"); char *map = field(keys, vals, nf, "map"); char *link = field(keys, vals, nf, "link");
            int link_id = -1;
            int link_ok = link && ((strcmp(link, "none") == 0) || parse_pos_int(link, &link_id));
            if (parse_pos_int(field(keys, vals, nf, "id"), &s.id) && op && (strcmp(op, "READ") == 0 || strcmp(op, "WRITE") == 0 || strcmp(op, "NOP") == 0 || strcmp(op, "TIMEOUT") == 0) && ref_ok(file) && ref_ok(map) && parse_size(field(keys, vals, nf, "off"), &s.off) && parse_size(field(keys, vals, nf, "mapoff"), &s.mapoff) && parse_size(field(keys, vals, nf, "len"), &s.len) && link_ok) {
                snprintf(s.op, sizeof(s.op), "%s", op); snprintf(s.file, sizeof(s.file), "%s", file); snprintf(s.map, sizeof(s.map), "%s", map); s.link = (link && strcmp(link, "none") == 0) ? -1 : link_id;
                Sub *dst = find_sub(s.id); if (!dst) { if (nsubs >= MAX_ITEMS) { order++; continue; } dst = &subs[nsubs++]; } *dst = s;
            }
        } else if (strcmp(rec, "CQ") == 0 && ncqs < MAX_ITEMS) {
            Cq c; memset(&c, 0, sizeof(c));
            if (parse_pos_int(field(keys, vals, nf, "id"), &c.id) && parse_u32(field(keys, vals, nf, "pos"), &c.pos) && parse_int(field(keys, vals, nf, "res"), &c.res)) { c.order = order; cqs[ncqs++] = c; }
        }
        order++;
    }
    fclose(fp);
}

static int cq_cmp(const void *a, const void *b) { const Cq *x = a, *y = b; uint32_t dx = x->pos - init_head, dy = y->pos - init_head; if (dx < dy) return -1; if (dx > dy) return 1; return x->order - y->order; }
static size_t min_size(size_t a, size_t b) { return a < b ? a : b; }
static size_t resident_span(const MapImg *m, size_t start, size_t limit) { if (start >= m->size || page_size == 0) return 0; limit = min_size(limit, m->size - start); size_t n = 0; while (n < limit) { size_t page = (start + n) / page_size; if (page >= m->npages || !m->present[page]) break; size_t page_end = ((start + n) / page_size + 1) * page_size; if (page_end > m->size) page_end = m->size; n = min_size(limit, page_end - start); } return n; }
static int completed_res(int id, int *res) { for (int i = 0; i < ndone; i++) if (done[i].id == id) { *res = done[i].res; return 1; } return 0; }

static void apply_one(Sub *s, int res) {
    Done d; memset(&d, 0, sizeof(d));
    d.id = s->id; snprintf(d.op, sizeof(d.op), "%s", s->op); d.res = res;
    if (res >= 0 && (strcmp(s->op, "READ") == 0 || strcmp(s->op, "WRITE") == 0)) {
        FileImg *f = find_file(s->file); MapImg *m = find_map(s->map);
        if (f && m && s->off < f->size) {
            size_t n = (size_t)res; n = min_size(n, s->len); n = min_size(n, f->size - s->off); n = min_size(n, resident_span(m, s->mapoff, n));
            if (n > 0 && strcmp(s->op, "READ") == 0) memcpy(m->data + s->mapoff, f->data + s->off, n);
            else if (n > 0) memcpy(f->data + s->off, m->data + s->mapoff, n);
            d.bytes = n;
        }
    }
    if (ndone < MAX_ITEMS) done[ndone++] = d;
}

static void replay(void) {
    qsort(cqs, (size_t)ncqs, sizeof(cqs[0]), cq_cmp);
    for (int i = 0; i < ncqs; i++) {
        if ((uint32_t)(cqs[i].pos - init_head) >= ring_entries) continue;
        Sub *s = find_sub(cqs[i].id);
        if (!s) continue;
        int prev = 0;
        if (completed_res(s->id, &prev)) continue;
        int res = cqs[i].res, linked = 0;
        if (strcmp(s->op, "TIMEOUT") == 0 && completed_res(s->link, &linked) && linked >= 0) res = -125;
        apply_one(s, res);
    }
}

static uint64_t fnv_bytes(uint64_t h, const unsigned char *p, size_t n) { for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= FNV_PRIME; } return h; }
static uint64_t digest_data(const unsigned char *p, size_t n) { return fnv_bytes(FNV_OFFSET, p, n); }
static void fnv_text(uint64_t *h, const char *s) { *h = fnv_bytes(*h, (const unsigned char *)s, strlen(s)); }
static void write_hex(FILE *fp, const unsigned char *p, size_t n) { static const char tab[] = "0123456789abcdef"; for (size_t i = 0; i < n; i++) { fputc(tab[p[i] >> 4], fp); fputc(tab[p[i] & 15], fp); } }
static void hex_to(char *dst, const unsigned char *p, size_t n) { static const char tab[] = "0123456789abcdef"; for (size_t i = 0; i < n; i++) { dst[2 * i] = tab[p[i] >> 4]; dst[2 * i + 1] = tab[p[i] & 15]; } dst[2 * n] = 0; }
static int file_cmp(const void *a, const void *b) { const FileImg *x = a, *y = b; return strcmp(x->name, y->name); }
static int map_cmp(const void *a, const void *b) { const MapImg *x = a, *y = b; return strcmp(x->name, y->name); }

static void write_report(const char *path) {
    qsort(files, (size_t)nfiles, sizeof(files[0]), file_cmp);
    qsort(maps, (size_t)nmaps, sizeof(maps[0]), map_cmp);
    int short_io = 0, cancelled = 0;
    for (int i = 0; i < ndone; i++) { Sub *s = find_sub(done[i].id); if (done[i].res == -125 && s && strcmp(s->op, "TIMEOUT") == 0) cancelled++; if (s && done[i].res >= 0 && (strcmp(done[i].op, "READ") == 0 || strcmp(done[i].op, "WRITE") == 0) && done[i].bytes < s->len) short_io++; }
    uint64_t h = FNV_OFFSET;
    char line[MAX_LINE];
    snprintf(line, sizeof(line), "A %d %d %d\n", ndone, cancelled, short_io); fnv_text(&h, line);
    for (int i = 0; i < ndone; i++) { snprintf(line, sizeof(line), "C %d %s %d %zu\n", done[i].id, done[i].op, done[i].res, done[i].bytes); fnv_text(&h, line); }
    for (int i = 0; i < nfiles; i++) { char *hx = malloc(files[i].size * 2 + 1); if (!hx) exit(10); hex_to(hx, files[i].data, files[i].size); snprintf(line, sizeof(line), "F %s %zu %s\n", files[i].name, files[i].size, hx); fnv_text(&h, line); free(hx); }
    for (int i = 0; i < nmaps; i++) { char *hx = malloc(maps[i].size * 2 + 1); if (!hx) exit(10); hex_to(hx, maps[i].data, maps[i].size); snprintf(line, sizeof(line), "M %s %zu %s\n", maps[i].name, maps[i].size, hx); fnv_text(&h, line); free(hx); }
    FILE *fp = fopen(path, "w"); if (!fp) exit(3);
    fprintf(fp, "{\"schema\":\"uring-replay-v1\",\"applied\":%d,\"cancelled\":%d,\"short_io\":%d,\"completions\":[", ndone, cancelled, short_io);
    for (int i = 0; i < ndone; i++) { if (i) fputc(',', fp); fprintf(fp, "{\"id\":%d,\"op\":\"%s\",\"res\":%d,\"bytes\":%zu}", done[i].id, done[i].op, done[i].res, done[i].bytes); }
    fprintf(fp, "],\"files\":[");
    for (int i = 0; i < nfiles; i++) { if (i) fputc(',', fp); fprintf(fp, "{\"name\":\"%s\",\"size\":%zu,\"hex\":\"", files[i].name, files[i].size); write_hex(fp, files[i].data, files[i].size); fprintf(fp, "\",\"digest\":\"%016llx\"}", (unsigned long long)digest_data(files[i].data, files[i].size)); }
    fprintf(fp, "],\"maps\":[");
    for (int i = 0; i < nmaps; i++) { if (i) fputc(',', fp); fprintf(fp, "{\"name\":\"%s\",\"size\":%zu,\"hex\":\"", maps[i].name, maps[i].size); write_hex(fp, maps[i].data, maps[i].size); fprintf(fp, "\",\"digest\":\"%016llx\"}", (unsigned long long)digest_data(maps[i].data, maps[i].size)); }
    fprintf(fp, "],\"digest\":\"%016llx\"}\n", (unsigned long long)h);
    fclose(fp);
}

int main(int argc, char **argv) { if (argc != 3) return 1; load_trace(argv[1]); replay(); write_report(argv[2]); return 0; }
SRC
make clean
make
