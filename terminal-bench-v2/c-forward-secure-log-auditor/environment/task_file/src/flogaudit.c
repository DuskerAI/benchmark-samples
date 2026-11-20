#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <openssl/hmac.h>
#include <openssl/sha.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define POLICY_LIMIT (64U * 1024U)
#define LOG_LIMIT (1024U * 1024U)
#define LINE_LIMIT 4096U
#define MAX_KEYS 32U
#define MAX_LEAVES 65536U

typedef struct {
    char stream[33];
    unsigned char genesis[32];
    uint64_t max_tail;
    unsigned char keys[MAX_KEYS][32];
    size_t key_count;
} Policy;

typedef struct {
    uint64_t accepted;
    uint64_t seq;
    uint64_t epoch;
    uint64_t last_checkpoint;
    uint64_t last_timestamp;
    int have_timestamp;
    int final_checkpoint;
    unsigned char chain[32];
    unsigned char leaves[MAX_LEAVES][32];
    uint64_t leaf_seq[MAX_LEAVES];
    size_t leaf_count;
} State;

static const unsigned char RECORD_DOMAIN[] = "FLOG-RECORD\0";
static const unsigned char MAC_DOMAIN[] = "FLOG-MAC\0";
static const unsigned char KEY_DOMAIN[] = "FLOG-KEY\0";
static const unsigned char NODE_DOMAIN[] = "FLOG-NODE\0";

static int read_file(const char *path, size_t limit, unsigned char **out, size_t *len) {
    struct stat st;
    FILE *f;
    unsigned char *p;
    size_t n;
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 0 || (uint64_t)st.st_size > limit) return 0;
    f = fopen(path, "rb");
    if (!f) return 0;
    p = malloc((size_t)st.st_size + 1U);
    if (!p) { fclose(f); return 0; }
    n = fread(p, 1, (size_t)st.st_size, f);
    if (n != (size_t)st.st_size || ferror(f)) { free(p); fclose(f); return 0; }
    fclose(f);
    p[n] = 0;
    *out = p;
    *len = n;
    return 1;
}

static int same_file(const char *a, const char *b) {
    struct stat sa, sb;
    if (strcmp(a, b) == 0) return 1;
    if (stat(a, &sa) != 0 || stat(b, &sb) != 0) return 0;
    return sa.st_dev == sb.st_dev && sa.st_ino == sb.st_ino;
}

static int decimal(const char *s, uint64_t max, uint64_t *value) {
    uint64_t v = 0;
    const unsigned char *p = (const unsigned char *)s;
    if (!*p || (*p == '0' && p[1])) return 0;
    while (*p) {
        unsigned digit;
        if (*p < '0' || *p > '9') return 0;
        digit = (unsigned)(*p - '0');
        if (v > (max - digit) / 10U) return 0;
        v = v * 10U + digit;
        p++;
    }
    *value = v;
    return 1;
}

static int hex_digit(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int unhex(const char *s, unsigned char *out, size_t bytes) {
    size_t i;
    if (strlen(s) != bytes * 2U) return 0;
    for (i = 0; i < bytes; i++) {
        int a = hex_digit((unsigned char)s[2U * i]);
        int b = hex_digit((unsigned char)s[2U * i + 1U]);
        if (a < 0 || b < 0) return 0;
        out[i] = (unsigned char)((a << 4) | b);
    }
    return 1;
}

static void tohex(const unsigned char *in, size_t n, char *out) {
    static const char h[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < n; i++) {
        out[2U * i] = h[in[i] >> 4];
        out[2U * i + 1U] = h[in[i] & 15U];
    }
    out[2U * n] = '\0';
}

static size_t fields(char *line, char **part, size_t cap) {
    size_t n = 0;
    char *p = line;
    if (!cap) return 0;
    part[n++] = p;
    while (*p) {
        if (*p == '\t') {
            *p = '\0';
            if (n == cap) return cap + 1U;
            part[n++] = p + 1;
        }
        p++;
    }
    return n;
}

static int valid_stream(const char *s) {
    size_t i, n = strlen(s);
    if (n < 1U || n > 32U) return 0;
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return 0;
    }
    return 1;
}

