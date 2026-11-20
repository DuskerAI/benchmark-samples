#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

typedef struct { unsigned char *p; size_t n; } Bytes;

typedef struct {
    char *key;
    int type; /* 1 bool, 2 uint, 3 bytes, 4 text */
    bool b;
    unsigned long long u;
    Bytes bytes;
    char *text;
} ExtVal;

typedef struct {
    char *rp_id;
    Bytes challenge;
    Bytes *creds;
    size_t ncreds;
    char **auths;
    size_t nauths;
    char **allow_ext;
    size_t nallow_ext;
    ExtVal *req_ext;
    size_t nreq_ext;
} Policy;

typedef struct { char *auth; Bytes cred; unsigned long long count; } Counter;

typedef struct {
    char *id;
    char *auth;
    Bytes cred;
    unsigned long long count;
    unsigned flags;
    Bytes challenge;
    char *rp;
    ExtVal *exts;
    size_t nexts;
} Assertion;

typedef struct {
    bool accepted;
    char *id;
    char *reason;
    char *auth;
    char *cred_hex;
    unsigned long long count;
    char *rp;
} Decision;

typedef struct { Decision *v; size_t n, cap; } Decisions;

typedef struct {
    unsigned long long arrival;
    char *base;
    int line;
    char cid[9];
    Bytes frame;
    bool malformed;
    char *fallback;
} Record;

typedef struct { Record *v; size_t n, cap; } Records;

typedef struct {
    char cid[9];
    bool active;
    unsigned next_seq;
    size_t need;
    Bytes buf;
    char *fallback;
} Channel;

typedef struct { const unsigned char *p; size_t n, pos; char *seen_id; } Cbor;

static void *xcalloc(size_t n, size_t s) { void *p = calloc(n, s); if (!p) { perror("calloc"); exit(1); } return p; }
static void *xrealloc(void *old, size_t n) { void *p = realloc(old, n); if (!p) { perror("realloc"); exit(1); } return p; }
static char *xstrdup(const char *s) { char *p = strdup(s ? s : ""); if (!p) { perror("strdup"); exit(1); } return p; }

static const char *arg_value(int argc, char **argv, const char *name) {
    for (int i = 1; i + 1 < argc; i++) if (strcmp(argv[i], name) == 0) return argv[i + 1];
    return NULL;
}

static char *trim(char *s) {
    while (isspace((unsigned char)*s)) s++;
    char *e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1])) *--e = '\0';
    return s;
}

static bool has_suffix(const char *s, const char *suffix) {
    size_t a = strlen(s), b = strlen(suffix);
    return a >= b && strcmp(s + a - b, suffix) == 0;
}

