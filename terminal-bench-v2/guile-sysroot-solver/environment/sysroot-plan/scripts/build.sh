#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p bin
cat > bin/sysplan <<'EOF'
#!/bin/sh
exec guile -L /app/sysroot-plan/src -s /app/sysroot-plan/src/sysplan.scm "$@"
EOF
chmod +x bin/sysplan
