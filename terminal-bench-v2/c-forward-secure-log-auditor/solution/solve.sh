#!/bin/bash
set -euo pipefail

cd /app/forwardlog

sed -i "/if (c >= 'A' && c <= 'F') return c - 'A' + 10;/d" src/flogaudit.c
sed -i 's/record_mac(p, kind == 2 && a < p->key_count ? a : st->epoch, hash, want_mac);/record_mac(p, st->epoch, hash, want_mac);/' src/flogaudit.c

if ! grep -q 'if (i + 1U == n)' src/flogaudit.c; then
    sed -i '/for (i = 0, j = 0; i < n; i += 2U, j++) {/a\            if (i + 1U == n) { memcpy(work[j], work[i], 32U); continue; }' src/flogaudit.c
fi

sed -i 's/if (st->last_checkpoint == 0) st->leaf_count = 0;/st->leaf_count = 0;/' src/flogaudit.c

sed -i '/if (!reason && tail) reason = "FORMAT";/d' src/flogaudit.c

make clean
make
