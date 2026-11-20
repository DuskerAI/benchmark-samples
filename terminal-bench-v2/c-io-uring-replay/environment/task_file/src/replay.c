#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ITEMS 64
#define MAX_LINE 8192

typedef struct {
    char name[32];
    unsigned char *data;
    size_t size;
} FileImg;

typedef struct {
    char name[32];
    unsigned char *data;
    unsigned char present[256];
    size_t size;
} MapImg;

typedef struct {
    int id;
    char op[12];
    char file[32];
    char map[32];
    size_t off;
    size_t mapoff;
    size_t len;
    int link;
} Sub;

typedef struct {
    int id;
    uint32_t pos;
    int res;
    int order;
} Cq;

typedef struct {
    int id;
    char op[12];
    int res;
    size_t bytes;
} Done;

static FileImg files[MAX_ITEMS];
static MapImg maps[MAX_ITEMS];
static Sub subs[MAX_ITEMS];
static Cq cqs[MAX_ITEMS];
static Done done[MAX_ITEMS];
static int nfiles, nmaps, nsubs, ncqs, ndone;
static uint32_t init_head;
static size_t page_size = 1;

static char *field(char **keys, char **vals, int n, const char *key) {
    for (int i = 0; i < n; i++) {
        if (strcmp(keys[i], key) == 0) return vals[i];
    }
    return NULL;
}

static int name_ok(const char *s) {
    if (!s || !*s || strlen(s) >= 32) return 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (!isalnum(*p) && *p != '_' && *p != '-' && *p != '.') return 0;
    }
    return 1;
}

static int parse_u32(const char *s, uint32_t *out) {
    char *end = NULL;
    errno = 0;
    unsigned long v = strtoul(s ? s : "", &end, 10);
    if (errno || !end || *end || v > 0xffffffffUL) return 0;
    *out = (uint32_t)v;
    return 1;
}

static int parse_size(const char *s, size_t *out) {
    char *end = NULL;
    errno = 0;
    unsigned long long v = strtoull(s ? s : "", &end, 10);
    if (errno || !end || *end) return 0;
    *out = (size_t)v;
    return 1;
}

static int parse_int(const char *s, int *out) {
    char *end = NULL;
    errno = 0;
    long v = strtol(s ? s : "", &end, 10);
    if (errno || !end || *end || v < -2147483647L - 1 || v > 2147483647L) return 0;
    *out = (int)v;
    return 1;
}

