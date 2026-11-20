#!/bin/bash
set -euo pipefail
mkdir -p /app/bin
cat > /app/bin/go_official <<'SH'
#!/bin/sh
exec swipl -q -s /app/src/arbiter.pl -- "$@"
SH
chmod 0755 /app/bin/go_official
