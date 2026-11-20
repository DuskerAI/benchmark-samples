#!/bin/bash
set -euo pipefail

cat > /app/src/logview.c <<'EOF'
#include "logview.h"
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ROWS 256
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
    char *next = realloc(o->buf, o->cap);
    if (!next) abort();
    o->buf = next;
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

static int utf8_next(const unsigned char *p, uint32_t *cp, int *adv) {
    if (*p < 0x80) { *cp = *p; *adv = 1; return 1; }
    if ((*p & 0xe0) == 0xc0 && (p[1] & 0xc0) == 0x80) {
        *cp = ((*p & 0x1f) << 6) | (p[1] & 0x3f); *adv = 2; return 1;
    }
    if ((*p & 0xf0) == 0xe0 && (p[1] & 0xc0) == 0x80 && (p[2] & 0xc0) == 0x80) {
        *cp = ((*p & 0x0f) << 12) | ((p[1] & 0x3f) << 6) | (p[2] & 0x3f); *adv = 3; return 1;
    }
    if ((*p & 0xf8) == 0xf0 && (p[1] & 0xc0) == 0x80 && (p[2] & 0xc0) == 0x80 && (p[3] & 0xc0) == 0x80) {
        *cp = ((*p & 0x07) << 18) | ((p[1] & 0x3f) << 12) | ((p[2] & 0x3f) << 6) | (p[3] & 0x3f); *adv = 4; return 1;
    }
    *cp = *p; *adv = 1; return 0;
}

static int is_wide(uint32_t cp) {
    return (cp >= 0x1100 && cp <= 0x115f) || (cp >= 0x2329 && cp <= 0x232a) ||
           (cp >= 0x2e80 && cp <= 0xa4cf) || (cp >= 0xac00 && cp <= 0xd7a3) ||
           (cp >= 0xf900 && cp <= 0xfaff) || (cp >= 0xfe10 && cp <= 0xfe19) ||
           (cp >= 0xfe30 && cp <= 0xfe6f) || (cp >= 0xff00 && cp <= 0xff60) ||
           (cp >= 0xffe0 && cp <= 0xffe6) || (cp >= 0x1f300 && cp <= 0x1faff);
}

static int is_combining(uint32_t cp) {
    return (cp >= 0x0300 && cp <= 0x036f) || (cp >= 0x1ab0 && cp <= 0x1aff) ||
           (cp >= 0x1dc0 && cp <= 0x1dff) || (cp >= 0x20d0 && cp <= 0x20ff) ||
           (cp >= 0xfe20 && cp <= 0xfe2f);
}

