#define _GNU_SOURCE
#include "replay.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_CH 32
#define MAX_ROWS 80
#define MAX_COLS 160
#define CELL_BYTES 8

typedef struct {
    char bytes[CELL_BYTES];
    int width;
} Cell;

typedef struct {
    char session[32], channel[32], label[64];
    int cols, rows, cur_r, cur_c;
    long ts;
    Cell cell[MAX_ROWS][MAX_COLS];
    int esc_len;
    char esc[32];
    int utf_need, utf_have;
    unsigned utf_code;
    char utf_buf[CELL_BYTES];
} Chan;

typedef struct {
    char id[32], label[64];
} Sess;

typedef struct {
    Sess sessions[MAX_CH];
    int ns;
    Chan chans[MAX_CH];
    int nc;
} Replay;

typedef struct { char *p; size_t n, cap; } Buf;

static void add(Buf *b, const char *fmt, ...) {
    for (;;) {
        if (b->cap - b->n < 256) { b->cap = b->cap ? b->cap * 2 : 4096; b->p = realloc(b->p, b->cap); }
        va_list ap; va_start(ap, fmt);
        int w = vsnprintf(b->p + b->n, b->cap - b->n, fmt, ap);
        va_end(ap);
        if (w < 0) exit(3);
        if ((size_t)w < b->cap - b->n) { b->n += (size_t)w; return; }
        b->cap *= 2; b->p = realloc(b->p, b->cap);
    }
}

static void jstr(Buf *b, const char *s) {
    add(b, "\"");
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') add(b, "\\%c", c);
        else if (c == '\n') add(b, "\\n");
        else if (c == '\r') add(b, "\\r");
        else if (c == '\t') add(b, "\\t");
        else if (c < 32) add(b, "\\u%04x", c);
        else add(b, "%c", c);
    }
    add(b, "\"");
}

static int eq(const char *a, const char *b) { return strcmp(a ? a : "", b ? b : "") == 0; }

static int contains_ci(const char *s, const char *q) {
    if (!q || !*q) return 1;
    size_t qn = strlen(q);
    for (; *s; s++) {
        size_t i = 0;
        while (i < qn && s[i] && tolower((unsigned char)s[i]) == tolower((unsigned char)q[i])) i++;
        if (i == qn) return 1;
    }
    return 0;
}

static int cp_width(unsigned cp) {
    if (cp == 0) return 0;
    if (cp < 32 || (cp >= 0x7f && cp < 0xa0)) return 0;
    if ((cp >= 0x300 && cp <= 0x36f) || (cp >= 0x1ab0 && cp <= 0x1aff)) return 0;
    if (cp >= 0x1100 && (cp <= 0x115f || cp == 0x2329 || cp == 0x232a || (cp >= 0x2e80 && cp <= 0xa4cf) || (cp >= 0xac00 && cp <= 0xd7a3) || (cp >= 0xf900 && cp <= 0xfaff) || (cp >= 0xfe10 && cp <= 0xfe6f) || (cp >= 0xff00 && cp <= 0xff60) || (cp >= 0x1f300 && cp <= 0x1faff))) return 2;
    return 1;
}

static void clear_cells(Chan *c, int r, int from, int to) {
    if (r < 0 || r >= c->rows) return;
    if (from < 0) from = 0;
    if (to > c->cols) to = c->cols;
    for (int i = from; i < to; i++) { c->cell[r][i].bytes[0] = 0; c->cell[r][i].width = 0; }
}

static void newline(Chan *c) { c->cur_r++; c->cur_c = 0; if (c->cur_r >= c->rows) c->cur_r = c->rows - 1; }

static void put_cp(Chan *c, unsigned cp, const char *bytes) {
    if (cp == '\n') { newline(c); return; }
    if (cp == '\r') { newline(c); return; }
    if (cp == '\b') { if (c->cur_c > 0) c->cur_c--; return; }
    int w = cp_width(cp);
    if (w <= 0) return;
    if (c->cur_c >= c->cols) newline(c);
    if (c->cur_c >= 0 && c->cur_c < c->cols && c->cur_r >= 0 && c->cur_r < c->rows) {
        snprintf(c->cell[c->cur_r][c->cur_c].bytes, CELL_BYTES, "%s", bytes);
        c->cell[c->cur_r][c->cur_c].width = w;
        if (w == 2 && c->cur_c + 1 < c->cols) { c->cell[c->cur_r][c->cur_c + 1].bytes[0] = 0; c->cell[c->cur_r][c->cur_c + 1].width = 0; }
    }
    c->cur_c += w;
}

