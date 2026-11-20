#include "logview.h"
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int keep_running = 1;

static void stop_server(int sig) { (void)sig; keep_running = 0; }

static void urldecode(char *s) {
    char *o = s;
    for (char *p = s; *p; p++) {
        if (*p == '%' && p[1] && p[2]) {
            char h[3] = {p[1], p[2], 0};
            *o++ = (char)strtol(h, NULL, 16);
            p += 2;
        } else if (*p == '+') *o++ = ' ';
        else *o++ = *p;
    }
    *o = 0;
}

static void param(const char *query, const char *name, char *dst, size_t n) {
    dst[0] = 0;
    if (!query) return;
    size_t ln = strlen(name);
    const char *p = query;
    while (*p) {
        if (strncmp(p, name, ln) == 0 && p[ln] == '=') {
            p += ln + 1;
            size_t i = 0;
            while (*p && *p != '&' && i + 1 < n) dst[i++] = *p++;
            dst[i] = 0;
            urldecode(dst);
            return;
        }
        p = strchr(p, '&');
        if (!p) return;
        p++;
    }
}

static char *read_file(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (!buf) { fclose(f); return NULL; }
    *len = fread(buf, 1, (size_t)n, f);
    buf[*len] = 0;
    fclose(f);
    return buf;
}

static void send_resp(int fd, int code, const char *type, const char *body, size_t len) {
    char head[256];
    int n = snprintf(head, sizeof(head), "HTTP/1.1 %d OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n", code, type, len);
    send(fd, head, (size_t)n, 0);
    send(fd, body, len, 0);
}

static void handle(int fd) {
    char req[4096];
    ssize_t n = recv(fd, req, sizeof(req) - 1, 0);
    if (n <= 0) return;
    req[n] = 0;
    char method[16], target[2048];
    if (sscanf(req, "%15s %2047s", method, target) != 2) return;
    if (strcmp(method, "GET") != 0) { send_resp(fd, 405, "text/plain", "method", 6); return; }
    char *query = strchr(target, '?');
    if (query) *query++ = 0;
    if (strcmp(target, "/") == 0) {
        size_t len = 0; char *body = read_file("/app/web/index.html", &len);
        if (!body) send_resp(fd, 404, "text/plain", "missing", 7); else { send_resp(fd, 200, "text/html; charset=utf-8", body, len); free(body); }
    } else if (strcmp(target, "/app.js") == 0) {
        size_t len = 0; char *body = read_file("/app/web/app.js", &len);
        if (!body) send_resp(fd, 404, "text/plain", "missing", 7); else { send_resp(fd, 200, "application/javascript; charset=utf-8", body, len); free(body); }
    } else if (strcmp(target, "/api/render") == 0 || strcmp(target, "/api/export") == 0) {
        char path[1024], q[256];
        param(query, "log", path, sizeof(path));
        param(query, "q", q, sizeof(q));
        if (!path[0]) strcpy(path, "/app/data/session.log");
        char *json = render_log_json(path, q, strcmp(target, "/api/export") == 0);
        if (!json) send_resp(fd, 404, "application/json", "{\"error\":\"not found\"}", 21);
        else { send_resp(fd, 200, "application/json", json, strlen(json)); free(json); }
    } else send_resp(fd, 404, "text/plain", "missing", 7);
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1";
    int port = 8765;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--host") == 0 && i + 1 < argc) host = argv[++i];
        else if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) port = atoi(argv[++i]);
    }
    signal(SIGTERM, stop_server);
    signal(SIGINT, stop_server);
    int s = socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    inet_pton(AF_INET, host, &addr.sin_addr);
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0 || listen(s, 16) != 0) {
        perror("listen");
        return 1;
    }
    while (keep_running) {
        int fd = accept(s, NULL, NULL);
        if (fd < 0) { if (errno == EINTR) continue; break; }
        handle(fd);
        close(fd);
    }
    close(s);
    return 0;
}