static int parse_policy(unsigned char *data, size_t len, Policy *p) {
    char *lines[40], *q;
    size_t count = 0, i;
    uint64_t epoch;
    memset(p, 0, sizeof(*p));
    if (!len || data[len - 1U] != '\n' || memchr(data, '\0', len) || memchr(data, '\r', len)) return 0;
    data[len - 1U] = '\0';
    q = (char *)data;
    lines[count++] = q;
    for (i = 0; i + 1U < len; i++) {
        if (data[i] == '\n') {
            data[i] = '\0';
            if (count >= 40U) return 0;
            lines[count++] = (char *)data + i + 1U;
        }
    }
    if (count < 6U || strcmp(lines[0], "FSPOLICY\t1") || strncmp(lines[1], "STREAM\t", 7U) ||
        strncmp(lines[2], "GENESIS\t", 8U) || strncmp(lines[3], "MAX_TAIL\t", 9U) || strcmp(lines[count - 1U], "END")) return 0;
    if (!valid_stream(lines[1] + 7U)) return 0;
    strcpy(p->stream, lines[1] + 7U);
    if (!unhex(lines[2] + 8U, p->genesis, 32U)) return 0;
    if (!decimal(lines[3] + 9U, 4096U, &p->max_tail)) return 0;
    p->key_count = count - 5U;
    if (!p->key_count || p->key_count > MAX_KEYS) return 0;
    for (i = 0; i < p->key_count; i++) {
        char *part[4];
        char line[256];
        size_t nf;
        if (strlen(lines[4U + i]) >= sizeof(line)) return 0;
        strcpy(line, lines[4U + i]);
        nf = fields(line, part, 4U);
        if (nf != 3U || strcmp(part[0], "KEY") || !decimal(part[1], MAX_KEYS - 1U, &epoch) || epoch != i ||
            !unhex(part[2], p->keys[i], 32U)) return 0;
    }
    return 1;
}

static void u32be(uint32_t v, unsigned char b[4]) {
    b[0] = (unsigned char)(v >> 24); b[1] = (unsigned char)(v >> 16);
    b[2] = (unsigned char)(v >> 8); b[3] = (unsigned char)v;
}

static void u64be(uint64_t v, unsigned char b[8]) {
    int i;
    for (i = 7; i >= 0; i--) { b[i] = (unsigned char)v; v >>= 8; }
}

static void record_hash(const unsigned char prev[32], const char *body, unsigned char out[32]) {
    SHA256_CTX c;
    unsigned char n[4];
    size_t len = strlen(body);
    u32be((uint32_t)len, n);
    SHA256_Init(&c);
    SHA256_Update(&c, RECORD_DOMAIN, sizeof(RECORD_DOMAIN) - 1U);
    SHA256_Update(&c, prev, 32U);
    SHA256_Update(&c, n, 4U);
    SHA256_Update(&c, body, len);
    SHA256_Final(out, &c);
}

static void record_mac(const Policy *p, uint64_t epoch, const unsigned char hash[32], unsigned char out[32]) {
    unsigned char message[128];
    size_t d = sizeof(MAC_DOMAIN) - 1U, s = strlen(p->stream), n = 0;
    unsigned out_len = 0;
    memcpy(message + n, MAC_DOMAIN, d); n += d;
    memcpy(message + n, p->stream, s); n += s;
    message[n++] = 0;
    memcpy(message + n, hash, 32U); n += 32U;
    HMAC(EVP_sha256(), p->keys[epoch], 32, message, n, out, &out_len);
}

static void key_commit(uint64_t epoch, const unsigned char key[32], unsigned char out[32]) {
    SHA256_CTX c;
    unsigned char e[8];
    u64be(epoch, e);
    SHA256_Init(&c);
    SHA256_Update(&c, KEY_DOMAIN, sizeof(KEY_DOMAIN) - 1U);
    SHA256_Update(&c, e, 8U);
    SHA256_Update(&c, key, 32U);
    SHA256_Final(out, &c);
}

static void merkle(unsigned char leaves[MAX_LEAVES][32], size_t count, unsigned char out[32]) {
    unsigned char (*work)[32] = malloc(count * 32U);
    size_t n = count, i, j;
    memcpy(work, leaves, count * 32U);
    while (n > 1U) {
        for (i = 0, j = 0; i < n; i += 2U, j++) {
            SHA256_CTX c;
            SHA256_Init(&c);
            SHA256_Update(&c, NODE_DOMAIN, sizeof(NODE_DOMAIN) - 1U);
            SHA256_Update(&c, work[i], 32U);
            SHA256_Update(&c, i + 1U < n ? work[i + 1U] : work[i], 32U);
            SHA256_Final(work[j], &c);
        }
        n = j;
    }
    memcpy(out, work[0], 32U);
    free(work);
}

