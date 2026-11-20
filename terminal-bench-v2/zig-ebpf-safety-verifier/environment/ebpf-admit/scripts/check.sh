#!/bin/sh
set -eu
if [ ! -x /opt/ebpf-admit/bin/ebpf-admit ] || [ /opt/ebpf-admit/src/main.zig -nt /opt/ebpf-admit/bin/ebpf-admit ]; then
    /opt/ebpf-admit/scripts/build.sh >/dev/null
fi
exec /opt/ebpf-admit/bin/ebpf-admit "$1"
