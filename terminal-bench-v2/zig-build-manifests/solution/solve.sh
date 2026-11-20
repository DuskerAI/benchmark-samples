#!/bin/bash
set -euo pipefail

cd /app/relkit

cat > tools/release-hash.zig <<'EOF'
const std = @import("std");

fn fnv64WithSalt(value: []const u8, salt: u32) u64 {
    var h: u64 = 1469598103934665603 ^ @as(u64, salt);
    for (value, 0..) |byte, index| {
        h ^= byte;
        h *%= 1099511628211;
        h +%= @as(u64, salt) << @intCast(index % 13);
    }
    return h;
}

pub fn main() !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const args = try std.process.argsAlloc(arena.allocator());
    if (args.len != 13) return error.InvalidArgument;

    const target = args[1];
    const name = args[2];
    const profile = args[3];
    const channel = args[4];
    const tier = args[5];
    const abi = try std.fmt.parseInt(u32, args[6][2..], 16);
    const packet_limit = try std.fmt.parseInt(u32, args[7], 10);
    const feature_mask = try std.fmt.parseInt(u32, args[8][2..], 16);
    const codec_salt = try std.fmt.parseInt(u32, args[9], 10);
    const audit_salt = try std.fmt.parseInt(u32, args[10], 10);
    const policy_epoch = try std.fmt.parseInt(u32, args[11], 10);
    const dependency_hash = try std.fmt.parseInt(u64, args[12][2..], 16);

    const layout = fnv64WithSalt(target, codec_salt) ^
        fnv64WithSalt(name, codec_salt) ^
        fnv64WithSalt(profile, codec_salt) ^
        fnv64WithSalt(channel, codec_salt) ^
        fnv64WithSalt(tier, codec_salt) ^
        dependency_hash ^
        @as(u64, abi) ^
        (@as(u64, packet_limit) << 8) ^
        (@as(u64, feature_mask) << 32) ^
        (@as(u64, audit_salt) << 17) ^
        (@as(u64, policy_epoch) << 23);
    const release_hash = layout ^ 0x9e3779b97f4a7c15;
    try std.io.getStdOut().writer().print("0x{x:0>16}\n", .{release_hash});
}
EOF

cat > CMakeLists.txt <<'EOF'
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
set(RELKIT_POLICY_EPOCH "" CACHE STRING "Release policy epoch")
set(RELKIT_DEPENDENCY_HASH "0x0000000000000000" CACHE STRING "Dependency hash")
set(RELKIT_RELEASE_HASH "" CACHE STRING "Release hash")
set(RELKIT_POLICY_FINGERPRINT "" CACHE STRING "Policy fingerprint")
set(RELKIT_VERSION "2.7.4")

foreach(required RELKIT_TARGET RELKIT_PROFILE RELKIT_TARGET_NAME RELKIT_ABI_HEX RELKIT_PACKET_LIMIT RELKIT_ENDIANNESS RELKIT_OPTIMIZATION RELKIT_FEATURE_MASK RELKIT_CODEC_SALT RELKIT_CHANNEL RELKIT_TIER RELKIT_AUDIT_SALT RELKIT_POLICY_EPOCH RELKIT_DEPENDENCY_HASH RELKIT_RELEASE_HASH RELKIT_POLICY_FINGERPRINT)
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
  COMMAND "${CMAKE_SOURCE_DIR}/tools/write-abi.sh" "${RELKIT_TARGET}" "${RELKIT_PROFILE}" "${RELKIT_TARGET_NAME}" "${RELKIT_ABI_HEX}" "${RELKIT_PACKET_LIMIT}" "${RELKIT_ENDIANNESS}" "${RELKIT_OPTIMIZATION}" "${RELKIT_FEATURE_MASK}" "${RELKIT_CODEC_SALT}" "${RELKIT_CHANNEL}" "${RELKIT_DEPENDENCY_HASH}" "${RELKIT_TIER}" "${RELKIT_AUDIT_SALT}" "${RELKIT_POLICY_EPOCH}" "${RELKIT_RELEASE_HASH}" "${RELKIT_POLICY_FINGERPRINT}" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a" "${ARTIFACT_DIR}/abi.txt"
  DEPENDS "${CMAKE_SOURCE_DIR}/tools/write-abi.sh" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/libcodec.a"
  VERBATIM)

add_custom_target(release_artifacts ALL
  DEPENDS "${ARTIFACT_DIR}/libcodec.a" "${ARTIFACT_DIR}/libcore.a" "${ARTIFACT_DIR}/relay" "${ARTIFACT_DIR}/abi.txt")
EOF

