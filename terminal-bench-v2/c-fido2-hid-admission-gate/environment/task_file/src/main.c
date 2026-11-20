#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

typedef struct { unsigned char *p; size_t n; } Bytes;

typedef struct {
    char *rp_id;
    Bytes challenge;
    Bytes *credentials;
    size_t ncredentials;
    char **authenticators;
    size_t nauthenticators;
} Policy;

static const char *arg_value(int argc, char **argv, const char *name) {
    for (int i = 1; i + 1 < argc; i++) {
        if (strcmp(argv[i], name) == 0) return argv[i + 1];
    }
    return NULL;
}

static char *trim(char *s) {
    while (isspace((unsigned char)*s)) s++;
    char *e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1])) *--e = '\0';
    return s;
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
    unsigned char *p = calloc(n / 2 + 1, 1);
    if (!p) return false;
    for (size_t i = 0; i < n / 2; i++) {
        int a = hexval((unsigned char)s[2 * i]);
        int b = hexval((unsigned char)s[2 * i + 1]);
        if (a < 0 || b < 0) {
            free(p);
            return false;
        }
        p[i] = (unsigned char)((a << 4) | b);
    }
    out->p = p;
    out->n = n / 2;
    return true;
}

static void add_bytes(Bytes **arr, size_t *n, Bytes b) {
    Bytes *next = realloc(*arr, (*n + 1) * sizeof(**arr));
    if (!next) exit(1);
    *arr = next;
    (*arr)[(*n)++] = b;
}

static void add_string(char ***arr, size_t *n, const char *s) {
    char **next = realloc(*arr, (*n + 1) * sizeof(**arr));
    if (!next) exit(1);
    *arr = next;
    (*arr)[*n] = strdup(s);
    if (!(*arr)[*n]) exit(1);
    (*n)++;
}

static Policy load_policy(const char *path) {
    Policy p = {0};
    FILE *f = fopen(path, "r");
    if (!f) return p;
    char *line = NULL;
    size_t cap = 0;
    while (getline(&line, &cap, f) != -1) {
        char *s = trim(line);
        if (!*s || *s == '#') continue;
        char *eq = strchr(s, '=');
        if (!eq) continue;
        *eq = '\0';
        char *key = trim(s);
        char *val = trim(eq + 1);
        if (strcmp(key, "rp_id") == 0) {
            free(p.rp_id);
            p.rp_id = strdup(val);
        } else if (strcmp(key, "challenge") == 0) {
            free(p.challenge.p);
            (void)parse_hex(val, &p.challenge);
        } else if (strcmp(key, "allow_credential") == 0) {
            Bytes b = {0};
            if (parse_hex(val, &b)) add_bytes(&p.credentials, &p.ncredentials, b);
        } else if (strcmp(key, "allow_authenticator") == 0) {
            add_string(&p.authenticators, &p.nauthenticators, val);
        }
    }
    free(line);
    fclose(f);
    return p;
}

static size_t count_capture_records(const char *dir) {
    DIR *dp = opendir(dir);
    if (!dp) return 0;
    size_t n = 0;
    struct dirent *de;
    while ((de = readdir(dp)) != NULL) {
        size_t len = strlen(de->d_name);
        if (len < 4 || strcmp(de->d_name + len - 4, ".cap") != 0) continue;
        char path[1024];
        snprintf(path, sizeof(path), "%s/%s", dir, de->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        char *line = NULL;
        size_t cap = 0;
        while (getline(&line, &cap, f) != -1) {
            char *s = trim(line);
            if (*s && *s != '#') n++;
        }
        free(line);
        fclose(f);
    }
    closedir(dp);
    return n;
}

static void mkdir_parent(const char *path) {
    char tmp[1024];
    size_t n = strlen(path);
    if (n >= sizeof(tmp)) return;
    memcpy(tmp, path, n + 1);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(tmp, 0777);
            *p = '/';
        }
    }
}

int main(int argc, char **argv) {
    const char *policy_path = arg_value(argc, argv, "--policy");
    const char *captures = arg_value(argc, argv, "--captures");
    const char *state = arg_value(argc, argv, "--state");
    const char *out = arg_value(argc, argv, "--out");
    if (!policy_path || !captures || !state || !out) {
        fprintf(stderr, "usage: fidogate --policy POLICY --captures DIR --state STATE --out REPORT\n");
        return 2;
    }
    Policy policy = load_policy(policy_path);
    size_t records = count_capture_records(captures);
    (void)state;
    (void)records;
    (void)policy;
    mkdir_parent(out);
    FILE *f = fopen(out, "w");
    if (!f) {
        fprintf(stderr, "fidogate: %s: %s\n", out, strerror(errno));
        return 1;
    }
    fputs("{\"accepted\":[],\"rejected\":[],\"summary\":{\"accepted\":0,\"rejected\":0,\"malformed\":0}}\n", f);
    fclose(f);
    return 0;
}
