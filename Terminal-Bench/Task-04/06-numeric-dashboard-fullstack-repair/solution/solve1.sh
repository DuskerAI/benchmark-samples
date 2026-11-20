#!/bin/bash
set -euo pipefail

# Repair 1 & 2: CMakeLists.txt — remove phantom source file, add math linker flag
cat > /app/CMakeLists.txt << 'EOF'
cmake_minimum_required(VERSION 3.10)
project(StatEngine C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

include_directories(${PROJECT_SOURCE_DIR}/include)

add_executable(statengine
    src/main.c
    src/csv_parser.c
    src/stats.c
    src/json_output.c
)

target_link_libraries(statengine m)
EOF

# Repair 3: stats.h/stats.c parameter order — make stats.c match header (definition + call sites)
sed -i 's/double compute_percentile(int n, double\* data, double p)/double compute_percentile(double* data, int n, double p)/' \
    /app/src/stats.c
sed -i 's/compute_percentile(n, data, 25\.0)/compute_percentile(data, n, 25.0)/' /app/src/stats.c
sed -i 's/compute_percentile(n, data, 75\.0)/compute_percentile(data, n, 75.0)/' /app/src/stats.c

# Repair 4: csv_parser.c — wrong include directive
sed -i 's/#include "stats.h"/#include "csv_parser.h"/' /app/src/csv_parser.c

# Repair 5: server.js — wrong default port number
sed -i 's/const PORT = process.env.PORT || 3001/const PORT = process.env.PORT || 3000/' /app/server/server.js

echo "Milestone 1 solved"