cat > tools/write-abi.sh <<'EOF'
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
policy_epoch=${14}
release_hash=${15}
policy_fingerprint=${16}
core=${17}
codec=${18}
out=${19}

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
  printf 'policy_epoch=%s\n' "$policy_epoch"
  printf 'dependency_hash=%s\n' "$dependency_hash"
  printf 'release_hash=%s\n' "$release_hash"
  printf 'policy_fingerprint=%s\n' "$policy_fingerprint"
  printf 'symbols=%s\n' "$symbols"
} > "$out"
EOF
chmod +x tools/write-abi.sh

python3 - <<'PY'
from pathlib import Path
p = Path('src/config.zig.in')
text = p.read_text()
text = text.replace('pub const policy_fingerprint: u64 = 0x0000000000000000;', 'pub const policy_fingerprint: u64 = @RELKIT_POLICY_FINGERPRINT@;')
text = text.replace('pub const audit_salt: u32 = @RELKIT_AUDIT_SALT@;\n', 'pub const audit_salt: u32 = @RELKIT_AUDIT_SALT@;\npub const policy_epoch: u32 = @RELKIT_POLICY_EPOCH@;\n')
p.write_text(text)
PY

python3 - <<'PY'
from pathlib import Path
p = Path('src/core.zig')
text = p.read_text()
text = text.replace('(@as(u64, config.audit_salt) << 17);', '(@as(u64, config.audit_salt) << 17) ^\n        (@as(u64, config.policy_epoch) << 23);')
text = text.replace('export fn relkit_policy_fingerprint() u64 {', 'export fn relkit_policy_epoch() u32 {\n    return config.policy_epoch;\n}\n\nexport fn relkit_policy_fingerprint() u64 {')
p.write_text(text)

p = Path('src/main.zig')
text = p.read_text()
text = text.replace('extern fn relkit_policy_fingerprint() u64;', 'extern fn relkit_policy_epoch() u32;\nextern fn relkit_policy_fingerprint() u64;')
text = text.replace('try stdout.print("audit_salt={d}\\n", .{config.audit_salt});', 'try stdout.print("audit_salt={d}\\n", .{config.audit_salt});\n    try stdout.print("policy_epoch={d}\\n", .{relkit_policy_epoch()});')
p.write_text(text)
PY

cat > scripts/release.sh <<'EOF'
#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 || "$1" != "--out" ]]; then
  printf 'usage: %s --out OUTDIR\n' "$0" >&2
  exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out=$2
build_root="$root/build"
artifacts=(abi.txt deps.txt libcodec.a libcore.a relay)
matrix="$root/config/targets.tsv"
profiles_file="$root/config/profiles.tsv"
channels_file="$root/config/channels.tsv"

if [[ ! -f "$matrix" || ! -f "$profiles_file" || ! -f "$channels_file" ]]; then
  printf 'missing release configuration\n' >&2
  exit 1
fi

declare -A channel_tier channel_audit channel_epoch channel_seen
while IFS=$'\t' read -r channel tier audit_salt policy_epoch extra; do
  if [[ "$channel" == "channel" && "$tier" == "tier" ]]; then
    continue
  fi
  [[ -z "${channel:-}" ]] && continue
  if [[ -n "${extra:-}" || ! "$channel" =~ ^[a-z0-9-]+$ || ! "$tier" =~ ^[a-z0-9-]+$ || ! "$audit_salt" =~ ^[1-9][0-9]*$ || ! "$policy_epoch" =~ ^[1-9][0-9]*$ ]]; then
    printf 'invalid channel policy row for %s\n' "$channel" >&2
    exit 1
  fi
  if [[ -n "${channel_seen[$channel]+set}" ]]; then
    printf 'duplicate channel policy row: %s\n' "$channel" >&2
    exit 1
  fi
  channel_seen[$channel]=1
  channel_tier[$channel]="$tier"
  channel_audit[$channel]="$audit_salt"
  channel_epoch[$channel]="$policy_epoch"
done < "$channels_file"

