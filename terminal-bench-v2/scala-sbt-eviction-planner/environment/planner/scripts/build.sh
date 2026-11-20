#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/bin" "$ROOT/target/classes"
stamp="$ROOT/target/classes/.sources.sha256"
sources=("$ROOT"/src/main/scala/planner/*.scala)
current="$(sha256sum "${sources[@]}" | sha256sum | cut -d' ' -f1)"
previous=""
if [ -f "$stamp" ]; then
  previous="$(cat "$stamp")"
fi
if [ "$current" != "$previous" ] || [ ! -d "$ROOT/target/classes/planner" ]; then
  rm -rf "$ROOT/target/classes"/*
  scalac -deprecation -d "$ROOT/target/classes" "${sources[@]}"
  printf '%s\n' "$current" > "$stamp"
fi
cat > "$ROOT/bin/eviction-planner" <<'SH'
#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec scala -cp "$ROOT/target/classes" planner.Main "$@"
SH
chmod +x "$ROOT/bin/eviction-planner"
