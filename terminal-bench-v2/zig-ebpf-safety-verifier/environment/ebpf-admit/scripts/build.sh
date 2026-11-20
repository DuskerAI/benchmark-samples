#!/bin/sh
set -eu
mkdir -p /opt/ebpf-admit/bin
zig build-exe /opt/ebpf-admit/src/main.zig -O ReleaseSafe -femit-bin=/opt/ebpf-admit/bin/ebpf-admit
