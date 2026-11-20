Conduct an offline security investigation of potentially malicious HTTP/2 header traffic with `/opt/hpackoscope`. Build it using `/opt/hpackoscope/scripts/build.sh`, then run `/opt/hpackoscope/bin/hpackoscope /etc/hpackoscope/runtime.conf`. Each run must recreate the configured output directory deterministically.

Inspect configured-root `*.h2c` captures lexically and their `seq stream type flags hex_payload` records by sequence. Reassemble `HEADERS`/`CONTINUATION` blocks per stream through `END_HEADERS`. Decode RFC 7541 static and dynamic tables, integers, Huffman strings, every literal form, and leading table-size updates bounded by configuration. Table state is per capture and commits only after a valid block. Record malformed fragmentation and invalid or incomplete blocks as evidence, clear only that stream's pending block, and continue. Do not hardcode inputs or outputs.

Write exactly these newline-terminated files under `/var/lib/hpackoscope/output`:
- `headers.jsonl`: `{"capture":string,"stream":int,"ordinal":int,"headers":[{"name":string,"value":string},...]}` per accepted block. Ordinals are zero-based per stream and count accepted blocks only; names are lowercase and decode order is retained.
- `stream_diagnostics.jsonl`: `{"capture":string,"stream":int,"level":"error","code":"decode-error","message":string}` per rejection.
- `security_findings.json`: `{"summary":{"duplicate-pseudo-header":int,"forbidden-header":int,"uppercase-name":int},"findings":[{"capture":string,"stream":int,"ordinal":int,"type":string,"name":string},...]}` in first-occurrence order.

Output must be valid JSON and preserve decoded values. Findings cover uppercase names, repeated pseudo-headers, and `connection`, `keep-alive`, `proxy-connection`, `transfer-encoding`, or `upgrade`. Do not modify `/opt/hpackoscope/fixtures` or `/etc/hpackoscope/runtime.conf`.