static int hexval(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int parse_hex(const char *s, unsigned char **out, size_t *len) {
    size_t n = strlen(s ? s : "");
    if (n % 2) return 0;
    unsigned char *buf = calloc(n / 2 ? n / 2 : 1, 1);
    if (!buf) return 0;
    for (size_t i = 0; i < n; i += 2) {
        int a = hexval((unsigned char)s[i]);
        int b = hexval((unsigned char)s[i + 1]);
        if (a < 0 || b < 0) {
            free(buf);
            return 0;
        }
        buf[i / 2] = (unsigned char)((a << 4) | b);
    }
    *out = buf;
    *len = n / 2;
    return 1;
}

static FileImg *find_file(const char *name) {
    for (int i = 0; i < nfiles; i++) if (strcmp(files[i].name, name) == 0) return &files[i];
    return NULL;
}

static MapImg *find_map(const char *name) {
    for (int i = 0; i < nmaps; i++) if (strcmp(maps[i].name, name) == 0) return &maps[i];
    return NULL;
}

static Sub *find_sub(int id) {
    for (int i = 0; i < nsubs; i++) if (subs[i].id == id) return &subs[i];
    return NULL;
}

static void parse_present(MapImg *m, const char *s) {
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s", s ? s : "");
    for (char *p = strtok(tmp, ","); p; p = strtok(NULL, ",")) {
        size_t idx = 0;
        if (parse_size(p, &idx) && idx < sizeof(m->present)) m->present[idx] = 1;
    }
}

static int split(char *line, char **keys, char **vals, int max) {
    int n = 0;
    for (char *tok = strtok(line, " \t\r\n"); tok && n < max; tok = strtok(NULL, " \t\r\n")) {
        char *eq = strchr(tok, '=');
        if (!eq) continue;
        *eq = 0;
        keys[n] = tok;
        vals[n] = eq + 1;
        n++;
    }
    return n;
}

static void load_trace(const char *path) {
    FILE *fp = fopen(path, "r");
    if (!fp) exit(2);
    char line[MAX_LINE];
    while (fgets(line, sizeof(line), fp)) {
        char raw[MAX_LINE];
        snprintf(raw, sizeof(raw), "%s", line);
        char *rec = strtok(raw, " \t\r\n");
        if (!rec || rec[0] == '#') continue;
        char *keys[32], *vals[32];
        char rest[MAX_LINE];
        char *after = line + strlen(rec);
        snprintf(rest, sizeof(rest), "%s", after);
        int nf = split(rest, keys, vals, 32);
        if (strcmp(rec, "INIT") == 0) {
            size_t entries = 0;
            uint32_t head = 0;
            if (parse_size(field(keys, vals, nf, "page"), &page_size) &&
                parse_size(field(keys, vals, nf, "entries"), &entries) && entries &&
                parse_u32(field(keys, vals, nf, "head"), &head)) init_head = head;
        } else if (strcmp(rec, "FILE") == 0 && nfiles < MAX_ITEMS) {
            char *name = field(keys, vals, nf, "name");
            char *hex = field(keys, vals, nf, "hex");
            unsigned char *buf = NULL;
            size_t len = 0;
            if (name_ok(name) && parse_hex(hex, &buf, &len)) {
                snprintf(files[nfiles].name, sizeof(files[nfiles].name), "%s", name);
                files[nfiles].data = buf;
                files[nfiles].size = len;
                nfiles++;
            }
        } else if (strcmp(rec, "MAP") == 0 && nmaps < MAX_ITEMS) {
            char *name = field(keys, vals, nf, "name");
            size_t size = 0, len = 0;
            unsigned char *buf = NULL;
            if (name_ok(name) && parse_size(field(keys, vals, nf, "size"), &size) &&
                parse_hex(field(keys, vals, nf, "data"), &buf, &len) && len == size) {
                snprintf(maps[nmaps].name, sizeof(maps[nmaps].name), "%s", name);
                maps[nmaps].data = buf;
                maps[nmaps].size = size;
                parse_present(&maps[nmaps], field(keys, vals, nf, "present"));
                nmaps++;
            }
        } else if (strcmp(rec, "SUB") == 0 && nsubs < MAX_ITEMS) {
            Sub s;
            memset(&s, 0, sizeof(s));
            char *id = field(keys, vals, nf, "id");
            char *op = field(keys, vals, nf, "op");
            char *link = field(keys, vals, nf, "link");
            if (parse_int(id, &s.id) && op && strlen(op) < sizeof(s.op) &&
                parse_size(field(keys, vals, nf, "off"), &s.off) &&
                parse_size(field(keys, vals, nf, "mapoff"), &s.mapoff) &&
                parse_size(field(keys, vals, nf, "len"), &s.len)) {
                snprintf(s.op, sizeof(s.op), "%s", op);
                snprintf(s.file, sizeof(s.file), "%s", field(keys, vals, nf, "file") ? field(keys, vals, nf, "file") : "-");
                snprintf(s.map, sizeof(s.map), "%s", field(keys, vals, nf, "map") ? field(keys, vals, nf, "map") : "-");
                s.link = -1;
                if (link && strcmp(link, "none") != 0) parse_int(link, &s.link);
                subs[nsubs++] = s;
            }
        } else if (strcmp(rec, "CQ") == 0 && ncqs < MAX_ITEMS) {
            Cq c;
            memset(&c, 0, sizeof(c));
            if (parse_int(field(keys, vals, nf, "id"), &c.id) &&
                parse_u32(field(keys, vals, nf, "pos"), &c.pos) &&
                parse_int(field(keys, vals, nf, "res"), &c.res)) {
                c.order = ncqs;
                cqs[ncqs++] = c;
            }
        }
    }
    fclose(fp);
}

static int cq_cmp(const void *a, const void *b) {
    const Cq *x = (const Cq *)a;
    const Cq *y = (const Cq *)b;
    if (x->pos < y->pos) return -1;
    if (x->pos > y->pos) return 1;
    return x->order - y->order;
}

static size_t min_size(size_t a, size_t b) { return a < b ? a : b; }

static void apply_one(Sub *s, int res) {
    Done d;
    memset(&d, 0, sizeof(d));
    d.id = s->id;
    snprintf(d.op, sizeof(d.op), "%s", s->op);
    d.res = res;
    if (res >= 0 && (strcmp(s->op, "READ") == 0 || strcmp(s->op, "WRITE") == 0)) {
        FileImg *f = find_file(s->file);
        MapImg *m = find_map(s->map);
        if (f && m && s->off < f->size && s->mapoff < m->size) {
            size_t n = min_size(s->len, f->size - s->off);
            n = min_size(n, m->size - s->mapoff);
            if (strcmp(s->op, "READ") == 0) memcpy(m->data + s->mapoff, f->data + s->off, n);
            else memcpy(f->data + s->off, m->data + s->mapoff, n);
            d.bytes = n;
        }
    }
    done[ndone++] = d;
}

static void replay(void) {
    (void)init_head;
    qsort(cqs, (size_t)ncqs, sizeof(cqs[0]), cq_cmp);
    for (int i = 0; i < ncqs; i++) {
        Sub *s = find_sub(cqs[i].id);
        if (s) apply_one(s, cqs[i].res);
    }
}

static uint64_t fnv_bytes(uint64_t h, const unsigned char *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }
    return h;
}

