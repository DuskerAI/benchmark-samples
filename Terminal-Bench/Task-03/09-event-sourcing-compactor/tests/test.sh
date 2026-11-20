#!/bin/bash
set -uo pipefail

mkdir -p /logs/verifier
echo "0" > /logs/verifier/reward.txt

cleanup() {
    if [ ! -f /logs/verifier/reward.txt ]; then
        echo "0" > /logs/verifier/reward.txt
    fi
}
trap cleanup EXIT

echo "=== Waiting for order service ==="
READY=0
for i in $(seq 1 30); do
    if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
        echo "Service ready after ${i}s"
        READY=1
        break
    fi
    sleep 2
done

if [ "${READY}" -eq 0 ]; then
    echo "Service did not start within 60 seconds"
    exit 1
fi

echo "=== Rebuilding TypeScript ==="
cd /app
if ! npm run build 2>&1; then
    echo "Build failed — check TypeScript errors above"
    exit 1
fi

echo "=== Restarting service with new build ==="
for pid_cmdline in /proc/*/cmdline; do
    pid=$(echo "$pid_cmdline" | cut -d/ -f3)
    if [ -f "$pid_cmdline" ] && grep -qa "node /app/dist/index.js" "$pid_cmdline" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
    fi
done
sleep 2

# Service restarts automatically via the while loop in entrypoint.sh
for i in $(seq 1 15); do
    if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
        echo "Service restarted."
        break
    fi
    sleep 1
done

echo "=== Installing uv ==="
curl -LsSf https://astral.sh/uv/0.9.7/install.sh | sh
source $HOME/.local/bin/env

echo "=== Running tests ==="
set +e
uvx \
  --with pytest==8.4.1 \
  --with pytest-json-ctrf==0.3.5 \
  --with requests==2.31.0 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
