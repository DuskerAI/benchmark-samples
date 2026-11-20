#!/bin/bash
set -euo pipefail

MILESTONE="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --milestone)
      MILESTONE="$2"; shift 2 ;;
    --milestone=*)
      MILESTONE="${1#*=}"; shift ;;
    *)
      MILESTONE="$1"; shift ;;
  esac
done

case "$MILESTONE" in
  1)
    bash /solution/solve1.sh
    ;;
  2)
    bash /solution/solve1.sh
    bash /solution/solve2.sh
    ;;
  3|all)
    bash /solution/solve1.sh
    bash /solution/solve2.sh
    bash /solution/solve3.sh
    ;;
  *)
    echo "Unknown milestone: $MILESTONE"
    exit 1
    ;;
esac
