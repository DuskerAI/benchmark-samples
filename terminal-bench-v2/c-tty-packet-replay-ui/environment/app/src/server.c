#define _GNU_SOURCE
#include "replay.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static void send_all(int fd, const char *s, size_t n) {
    while (n) {
        ssize_t w = write(fd, s, n);
        if (w <= 0) return;
        s += w; n -= (size_t)w;
    }
}

static void reply(int fd, const char *type, const char *body) {
    char h[256];
    snprintf(h, sizeof h, "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", type, strlen(body));
    send_all(fd, h, strlen(h)); send_all(fd, body, strlen(body));
}

static void not_found(int fd) {
    const char *b = "not found\n";
    char h[160];
    snprintf(h, sizeof h, "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", strlen(b));
    send_all(fd, h, strlen(h)); send_all(fd, b, strlen(b));
}

static char *file_body(const char *path) {
    FILE *f = fopen(path, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *p = malloc((size_t)n + 1); if (!p) exit(3);
    fread(p, 1, (size_t)n, f); p[n] = 0; fclose(f); return p;
}

static char *param(const char *q, const char *key) {
    if (!q) return strdup("");
    size_t kn = strlen(key);
    const char *p = q;
    while (*p) {
        if ((p == q || p[-1] == '&') && strncmp(p, key, kn) == 0 && p[kn] == '=') {
            const char *v = p + kn + 1; const char *e = strchr(v, '&'); size_t n = e ? (size_t)(e - v) : strlen(v);
            char *tmp = strndup(v, n); char *d = url_decode_alloc(tmp); free(tmp); return d;
        }
        p = strchr(p, '&'); if (!p) break; p++;
    }
    return strdup("");
}

static void handle(int fd, char *req) {
    char method[16], target[2048];
    if (sscanf(req, "%15s %2047s", method, target) != 2) { not_found(fd); return; }
    char *q = strchr(target, '?'); if (q) *q++ = 0;
    if (strcmp(target, "/") == 0) { char *b = file_body("/app/web/index.html"); if (b) { reply(fd, "text/html; charset=utf-8", b); free(b); } else not_found(fd); }
    else if (strcmp(target, "/app.js") == 0) { char *b = file_body("/app/web/app.js"); if (b) { reply(fd, "application/javascript; charset=utf-8", b); free(b); } else not_found(fd); }
    else if (strcmp(target, "/style.css") == 0) { char *b = file_body("/app/web/style.css"); if (b) { reply(fd, "text/css; charset=utf-8", b); free(b); } else not_found(fd); }
    else if (strcmp(target, "/api/replay") == 0) {
        char *path = param(q, "path"), *query = param(q, "q"), *seek_s = param(q, "seek"), *sf = param(q, "session"), *cf = param(q, "channel"), *lim_s = param(q, "limit");
        long seek = *seek_s ? strtol(seek_s, NULL, 10) : -1; int lim = *lim_s ? atoi(lim_s) : 256; char err[256] = {0};
        char *json = render_capture_json(path, query, seek, sf, cf, lim, err, sizeof err);
        if (!json) { char e[512]; snprintf(e, sizeof e, "{\"error\":\"%s\"}\n", err); reply(fd, "application/json", e); }
        else { reply(fd, "application/json", json); free(json); }
        free(path); free(query); free(seek_s); free(sf); free(cf); free(lim_s);
    } else not_found(fd);
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1"; int port = 8080;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--host") == 0 && i + 1 < argc) host = argv[++i];
        else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) port = atoi(argv[++i]);
    }
    int s = socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a; memset(&a, 0, sizeof a); a.sin_family = AF_INET; a.sin_port = htons((unsigned short)port); inet_pton(AF_INET, host, &a.sin_addr);
    if (bind(s, (struct sockaddr *)&a, sizeof a) || listen(s, 32)) { perror("listen"); return 1; }
    for (;;) {
        int fd = accept(s, NULL, NULL); if (fd < 0) continue;
        char buf[8192]; ssize_t n = read(fd, buf, sizeof buf - 1); if (n > 0) { buf[n] = 0; handle(fd, buf); }
        close(fd);
    }
}
