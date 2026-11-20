#!/bin/bash
set -euo pipefail

target=$1
profile=$2
name=$3
abi=$4
limit=$5
endianness=$6
optimization=$7
feature_mask=$8
codec_salt=$9
channel=${10}
dependency_hash=${11}
tier=${12}
audit_salt=${13}
release_hash=${14}
core=${15}
codec=${16}
out=${17}

symbols=$(nm -g --defined-only "$core" "$codec" \
  | awk '{print $3}' \
  | grep '^relkit_' \
  | sort -u \
  | paste -sd, -)

{
  printf 'target=%s\n' "$target"
  printf 'profile=%s\n' "$profile"
  printf 'name=%s\n' "$name"
  printf 'abi=%s\n' "$abi"
  printf 'packet_limit=%s\n' "$limit"
  printf 'endianness=%s\n' "$endianness"
  printf 'optimization=%s\n' "$optimization"
  printf 'feature_mask=%s\n' "$feature_mask"
  printf 'codec_salt=%s\n' "$codec_salt"
  printf 'channel=%s\n' "$channel"
  printf 'tier=%s\n' "$tier"
  printf 'audit_salt=%s\n' "$audit_salt"
  printf 'dependency_hash=%s\n' "$dependency_hash"
  printf 'release_hash=%s\n' "$release_hash"
  printf 'symbols=%s\n' "$symbols"
} > "$out"