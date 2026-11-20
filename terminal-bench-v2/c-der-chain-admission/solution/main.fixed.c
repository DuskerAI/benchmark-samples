#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <openssl/sha.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#define MAX_CERTS 8
#define MAX_DNS 8

typedef struct { const unsigned char *p; size_t n; } Slice;
typedef struct {
    uint64_t serial, before, after, path_len;
    char issuer[64], subject[64];
    int is_ca;
    unsigned usage;
    char dns[MAX_DNS][254], permit[MAX_DNS][254], exclude[MAX_DNS][254];
    size_t ndns, npermit, nexclude;
    unsigned char key[32], authority[32], signature[32];
    const unsigned char *body;
    size_t body_len;
} Cert;
typedef struct {
    uint64_t time;
    char trust_subject[64], dns_suffix[254];
    unsigned char trust_key[32];
} Policy;

static int tlv(Slice *in, unsigned tag, Slice *value, size_t *whole) {
    if (in->n < 2 || in->p[0] != tag) return 0;
    size_t pos = 1, len = in->p[pos++];
    if (len & 0x80) {
        size_t count = len & 0x7f;
        if (count == 0 || count > 3 || in->n < pos + count || in->p[pos] == 0) return 0;
        len = 0;
        for (size_t i = 0; i < count; ++i) len = (len << 8) | in->p[pos++];
        if (len < 128) return 0;
    }
    if (len > in->n - pos) return 0;
    value->p = in->p + pos;
    value->n = len;
    if (whole) *whole = pos + len;
    in->p += pos + len;
    in->n -= pos + len;
    return 1;
}

static int der_uint(Slice *in, uint64_t *out) {
    Slice v;
    if (!tlv(in, 0x02, &v, NULL) || v.n == 0 || v.n > 9) return 0;
    if (v.p[0] & 0x80) return 0;
    if (v.n > 1 && v.p[0] == 0 && !(v.p[1] & 0x80)) return 0;
    if (v.n == 9 && (v.p[0] != 0 || !(v.p[1] & 0x80))) return 0;
    uint64_t x = 0;
    size_t start = (v.n == 9) ? 1 : 0;
    for (size_t i = start; i < v.n; ++i) x = (x << 8) | v.p[i];
    *out = x;
    return 1;
}

static int identifier(const char *s) {
    size_t n = strlen(s);
    if (n == 0 || n > 63) return 0;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return 0;
    }
    return 1;
}

static int text_tlv(Slice *in, unsigned tag, char *dst, size_t cap) {
    Slice v;
    if (!tlv(in, tag, &v, NULL) || v.n == 0 || v.n >= cap || memchr(v.p, 0, v.n)) return 0;
    memcpy(dst, v.p, v.n);
    dst[v.n] = 0;
    return 1;
}

static int dns_valid(const char *s) {
    size_t n = strlen(s), label = 0;
    if (n == 0 || n > 253 || s[0] == '.' || s[n - 1] == '.') return 0;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)s[i];
        if (c == '.') {
            if (label == 0 || label > 63 || s[i - 1] == '-') return 0;
            label = 0;
        } else {
            if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-')) return 0;
            if (label == 0 && c == '-') return 0;
            ++label;
        }
    }
    return label > 0 && label <= 63 && s[n - 1] != '-';
}

static int dns_list(Slice *in, unsigned outer_tag, char dst[MAX_DNS][254], size_t *count) {
    Slice outer, seq;
    if (!tlv(in, outer_tag, &outer, NULL)) return 0;
    if (outer_tag != 0x30) {
        if (!tlv(&outer, 0x30, &seq, NULL) || outer.n != 0) return 0;
    } else seq = outer;
    *count = 0;
    while (seq.n) {
        if (*count == MAX_DNS || !text_tlv(&seq, 0x16, dst[*count], 254)) return 0;
        ++*count;
    }
    return 1;
}

