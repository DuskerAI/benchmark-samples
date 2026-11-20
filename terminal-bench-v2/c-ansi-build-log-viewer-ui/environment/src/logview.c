#include "logview.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ROWS 256
#define MAX_COLS 160
#define MAX_TEXT 1024
#define OUT_INITIAL 65536

typedef struct {
    int index;
    char text[MAX_TEXT];
    int width;
    char level[12];
    char group[96];
    int match;
} Row;

typedef struct {
    Row rows[MAX_ROWS];
    int count;
    int next;
    char group[96];
    char level[12];
} State;

typedef struct {
    char *buf;
    size_t len;
    size_t cap;
} Out;

static int starts(const char *s, const char *p) { return strncmp(s, p, strlen(p)) == 0; }

static void out_grow(Out *o, size_t need) {
    if (o->len + need + 1 <= o->cap) return;
    while (o->len + need + 1 > o->cap) o->cap *= 2;
    o->buf = realloc(o->buf, o->cap);
}

static void out_raw(Out *o, const char *s) {
    size_t n = strlen(s);
    out_grow(o, n);
    memcpy(o->buf + o->len, s, n + 1);
    o->len += n;
}

static void out_chr(Out *o, char c) {
    out_grow(o, 1);
    o->buf[o->len++] = c;
    o->buf[o->len] = 0;
}

static void out_int(Out *o, int v) {
    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%d", v);
    out_raw(o, tmp);
}

static void out_json(Out *o, const char *s) {
    out_chr(o, '"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { out_chr(o, '\\'); out_chr(o, (char)*p); }
        else if (*p == '\n') out_raw(o, "\\n");
        else if (*p == '\r') out_raw(o, "\\r");
        else if (*p == '\t') out_raw(o, "\\t");
        else if (*p < 32) { char tmp[8]; snprintf(tmp, sizeof(tmp), "\\u%04x", *p); out_raw(o, tmp); }
        else out_chr(o, (char)*p);
    }
    out_chr(o, '"');
}

static int simple_width(const unsigned char *p, int *adv) {
    if (*p < 0x80) { *adv = 1; return 1; }
    if ((*p & 0xe0) == 0xc0) *adv = 2;
    else if ((*p & 0xf0) == 0xe0) *adv = 3;
    else if ((*p & 0xf8) == 0xf0) *adv = 4;
    else { *adv = 1; return 1; }
    return *adv;
}

static int text_width(const char *s) {
    int w = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p;) {
        int adv = 1;
        w += simple_width(p, &adv);
        p += adv;
    }
    return w;
}

static void lower_copy(char *dst, size_t n, const char *src) {
    size_t i = 0;
    for (; src[i] && i + 1 < n; i++) dst[i] = (char)tolower((unsigned char)src[i]);
    dst[i] = 0;
}

static int contains_fold(const char *hay, const char *needle) {
    if (!needle || !*needle) return 1;
    char a[MAX_TEXT], b[256];
    lower_copy(a, sizeof(a), hay);
    lower_copy(b, sizeof(b), needle);
    return strstr(a, b) != NULL;
}

static void trim_right(char *s) {
    size_t n = strlen(s);
    while (n && (s[n-1] == ' ' || s[n-1] == '\t')) s[--n] = 0;
}

static void set_meta(State *st, Row *r, const char *line) {
    strcpy(r->level, st->level[0] ? st->level : "info");
    strcpy(r->group, st->group);
    const char *p = line;
    while (*p == ' ') p++;
    if (starts(p, "error:") || starts(p, "FAILED") || strstr(p, " failed")) strcpy(st->level, "error");
    else if (starts(p, "warning:") || strstr(p, " warning")) strcpy(st->level, "info");
    else if (starts(p, "[")) {
        const char *end = strchr(p, ']');
        if (end && end - p < 90) {
            size_t n = (size_t)(end - p - 1);
            memcpy(st->group, p + 1, n);
            st->group[n] = 0;
            strcpy(st->level, "info");
        }
    }
}

static void add_row(State *st, const char *text, int index) {
    int slot = st->next++ % MAX_ROWS;
    if (st->count < MAX_ROWS) st->count++;
    Row *r = &st->rows[slot];
    memset(r, 0, sizeof(*r));
    r->index = index;
    snprintf(r->text, sizeof(r->text), "%s", text);
    trim_right(r->text);
    r->width = text_width(r->text);
    set_meta(st, r, r->text);
}

static void parse_stream(FILE *f, State *st) {
    char line[MAX_TEXT];
    int pos = 0, c, idx = 1, esc = 0;
    while ((c = fgetc(f)) != EOF) {
        if (esc) {
            if ((c >= '@' && c <= '~')) esc = 0;
            continue;
        }
        if (c == 27) { esc = 1; continue; }
        if (c == '\r') { if (pos + 1 < MAX_TEXT) line[pos++] = '\r'; continue; }
        if (c == '\n') {
            line[pos] = 0;
            add_row(st, line, idx++);
            pos = 0;
            continue;
        }
        if (pos + 1 < MAX_TEXT) line[pos++] = (char)c;
    }
    if (pos) { line[pos] = 0; add_row(st, line, idx); }
}

char *render_log_json(const char *path, const char *query, int for_export) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    State st;
    memset(&st, 0, sizeof(st));
    strcpy(st.level, "info");
    parse_stream(f, &st);
    fclose(f);
    Out out = { malloc(OUT_INITIAL), 0, OUT_INITIAL };
    if (!out.buf) return NULL;
    out.buf[0] = 0;
    out_raw(&out, "{\"source\":"); out_json(&out, path);
    out_raw(&out, ",\"query\":"); out_json(&out, query ? query : "");
    out_raw(&out, ",\"rows\":[");
    int first = 1, matches = 0;
    int start = st.count == MAX_ROWS ? st.next % MAX_ROWS : 0;
    for (int i = 0; i < st.count; i++) {
        Row *r = &st.rows[(start + i) % MAX_ROWS];
        int m = contains_fold(r->text, query);
        if (m && query && *query) matches++;
        if (for_export && query && *query && !m) continue;
        if (!first) out_chr(&out, ',');
        first = 0;
        out_raw(&out, "{\"index\":"); out_int(&out, r->index);
        out_raw(&out, ",\"text\":"); out_json(&out, r->text);
        out_raw(&out, ",\"width\":"); out_int(&out, r->width);
        out_raw(&out, ",\"level\":"); out_json(&out, r->level);
        out_raw(&out, ",\"group\":"); out_json(&out, r->group);
        out_raw(&out, ",\"match\":"); out_raw(&out, m && query && *query ? "true" : "false");
        out_chr(&out, '}');
    }
    out_raw(&out, "],\"matches\":"); out_int(&out, matches);
    out_raw(&out, ",\"limit\":"); out_int(&out, MAX_ROWS);
    out_chr(&out, '}');
    return out.buf;
}
