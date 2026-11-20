#ifndef REPLAY_H
#define REPLAY_H

#include <stddef.h>

char *render_capture_json(const char *path, const char *query, long seek, const char *session_filter, const char *channel_filter, int limit, char *err, size_t errlen);
char *url_decode_alloc(const char *s);

#endif