declare -A profile_opt profile_feature profile_salt profile_channel profile_seen
while IFS=$'\t' read -r profile optimization feature_mask codec_salt channel extra; do
  if [[ "$profile" == "profile" && "$optimization" == "optimization" ]]; then
    continue
  fi
  [[ -z "${profile:-}" ]] && continue
  if [[ -n "${extra:-}" || ! "$profile" =~ ^[a-z0-9-]+$ || ! "$optimization" =~ ^Release(Small|Fast|Safe)$ || ! "$feature_mask" =~ ^0x[0-9a-f]{1,8}$ || ! "$codec_salt" =~ ^[1-9][0-9]*$ || ! "$channel" =~ ^[a-z0-9-]+$ ]]; then
    printf 'invalid profile catalog row for %s\n' "$profile" >&2
    exit 1
  fi
  if [[ -n "${profile_seen[$profile]+set}" ]]; then
    printf 'duplicate profile catalog row: %s\n' "$profile" >&2
    exit 1
  fi
  profile_seen[$profile]=1
  if [[ -z "${channel_seen[$channel]+set}" ]]; then
    printf 'missing channel policy row: %s\n' "$channel" >&2
    exit 1
  fi
  profile_opt[$profile]="$optimization"
  profile_feature[$profile]="$feature_mask"
  profile_salt[$profile]="$codec_salt"
  profile_channel[$profile]="$channel"
done < "$profiles_file"

declare -A row_name row_abi row_limit row_endianness row_target row_profile row_deps seen
keys=()
while IFS=$'\t' read -r target profile name abi limit endianness depends_on extra; do
  if [[ "$target" == "target" && "$profile" == "profile" ]]; then
    continue
  fi
  [[ -z "${target:-}" ]] && continue
  if [[ -n "${extra:-}" ]]; then
    printf 'invalid target matrix row for %s/%s\n' "$target" "$profile" >&2
    exit 1
  fi
  case "$target" in
    x86_64-linux-musl|aarch64-linux-musl) ;;
    *) printf 'unsupported target in matrix: %s\n' "$target" >&2; exit 1 ;;
  esac
  if [[ ! "$profile" =~ ^[a-z0-9-]+$ ]]; then
    printf 'invalid profile in matrix: %s\n' "$profile" >&2
    exit 1
  fi
  if [[ -z "${profile_seen[$profile]+set}" ]]; then
    printf 'missing profile catalog row: %s\n' "$profile" >&2
    exit 1
  fi
  if [[ ! "$abi" =~ ^0x[0-9a-f]{1,8}$ || ! "$limit" =~ ^[1-9][0-9]*$ || "$endianness" != "little" ]]; then
    printf 'invalid release metadata for %s/%s\n' "$target" "$profile" >&2
    exit 1
  fi
  key="$target	$profile"
  if [[ -n "${seen[$key]+set}" ]]; then
    printf 'duplicate target profile in matrix: %s %s\n' "$target" "$profile" >&2
    exit 1
  fi
  seen[$key]=1
  keys+=("$key")
  row_target[$key]="$target"
  row_profile[$key]="$profile"
  row_name[$key]="$name"
  row_abi[$key]="$abi"
  row_limit[$key]="$limit"
  row_endianness[$key]="$endianness"
  row_deps[$key]="${depends_on:-}"
done < "$matrix"

