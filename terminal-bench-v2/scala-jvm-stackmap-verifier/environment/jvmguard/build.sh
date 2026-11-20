#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out bin
scalac -deprecation -feature -d out src/JvmGuard.scala
cat > bin/jvmguard <<'SH'
#!/bin/bash
set -euo pipefail
exec scala -cp /app/jvmguard/out JvmGuard "$@"
SH
chmod +x bin/jvmguard