static int hexval(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static bool parse_hex(const char *s, Bytes *out) {
    size_t n = strlen(s);
    if (n % 2) return false;
    out->p = xcalloc(n / 2 + 1, 1);
    out->n = n / 2;
    for (size_t i = 0; i < out->n; i++) {
        int a = hexval((unsigned char)s[2 * i]);
        int b = hexval((unsigned char)s[2 * i + 1]);
        if (a < 0 || b < 0) { free(out->p); out->p = NULL; out->n = 0; return false; }
        out->p[i] = (unsigned char)((a << 4) | b);
    }
    return true;
}

static char *hex_lower(const unsigned char *p, size_t n) {
    static const char tab[] = "0123456789abcdef";
    char *s = xcalloc(n * 2 + 1, 1);
    for (size_t i = 0; i < n; i++) { s[2 * i] = tab[p[i] >> 4]; s[2 * i + 1] = tab[p[i] & 15]; }
    return s;
}

static bool bytes_eq(Bytes a, Bytes b) { return a.n == b.n && (a.n == 0 || memcmp(a.p, b.p, a.n) == 0); }

static void append_bytes(Bytes *b, const unsigned char *p, size_t n) {
    b->p = xrealloc(b->p, b->n + n + 1);
    memcpy(b->p + b->n, p, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void add_decision(Decisions *d, Decision row) {
    if (d->n == d->cap) { d->cap = d->cap ? d->cap * 2 : 16; d->v = xrealloc(d->v, d->cap * sizeof(*d->v)); }
    d->v[d->n++] = row;
}

static void reject_row(Decisions *d, const char *id, const char *reason) {
    Decision r = {0};
    r.id = xstrdup(id && *id ? id : "");
    r.reason = xstrdup(reason);
    add_decision(d, r);
}

static void accept_row(Decisions *d, const Assertion *a) {
    Decision r = {0};
    r.accepted = true;
    r.id = xstrdup(a->id);
    r.auth = xstrdup(a->auth);
    r.cred_hex = hex_lower(a->cred.p, a->cred.n);
    r.count = a->count;
    r.rp = xstrdup(a->rp);
    add_decision(d, r);
}

static void records_add(Records *rs, Record r) {
    if (rs->n == rs->cap) { rs->cap = rs->cap ? rs->cap * 2 : 32; rs->v = xrealloc(rs->v, rs->cap * sizeof(*rs->v)); }
    rs->v[rs->n++] = r;
}

static int cmp_record(const void *aa, const void *bb) {
    const Record *a = aa, *b = bb;
    if (a->arrival < b->arrival) return -1;
    if (a->arrival > b->arrival) return 1;
    int c = strcmp(a->base, b->base);
    if (c) return c;
    return (a->line > b->line) - (a->line < b->line);
}

static char *fallback_id(const char *base, int line) {
    char buf[512];
    snprintf(buf, sizeof(buf), "%s:%d", base, line);
    return xstrdup(buf);
}

static bool parse_arrival(const char *s, unsigned long long *out) {
    if (!*s) return false;
    unsigned long long v = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (!isdigit(*p)) return false;
        v = v * 10 + (unsigned)(*p - '0');
    }
    *out = v;
    return true;
}

static void load_capture_file(const char *dir, const char *base, Records *rs) {
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/%s", dir, base);
    FILE *f = fopen(path, "r");
    if (!f) return;
    char *line = NULL;
    size_t cap = 0;
    int lno = 0;
    while (getline(&line, &cap, f) != -1) {
        lno++;
        char *s = trim(line);
        if (*s == '\0' || *s == '#') continue;
        char *a = strtok(s, " \t");
        char *cid = strtok(NULL, " \t");
        char *hx = strtok(NULL, " \t");
        char *extra = strtok(NULL, " \t");
        Record r = {0};
        r.base = xstrdup(base);
        r.line = lno;
        r.fallback = fallback_id(base, lno);
        if (!a || !cid || !hx || extra || !parse_arrival(a, &r.arrival) || strlen(cid) != 8) {
            r.arrival = ULLONG_MAX;
            r.malformed = true;
        } else {
            bool cid_ok = true;
            for (int i = 0; i < 8; i++) if (hexval((unsigned char)cid[i]) < 0) cid_ok = false;
            for (int i = 0; i < 8; i++) r.cid[i] = (char)tolower((unsigned char)cid[i]);
            r.cid[8] = 0;
            if (!cid_ok || !parse_hex(hx, &r.frame)) r.malformed = true;
        }
        records_add(rs, r);
    }
    free(line);
    fclose(f);
}

static Records load_records(const char *dir) {
    Records rs = {0};
    DIR *dp = opendir(dir);
    if (!dp) return rs;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL) {
        if (de->d_name[0] == '.' || !has_suffix(de->d_name, ".cap")) continue;
        load_capture_file(dir, de->d_name, &rs);
    }
    closedir(dp);
    qsort(rs.v, rs.n, sizeof(*rs.v), cmp_record);
    return rs;
}

static bool read_uint(Cbor *c, int major, unsigned long long *out, size_t *start, size_t *end) {
    if (c->pos >= c->n) return false;
    if (start) *start = c->pos;
    unsigned char ib = c->p[c->pos++];
    if ((ib >> 5) != (unsigned)major) return false;
    unsigned ai = ib & 31U;
    unsigned long long v = 0;
    if (ai < 24) v = ai;
    else if (ai == 24) { if (c->pos + 1 > c->n) return false; v = c->p[c->pos++]; if (v < 24) return false; }
    else if (ai == 25) { if (c->pos + 2 > c->n) return false; v = ((unsigned)c->p[c->pos] << 8) | c->p[c->pos + 1]; c->pos += 2; if (v < 256) return false; }
    else if (ai == 26) { if (c->pos + 4 > c->n) return false; for (int i = 0; i < 4; i++) v = (v << 8) | c->p[c->pos++]; if (v < 65536ULL) return false; }
    else if (ai == 27) { if (c->pos + 8 > c->n) return false; for (int i = 0; i < 8; i++) v = (v << 8) | c->p[c->pos++]; if (v < 4294967296ULL) return false; }
    else return false;
    if (end) *end = c->pos;
    *out = v;
    return true;
}

static bool read_bytes_major(Cbor *c, int major, Bytes *out) {
    unsigned long long len = 0;
    if (!read_uint(c, major, &len, NULL, NULL)) return false;
    if (len > SIZE_MAX || c->pos + (size_t)len > c->n) return false;
    out->p = xcalloc((size_t)len + 1, 1);
    out->n = (size_t)len;
    memcpy(out->p, c->p + c->pos, out->n);
    c->pos += out->n;
    return true;
}

static bool read_text(Cbor *c, char **out) {
    Bytes b = {0};
    if (!read_bytes_major(c, 3, &b)) return false;
    for (size_t i = 0; i < b.n; i++) if (b.p[i] == 0) { free(b.p); return false; }
    *out = (char *)b.p;
    return true;
}

static int encoded_cmp(const unsigned char *a, size_t an, const unsigned char *b, size_t bn) {
    size_t m = an < bn ? an : bn;
    int c = memcmp(a, b, m);
    if (c) return c;
    return (an > bn) - (an < bn);
}

static bool read_ext_value(Cbor *c, ExtVal *v) {
    if (c->pos >= c->n) return false;
    if (c->p[c->pos] == 0xf4 || c->p[c->pos] == 0xf5) {
        v->type = 1;
        v->b = c->p[c->pos++] == 0xf5;
        return true;
    }
    if ((c->p[c->pos] >> 5) == 2) {
        v->type = 3;
        return read_bytes_major(c, 2, &v->bytes);
    }
    if ((c->p[c->pos] >> 5) == 3) {
        v->type = 4;
        return read_text(c, &v->text);
    }
    v->type = 2;
    return read_uint(c, 0, &v->u, NULL, NULL);
}

static bool read_ext_map(Cbor *c, Assertion *a) {
    unsigned long long n = 0;
    if (!read_uint(c, 5, &n, NULL, NULL)) return false;
    if (n > 256) return false;
    const unsigned char *prev = NULL;
    size_t prevn = 0;
    a->exts = xcalloc((size_t)n ? (size_t)n : 1, sizeof(*a->exts));
    a->nexts = (size_t)n;
    for (size_t i = 0; i < (size_t)n; i++) {
        size_t ks = 0, ke = 0;
        ks = c->pos;
        if (!read_text(c, &a->exts[i].key)) return false;
        ke = c->pos;
        if (prev && encoded_cmp(prev, prevn, c->p + ks, ke - ks) >= 0) return false;
        prev = c->p + ks;
        prevn = ke - ks;
        if (!read_ext_value(c, &a->exts[i])) return false;
    }
    return true;
}

static bool parse_assertion(Bytes payload, Assertion *a, char **id_hint) {
    memset(a, 0, sizeof(*a));
    if (payload.n < 2 || payload.p[0] != 0x00) return false;
    Cbor c = {.p = payload.p + 1, .n = payload.n - 1, .pos = 0, .seen_id = NULL};
    unsigned long long mapn = 0;
    if (!read_uint(&c, 5, &mapn, NULL, NULL) || mapn != 8) return false;
    bool seen[8] = {0};
    const unsigned char *prev = NULL;
    size_t prevn = 0;
    for (size_t i = 0; i < 8; i++) {
        size_t ks = 0, ke = 0;
        unsigned long long key = 0;
        if (!read_uint(&c, 0, &key, &ks, &ke) || key > 7 || seen[key]) return false;
        if (prev && encoded_cmp(prev, prevn, c.p + ks, ke - ks) >= 0) return false;
        prev = c.p + ks;
        prevn = ke - ks;
        seen[key] = true;
        switch (key) {
            case 0:
                if (!read_text(&c, &a->id)) return false;
                *id_hint = a->id;
                break;
            case 1: if (!read_text(&c, &a->auth)) return false; break;
            case 2: if (!read_bytes_major(&c, 2, &a->cred)) return false; break;
            case 3: if (!read_uint(&c, 0, &a->count, NULL, NULL)) return false; break;
            case 4: { unsigned long long fl = 0; if (!read_uint(&c, 0, &fl, NULL, NULL) || fl > UINT_MAX) return false; a->flags = (unsigned)fl; break; }
            case 5: if (!read_bytes_major(&c, 2, &a->challenge)) return false; break;
            case 6: if (!read_text(&c, &a->rp)) return false; break;
            case 7: if (!read_ext_map(&c, a)) return false; break;
        }
    }
    if (c.pos != c.n) return false;
    for (int i = 0; i < 8; i++) if (!seen[i]) return false;
    if (!a->id || !a->auth || !a->rp || a->cred.n == 0) return false;
    if ((a->flags & 0x01U) == 0) return false;
    if ((a->flags & 0x80U) != 0) { if (a->nexts == 0) return false; }
    else if (a->nexts != 0) return false;
    return true;
}

static void add_string(char ***arr, size_t *n, const char *s) {
    *arr = xrealloc(*arr, (*n + 1) * sizeof(**arr));
    (*arr)[(*n)++] = xstrdup(s);
}

static void add_bytes(Bytes **arr, size_t *n, Bytes b) {
    *arr = xrealloc(*arr, (*n + 1) * sizeof(**arr));
    (*arr)[(*n)++] = b;
}

static bool parse_ext_value(const char *s, ExtVal *v) {
    if (strcmp(s, "true") == 0) { v->type = 1; v->b = true; return true; }
    if (strcmp(s, "false") == 0) { v->type = 1; v->b = false; return true; }
    if (strncmp(s, "hex:", 4) == 0) { v->type = 3; return parse_hex(s + 4, &v->bytes); }
    if (strncmp(s, "text:", 5) == 0) { v->type = 4; v->text = xstrdup(s + 5); return true; }
    if (!*s) return false;
    unsigned long long u = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) { if (!isdigit(*p)) return false; u = u * 10 + (unsigned)(*p - '0'); }
    v->type = 2; v->u = u; return true;
}