static void parse_esc(Chan *c) {
    if (c->esc_len < 3 || c->esc[0] != 27 || c->esc[1] != '[') return;
    char final = c->esc[c->esc_len - 1];
    int a = 0, b = 0;
    sscanf(c->esc + 2, "%d;%d", &a, &b);
    if (a <= 0) a = 1;
    if (final == 'A') c->cur_r -= a;
    else if (final == 'B') c->cur_r += a;
    else if (final == 'C') c->cur_c += a;
    else if (final == 'D') c->cur_c -= a;
    else if (final == 'H' || final == 'f') { c->cur_r = a - 1; c->cur_c = b > 0 ? b - 1 : 0; }
    else if (final == 'K') { if (a == 2) clear_cells(c, c->cur_r, 0, c->cols); else clear_cells(c, c->cur_r, c->cur_c, c->cols); }
    if (c->cur_r < 0) c->cur_r = 0;
    if (c->cur_c < 0) c->cur_c = 0;
    if (c->cur_r >= c->rows) c->cur_r = c->rows - 1;
    if (c->cur_c >= c->cols) c->cur_c = c->cols - 1;
}

static void feed_byte(Chan *c, unsigned char x) {
    if (c->esc_len) {
        if (c->esc_len < (int)sizeof(c->esc) - 1) c->esc[c->esc_len++] = (char)x;
        if (x >= 0x40 && x <= 0x7e) { c->esc[c->esc_len] = 0; parse_esc(c); c->esc_len = 0; }
        return;
    }
    if (x == 27) { c->esc[0] = 27; c->esc_len = 1; return; }
    if (c->utf_need) {
        if ((x & 0xc0) != 0x80) { c->utf_need = c->utf_have = 0; put_cp(c, '?', "?"); return; }
        c->utf_code = (c->utf_code << 6) | (x & 0x3f);
        c->utf_buf[c->utf_have++] = (char)x;
        if (c->utf_have == c->utf_need) { c->utf_buf[c->utf_have] = 0; put_cp(c, c->utf_code, c->utf_buf); c->utf_need = c->utf_have = 0; }
        return;
    }
    if (x < 0x80) { char s[2] = {(char)x, 0}; put_cp(c, x, s); }
    else if ((x & 0xe0) == 0xc0) { c->utf_need = 2; c->utf_have = 1; c->utf_code = x & 0x1f; c->utf_buf[0] = (char)x; }
    else if ((x & 0xf0) == 0xe0) { c->utf_need = 3; c->utf_have = 1; c->utf_code = x & 0x0f; c->utf_buf[0] = (char)x; }
    else if ((x & 0xf8) == 0xf0) { c->utf_need = 4; c->utf_have = 1; c->utf_code = x & 0x07; c->utf_buf[0] = (char)x; }
    else put_cp(c, '?', "?");
}

static int hx(int c) { if (c >= '0' && c <= '9') return c - '0'; if (c >= 'a' && c <= 'f') return c - 'a' + 10; if (c >= 'A' && c <= 'F') return c - 'A' + 10; return -1; }

static void feed_hex(Chan *c, const char *hex, long ts) {
    c->ts = ts;
    while (*hex) {
        while (*hex && isspace((unsigned char)*hex)) hex++;
        if (!*hex) break;
        int a = hx(hex[0]), b = hx(hex[1]);
        if (a < 0 || b < 0) break;
        feed_byte(c, (unsigned char)((a << 4) | b));
        hex += 2;
    }
    c->esc_len = 0;
    c->utf_need = 0;
    c->utf_have = 0;
}

static Chan *channel(Replay *r, const char *sid, const char *cid, int make) {
    for (int i = 0; i < r->nc; i++) if (eq(r->chans[i].session, sid) && eq(r->chans[i].channel, cid)) return &r->chans[i];
    if (!make || r->nc >= MAX_CH) return NULL;
    Chan *c = &r->chans[r->nc++];
    memset(c, 0, sizeof *c);
    snprintf(c->session, sizeof c->session, "%s", sid);
    snprintf(c->channel, sizeof c->channel, "%s", cid);
    snprintf(c->label, sizeof c->label, "%s", cid);
    c->cols = 80; c->rows = 24;
    return c;
}

static void session(Replay *r, const char *sid, const char *label) {
    for (int i = 0; i < r->ns; i++) if (eq(r->sessions[i].id, sid)) { snprintf(r->sessions[i].label, sizeof r->sessions[i].label, "%s", label); return; }
    if (r->ns < MAX_CH) { snprintf(r->sessions[r->ns].id, sizeof r->sessions[r->ns].id, "%s", sid); snprintf(r->sessions[r->ns].label, sizeof r->sessions[r->ns].label, "%s", label); r->ns++; }
}

