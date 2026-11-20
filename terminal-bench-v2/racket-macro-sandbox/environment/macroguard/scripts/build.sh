#!/bin/bash
set -euo pipefail
mkdir -p /app/macroguard/bin
cat > /app/macroguard/bin/macroguard <<'SH'
#!/bin/bash
exec racket /app/macroguard/src/macroguard.rkt "$@"
SH
chmod +x /app/macroguard/bin/macroguard
