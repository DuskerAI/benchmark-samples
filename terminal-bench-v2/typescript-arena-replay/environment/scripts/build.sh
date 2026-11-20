#!/bin/bash
set -euo pipefail

cd /app/arena
rm -rf dist bin
npm run build >/dev/null
mkdir -p bin
cat > bin/arena-official <<'EOF'
#!/usr/bin/env node
require('/app/arena/dist/index.js');
EOF
chmod +x bin/arena-official