static int ascii_line(const unsigned char *s, size_t n) {
    size_t i;
    if (!n || n > LINE_LIMIT) return 0;
    for (i = 0; i < n; i++) if (s[i] != '\t' && (s[i] < 0x20U || s[i] > 0x7eU)) return 0;
    return 1;
}

static const char *apply_line(const Policy *p, State *st, const unsigned char *raw, size_t raw_len) {
    char line[LINE_LIMIT + 1U], original[LINE_LIMIT + 1U], body[LINE_LIMIT + 1U];
    char *f[10];
    size_t nf, body_fields, i, used;
    uint64_t seq, epoch, a = 0, b = 0, c = 0;
    unsigned char prev[32], given_mac[32], hash[32], want_mac[32], value[32], payload[512];
    int kind;
    if (!ascii_line(raw, raw_len)) return "FORMAT";
    memcpy(line, raw, raw_len); line[raw_len] = '\0';
    memcpy(original, line, raw_len + 1U);
    nf = fields(line, f, 10U);
    if (!strcmp(f[0], "E")) { kind = 1; body_fields = 5U; if (nf != 7U) return "FORMAT"; }
    else if (!strcmp(f[0], "R")) { kind = 2; body_fields = 5U; if (nf != 7U) return "FORMAT"; }
    else if (!strcmp(f[0], "C")) { kind = 3; body_fields = 7U; if (nf != 9U) return "FORMAT"; }
    else return "FORMAT";
    if (!decimal(f[1], UINT64_MAX, &seq) || !decimal(f[2], MAX_KEYS - 1U, &epoch)) return "FORMAT";
    if (kind == 1) {
        size_t payload_len = strlen(f[4]);
        if (!decimal(f[3], INT64_MAX, &a) || payload_len < 2U || payload_len > 1024U || payload_len % 2U || !unhex(f[4], payload, payload_len / 2U)) return "FORMAT";
    } else if (kind == 2) {
        if (!decimal(f[3], MAX_KEYS - 1U, &a) || !unhex(f[4], value, 32U)) return "FORMAT";
    } else {
        if (!decimal(f[3], UINT64_MAX, &a) || !decimal(f[4], UINT64_MAX, &b) ||
            !decimal(f[5], MAX_LEAVES, &c) || !unhex(f[6], value, 32U)) return "FORMAT";
    }
    if (!unhex(f[body_fields], prev, 32U) || !unhex(f[body_fields + 1U], given_mac, 32U)) return "FORMAT";
    used = 0;
    for (i = 0; i < body_fields; i++) {
        size_t z = strlen(f[i]);
        if (used + z + (i ? 1U : 0U) >= sizeof(body)) return "FORMAT";
        if (i) body[used++] = '\t';
        memcpy(body + used, f[i], z); used += z;
    }
    body[used] = '\0';
    if (strncmp(original, body, used) || original[used] != '\t') return "FORMAT";
    if (seq != st->seq + 1U) return "SEQUENCE";
    if (epoch != st->epoch || epoch >= p->key_count) return "EPOCH";
    if (memcmp(prev, st->chain, 32U)) return "PREVIOUS";
    record_hash(st->chain, body, hash);
    record_mac(p, kind == 2 && a < p->key_count ? a : st->epoch, hash, want_mac);
    if (memcmp(given_mac, want_mac, 32U)) return "AUTH";
    if (kind == 1 && st->have_timestamp && a < st->last_timestamp) return "TIMESTAMP";
    if (kind == 2) {
        unsigned char expected[32];
        if (a != st->epoch + 1U || a >= p->key_count) return "ROTATION";
        key_commit(a, p->keys[a], expected);
        if (memcmp(value, expected, 32U)) return "ROTATION";
    }
    if (kind == 3) {
        unsigned char root[32];
        if (!st->leaf_count || c != st->leaf_count || a != st->leaf_seq[0] || b != st->leaf_seq[st->leaf_count - 1U]) return "CHECKPOINT";
        merkle(st->leaves, st->leaf_count, root);
        if (memcmp(value, root, 32U)) return "CHECKPOINT";
    }
    memcpy(st->chain, hash, 32U);
    st->seq = seq;
    st->accepted++;
    st->final_checkpoint = kind == 3;
    if (kind == 1) { st->last_timestamp = a; st->have_timestamp = 1; }
    if (kind == 1 || kind == 2) {
        if (st->leaf_count >= MAX_LEAVES) return "FORMAT";
        memcpy(st->leaves[st->leaf_count], hash, 32U);
        st->leaf_seq[st->leaf_count++] = seq;
    }
    if (kind == 2) st->epoch = a;
    if (kind == 3) {
        if (st->last_checkpoint == 0) st->leaf_count = 0;
        st->last_checkpoint = seq;
    }
    return NULL;
}

