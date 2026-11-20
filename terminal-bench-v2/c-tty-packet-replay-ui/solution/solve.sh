#!/bin/bash
set -euo pipefail

python3 <<'PY'
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"expected one {label} block, found {text.count(old)}")
    return text.replace(old, new, 1)


path = Path('/app/src/replay.c')
s = path.read_text()
s = replace_once(s, """    int esc_len;
    char esc[32];
    int utf_need, utf_have;""", """    int esc_len;
    char esc[32];
    int osc, osc_esc;
    int utf_need, utf_have;""", 'OSC parser state fields')
s = replace_once(s, r"if (cp == '\r') { newline(c); return; }", r"if (cp == '\r') { c->cur_c = 0; return; }", 'carriage-return handling')
s = replace_once(s, r"static void newline(Chan *c) { c->cur_r++; c->cur_c = 0; if (c->cur_r >= c->rows) c->cur_r = c->rows - 1; }", r"""static void scroll_up(Chan *c) {
    if (c->rows <= 1) { clear_cells(c, 0, 0, c->cols); return; }
    memmove(c->cell[0], c->cell[1], (size_t)(c->rows - 1) * sizeof c->cell[0]);
    for (int i = 0; i < c->cols; i++) { c->cell[c->rows - 1][i].bytes[0] = 0; c->cell[c->rows - 1][i].width = 0; }
}

static void newline(Chan *c) { c->cur_r++; c->cur_c = 0; if (c->cur_r >= c->rows) { scroll_up(c); c->cur_r = c->rows - 1; } }""", 'newline/scroll handling')
s = replace_once(s, """static void newline(Chan *c) { c->cur_r++; c->cur_c = 0; if (c->cur_r >= c->rows) { scroll_up(c); c->cur_r = c->rows - 1; } }

static void put_cp""", """static void newline(Chan *c) { c->cur_r++; c->cur_c = 0; if (c->cur_r >= c->rows) { scroll_up(c); c->cur_r = c->rows - 1; } }

static void delete_cells(Chan *c, int r, int from, int count) {
    if (r < 0 || r >= c->rows || from < 0 || from >= c->cols || count <= 0) return;
    if (from + count > c->cols) count = c->cols - from;
    int remain = c->cols - from - count;
    if (remain > 0) memmove(&c->cell[r][from], &c->cell[r][from + count], (size_t)remain * sizeof c->cell[r][0]);
    clear_cells(c, r, c->cols - count, c->cols);
}

static void put_cp""", 'delete-character helper')
s = replace_once(s, r"if (cp == '\b') { if (c->cur_c > 0) c->cur_c--; return; }", r"""if (cp == '\b') { if (c->cur_c > 0) c->cur_c--; return; }
    if (cp == '\t') { c->cur_c = ((c->cur_c / 8) + 1) * 8; if (c->cur_c >= c->cols) newline(c); return; }""", 'tab handling')
s = replace_once(s, """sscanf(c->esc + 2, "%d;%d", &a, &b);
    if (a <= 0) a = 1;""", """sscanf(c->esc + 2, "%d;%d", &a, &b);
    if ((final == 'A' || final == 'B' || final == 'C' || final == 'D' || final == 'G' || final == 'H' || final == 'f' || final == 'P') && a <= 0) a = 1;""", 'CSI default argument handling')
s = replace_once(s, r"if (x >= 0x40 && x <= 0x7e) { c->esc[c->esc_len] = 0; parse_esc(c); c->esc_len = 0; }", r"if (c->esc_len > 2 && x >= 0x40 && x <= 0x7e) { c->esc[c->esc_len] = 0; parse_esc(c); c->esc_len = 0; }", 'CSI split handling')
s = replace_once(s, """else if (final == 'D') c->cur_c -= a;
    else if (final == 'H' || final == 'f') { c->cur_r = a - 1; c->cur_c = b > 0 ? b - 1 : 0; }""", """else if (final == 'D') c->cur_c -= a;
    else if (final == 'G') c->cur_c = a - 1;
    else if (final == 'H' || final == 'f') { c->cur_r = a - 1; c->cur_c = b > 0 ? b - 1 : 0; }
    else if (final == 'P') delete_cells(c, c->cur_r, c->cur_c, a);""", 'CSI horizontal absolute and delete-character handling')
s = replace_once(s, r"else if (final == 'K') { if (a == 2) clear_cells(c, c->cur_r, 0, c->cols); else clear_cells(c, c->cur_r, c->cur_c, c->cols); }", r"""else if (final == 'K') { if (a == 2) clear_cells(c, c->cur_r, 0, c->cols); else if (a == 1) clear_cells(c, c->cur_r, 0, c->cur_c + 1); else clear_cells(c, c->cur_r, c->cur_c, c->cols); }
    else if (final == 'J') {
        if (a == 2) { for (int r = 0; r < c->rows; r++) clear_cells(c, r, 0, c->cols); }
        else if (a == 1) { for (int r = 0; r < c->cur_r; r++) clear_cells(c, r, 0, c->cols); clear_cells(c, c->cur_r, 0, c->cur_c + 1); }
        else { clear_cells(c, c->cur_r, c->cur_c, c->cols); for (int r = c->cur_r + 1; r < c->rows; r++) clear_cells(c, r, 0, c->cols); }
    }""", 'erase handling')