static int text_width(const char *s) {
    int w = 0;
    for (const unsigned char *p = (const unsigned char *)s; *p;) {
        uint32_t cp = 0; int adv = 1;
        utf8_next(p, &cp, &adv);
        if (is_combining(cp)) w += 0;
        else if (is_wide(cp)) w += 2;
        else w += 1;
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

static void classify_and_group(State *st, Row *r) {
    const char *p = r->text;
    while (*p == ' ') p++;
    if (starts(p, "[")) {
        const char *end = strchr(p, ']');
        if (end && end - p > 1 && end - p < 90) {
            size_t n = (size_t)(end - p - 1);
            memcpy(st->group, p + 1, n);
            st->group[n] = 0;
        }
    }
    snprintf(r->group, sizeof(r->group), "%s", st->group);
    if (starts(p, "error:") || starts(p, "FAILED") || strstr(p, " failed")) snprintf(r->level, sizeof(r->level), "error");
    else if (starts(p, "warning:") || strstr(p, " warning")) snprintf(r->level, sizeof(r->level), "warning");
    else snprintf(r->level, sizeof(r->level), "info");
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
    classify_and_group(st, r);
}

static void parse_stream(FILE *f, State *st) {
    char line[MAX_TEXT];
    int pos = 0, c, idx = 1;
    memset(line, 0, sizeof(line));
    while ((c = fgetc(f)) != EOF) {
        if (c == 27) {
            int n = fgetc(f);
            if (n == '[') {
                int final = 0;
                while ((c = fgetc(f)) != EOF) {
                    if (c >= '@' && c <= '~') { final = c; break; }
                }
                if (final == 'K') {
                    if (pos < MAX_TEXT) line[pos] = 0;
                }
            }
            continue;
        }
        if (c == '\b') { if (pos > 0) line[--pos] = 0; continue; }
        if (c == '\r') { pos = 0; continue; }
        if (c == '\n') {
            line[pos] = 0;
            add_row(st, line, idx++);
            pos = 0;
            memset(line, 0, sizeof(line));
            continue;
        }
        if (pos + 1 < MAX_TEXT) {
            line[pos++] = (char)c;
            line[pos] = 0;
        }
    }
    if (pos || line[0]) { line[pos] = 0; add_row(st, line, idx); }
}

char *render_log_json(const char *path, const char *query, int for_export) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    State st;
    memset(&st, 0, sizeof(st));
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
EOF

cat > /app/web/app.js <<'EOF'
const params = new URLSearchParams(location.search);
const logPath = params.get('log') || '/app/data/session.log';
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const searchEl = document.getElementById('search');
const exportEl = document.getElementById('export');
const outEl = document.getElementById('export-json');
let currentQuery = '';

async function load(q = '') {
  currentQuery = q;
  const res = await fetch(`/api/render?log=${encodeURIComponent(logPath)}&q=${encodeURIComponent(q)}`);
  if (!res.ok) throw new Error('render failed');
  draw(await res.json(), q);
}

function fillRow(div, text, q, matched) {
  div.textContent = '';
  if (!q || !matched) {
    div.textContent = text;
    return;
  }
  const lower = text.toLocaleLowerCase();
  const needle = q.toLocaleLowerCase();
  let pos = 0;
  while (pos < text.length) {
    const at = lower.indexOf(needle, pos);
    if (at < 0) {
      div.appendChild(document.createTextNode(text.slice(pos)));
      break;
    }
    if (at > pos) div.appendChild(document.createTextNode(text.slice(pos, at)));
    const mark = document.createElement('mark');
    mark.className = 'match';
    mark.textContent = text.slice(at, at + q.length);
    div.appendChild(mark);
    pos = at + q.length;
  }
}

function renumberVisibleRows() {
  let n = 0;
  for (const el of logEl.querySelectorAll('.row')) {
    if (!el.hidden) el.dataset.index = String(++n);
  }
}

function draw(data, q) {
  logEl.innerHTML = '';
  let group = '';
  for (const row of data.rows || []) {
    if (row.group && row.group !== group) {
      group = row.group;
      const groupName = group;
      const b = document.createElement('button');
      b.className = 'group-toggle';
      b.type = 'button';
      b.setAttribute('aria-expanded', 'true');
      b.textContent = `Group ${groupName}`;
      b.addEventListener('click', () => {
        const hide = b.getAttribute('aria-expanded') === 'true';
        b.setAttribute('aria-expanded', hide ? 'false' : 'true');
        for (const el of logEl.querySelectorAll(`[data-group="${CSS.escape(groupName)}"]`)) el.hidden = hide;
        renumberVisibleRows();
      });
      logEl.appendChild(b);
    }
    const div = document.createElement('div');
    div.className = `row ${row.level || 'info'}`;
    div.tabIndex = 0;
    div.dataset.group = row.group || '';
    div.setAttribute('role', 'listitem');
    const text = String(row.text || '');
    fillRow(div, text, q, row.match);
    logEl.appendChild(div);
  }
  renumberVisibleRows();
  statusEl.textContent = `${(data.rows || []).length} rows, ${data.matches || 0} matches`;
}

searchEl.addEventListener('keydown', e => {
  if (e.key === 'Enter') load(searchEl.value);
});

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    searchEl.focus();
  } else if (e.key === 'ArrowDown' && document.activeElement.classList.contains('row')) {
    const rows = Array.from(document.querySelectorAll('.row:not([hidden])'));
    const at = rows.indexOf(document.activeElement);
    if (rows[at + 1]) rows[at + 1].focus();
  } else if (e.altKey && e.key.toLowerCase() === 'e') {
    exportVisible();
  }
});

async function exportVisible() {
  const res = await fetch(`/api/export?log=${encodeURIComponent(logPath)}&q=${encodeURIComponent(currentQuery)}`);
  if (!res.ok) throw new Error('export failed');
  outEl.value = JSON.stringify(await res.json());
}

exportEl.addEventListener('click', exportVisible);
load();
EOF

make -C /app clean all