static uint64_t digest_data(const unsigned char *p, size_t n) {
    return fnv_bytes(1469598103934665603ULL, p, n);
}

static int file_cmp(const void *a, const void *b) {
    const FileImg *x = (const FileImg *)a;
    const FileImg *y = (const FileImg *)b;
    return strcmp(x->name, y->name);
}

static int map_cmp(const void *a, const void *b) {
    const MapImg *x = (const MapImg *)a;
    const MapImg *y = (const MapImg *)b;
    return strcmp(x->name, y->name);
}

static void write_hex(FILE *fp, const unsigned char *p, size_t n) {
    static const char tab[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) fputc(tab[p[i] >> 4], fp), fputc(tab[p[i] & 15], fp);
}

static void write_report(const char *path) {
    qsort(files, (size_t)nfiles, sizeof(files[0]), file_cmp);
    qsort(maps, (size_t)nmaps, sizeof(maps[0]), map_cmp);
    int short_io = 0;
    for (int i = 0; i < ndone; i++) {
        Sub *s = find_sub(done[i].id);
        if (s && done[i].res >= 0 && (strcmp(done[i].op, "READ") == 0 || strcmp(done[i].op, "WRITE") == 0) && done[i].bytes < s->len) short_io++;
    }
    uint64_t h = 1469598103934665603ULL;
    for (int i = 0; i < ndone; i++) h = fnv_bytes(h, (unsigned char *)&done[i], sizeof(done[i]));
    for (int i = 0; i < nfiles; i++) h = fnv_bytes(fnv_bytes(h, (unsigned char *)files[i].name, strlen(files[i].name)), files[i].data, files[i].size);
    for (int i = 0; i < nmaps; i++) h = fnv_bytes(fnv_bytes(h, (unsigned char *)maps[i].name, strlen(maps[i].name)), maps[i].data, maps[i].size);
    FILE *fp = fopen(path, "w");
    if (!fp) exit(3);
    fprintf(fp, "{\"schema\":\"uring-replay-v1\",\"applied\":%d,\"cancelled\":0,\"short_io\":%d,\"completions\":[", ndone, short_io);
    for (int i = 0; i < ndone; i++) {
        if (i) fputc(',', fp);
        fprintf(fp, "{\"id\":%d,\"op\":\"%s\",\"res\":%d,\"bytes\":%zu}", done[i].id, done[i].op, done[i].res, done[i].bytes);
    }
    fprintf(fp, "],\"files\":[");
    for (int i = 0; i < nfiles; i++) {
        if (i) fputc(',', fp);
        fprintf(fp, "{\"name\":\"%s\",\"size\":%zu,\"hex\":\"", files[i].name, files[i].size);
        write_hex(fp, files[i].data, files[i].size);
        fprintf(fp, "\",\"digest\":\"%016llx\"}", (unsigned long long)digest_data(files[i].data, files[i].size));
    }
    fprintf(fp, "],\"maps\":[");
    for (int i = 0; i < nmaps; i++) {
        if (i) fputc(',', fp);
        fprintf(fp, "{\"name\":\"%s\",\"size\":%zu,\"hex\":\"", maps[i].name, maps[i].size);
        write_hex(fp, maps[i].data, maps[i].size);
        fprintf(fp, "\",\"digest\":\"%016llx\"}", (unsigned long long)digest_data(maps[i].data, maps[i].size));
    }
    fprintf(fp, "],\"digest\":\"%016llx\"}\n", (unsigned long long)h);
    fclose(fp);
}

int main(int argc, char **argv) {
    if (argc != 3) return 1;
    load_trace(argv[1]);
    replay();
    write_report(argv[2]);
    return 0;
}