static Policy load_policy(const char *path) {
    Policy p = {0};
    FILE *f = fopen(path, "r");
    if (!f) return p;
    char *line = NULL; size_t cap = 0;
    while (getline(&line, &cap, f) != -1) {
        char *s = trim(line);
        if (!*s || *s == '#') continue;
        char *eq = strchr(s, '=');
        if (!eq) continue;
        *eq = 0;
        char *key = trim(s), *val = trim(eq + 1);
        if (strcmp(key, "rp_id") == 0) { free(p.rp_id); p.rp_id = xstrdup(val); }
        else if (strcmp(key, "challenge") == 0) { free(p.challenge.p); (void)parse_hex(val, &p.challenge); }
        else if (strcmp(key, "allow_credential") == 0) { Bytes b = {0}; if (parse_hex(val, &b)) add_bytes(&p.creds, &p.ncreds, b); }
        else if (strcmp(key, "allow_authenticator") == 0) add_string(&p.auths, &p.nauths, val);
        else if (strcmp(key, "allow_extension") == 0) add_string(&p.allow_ext, &p.nallow_ext, val);
        else if (strcmp(key, "require_extension") == 0) {
            char *eq2 = strchr(val, '=');
            if (eq2) {
                *eq2 = 0;
                ExtVal ev = {.key = xstrdup(trim(val))};
                if (parse_ext_value(trim(eq2 + 1), &ev)) {
                    p.req_ext = xrealloc(p.req_ext, (p.nreq_ext + 1) * sizeof(*p.req_ext));
                    p.req_ext[p.nreq_ext++] = ev;
                } else free(ev.key);
            }
        }
    }
    free(line); fclose(f); return p;
}

