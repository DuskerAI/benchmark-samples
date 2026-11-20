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
    src/regression.c
)