s = replace_once(s, r"c->cell[c->cur_r][c->cur_c + 1].bytes[0] = 0; c->cell[c->cur_r][c->cur_c + 1].width = 0;", r"c->cell[c->cur_r][c->cur_c + 1].bytes[0] = 0; c->cell[c->cur_r][c->cur_c + 1].width = -1;", 'wide-cell continuation marker')
s = replace_once(s, r"if (w <= 0) return;", r"""if (w <= 0) {
        if (cp >= 0x300 && c->cur_c > 0 && c->cur_r >= 0 && c->cur_r < c->rows) {
            int pc = c->cur_c - 1;
            if (pc < c->cols && c->cell[c->cur_r][pc].bytes[0]) {
                size_t have = strlen(c->cell[c->cur_r][pc].bytes);
                size_t addn = strlen(bytes);
                if (have + addn < CELL_BYTES) memcpy(c->cell[c->cur_r][pc].bytes + have, bytes, addn + 1);
            }
        }
        return;
    }""", 'combining-mark handling')
s = replace_once(s, r"else { out[k++] = ' '; (*w)++; }", r"""else if (c->cell[r][i].width == -1) { }
        else { out[k++] = ' '; (*w)++; }""", 'wide-cell row reconstruction')
s = replace_once(s, """static void feed_byte(Chan *c, unsigned char x) {
    if (c->esc_len) {
        if (c->esc_len < (int)sizeof(c->esc) - 1) c->esc[c->esc_len++] = (char)x;
        if (c->esc_len > 2 && x >= 0x40 && x <= 0x7e) { c->esc[c->esc_len] = 0; parse_esc(c); c->esc_len = 0; }
        return;
    }
    if (x == 27) { c->esc[0] = 27; c->esc_len = 1; return; }""", """static void feed_byte(Chan *c, unsigned char x) {
    if (c->osc) {
        if (c->osc_esc) {
            if (x == '\\\\') { c->osc = 0; c->osc_esc = 0; }
            else c->osc_esc = (x == 27);
        } else if (x == 7) c->osc = 0;
        else if (x == 27) c->osc_esc = 1;
        return;
    }
    if (c->esc_len) {
        if (c->esc_len == 1 && x == ']') { c->osc = 1; c->osc_esc = 0; c->esc_len = 0; return; }
        if (c->esc_len < (int)sizeof(c->esc) - 1) c->esc[c->esc_len++] = (char)x;
        if (c->esc_len > 2 && x >= 0x40 && x <= 0x7e) { c->esc[c->esc_len] = 0; parse_esc(c); c->esc_len = 0; }
        return;
    }
    if (x == 27) { c->esc[0] = 27; c->esc_len = 1; return; }""", 'OSC escape handling')
s = replace_once(s, """
    c->esc_len = 0;
    c->utf_need = 0;
    c->utf_have = 0;
}

static Chan *channel""", """
}

static Chan *channel""", 'cross-record parser state')
s = replace_once(s, r"""if (sscanf(p, "SESSION %31s", sid) == 1) { char *lab = strchr(p, ' '); if (lab) { while (*lab == ' ') lab++; lab = strchr(lab, ' '); } session(r, sid, lab ? lab + 1 : sid); }""", r"""if (sscanf(p, "SESSION %31s", sid) == 1) { char *lab = strchr(p, ' '); if (lab) { while (*lab == ' ') lab++; lab = strchr(lab, ' '); } if (lab) { lab++; lab[strcspn(lab, "\r\n")] = 0; while (*lab == ' ') lab++; } session(r, sid, (lab && *lab) ? lab : sid); }""", 'session label parsing')
s = replace_once(s, r"""char *url_decode_alloc(const char *s) {
    char *o = malloc(strlen(s) + 1);
    char *p = o;
    while (*s) {
        *p++ = *s++;
    }
    *p = 0;
    return o;
}
""", r"""char *url_decode_alloc(const char *s) {
    char *o = malloc(strlen(s) + 1);
    char *p = o;
    while (*s) {
        if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            *p++ = (char)((hx((unsigned char)s[1]) << 4) | hx((unsigned char)s[2]));
            s += 3;
        } else if (*s == '+') {
            *p++ = ' ';
            s++;
        } else {
            *p++ = *s++;
        }
    }
    *p = 0;
    return o;
}
""", 'URL decoding')
path.write_text(s)

path = Path('/app/web/app.js')
s = path.read_text()
s = replace_once(s, "const out = Object.assign({}, lastData, { rows: lastData.rows });", "const out = Object.assign({}, lastData, { rows: visibleRows() });", 'visible JSON export')
path.write_text(s)
PY
make -C /app clean
make -C /app