static Counter *load_counters(const char *path, size_t *n) {
    Counter *v = NULL; *n = 0;
    FILE *f = fopen(path, "r"); if (!f) return NULL;
    char *line = NULL; size_t cap = 0;
    while (getline(&line, &cap, f) != -1) {
        char *s = trim(line); if (!*s || *s == '#') continue;
        char *auth = strtok(s, " \t"); char *cred = strtok(NULL, " \t"); char *cnt = strtok(NULL, " \t");
        if (!auth || !cred || !cnt) continue;
        Bytes b = {0}; if (!parse_hex(cred, &b)) continue;
        unsigned long long u = 0; if (!parse_arrival(cnt, &u)) { free(b.p); continue; }
        v = xrealloc(v, (*n + 1) * sizeof(*v));
        v[*n].auth = xstrdup(auth); v[*n].cred = b; v[*n].count = u; (*n)++;
    }
    free(line); fclose(f); return v;
}

static bool string_in(char **v, size_t n, const char *s) { for (size_t i = 0; i < n; i++) if (strcmp(v[i], s) == 0) return true; return false; }
static bool bytes_in(Bytes *v, size_t n, Bytes b) { for (size_t i = 0; i < n; i++) if (bytes_eq(v[i], b)) return true; return false; }

static ExtVal *find_ext(ExtVal *v, size_t n, const char *key) { for (size_t i = 0; i < n; i++) if (strcmp(v[i].key, key) == 0) return &v[i]; return NULL; }
static bool ext_equal(const ExtVal *a, const ExtVal *b) {
    return a && b && a->type == b->type &&
        ((a->type == 1 && a->b == b->b) ||
         (a->type == 2 && a->u == b->u) ||
         (a->type == 3 && bytes_eq(a->bytes, b->bytes)) ||
         (a->type == 4 && strcmp(a->text ? a->text : "", b->text ? b->text : "") == 0));
}

