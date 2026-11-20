#!/bin/bash
set -euo pipefail
SRC=${1:?source directory required}
OUT=${2:?output directory required}
mkdir -p "$OUT"
cc -fPIC -shared "$SRC/dep.c" -Wl,--version-script,"$SRC/dep.map" -Wl,-soname,libdep.so.1 -o "$OUT/libdep.so"
cc -fPIC -shared "$SRC/alpha.c" -I"$SRC" -L"$OUT" -ldep -Wl,--version-script,"$SRC/alpha.map" -Wl,-soname,libalpha.so.2 -Wl,-rpath,'$ORIGIN' -o "$OUT/libalpha.so"
cc -fPIC -shared "$SRC/beta.c" -Wl,--version-script,"$SRC/beta.map" -Wl,-soname,libbeta.so.1 -o "$OUT/libbeta.so"
cc -fPIC -shared "$SRC/gamma.c" -Wl,--version-script,"$SRC/gamma.map" -Wl,-soname,libgamma.so.1 -o "$OUT/libgamma.so"
