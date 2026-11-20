#!/usr/bin/env bash
set -euo pipefail
cd /app/aptplan
rm -rf build
mkdir -p build/classes
find src/main/java -name '*.java' | sort > build/sources.list
javac -encoding UTF-8 -d build/classes @build/sources.list
jar --create --file build/aptplan.jar --main-class aptplan.Main -C build/classes .