static int parse_cert(Slice *bundle, Cert *c) {
    const unsigned char *outer_start = bundle->p;
    Slice seq;
    size_t total;
    memset(c, 0, sizeof(*c));
    if (!tlv(bundle, 0x30, &seq, &total) || total > 4096) return 0;
    const unsigned char *body_start = seq.p;
    uint64_t version;
    if (!der_uint(&seq, &version) || version != 1 ||
        !der_uint(&seq, &c->serial) || c->serial == 0 || c->serial > 2147483647ULL ||
        !text_tlv(&seq, 0x0c, c->issuer, sizeof(c->issuer)) || !identifier(c->issuer) ||
        !text_tlv(&seq, 0x0c, c->subject, sizeof(c->subject)) || !identifier(c->subject)) return 0;
    Slice validity;
    if (!tlv(&seq, 0x30, &validity, NULL) || !der_uint(&validity, &c->before) ||
        !der_uint(&validity, &c->after) || validity.n) return 0;
    Slice ext, v;
    if (!tlv(&seq, 0x30, &ext, NULL) || !tlv(&ext, 0x01, &v, NULL) ||
        v.n != 1 || (v.p[0] != 0x00 && v.p[0] != 0xff)) return 0;
    c->is_ca = v.p[0] == 0xff;
    if (!der_uint(&ext, &c->path_len) || c->path_len > 7 ||
        !tlv(&ext, 0x03, &v, NULL) || v.n != 2 || v.p[0] != 0) return 0;
    c->usage = v.p[1];
    if (!dns_list(&ext, 0x30, c->dns, &c->ndns) ||
        !dns_list(&ext, 0xa0, c->permit, &c->npermit) ||
        !dns_list(&ext, 0xa1, c->exclude, &c->nexclude) || ext.n) return 0;
    if (!tlv(&seq, 0x04, &v, NULL) || v.n != 32) return 0;
    memcpy(c->key, v.p, 32);
    if (!tlv(&seq, 0x04, &v, NULL) || v.n != 32) return 0;
    memcpy(c->authority, v.p, 32);
    c->body = body_start;
    c->body_len = (size_t)(seq.p - body_start);
    if (!tlv(&seq, 0x04, &v, NULL) || v.n != 32 || seq.n) return 0;
    memcpy(c->signature, v.p, 32);
    (void)outer_start;
    return 1;
}

static int hex_nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int parse_u64_decimal(const char *s, uint64_t *out) {
    if (!*s || (*s == '0' && s[1])) return 0;
    uint64_t x = 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9') return 0;
        unsigned d = (unsigned)(*s - '0');
        if (x > ((uint64_t)INT64_MAX - d) / 10U) return 0;
        x = x * 10U + d;
    }
    *out = x;
    return 1;
}

static int parse_policy(const char *path, Policy *p) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    memset(p, 0, sizeof(*p));
    unsigned seen = 0;
    char *line = NULL;
    size_t cap = 0;
    ssize_t n;
    int ok = 1;
    while ((n = getline(&line, &cap, f)) >= 0) {
        if (n > 0 && line[n - 1] == '\n') --n;
        if (n == 0 || line[n - 1] == '\r' || memchr(line, 0, (size_t)n) != NULL) { ok = 0; break; }
        line[n] = 0;
        if (strncmp(line, "time=", 5) == 0 && !(seen & 1)) {
            ok = parse_u64_decimal(line + 5, &p->time); seen |= 1;
        } else if (strncmp(line, "trust_subject=", 14) == 0 && !(seen & 2) && strlen(line + 14) < sizeof(p->trust_subject)) {
            strcpy(p->trust_subject, line + 14); ok = identifier(p->trust_subject); seen |= 2;
        } else if (strncmp(line, "trust_key=", 10) == 0 && !(seen & 4) && strlen(line + 10) == 64) {
            seen |= 4;
            for (size_t i = 0; i < 32 && ok; ++i) {
                int a = hex_nibble(line[10 + i * 2]), b = hex_nibble(line[11 + i * 2]);
                if (a < 0 || b < 0) ok = 0; else p->trust_key[i] = (unsigned char)((a << 4) | b);
            }
        } else if (strncmp(line, "dns_suffix=", 11) == 0 && !(seen & 8) && strlen(line + 11) < sizeof(p->dns_suffix)) {
            strcpy(p->dns_suffix, line + 11); ok = dns_valid(p->dns_suffix); seen |= 8;
        } else ok = 0;
        if (!ok) break;
    }
    free(line);
    if (ferror(f)) ok = 0;
    fclose(f);
    return ok && seen == 15;
}

static unsigned char *read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) || ftell(f) < 0) { fclose(f); return NULL; }
    long end = ftell(f);
    if (fseek(f, 0, SEEK_SET)) { fclose(f); return NULL; }
    unsigned char *buf = malloc((size_t)end + 1);
    if (!buf) { fclose(f); return NULL; }
    if (fread(buf, 1, (size_t)end, f) != (size_t)end || ferror(f)) { free(buf); fclose(f); return NULL; }
    fclose(f);
    *size = (size_t)end;
    return buf;
}

static int suffix_match(const char *name, const char *suffix) {
    size_t n = strlen(name), s = strlen(suffix);
    return n == s ? strcmp(name, suffix) == 0 : n > s && name[n - s - 1] == '.' && strcmp(name + n - s, suffix) == 0;
}