static int write_report(const char *path, int admitted, const char *reason, const Policy *p,
                        const State *st, size_t tail) {
    char tmp[4096], chain[65];
    FILE *f;
    int n;
    tohex(st->chain, 32U, chain);
    n = snprintf(tmp, sizeof(tmp), "%s.tmp.%ld", path, (long)getpid());
    if (n < 0 || (size_t)n >= sizeof(tmp)) return 0;
    f = fopen(tmp, "wb");
    if (!f) return 0;
    if (fprintf(f, "{\"admitted\":%s,\"reason\":\"%s\",\"stream\":\"%s\",\"committed_records\":%" PRIu64
                   ",\"last_sequence\":%" PRIu64 ",\"epoch\":%" PRIu64 ",\"chain_hash\":\"%s\",\"last_checkpoint\":%" PRIu64
                   ",\"recovered_tail_bytes\":%zu}\n",
                admitted ? "true" : "false", reason, p->stream, st->accepted, st->seq, st->epoch,
                chain, st->last_checkpoint, tail) < 0 || fclose(f) != 0) {
        unlink(tmp); return 0;
    }
    if (rename(tmp, path) != 0) { unlink(tmp); return 0; }
    return 1;
}

int main(int argc, char **argv) {
    const char *policy_path = NULL, *log_path = NULL, *report_path = NULL, *reason = NULL;
    unsigned char *policy_data = NULL, *log_data = NULL;
    size_t policy_len = 0, log_len = 0, committed_len, tail = 0, start = 0, i;
    Policy policy;
    State state;
    int admitted = 0;
    if (argc == 7 && !strcmp(argv[1], "--policy") && !strcmp(argv[3], "--log") && !strcmp(argv[5], "--report")) {
        policy_path = argv[2]; log_path = argv[4]; report_path = argv[6];
    } else return 64;
    if (same_file(policy_path, log_path) || same_file(policy_path, report_path) || same_file(log_path, report_path)) return 64;
    if (!read_file(policy_path, POLICY_LIMIT, &policy_data, &policy_len) || !read_file(log_path, LOG_LIMIT, &log_data, &log_len)) {
        free(policy_data); free(log_data); return 64;
    }
    memset(&policy, 0, sizeof(policy));
    memset(&state, 0, sizeof(state));
    committed_len = log_len;
    if (log_len && log_data[log_len - 1U] != '\n') {
        for (i = log_len; i > 0 && log_data[i - 1U] != '\n'; i--) { }
        committed_len = i;
        tail = log_len - i;
    }
    if (!parse_policy(policy_data, policy_len, &policy)) {
        memset(&policy, 0, sizeof(policy));
        reason = "POLICY";
    } else {
        memcpy(state.chain, policy.genesis, 32U);
        for (i = 0; !reason && i < committed_len; i++) {
            if (log_data[i] == '\n') {
                reason = apply_line(&policy, &state, log_data + start, i - start);
                start = i + 1U;
            }
        }
        if (!reason && tail) reason = "FORMAT";
        if (!reason && tail > policy.max_tail) reason = "TAIL";
        if (!reason && (!state.accepted || !state.final_checkpoint || state.leaf_count)) reason = "UNSEALED";
        if (!reason) { reason = "OK"; admitted = 1; }
    }
    if (!write_report(report_path, admitted, reason, &policy, &state, tail)) {
        free(policy_data); free(log_data); return 64;
    }
    free(policy_data); free(log_data);
    return admitted ? 0 : 2;
}
