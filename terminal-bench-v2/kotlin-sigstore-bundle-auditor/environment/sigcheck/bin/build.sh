#!/bin/sh
set -eu
cd /app/sigcheck
mkdir -p build
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/default-java}"
export PATH="$JAVA_HOME/bin:$PATH"
kotlinc src/main/kotlin/*.kt -include-runtime -d build/sigcheck.jar