static int cert_constraints(const Cert *ca, const Cert *leaf) {
    for (size_t d = 0; d < leaf->ndns; ++d) {
        int permitted = ca->npermit == 0;
        for (size_t i = 0; i < ca->npermit; ++i) if (suffix_match(leaf->dns[d], ca->permit[i])) permitted = 1;
        if (!permitted) return 0;
        for (size_t i = 0; i < ca->nexclude; ++i) if (suffix_match(leaf->dns[d], ca->exclude[i])) return 0;
    }
    return 1;
}

static int signature_ok(const Cert *c, const unsigned char issuer_key[32]) {
    if (c->body_len > 4096) return 0;
    unsigned char *input = malloc(c->body_len + 32), digest[SHA256_DIGEST_LENGTH];
    if (!input) return 0;
    memcpy(input, c->body, c->body_len);
    memcpy(input + c->body_len, issuer_key, 32);
    SHA256(input, c->body_len + 32, digest);
    free(input);
    return memcmp(digest, c->signature, 32) == 0;
}

static int remove_tree(const char *path) {
    struct stat st;
    if (lstat(path, &st) != 0) return errno == ENOENT;
    if (!S_ISDIR(st.st_mode)) return unlink(path) == 0;
    DIR *d = opendir(path);
    if (!d) return 0;
    struct dirent *e;
    int ok = 1;
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        size_t need = strlen(path) + strlen(e->d_name) + 2;
        char *child = malloc(need);
        if (!child) { ok = 0; break; }
        snprintf(child, need, "%s/%s", path, e->d_name);
        if (!remove_tree(child)) ok = 0;
        free(child);
        if (!ok) break;
    }
    closedir(d);
    return ok && rmdir(path) == 0;
}

static int write_reports(const char *out, const char *decision, const char *code,
                         Cert certs[MAX_CERTS], size_t count, const Policy *policy,
                         const int time_ok[MAX_CERTS], const int sig_ok[MAX_CERTS]) {
    size_t need = strlen(out) + 20;
    char *tmp = malloc(need), *path = malloc(need + 20);
    if (!tmp || !path) { free(tmp); free(path); return 0; }
    snprintf(tmp, need, "%s.certgate-new", out);
    if (!remove_tree(tmp) || mkdir(tmp, 0777) != 0) { free(tmp); free(path); return 0; }
    snprintf(path, need + 20, "%s/decision.json", tmp);
    FILE *f = fopen(path, "w");
    if (!f) goto fail;
    const char *leaf = count ? certs[0].subject : "";
    if (fprintf(f, "{\"decision\":\"%s\",\"code\":\"%s\",\"certificates\":%zu,\"leaf\":\"%s\"}\n",
                decision, code, count, leaf) < 0 || fclose(f) != 0) goto fail_unclosed;
    snprintf(path, need + 20, "%s/trace.tsv", tmp);
    f = fopen(path, "w");
    if (!f) goto fail;
    if (fputs("index\tsubject\tissuer\trole\ttime\tsignature\tconstraints\n", f) < 0) { fclose(f); goto fail; }
    for (size_t i = 0; i < count; ++i) {
        int constraints = i == 0;
        if (i == 0) {
            constraints = 1;
            for (size_t d = 0; d < certs[0].ndns; ++d)
                if (!dns_valid(certs[0].dns[d]) || !suffix_match(certs[0].dns[d], policy->dns_suffix)) constraints = 0;
        } else constraints = cert_constraints(&certs[i], &certs[0]);
        if (fprintf(f, "%zu\t%s\t%s\t%s\t%s\t%s\t%s\n", i, certs[i].subject, certs[i].issuer,
                    i == 0 ? "leaf" : "ca", time_ok[i] ? "ok" : "fail",
                    sig_ok[i] ? "ok" : "fail", constraints ? "ok" : "fail") < 0) { fclose(f); goto fail; }
    }
    if (fclose(f) != 0) goto fail_unclosed;
    struct stat st;
    if (lstat(out, &st) == 0) {
        if (syscall(SYS_renameat2, AT_FDCWD, tmp, AT_FDCWD, out, RENAME_EXCHANGE) != 0) goto fail;
        (void)remove_tree(tmp);
    } else if (errno == ENOENT) {
        if (rename(tmp, out) != 0) goto fail;
    } else goto fail;
    free(tmp); free(path); return 1;
fail:
    remove_tree(tmp);
fail_unclosed:
    free(tmp); free(path); return 0;
}