static bool extension_ok(const Policy *p, const Assertion *a) {
    for (size_t i = 0; i < a->nexts; i++) {
        if (!string_in(p->allow_ext, p->nallow_ext, a->exts[i].key) && !find_ext(p->req_ext, p->nreq_ext, a->exts[i].key)) return false;
    }
    for (size_t i = 0; i < p->nreq_ext; i++) {
        ExtVal *got = find_ext(a->exts, a->nexts, p->req_ext[i].key);
        if (!ext_equal(got, &p->req_ext[i])) return false;
    }
    return true;
}

static Counter *find_counter(Counter *v, size_t n, const char *auth, Bytes cred) {
    for (size_t i = 0; i < n; i++) if (strcmp(v[i].auth, auth) == 0 && bytes_eq(v[i].cred, cred)) return &v[i];
    return NULL;
}

static void advance_counter(Counter **v, size_t *n, const char *auth, Bytes cred, unsigned long long count) {
    Counter *c = find_counter(*v, *n, auth, cred);
    if (c) { if (count > c->count) c->count = count; return; }
    *v = xrealloc(*v, (*n + 1) * sizeof(**v));
    (*v)[*n].auth = xstrdup(auth);
    (*v)[*n].cred.p = xcalloc(cred.n + 1, 1);
    memcpy((*v)[*n].cred.p, cred.p, cred.n);
    (*v)[*n].cred.n = cred.n;
    (*v)[*n].count = count;
    (*n)++;
}

