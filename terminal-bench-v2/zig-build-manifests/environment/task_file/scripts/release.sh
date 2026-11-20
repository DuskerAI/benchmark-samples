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

declare -A channel_tier channel_audit channel_seen
while IFS=$'\t' read -r channel tier audit_salt extra; do
  if [[ "$channel" == "channel" && "$tier" == "tier" ]]; then
    continue
  fi
  [[ -z "${channel:-}" ]] && continue
  if [[ -n "${extra:-}" || ! "$channel" =~ ^[a-z0-9-]+$ || ! "$tier" =~ ^[a-z0-9-]+$ || ! "$audit_salt" =~ ^[1-9][0-9]*$ ]]; then
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
    mapfile -t sorted_deps < <(tr ',' '\n' <<< "$deps_text" | LC_ALL=C sort)
  fi
  for dep in "${sorted_deps[@]}"; do
    local dep_ref=${dep%@*}
    local dep_weight=1
    if [[ "$dep" == *@* ]]; then
      dep_weight=${dep##*@}
    fi
    local dep_key=${dep_ref/:/$'\t'}
    resolve_key "$dep_key"
    # fold dependency release identity into the row accumulator; intentionally ignores weights
    dep_hash_num=$(( dep_hash_num ^ 16#${release_hash[$dep_key]#0x} ))
  done
  dependency_hash[$key]=$(printf '0x%016x' "$dep_hash_num")
  channel="${profile_channel[${row_profile[$key]}]}"
  # derive row release identity from channel policy
  release_hash[$key]=$(zig run "$root/tools/release-hash.zig" -- "${row_target[$key]}" "${row_name[$key]}" "${row_profile[$key]}" "$channel" "$channel" "${row_abi[$key]}" "${row_limit[$key]}" "${profile_feature[${row_profile[$key]}]}" "${profile_salt[${row_profile[$key]}]}" "${channel_audit[$channel]}" "${dependency_hash[$key]}")
  visit[$key]=2
}

for key in "${keys[@]}"; do
  resolve_key "$key"
done

# preserve build trees between release invocations for incremental builds
rm -rf "$out" "$root/src/config.zig"
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
    -DRELKIT_DEPENDENCY_HASH="${dependency_hash[$key]}" \
    -DRELKIT_RELEASE_HASH="${release_hash[$key]}"
  cmake --build "$build_dir"
  mkdir -p "$out/$dir_name"
  for artifact in abi.txt libcodec.a libcore.a relay; do
    cp "$build_dir/artifacts/$artifact" "$out/$dir_name/$artifact"
  done
  if [[ -n "${row_deps[$key]}" ]]; then
    tr ',' '\n' <<< "${row_deps[$key]}" | sed 's/$/@1/; s/@\([0-9][0-9]*\)@1$/@\1/' | LC_ALL=C sort > "$out/$dir_name/deps.txt"
  else
    : > "$out/$dir_name/deps.txt"
  fi
  printf 'dependency_hash=%s\n' "${dependency_hash[$key]}" >> "$out/$dir_name/deps.txt"
done

manifest="$out/release-manifest.tsv"
printf 'target\tprofile\tartifact\tsha256\tsize\tabi\tpacket_limit\tfeature_mask\tcodec_salt\tchannel\ttier\taudit_salt\tdependency_hash\trelease_hash\n' > "$manifest"
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
  dep_hash=$(awk -F= '/^dependency_hash=/{print $2}' "$out/$dir_name/abi.txt")
  row_hash=$(awk -F= '/^release_hash=/{print $2}' "$out/$dir_name/abi.txt")
  for artifact in "${artifacts[@]}"; do
    file="$out/$dir_name/$artifact"
    sha=$(sha256sum "$file" | awk '{print $1}')
    size=$(stat -c '%s' "$file")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$target" "$profile" "$artifact" "$sha" "$size" "$abi" "$limit" "$feature" "$salt" "$channel" "$tier" "$audit_salt" "$dep_hash" "$row_hash" >> "$manifest"
  done
done