int main(int argc, char **argv) {
    const char *policy_path = NULL, *chain_path = NULL, *out = NULL;
    if (argc != 7) return 1;
    for (int i = 1; i < argc; i += 2) {
        if (!strcmp(argv[i], "--policy") && !policy_path) policy_path = argv[i + 1];
        else if (!strcmp(argv[i], "--chain") && !chain_path) chain_path = argv[i + 1];
        else if (!strcmp(argv[i], "--out") && !out) out = argv[i + 1];
        else return 1;
    }
    Policy policy;
    if (!policy_path || !chain_path || !out || !parse_policy(policy_path, &policy)) return 1;
    size_t bytes = 0;
    unsigned char *data = read_file(chain_path, &bytes);
    if (!data) return 1;
    Cert certs[MAX_CERTS];
    size_t count = 0;
    Slice bundle = { data, bytes };
    int malformed = bytes == 0 || bytes > 32768;
    while (!malformed && bundle.n) {
        if (count == MAX_CERTS || !parse_cert(&bundle, &certs[count])) malformed = 1;
        else ++count;
    }
    int time_ok[MAX_CERTS] = {0}, sig_ok[MAX_CERTS] = {0};
    for (size_t i = 0; i < count; ++i) {
        time_ok[i] = certs[i].before <= certs[i].after && certs[i].before <= policy.time && policy.time <= certs[i].after;
        const unsigned char *issuer_key = i + 1 < count ? certs[i + 1].key : certs[i].key;
        sig_ok[i] = signature_ok(&certs[i], issuer_key);
    }
    const char *code = malformed ? "DER" : "OK";
    if (!malformed) {
        for (size_t i = 0; i < count && !strcmp(code, "OK"); ++i) {
            int expected_ca = i != 0;
            if (certs[i].is_ca != expected_ca || certs[i].usage != (expected_ca ? 0x04U : 0x80U) ||
                (!expected_ca && (certs[i].path_len != 0 || certs[i].ndns == 0 || certs[i].npermit || certs[i].nexclude)) ||
                (expected_ca && certs[i].ndns != 0)) code = "ROLE";
        }
    }
    if (!strcmp(code, "OK")) {
        for (size_t i = 0; i < count; ++i) for (size_t j = i + 1; j < count; ++j)
            if (certs[i].serial == certs[j].serial || !strcmp(certs[i].subject, certs[j].subject)) code = "DUPLICATE";
    }
    if (!strcmp(code, "OK")) for (size_t i = 0; i < count; ++i) if (!time_ok[i]) code = "TIME";
    if (!strcmp(code, "OK")) {
        for (size_t i = 0; i + 1 < count; ++i)
            if (strcmp(certs[i].issuer, certs[i + 1].subject) || memcmp(certs[i].authority, certs[i + 1].key, 32)) code = "LINK";
        if (count && (strcmp(certs[count - 1].issuer, certs[count - 1].subject) ||
                      memcmp(certs[count - 1].authority, certs[count - 1].key, 32))) code = "LINK";
    }
    if (!strcmp(code, "OK")) for (size_t i = 0; i < count; ++i) if (!sig_ok[i]) code = "SIGNATURE";
    if (!strcmp(code, "OK")) for (size_t i = 1; i < count; ++i) if (certs[i].path_len < i - 1) code = "PATHLEN";
    if (!strcmp(code, "OK")) {
        for (size_t i = 0; i < count; ++i) {
            for (size_t d = 0; d < certs[i].ndns; ++d) if (!dns_valid(certs[i].dns[d])) code = "NAME";
            for (size_t d = 0; d < certs[i].npermit; ++d) if (!dns_valid(certs[i].permit[d])) code = "NAME";
            for (size_t d = 0; d < certs[i].nexclude; ++d) if (!dns_valid(certs[i].exclude[d])) code = "NAME";
        }
        for (size_t d = 0; count && d < certs[0].ndns; ++d) if (!suffix_match(certs[0].dns[d], policy.dns_suffix)) code = "NAME";
        for (size_t i = 1; i < count; ++i) if (!cert_constraints(&certs[i], &certs[0])) code = "NAME";
    }
    if (!strcmp(code, "OK") && (strcmp(certs[count - 1].subject, policy.trust_subject) ||
                                  memcmp(certs[count - 1].key, policy.trust_key, 32))) code = "TRUST";
    const char *decision = !strcmp(code, "OK") ? "ACCEPT" : "REJECT";
    if (!write_reports(out, decision, code, certs, count, &policy, time_ok, sig_ok)) { free(data); return 1; }
    free(data);
    return !strcmp(code, "OK") ? 0 : 2;
}