static bool policy_ok(const Policy *p, const Assertion *a) {
    return p->rp_id && strcmp(p->rp_id, a->rp) == 0 && bytes_eq(p->challenge, a->challenge) && bytes_in(p->creds, p->ncreds, a->cred) && string_in(p->auths, p->nauths, a->auth);
}

static void handle_message(Bytes payload, const char *fallback, const Policy *p, Counter **counters, size_t *ncounters, Decisions *d) {
    Assertion a = {0};
    char *hint = NULL;
    if (!parse_assertion(payload, &a, &hint)) { reject_row(d, hint ? hint : fallback, "malformed"); return; }
    if (!policy_ok(p, &a)) { reject_row(d, a.id, "policy"); return; }
    if (!extension_ok(p, &a)) { reject_row(d, a.id, "extension"); return; }
    Counter *c = find_counter(*counters, *ncounters, a.auth, a.cred);
    if (c && a.count <= c->count) { reject_row(d, a.id, "replay"); return; }
    accept_row(d, &a);
    advance_counter(counters, ncounters, a.auth, a.cred, a.count);
}

static void cid_from_frame(const unsigned char *p, char out[9]) {
    char *s = hex_lower(p, 4);
    memcpy(out, s, 9);
    free(s);
}

static Channel *channel_for(Channel **chs, size_t *n, const char *cid) {
    for (size_t i = 0; i < *n; i++) if (strcmp((*chs)[i].cid, cid) == 0) return &(*chs)[i];
    *chs = xrealloc(*chs, (*n + 1) * sizeof(**chs));
    Channel *c = &(*chs)[(*n)++];
    memset(c, 0, sizeof(*c));
    memcpy(c->cid, cid, 9);
    return c;
}

static void clear_channel(Channel *c) { c->active = false; c->next_seq = 0; c->need = 0; free(c->buf.p); c->buf.p = NULL; c->buf.n = 0; free(c->fallback); c->fallback = NULL; }

static void process_record(const Record *r, const Policy *p, Counter **counters, size_t *ncounters, Decisions *d, Channel **chs, size_t *nchs) {
    if (r->malformed) { reject_row(d, r->fallback, "malformed"); return; }
    if (r->frame.n < 5) { reject_row(d, r->fallback, "malformed"); return; }
    char fc[9]; cid_from_frame(r->frame.p, fc);
    if (strcmp(fc, r->cid) != 0) { reject_row(d, r->fallback, "malformed"); return; }
    Channel *ch = channel_for(chs, nchs, fc);
    unsigned char tag = r->frame.p[4];
    if (tag & 0x80U) {
        if (ch->active) { reject_row(d, ch->fallback, "malformed"); clear_channel(ch); }
        if (r->frame.n < 7 || tag != 0x90) { reject_row(d, r->fallback, "malformed"); return; }
        size_t need = ((size_t)r->frame.p[5] << 8) | r->frame.p[6];
        ch->active = true; ch->need = need; ch->next_seq = 0; ch->fallback = xstrdup(r->fallback);
        size_t got = r->frame.n - 7;
        if (got > need) got = need;
        append_bytes(&ch->buf, r->frame.p + 7, got);
        if (ch->buf.n >= ch->need) { Bytes msg = ch->buf; ch->buf.p = NULL; ch->buf.n = 0; handle_message(msg, ch->fallback, p, counters, ncounters, d); free(msg.p); clear_channel(ch); }
    } else {
        if (!ch->active || tag != ch->next_seq) { reject_row(d, ch->active ? ch->fallback : r->fallback, "malformed"); clear_channel(ch); return; }
        ch->next_seq++;
        size_t take = r->frame.n - 5;
        if (ch->buf.n + take > ch->need) take = ch->need - ch->buf.n;
        append_bytes(&ch->buf, r->frame.p + 5, take);
        if (ch->buf.n >= ch->need) { Bytes msg = ch->buf; ch->buf.p = NULL; ch->buf.n = 0; handle_message(msg, ch->fallback, p, counters, ncounters, d); free(msg.p); clear_channel(ch); }
    }
}