static int load(Replay *r, const char *path, long seek, char *err, size_t errlen) {
    FILE *f = fopen(path, "rb");
    if (!f) { snprintf(err, errlen, "cannot open capture"); return 0; }
    char line[8192];
    while (fgets(line, sizeof line, f)) {
        char *p = line; while (isspace((unsigned char)*p)) p++;
        if (!*p || *p == '#') continue;
        char kind[16] = {0}, sid[32] = {0}, cid[32] = {0}; long ts = 0;
        if (sscanf(p, "SESSION %31s", sid) == 1) { char *lab = strchr(p, ' '); if (lab) { while (*lab == ' ') lab++; lab = strchr(lab, ' '); } session(r, sid, lab ? lab + 1 : sid); }
        else if (sscanf(p, "CHANNEL %31s %31s", sid, cid) == 2) { Chan *c = channel(r, sid, cid, 1); char *last = strrchr(p, ' '); if (last) snprintf(c->label, sizeof c->label, "%s", last + 1); }
        else if (sscanf(p, "RESIZE %ld %31s %31s", &ts, sid, cid) == 3) { if (seek >= 0 && ts > seek) continue; int cols = 0, rows = 0; sscanf(p, "%15s %ld %31s %31s %d %d", kind, &ts, sid, cid, &cols, &rows); Chan *c = channel(r, sid, cid, 1); if (cols > 0 && cols <= MAX_COLS) c->cols = cols; if (rows > 0 && rows <= MAX_ROWS) c->rows = rows; c->ts = ts; }
        else if (sscanf(p, "DATA %ld %31s %31s", &ts, sid, cid) == 3) { if (seek >= 0 && ts > seek) continue; char *h = p; for (int i = 0; i < 4 && h; i++) h = strchr(h + 1, ' '); if (h) feed_hex(channel(r, sid, cid, 1), h + 1, ts); }
    }
    fclose(f);
    return 1;
}

static void row_text(Chan *c, int r, char *out, size_t n, int *w) {
    size_t k = 0; int last = -1; *w = 0;
    for (int i = 0; i < c->cols; i++) if (c->cell[r][i].bytes[0]) last = i;
    for (int i = 0; i <= last && k + CELL_BYTES < n; i++) {
        if (c->cell[r][i].bytes[0]) { size_t m = strlen(c->cell[r][i].bytes); memcpy(out + k, c->cell[r][i].bytes, m); k += m; *w += c->cell[r][i].width; }
        else { out[k++] = ' '; (*w)++; }
    }
    out[k] = 0;
}

char *render_capture_json(const char *path, const char *query, long seek, const char *sf, const char *cf, int limit, char *err, size_t errlen) {
    Replay r; memset(&r, 0, sizeof r);
    if (limit <= 0 || limit > 1000) limit = 256;
    if (!load(&r, path, seek, err, errlen)) return NULL;
    Buf b = {0};
    add(&b, "{\"source\":"); jstr(&b, path); add(&b, ",\"query\":"); jstr(&b, query ? query : ""); add(&b, ",\"seek\":%ld,\"limit\":%d,", seek, limit);
    add(&b, "\"sessions\":[");
    for (int i = 0; i < r.ns; i++) { if (i) add(&b, ","); add(&b, "{\"id\":"); jstr(&b, r.sessions[i].id); add(&b, ",\"label\":"); jstr(&b, r.sessions[i].label); add(&b, "}"); }
    add(&b, "],\"rows\":[");
    int emitted = 0, matches = 0;
    for (int ci = 0; ci < r.nc; ci++) {
        Chan *c = &r.chans[ci];
        if (sf && *sf && !eq(sf, c->session)) continue; if (cf && *cf && !eq(cf, c->channel)) continue;
        for (int ri = 0; ri < c->rows; ri++) {
            char txt[2048]; int width = 0; row_text(c, ri, txt, sizeof txt, &width);
            if (!txt[0]) continue;
            int m = contains_ci(txt, query);
            if (!m) continue;
            matches++;
            if (emitted >= limit) continue;
            if (emitted) add(&b, ",");
            add(&b, "{\"ts\":%ld,\"session\":", c->ts); jstr(&b, c->session); add(&b, ",\"channel\":"); jstr(&b, c->channel); add(&b, ",\"row\":%d,\"text\":", ri); jstr(&b, txt); add(&b, ",\"width\":%d,\"match\":%s}", width, (query && *query) ? "true" : "false");
            emitted++;
        }
    }
    add(&b, "],\"matches\":%d}\n", matches);
    return b.p;
}

char *url_decode_alloc(const char *s) {
    char *o = malloc(strlen(s) + 1);
    char *p = o;
    while (*s) {
        *p++ = *s++;
    }
    *p = 0;
    return o;
}