if [[ ${#keys[@]} -eq 0 ]]; then
  printf 'target matrix has no data rows\n' >&2
  exit 1
fi

for key in "${keys[@]}"; do
  IFS=',' read -ra deps <<< "${row_deps[$key]}"
  for dep in "${deps[@]}"; do
    [[ -z "$dep" ]] && continue
    if [[ ! "$dep" =~ ^[^:@]+:[^:@]+(@[1-9][0-9]*)?$ ]]; then
      printf 'invalid dependency row: %s\n' "$dep" >&2
      exit 1
    fi
    dep_ref=${dep%@*}
    dep_key=${dep_ref/:/$'\t'}
    if [[ -z "${seen[$dep_key]+set}" ]]; then
      printf 'missing dependency row: %s\n' "$dep" >&2
      exit 1
    fi
    if [[ "$dep_key" == "$key" ]]; then
      printf 'cycle in release matrix: %s\n' "$dep" >&2
      exit 1
    fi
  done
done

mapfile -t keys < <(printf '%s\n' "${keys[@]}" | LC_ALL=C sort)

declare -A visit release_hash dependency_hash
declare -A policy_fingerprint
resolve_key() {
  local key=$1
  if [[ -n "${release_hash[$key]+set}" ]]; then
    return 0
  fi
  if [[ "${visit[$key]:-}" == "1" ]]; then
    printf 'cycle in release matrix: %s\n' "$key" >&2
    exit 1
  fi
  visit[$key]=1
  local dep_hash_num=0
  local deps_text="${row_deps[$key]}"
  local sorted_deps=()
  if [[ -n "$deps_text" ]]; then
    mapfile -t sorted_deps < <(tr ',' '\n' <<< "$deps_text" | python3 -c 'import sys
items=[]
for line in sys.stdin:
    token=line.strip()
    if not token:
        continue
    if "@" in token:
        ref, weight = token.rsplit("@", 1)
        weight = int(weight)
    else:
        ref, weight = token, 1
    items.append((weight, ref, f"{ref}@{weight}"))
for _, _, canonical in sorted(items):
    print(canonical)')
  fi
  for dep in "${sorted_deps[@]}"; do
    local dep_ref=${dep%@*}
    local dep_weight=${dep##*@}
    local dep_key=${dep_ref/:/$'\t'}
    resolve_key "$dep_key"
    dep_hash_num=$(python3 - "$dep_hash_num" "${release_hash[$dep_key]}" "$dep_weight" "$dep" "${profile_salt[${row_profile[$key]}]}" <<'PY'
import sys
acc = int(sys.argv[1], 0) & ((1 << 64) - 1)
value = int(sys.argv[2], 16) & ((1 << 64) - 1)
weight = int(sys.argv[3]) % 64
canonical = sys.argv[4]
salt = int(sys.argv[5])
rot = value if weight == 0 else ((value << weight) | (value >> (64 - weight))) & ((1 << 64) - 1)
h = 1469598103934665603 ^ salt
for index, byte in enumerate(canonical.encode()):
    h ^= byte
    h = (h * 1099511628211) & ((1 << 64) - 1)
    h = (h + (salt << (index % 13))) & ((1 << 64) - 1)
print(f"0x{(acc ^ rot ^ h):016x}")
PY
)
  done
  dependency_hash[$key]=$(python3 - "$dep_hash_num" <<'PY'
import sys
print(f"0x{int(sys.argv[1], 0) & ((1 << 64) - 1):016x}")
PY
)
  channel="${profile_channel[${row_profile[$key]}]}"
  release_hash[$key]=$(zig run "$root/tools/release-hash.zig" -- "${row_target[$key]}" "${row_name[$key]}" "${row_profile[$key]}" "$channel" "${channel_tier[$channel]}" "${row_abi[$key]}" "${row_limit[$key]}" "${profile_feature[${row_profile[$key]}]}" "${profile_salt[${row_profile[$key]}]}" "${channel_audit[$channel]}" "${channel_epoch[$channel]}" "${dependency_hash[$key]}")
  local policy_num=${release_hash[$key]}
  if [[ ${#sorted_deps[@]} -eq 0 ]]; then
    policy_num=$(python3 - "$policy_num" "${row_profile[$key]}|$channel|${channel_tier[$channel]}|${channel_audit[$channel]}|${channel_epoch[$channel]}" "${profile_salt[${row_profile[$key]}]}" <<'PY'
import sys
value = int(sys.argv[1], 16)
payload = sys.argv[2]
salt = int(sys.argv[3])
h = 1469598103934665603 ^ salt
for index, byte in enumerate(payload.encode()):
    h ^= byte
    h = (h * 1099511628211) & ((1 << 64) - 1)
    h = (h + (salt << (index % 13))) & ((1 << 64) - 1)
print(f"0x{(value ^ h) & ((1 << 64) - 1):016x}")
PY
)
  else
    for dep in "${sorted_deps[@]}"; do
      local dep_ref=${dep%@*}
      local dep_weight=${dep##*@}
      local dep_key=${dep_ref/:/$'\t'}
      local dep_profile=${row_profile[$dep_key]}
      local dep_channel=${profile_channel[$dep_profile]}
      policy_num=$(python3 - "$policy_num" "$dep_weight" "$dep|$dep_channel|${channel_tier[$dep_channel]}|${channel_audit[$dep_channel]}|${channel_epoch[$dep_channel]}" "${profile_salt[${row_profile[$key]}]}" <<'PY'
import sys
value = int(sys.argv[1], 16) & ((1 << 64) - 1)
weight = int(sys.argv[2]) % 64
payload = sys.argv[3]
salt = int(sys.argv[4])
h = 1469598103934665603 ^ salt
for index, byte in enumerate(payload.encode()):
    h ^= byte
    h = (h * 1099511628211) & ((1 << 64) - 1)
    h = (h + (salt << (index % 13))) & ((1 << 64) - 1)
value ^= h
rot = value if weight == 0 else ((value << weight) | (value >> (64 - weight))) & ((1 << 64) - 1)
print(f"0x{rot:016x}")
PY
)
    done
  fi
  policy_fingerprint[$key]=$policy_num
  visit[$key]=2
}

for key in "${keys[@]}"; do
  resolve_key "$key"
done

rm -rf "$out" "$build_root" "$root/src/config.zig"
mkdir -p "$out"

for key in "${keys[@]}"; do
  target=${row_target[$key]}
  profile=${row_profile[$key]}
  dir_name="$target.$profile"
  build_dir="$build_root/$dir_name"
  cmake -S "$root" -B "$build_dir" -G Ninja \
    -DRELKIT_TARGET="$target" \
    -DRELKIT_PROFILE="$profile" \
    -DRELKIT_TARGET_NAME="${row_name[$key]}" \
    -DRELKIT_ABI_HEX="${row_abi[$key]}" \
    -DRELKIT_PACKET_LIMIT="${row_limit[$key]}" \
    -DRELKIT_ENDIANNESS="${row_endianness[$key]}" \
    -DRELKIT_OPTIMIZATION="${profile_opt[$profile]}" \
    -DRELKIT_FEATURE_MASK="${profile_feature[$profile]}" \
    -DRELKIT_CODEC_SALT="${profile_salt[$profile]}" \
    -DRELKIT_CHANNEL="${profile_channel[$profile]}" \
    -DRELKIT_TIER="${channel_tier[${profile_channel[$profile]}]}" \
    -DRELKIT_AUDIT_SALT="${channel_audit[${profile_channel[$profile]}]}" \
    -DRELKIT_POLICY_EPOCH="${channel_epoch[${profile_channel[$profile]}]}" \
    -DRELKIT_DEPENDENCY_HASH="${dependency_hash[$key]}" \
    -DRELKIT_RELEASE_HASH="${release_hash[$key]}" \
    -DRELKIT_POLICY_FINGERPRINT="${policy_fingerprint[$key]}"
  cmake --build "$build_dir"
  mkdir -p "$out/$dir_name"
  for artifact in abi.txt libcodec.a libcore.a relay; do
    cp "$build_dir/artifacts/$artifact" "$out/$dir_name/$artifact"
  done
  if [[ -n "${row_deps[$key]}" ]]; then
    tr ',' '\n' <<< "${row_deps[$key]}" | python3 -c 'import sys
items=[]
for line in sys.stdin:
    token=line.strip()
    if not token:
        continue
    if "@" in token:
        ref, weight = token.rsplit("@", 1)
        weight = int(weight)
    else:
        ref, weight = token, 1
    items.append((weight, ref, f"{ref}@{weight}"))
for _, _, canonical in sorted(items):
    print(canonical)' > "$out/$dir_name/deps.txt"
  else
    : > "$out/$dir_name/deps.txt"
  fi
  printf 'dependency_hash=%s\n' "${dependency_hash[$key]}" >> "$out/$dir_name/deps.txt"
done

manifest="$out/release-manifest.tsv"
printf 'target\tprofile\tartifact\tsha256\tsize\tabi\tpacket_limit\tfeature_mask\tcodec_salt\tchannel\ttier\taudit_salt\tpolicy_epoch\tdependency_hash\trelease_hash\tpolicy_fingerprint\n' > "$manifest"
for key in "${keys[@]}"; do
  target=${row_target[$key]}
  profile=${row_profile[$key]}
  dir_name="$target.$profile"
  abi=$(awk -F= '/^abi=/{print $2}' "$out/$dir_name/abi.txt")
  limit=$(awk -F= '/^packet_limit=/{print $2}' "$out/$dir_name/abi.txt")
  feature=$(awk -F= '/^feature_mask=/{print $2}' "$out/$dir_name/abi.txt")
  salt=$(awk -F= '/^codec_salt=/{print $2}' "$out/$dir_name/abi.txt")
  channel=$(awk -F= '/^channel=/{print $2}' "$out/$dir_name/abi.txt")
  tier=$(awk -F= '/^tier=/{print $2}' "$out/$dir_name/abi.txt")
  audit_salt=$(awk -F= '/^audit_salt=/{print $2}' "$out/$dir_name/abi.txt")
  policy_epoch=$(awk -F= '/^policy_epoch=/{print $2}' "$out/$dir_name/abi.txt")
  dep_hash=$(awk -F= '/^dependency_hash=/{print $2}' "$out/$dir_name/abi.txt")
  row_hash=$(awk -F= '/^release_hash=/{print $2}' "$out/$dir_name/abi.txt")
  policy=$(awk -F= '/^policy_fingerprint=/{print $2}' "$out/$dir_name/abi.txt")
  for artifact in "${artifacts[@]}"; do
    file="$out/$dir_name/$artifact"
    sha=$(sha256sum "$file" | awk '{print $1}')
    size=$(stat -c '%s' "$file")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$target" "$profile" "$artifact" "$sha" "$size" "$abi" "$limit" "$feature" "$salt" "$channel" "$tier" "$audit_salt" "$policy_epoch" "$dep_hash" "$row_hash" "$policy" >> "$manifest"
  done
done
EOF
chmod +x scripts/release.sh

scripts/release.sh --out /app/relkit/dist