static void mkdir_parent(const char *path) {
    char tmp[PATH_MAX];
    size_t n = strlen(path);
    if (n >= sizeof(tmp)) return;
    memcpy(tmp, path, n + 1);
    for (char *p = tmp + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(tmp, 0777); *p = '/'; }
}

static void json_string(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)(s ? s : ""); *p; p++) {
        if (*p == '"' || *p == '\\') fprintf(f, "\\%c", *p);
        else if (*p >= 0x20 && *p <= 0x7e) fputc(*p, f);
        else fprintf(f, "\\u%04x", *p);
    }
    fputc('"', f);
}

static int write_report(const char *path, const Decisions *d) {
    mkdir_parent(path);
    FILE *f = fopen(path, "w");
    if (!f) return 1;
    size_t acc = 0, rej = 0, mal = 0;
    for (size_t i = 0; i < d->n; i++) { if (d->v[i].accepted) acc++; else { rej++; if (strcmp(d->v[i].reason, "malformed") == 0) mal++; } }
    fputs("{\"accepted\":[", f);
    bool first = true;
    for (size_t i = 0; i < d->n; i++) if (d->v[i].accepted) {
        if (!first) fputc(',', f);
        first = false;
        fputs("{\"id\":", f); json_string(f, d->v[i].id);
        fputs(",\"authenticator\":", f); json_string(f, d->v[i].auth);
        fputs(",\"credential\":", f); json_string(f, d->v[i].cred_hex);
        fprintf(f, ",\"sign_count\":%llu,\"rp_id\":", d->v[i].count); json_string(f, d->v[i].rp); fputc('}', f);
    }
    fputs("],\"rejected\":[", f);
    first = true;
    for (size_t i = 0; i < d->n; i++) if (!d->v[i].accepted) {
        if (!first) fputc(',', f);
        first = false;
        fputs("{\"id\":", f); json_string(f, d->v[i].id);
        fputs(",\"reason\":", f); json_string(f, d->v[i].reason); fputc('}', f);
    }
    fprintf(f, "],\"summary\":{\"accepted\":%zu,\"rejected\":%zu,\"malformed\":%zu}}\n", acc, rej, mal);
    fclose(f); return 0;
}

int main(int argc, char **argv) {
    const char *policy_path = arg_value(argc, argv, "--policy");
    const char *captures = arg_value(argc, argv, "--captures");
    const char *state_path = arg_value(argc, argv, "--state");
    const char *out = arg_value(argc, argv, "--out");
    if (!policy_path || !captures || !state_path || !out) return 2;
    Policy p = load_policy(policy_path);
    size_t ncounters = 0;
    Counter *counters = load_counters(state_path, &ncounters);
    Records rs = load_records(captures);
    Decisions d = {0};
    Channel *chs = NULL; size_t nchs = 0;
    for (size_t i = 0; i < rs.n; i++) process_record(&rs.v[i], &p, &counters, &ncounters, &d, &chs, &nchs);
    for (size_t i = 0; i < nchs; i++) if (chs[i].active) { reject_row(&d, chs[i].fallback, "malformed"); clear_channel(&chs[i]); }
    return write_report(out, &d);
}
