cmake_minimum_required(VERSION 3.25)
project(relkit-release NONE)

set(RELKIT_TARGET "" CACHE STRING "Zig target triple")
set(RELKIT_PROFILE "" CACHE STRING "Release profile")
set(RELKIT_TARGET_NAME "" CACHE STRING "Public target name")
set(RELKIT_ABI_HEX "" CACHE STRING "Relkit ABI value")
set(RELKIT_PACKET_LIMIT "" CACHE STRING "Relkit packet limit")
set(RELKIT_ENDIANNESS "little" CACHE STRING "Relkit endianness")
set(RELKIT_OPTIMIZATION "ReleaseSmall" CACHE STRING "Zig optimization mode")
set(RELKIT_FEATURE_MASK "" CACHE STRING "Relkit feature mask")
set(RELKIT_CODEC_SALT "" CACHE STRING "Relkit codec salt")
set(RELKIT_CHANNEL "" CACHE STRING "Release channel")
set(RELKIT_TIER "" CACHE STRING "Release channel tier")
set(RELKIT_AUDIT_SALT "" CACHE STRING "Release audit salt")
set(RELKIT_DEPENDENCY_HASH "0x0000000000000000" CACHE STRING "Dependency hash")
set(RELKIT_RELEASE_HASH "" CACHE STRING "Release hash")
set(RELKIT_VERSION "2.7.4")

foreach(required RELKIT_TARGET RELKIT_PROFILE RELKIT_TARGET_NAME RELKIT_ABI_HEX RELKIT_PACKET_LIMIT RELKIT_ENDIANNESS RELKIT_OPTIMIZATION RELKIT_FEATURE_MASK RELKIT_CODEC_SALT RELKIT_CHANNEL RELKIT_TIER RELKIT_AUDIT_SALT RELKIT_DEPENDENCY_HASH RELKIT_RELEASE_HASH)
  if("${${required}}" STREQUAL "")
    message(FATAL_ERROR "missing required release variable: ${required}")
  endif()
endforeach()

if(NOT RELKIT_TARGET STREQUAL "x86_64-linux-musl" AND NOT RELKIT_TARGET STREQUAL "aarch64-linux-musl")
  message(FATAL_ERROR "unsupported target: ${RELKIT_TARGET}")
endif()

set(GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GENERATED_DIR}")
set(GENERATED_CONFIG "${GENERATED_DIR}/config.zig")
configure_file("${CMAKE_SOURCE_DIR}/src/config.zig.in" "${GENERATED_CONFIG}" @ONLY)
set(GENERATED_CORE "${GENERATED_DIR}/core.zig")
set(GENERATED_MAIN "${GENERATED_DIR}/main.zig")
configure_file("${CMAKE_SOURCE_DIR}/src/core.zig" "${GENERATED_CORE}" COPYONLY)
configure_file("${CMAKE_SOURCE_DIR}/src/main.zig" "${GENERATED_MAIN}" COPYONLY)

set(ARTIFACT_DIR "${CMAKE_BINARY_DIR}/artifacts")
file(MAKE_DIRECTORY "${ARTIFACT_DIR}")

add_custom_command(
  OUTPUT "${ARTIFACT_DIR}/libcodec.a"
  COMMAND zig build-lib -target ${RELKIT_TARGET} -O ${RELKIT_OPTIMIZATION} -static -femit-bin=${ARTIFACT_DIR}/libcodec.a "${CMAKE_SOURCE_DIR}/src/codec.zig"
  DEPENDS "${CMAKE_SOURCE_DIR}/src/codec.zig"
  VERBATIM)

add_custom_command(
  OUTPUT "${ARTIFACT_DIR}/libcore.a"
  COMMAND zig build-lib -target ${RELKIT_TARGET} -O ${RELKIT_OPTIMIZATION} -static -femit-bin=${ARTIFACT_DIR}/libcore.a "${GENERATED_CORE}"
  DEPENDS "${CMAKE_SOURCE_DIR}/src/core.zig" "${GENERATED_CORE}" "${GENERATED_CONFIG}" "${ARTIFACT_DIR}/libcodec.a"
  VERBATIM)

add_custom_command(
  OUTPUT "${ARTIFACT_DIR}/relay"
  COMMAND zig build-exe -target ${RELKIT_TARGET} -O ${RELKIT_OPTIMIZATION} -femit-bin=${ARTIFACT_DIR}/relay "${GENERATED_MAIN}" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a"
  DEPENDS "${CMAKE_SOURCE_DIR}/src/main.zig" "${GENERATED_MAIN}" "${GENERATED_CONFIG}" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a"
  VERBATIM)

add_custom_command(
  OUTPUT "${ARTIFACT_DIR}/abi.txt"
  COMMAND "${CMAKE_SOURCE_DIR}/tools/write-abi.sh" "${RELKIT_TARGET}" "${RELKIT_PROFILE}" "${RELKIT_TARGET_NAME}" "${RELKIT_ABI_HEX}" "${RELKIT_PACKET_LIMIT}" "${RELKIT_ENDIANNESS}" "${RELKIT_OPTIMIZATION}" "${RELKIT_FEATURE_MASK}" "${RELKIT_CODEC_SALT}" "${RELKIT_CHANNEL}" "${RELKIT_DEPENDENCY_HASH}" "${RELKIT_TIER}" "${RELKIT_AUDIT_SALT}" "${RELKIT_RELEASE_HASH}" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a" "${ARTIFACT_DIR}/abi.txt"
  DEPENDS "${CMAKE_SOURCE_DIR}/tools/write-abi.sh" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a"
  VERBATIM)

add_custom_target(release_artifacts ALL
  DEPENDS "${ARTIFACT_DIR}/libcodec.a" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/relay" "${ARTIFACT_DIR}/abi.txt")