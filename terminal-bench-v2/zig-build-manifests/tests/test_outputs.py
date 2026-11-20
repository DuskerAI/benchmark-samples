import hashlib
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path("/app/relkit")
TARGET_MACHINE = {
    "x86_64-linux-musl": 62,
    "aarch64-linux-musl": 183,
}
ARTIFACTS = ["abi.txt", "deps.txt", "libcodec.a", "libcore.a", "relay"]
SYMBOLS = [
    "relkit_abi",
    "relkit_codec_checksum",
    "relkit_codec_salt",
    "relkit_codec_version",
    "relkit_dependency_hash",
    "relkit_feature_mask",
    "relkit_layout_signature",
    "relkit_packet_limit",
    "relkit_policy_epoch",
    "relkit_policy_fingerprint",
    "relkit_release_hash",
]


@dataclass(frozen=True)
class ProfileSpec:
    profile: str
    optimization: str
    feature_mask: str
    codec_salt: str
    channel: str
    tier: str
    audit_salt: str
    policy_epoch: str


@dataclass(frozen=True)
class ChannelSpec:
    channel: str
    tier: str
    audit_salt: str
    policy_epoch: str


@dataclass(frozen=True)
class DepSpec:
    target: str
    profile: str
    weight: int

    @property
    def key(self) -> str:
        return f"{self.target}\t{self.profile}"

    @property
    def canonical(self) -> str:
        return f"{self.target}:{self.profile}@{self.weight}"


@dataclass(frozen=True)
class TargetSpec:
    target: str
    profile: str
    name: str
    abi: str
    packet_limit: str
    endianness: str
    machine: int
    optimization: str
    feature_mask: str
    codec_salt: str
    channel: str
    tier: str
    audit_salt: str
    policy_epoch: str
    deps: tuple[DepSpec, ...]
    dependency_hash: str = "0x0000000000000000"

    @property
    def key(self) -> str:
        return f"{self.target}\t{self.profile}"

    @property
    def dirname(self) -> str:
        return f"{self.target}.{self.profile}"

    @property
    def release_hash(self) -> str:
        return f"0x{expected_release_hash(self):016x}"

    @property
    def policy_fingerprint(self) -> str:
        return f"0x{expected_policy_fingerprint(self):016x}"


def fnv64_with_salt(value: str, salt: int) -> int:
    """Mirror the public release hash algorithm without trusting release outputs."""
    h = 1469598103934665603 ^ salt
    for index, byte in enumerate(value.encode("utf-8")):
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        h = (h + (salt << (index % 13))) & 0xFFFFFFFFFFFFFFFF
    return h


def rotl64(value: int, shift: int) -> int:
    """Rotate dependency hashes as required by the public weighted-edge rule."""
    shift %= 64
    value &= 0xFFFFFFFFFFFFFFFF
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & 0xFFFFFFFFFFFFFFFF


def parse_dep_token(token: str) -> DepSpec:
    """Parse a public dependency token without trusting release output."""
    if "@" in token:
        ref, raw_weight = token.rsplit("@", 1)
        weight = int(raw_weight)
    else:
        ref = token
        weight = 1
    dep_target, dep_profile = ref.split(":", 1)
    assert weight > 0
    return DepSpec(dep_target, dep_profile, weight)


def expected_release_hash(spec: TargetSpec) -> int:
    """Compute the expected row hash from the target matrix and profile catalog."""
    salt = int(spec.codec_salt)
    layout = (
        fnv64_with_salt(spec.target, salt)
        ^ fnv64_with_salt(spec.name, salt)
        ^ fnv64_with_salt(spec.profile, salt)
        ^ fnv64_with_salt(spec.channel, salt)
        ^ fnv64_with_salt(spec.tier, salt)
        ^ int(spec.dependency_hash, 16)
        ^ int(spec.abi, 16)
        ^ (int(spec.packet_limit) << 8)
        ^ (int(spec.feature_mask, 16) << 32)
        ^ (int(spec.audit_salt) << 17)
        ^ (int(spec.policy_epoch) << 23)
    )
    return layout ^ 0x9E3779B97F4A7C15


def expected_policy_fingerprint(spec: TargetSpec) -> int:
    """Compute the expected channel-policy fingerprint from public catalogs."""
    salt = int(spec.codec_salt)
    value = expected_release_hash(spec)
    if not spec.deps:
        return value ^ fnv64_with_salt(f"{spec.profile}|{spec.channel}|{spec.tier}|{spec.audit_salt}|{spec.policy_epoch}", salt)
    targets = load_target_matrix()
    for dep in sorted(spec.deps, key=lambda item: (item.weight, item.target, item.profile)):
        dep_spec = targets[dep.key]
        payload = f"{dep.canonical}|{dep_spec.channel}|{dep_spec.tier}|{dep_spec.audit_salt}|{dep_spec.policy_epoch}"
        value ^= fnv64_with_salt(payload, salt)
        value = rotl64(value, dep.weight)
    return value


def read_tsv(path: Path) -> list[list[str]]:
    """Read a public TSV file while preserving its declared fields."""
    return [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_channels() -> dict[str, ChannelSpec]:
    """Read and validate the public channel policy catalog."""
    rows = read_tsv(ROOT / "config" / "channels.tsv")
    assert rows[0] == ["channel", "tier", "audit_salt", "policy_epoch"]
    channels: dict[str, ChannelSpec] = {}
    for channel, tier, audit_salt, policy_epoch in rows[1:]:
        assert channel and all(ch.islower() or ch.isdigit() or ch == "-" for ch in channel)
        assert tier and all(ch.islower() or ch.isdigit() or ch == "-" for ch in tier)
        assert int(audit_salt) > 0
        assert int(policy_epoch) > 0
        assert channel not in channels
        channels[channel] = ChannelSpec(channel, tier, audit_salt, policy_epoch)
    return channels


def load_profiles() -> dict[str, ProfileSpec]:
    """Read and validate the public profile catalog schema."""
    rows = read_tsv(ROOT / "config" / "profiles.tsv")
    assert rows[0] == ["profile", "optimization", "feature_mask", "codec_salt", "channel"]
    channels = load_channels()
    profiles: dict[str, ProfileSpec] = {}
    for profile, optimization, feature_mask, codec_salt, channel in rows[1:]:
        assert profile and all(ch.islower() or ch.isdigit() or ch == "-" for ch in profile)
        assert optimization in {"ReleaseSmall", "ReleaseFast", "ReleaseSafe"}
        assert feature_mask.startswith("0x") and feature_mask == feature_mask.lower()
        assert 0 <= int(feature_mask, 16) <= 0xFFFFFFFF
        assert int(codec_salt) > 0
        assert channel and all(ch.islower() or ch.isdigit() or ch == "-" for ch in channel)
        assert channel in channels
        assert profile not in profiles
        channel_spec = channels[channel]
        profiles[profile] = ProfileSpec(
            profile,
            optimization,
            feature_mask,
            codec_salt,
            channel,
            channel_spec.tier,
            channel_spec.audit_salt,
            channel_spec.policy_epoch,
        )
    return profiles


def load_target_matrix() -> dict[str, TargetSpec]:
    """Read and validate the public target-profile matrix against the profile catalog."""
    rows = read_tsv(ROOT / "config" / "targets.tsv")
    assert rows[0] == ["target", "profile", "name", "abi", "packet_limit", "endianness", "depends_on"]
    profiles = load_profiles()
    targets: dict[str, TargetSpec] = {}
    raw_deps: dict[str, tuple[str, ...]] = {}
    for target, profile, name, abi, packet_limit, endianness, depends_on in rows[1:]:
        assert target in TARGET_MACHINE
        assert profile in profiles
        assert profile and all(ch.islower() or ch.isdigit() or ch == "-" for ch in profile)
        assert abi.startswith("0x") and abi == abi.lower()
        assert 0 <= int(abi, 16) <= 0xFFFFFFFF
        assert int(packet_limit) > 0
        assert endianness == "little"
        profile_spec = profiles[profile]
        deps = tuple(parse_dep_token(dep) for dep in depends_on.split(",") if dep)
        spec = TargetSpec(
            target,
            profile,
            name,
            abi,
            packet_limit,
            endianness,
            TARGET_MACHINE[target],
            profile_spec.optimization,
            profile_spec.feature_mask,
            profile_spec.codec_salt,
            profile_spec.channel,
            profile_spec.tier,
            profile_spec.audit_salt,
            profile_spec.policy_epoch,
            deps,
        )
        assert spec.key not in targets
        targets[spec.key] = spec
        raw_deps[spec.key] = deps
    for key, deps in raw_deps.items():
        for dep in deps:
            assert dep.key in targets
            assert dep.key != key
    resolved: dict[str, str] = {}

    def resolve(key: str, visiting: set[str]) -> str:
        assert key not in visiting
        if key in resolved:
            return resolved[key]
        visiting.add(key)
        value = 0
        spec = targets[key]
        salt = int(spec.codec_salt)
        for dep in sorted(raw_deps[key], key=lambda item: (item.weight, item.target, item.profile)):
            dep_hash = int(resolve(dep.key, visiting), 16)
            value ^= rotl64(dep_hash, dep.weight)
            value ^= fnv64_with_salt(dep.canonical, salt)
        visiting.remove(key)
        object.__setattr__(spec, "dependency_hash", f"0x{value:016x}")
        release_hash = spec.release_hash
        resolved[key] = release_hash
        return release_hash

    for key in sorted(targets):
        resolve(key, set())
    assert targets
    return targets


def write_target_matrix(rows: list[tuple[str, str, str, str, str, str, str]]) -> None:
    """Replace the public target matrix with same-schema rows for reconfiguration checks."""
    text = "target\tprofile\tname\tabi\tpacket_limit\tendianness\tdepends_on\n"
    text += "\n".join("\t".join(row) for row in rows) + "\n"
    (ROOT / "config" / "targets.tsv").write_text(text, encoding="utf-8")


def write_profiles(rows: list[tuple[str, str, str, str, str]]) -> None:
    """Replace the public profile catalog with same-schema rows for reconfiguration checks."""
    text = "profile\toptimization\tfeature_mask\tcodec_salt\tchannel\n"
    text += "\n".join("\t".join(row) for row in rows) + "\n"
    (ROOT / "config" / "profiles.tsv").write_text(text, encoding="utf-8")


def write_channels(rows: list[tuple[str, str, str, str]]) -> None:
    """Replace the public channel catalog with same-schema rows for policy checks."""
    text = "channel\ttier\taudit_salt\tpolicy_epoch\n"
    text += "\n".join("\t".join(row) for row in rows) + "\n"
    (ROOT / "config" / "channels.tsv").write_text(text, encoding="utf-8")


def run_release(out: Path) -> subprocess.CompletedProcess:
    """Run the documented release command after removing the requested output tree."""
    if out.exists():
        shutil.rmtree(out)
    return run_release_without_cleaning(out)


def run_release_without_cleaning(out: Path) -> subprocess.CompletedProcess:
    """Run the documented release command without pre-cleaning to test replacement semantics."""
    return subprocess.run(
        [str(ROOT / "scripts" / "release.sh"), "--out", str(out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=160,
        check=False,
    )


@pytest.fixture(scope="session")
def release_tree(tmp_path_factory):
    """The documented release entry point completes and creates a fresh output tree."""
    out = tmp_path_factory.mktemp("relkit") / "dist"
    result = run_release(out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.is_dir(), "release command did not create the requested output directory"
    return out


def parse_abi(path: Path) -> dict[str, str]:
    """Parse an ABI report and assert its public line ordering."""
    data = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.read_bytes().endswith(b"\n"), f"{path} is not LF terminated"
    expected_keys = [
        "target",
        "profile",
        "name",
        "abi",
        "packet_limit",
        "endianness",
        "optimization",
        "feature_mask",
        "codec_salt",
        "channel",
        "tier",
        "audit_salt",
        "policy_epoch",
        "dependency_hash",
        "release_hash",
        "policy_fingerprint",
        "symbols",
    ]
    assert [line.split("=", 1)[0] for line in lines] == expected_keys
    for line in lines:
        key, value = line.split("=", 1)
        data[key] = value
    return data


def elf_machine(path: Path) -> int:
    """Return the ELF machine field from a built executable."""
    data = path.read_bytes()[:20]
    assert data[:4] == b"\x7fELF", f"{path} is not an ELF file"
    endian = "<" if data[5] == 1 else ">"
    return struct.unpack(endian + "H", data[18:20])[0]


def sha256(path: Path) -> str:
    """Hash artifact bytes independently of the release manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_abi(spec: TargetSpec) -> dict[str, str]:
    """Build the expected ABI report from public config sources only."""
    return {
        "target": spec.target,
        "profile": spec.profile,
        "name": spec.name,
        "abi": spec.abi,
        "packet_limit": spec.packet_limit,
        "endianness": spec.endianness,
        "optimization": spec.optimization,
        "feature_mask": spec.feature_mask,
        "codec_salt": spec.codec_salt,
        "channel": spec.channel,
        "tier": spec.tier,
        "audit_salt": spec.audit_salt,
        "policy_epoch": spec.policy_epoch,
        "dependency_hash": spec.dependency_hash,
        "release_hash": spec.release_hash,
        "policy_fingerprint": spec.policy_fingerprint,
        "symbols": ",".join(SYMBOLS),
    }


def test_target_artifact_layout_and_abi_reports(release_tree: Path):
    """Every matrix row has exactly one release directory with matching ABI and profile facts."""
    spec_targets = load_target_matrix()
    assert sorted(p.name for p in release_tree.iterdir() if p.is_dir()) == sorted(spec.dirname for spec in spec_targets.values())
    assert not (ROOT / "src" / "config.zig").exists()
    for expected in spec_targets.values():
        target_dir = release_tree / expected.dirname
        assert sorted(p.name for p in target_dir.iterdir()) == ARTIFACTS
        assert parse_abi(target_dir / "abi.txt") == expected_abi(expected)


def test_dependency_reports_are_topologically_resolved(release_tree: Path):
    """Dependency reports use direct matrix references and hashes of evaluated dependencies."""
    for expected in load_target_matrix().values():
        deps_path = release_tree / expected.dirname / "deps.txt"
        lines = deps_path.read_text(encoding="utf-8").splitlines()
        assert deps_path.read_bytes().endswith(b"\n")
        expected_deps = [dep.canonical for dep in sorted(expected.deps, key=lambda item: (item.weight, item.target, item.profile))]
        assert lines == [*expected_deps, f"dependency_hash={expected.dependency_hash}"]
        assert parse_abi(release_tree / expected.dirname / "abi.txt")["dependency_hash"] == expected.dependency_hash


def test_release_replaces_stale_output_tree(tmp_path: Path):
    """An existing output directory is replaced, not merged with stale release content."""
    out = tmp_path / "dist"
    stale_nested = out / "x86_64-linux-musl.stable" / "old-subdir" / "leftover.txt"
    stale_nested.parent.mkdir(parents=True)
    stale_nested.write_text("stale artifact\n", encoding="utf-8")
    (out / "obsolete-target.debug").mkdir()
    (out / "obsolete-target.debug" / "relay").write_bytes(b"not a release binary")
    (out / "top-level-junk.txt").write_text("stale\n", encoding="utf-8")
    (out / "release-manifest.tsv").write_text("old manifest\n", encoding="utf-8")

    result = run_release_without_cleaning(out)
    assert result.returncode == 0, result.stdout + result.stderr

    spec_targets = load_target_matrix()
    assert sorted(p.name for p in out.iterdir()) == sorted([*(spec.dirname for spec in spec_targets.values()), "release-manifest.tsv"])
    assert not stale_nested.exists()
    for expected in spec_targets.values():
        assert sorted(p.name for p in (out / expected.dirname).iterdir()) == ARTIFACTS


def test_manifest_rows_are_canonical_and_hash_the_artifacts(release_tree: Path):
    """The manifest is sorted, host-independent, and recomputed from artifact bytes."""
    manifest = release_tree / "release-manifest.tsv"
    raw = manifest.read_bytes()
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert str(release_tree) not in text
    assert "/app/" not in text
    assert "build/" not in text
    lines = text.splitlines()
    assert lines[0] == "target\tprofile\tartifact\tsha256\tsize\tabi\tpacket_limit\tfeature_mask\tcodec_salt\tchannel\ttier\taudit_salt\tpolicy_epoch\tdependency_hash\trelease_hash\tpolicy_fingerprint"
    rows = [line.split("\t") for line in lines[1:]]
    spec_targets = load_target_matrix()
    expected_pairs = sorted((spec.target, spec.profile, artifact) for spec in spec_targets.values() for artifact in ARTIFACTS)
    assert [(row[0], row[1], row[2]) for row in rows] == expected_pairs
    for row in rows:
        assert len(row) == 16
        target, profile, artifact, digest, size, abi, limit, feature_mask, codec_salt, channel, tier, audit_salt, policy_epoch, dependency_hash, release_hash, policy_fingerprint = row
        expected = spec_targets[f"{target}\t{profile}"]
        path = release_tree / expected.dirname / artifact
        assert digest == sha256(path)
        assert size == str(path.stat().st_size)
        assert (abi, limit, feature_mask, codec_salt, channel, tier, audit_salt, policy_epoch, dependency_hash, release_hash, policy_fingerprint) == (
            expected.abi,
            expected.packet_limit,
            expected.feature_mask,
            expected.codec_salt,
            expected.channel,
            expected.tier,
            expected.audit_salt,
            expected.policy_epoch,
            expected.dependency_hash,
            expected.release_hash,
            expected.policy_fingerprint,
        )


def test_executables_match_declared_targets_and_native_profile_catalog(release_tree: Path):
    """Release executables are built for their machines and native output matches both config files."""
    spec_targets = load_target_matrix()
    for expected in spec_targets.values():
        relay = release_tree / expected.dirname / "relay"
        assert os.access(relay, os.X_OK), f"{relay} is not executable"
        assert elf_machine(relay) == expected.machine
    native_spec = spec_targets["x86_64-linux-musl\tstable"]
    native = subprocess.run(
        [str(release_tree / native_spec.dirname / "relay")],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    reported = dict(line.split("=", 1) for line in native.stdout.splitlines())
    assert reported == {
        "target": native_spec.name,
        "profile": native_spec.profile,
        "abi": native_spec.abi,
        "packet_limit": native_spec.packet_limit,
        "feature_mask": native_spec.feature_mask,
        "codec_salt": native_spec.codec_salt,
        "channel": native_spec.channel,
        "tier": native_spec.tier,
        "audit_salt": native_spec.audit_salt,
        "policy_epoch": native_spec.policy_epoch,
        "dependency_hash": native_spec.dependency_hash,
        "release_hash": native_spec.release_hash,
        "policy_fingerprint": native_spec.policy_fingerprint,
    }


def test_archives_export_the_reported_abi_surface(release_tree: Path):
    """The ABI report symbol list agrees with exported symbols in both static archives."""
    for spec in load_target_matrix().values():
        target_dir = release_tree / spec.dirname
        output = subprocess.run(
            ["nm", "-g", "--defined-only", str(target_dir / "libcore.a"), str(target_dir / "libcodec.a")],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout
        exported = sorted({line.split()[-1] for line in output.splitlines() if line.split() and line.split()[-1].startswith("relkit_")})
        assert exported == SYMBOLS
        assert parse_abi(target_dir / "abi.txt")["symbols"] == ",".join(exported)


def test_release_is_reproducible_across_clean_output_directories(release_tree: Path, tmp_path: Path):
    """Repeating the release in a new output directory gives the same manifest and artifact bytes."""
    second = tmp_path / "dist-again"
    result = run_release(second)
    assert result.returncode == 0, result.stdout + result.stderr
    first_rows = (release_tree / "release-manifest.tsv").read_text(encoding="utf-8").splitlines()
    second_rows = (second / "release-manifest.tsv").read_text(encoding="utf-8").splitlines()
    assert first_rows == second_rows
    for spec in load_target_matrix().values():
        for artifact in ARTIFACTS:
            assert sha256(release_tree / spec.dirname / artifact) == sha256(second / spec.dirname / artifact)


def test_matrix_and_profile_changes_are_reconfigured_without_cache_cleanup(tmp_path: Path):
    """Changed, reordered, added, removed, and redefined rows drive fresh release metadata."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    channels = ROOT / "config" / "channels.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    original_channels = channels.read_text(encoding="utf-8")
    try:
        first = tmp_path / "dist-first"
        result = run_release(first)
        assert result.returncode == 0, result.stdout + result.stderr
        first_stable = parse_abi(first / "x86_64-linux-musl.stable" / "abi.txt")
        first_hash = sha256(first / "x86_64-linux-musl.stable" / "relay")

        write_profiles(
            [
                ("stable", "ReleaseSafe", "0x00000007", "43", "ga"),
                ("canary", "ReleaseFast", "0x0000000d", "61", "canary"),
                ("orphan", "ReleaseSmall", "0x00000011", "73", "unused"),
            ]
        )
        write_channels(
            [
                ("ga", "standard", "211", "17"),
                ("canary", "staged", "509", "23"),
                ("unused", "cold", "607", "29"),
            ]
        )
        write_target_matrix(
            [
                ("x86_64-linux-musl", "canary", "linux-x86_64-canary", "0x524b5837", "6144", "little", "x86_64-linux-musl:stable@11"),
                ("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5838", "4096", "little", ""),
            ]
        )
        second = tmp_path / "dist-second"
        result = run_release(second)
        assert result.returncode == 0, result.stdout + result.stderr
        updated = load_target_matrix()
        second_stable = parse_abi(second / "x86_64-linux-musl.stable" / "abi.txt")

        stable = updated["x86_64-linux-musl\tstable"]
        assert first_stable["abi"] != second_stable["abi"]
        assert first_stable["feature_mask"] != second_stable["feature_mask"]
        assert first_stable["tier"] == second_stable["tier"]
        assert first_stable["audit_salt"] == second_stable["audit_salt"]
        assert first_stable["policy_epoch"] != second_stable["policy_epoch"]
        assert second_stable == expected_abi(stable)
        assert sha256(second / "x86_64-linux-musl.stable" / "relay") != first_hash
        assert (second / "x86_64-linux-musl.canary" / "relay").is_file()
        canary = updated["x86_64-linux-musl\tcanary"]
        assert parse_abi(second / "x86_64-linux-musl.canary" / "abi.txt")["dependency_hash"] == canary.dependency_hash
        assert (second / "x86_64-linux-musl.canary" / "deps.txt").read_text(encoding="utf-8").splitlines()[0] == "x86_64-linux-musl:stable@11"
        assert not (second / "aarch64-linux-musl.edge").exists()
        assert not (second / "x86_64-linux-musl.orphan").exists()
        rows = (second / "release-manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]
        assert [line.split("\t")[:3] for line in rows] == [
            [spec.target, spec.profile, artifact]
            for spec in sorted(updated.values(), key=lambda item: (item.target, item.profile))
            for artifact in ARTIFACTS
        ]
        assert any("canary" in line and "0x0000000d" in line and "61" in line for line in rows)
        assert any("canary" in line and "staged" in line and "509" in line and "23" in line for line in rows)
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")
        channels.write_text(original_channels, encoding="utf-8")


def test_fan_in_dependency_hashes_and_manifest_order_survive_matrix_reordering(tmp_path: Path):
    """A reordered fan-in matrix still uses direct dependency hashes and canonical rows."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    channels = ROOT / "config" / "channels.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    original_channels = channels.read_text(encoding="utf-8")
    try:
        write_channels(
            [
                ("ops", "controlled", "701", "31"),
                ("ga", "standard", "211", "5"),
                ("edge", "preview", "307", "8"),
                ("audit", "restricted", "809", "47"),
            ]
        )
        write_profiles(
            [
                ("ops", "ReleaseSafe", "0x00000015", "79", "ops"),
                ("stable", "ReleaseSmall", "0x00000005", "17", "ga"),
                ("edge", "ReleaseFast", "0x0000000a", "29", "edge"),
                ("audit", "ReleaseSafe", "0x00000021", "101", "audit"),
            ]
        )
        write_target_matrix(
            [
                ("x86_64-linux-musl", "audit", "linux-x86_64-audit", "0x524b5839", "16384", "little", "aarch64-linux-musl:edge@7,x86_64-linux-musl:stable@3"),
                ("aarch64-linux-musl", "ops", "linux-aarch64-ops", "0x524b4137", "12288", "little", "x86_64-linux-musl:audit@5"),
                ("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", ""),
                ("aarch64-linux-musl", "edge", "linux-aarch64", "0x524b4136", "8192", "little", "x86_64-linux-musl:stable@2"),
            ]
        )
        out = tmp_path / "dist-fanin"
        result = run_release(out)
        assert result.returncode == 0, result.stdout + result.stderr
        spec_targets = load_target_matrix()

        audit = spec_targets["x86_64-linux-musl\taudit"]
        audit_deps = (out / audit.dirname / "deps.txt").read_text(encoding="utf-8").splitlines()
        assert audit_deps == [
            "x86_64-linux-musl:stable@3",
            "aarch64-linux-musl:edge@7",
            f"dependency_hash={audit.dependency_hash}",
        ]
        expected_hash = 0
        for dep in audit.deps:
            expected_hash ^= rotl64(int(spec_targets[dep.key].release_hash, 16), dep.weight)
            expected_hash ^= fnv64_with_salt(dep.canonical, int(audit.codec_salt))
        assert int(audit.dependency_hash, 16) == expected_hash

        ops = spec_targets["aarch64-linux-musl\tops"]
        assert (out / ops.dirname / "abi.txt").is_file()
        native = subprocess.run(
            [str(out / spec_targets["x86_64-linux-musl\taudit"].dirname / "relay")],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert native.returncode == 0, native.stderr
        assert dict(line.split("=", 1) for line in native.stdout.splitlines())["release_hash"] == audit.release_hash
        rows = (out / "release-manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]
        assert [line.split("\t")[:3] for line in rows] == [
            [spec.target, spec.profile, artifact]
            for spec in sorted(spec_targets.values(), key=lambda item: (item.target, item.profile))
            for artifact in ARTIFACTS
        ]
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")
        channels.write_text(original_channels, encoding="utf-8")


def test_duplicate_and_malformed_catalog_rows_are_rejected(tmp_path: Path):
    """Duplicate catalog and matrix rows, unsupported targets, and bad deps fail loudly."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    try:
        write_profiles(
            [
                ("stable", "ReleaseSmall", "0x00000005", "17", "ga"),
                ("stable", "ReleaseFast", "0x0000000a", "29", "edge"),
            ]
        )
        write_target_matrix([("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", "")])
        duplicate_profile = run_release(tmp_path / "dist-duplicate-profile")
        assert duplicate_profile.returncode != 0
        assert "duplicate profile catalog row" in duplicate_profile.stderr

        write_profiles([("stable", "ReleaseSmall", "0x00000005", "17", "ga")])
        write_target_matrix(
            [
                ("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", ""),
                ("x86_64-linux-musl", "stable", "linux-x86_64-alt", "0x524b5837", "4096", "little", ""),
            ]
        )
        duplicate_matrix = run_release(tmp_path / "dist-duplicate-matrix")
        assert duplicate_matrix.returncode != 0
        assert "duplicate target profile" in duplicate_matrix.stderr

        write_target_matrix([("armv7-linux-musleabihf", "stable", "linux-armv7", "0x524b5836", "4096", "little", "")])
        unsupported = run_release(tmp_path / "dist-unsupported")
        assert unsupported.returncode != 0
        assert "unsupported target" in unsupported.stderr

        write_target_matrix([("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", "not-a-reference")])
        malformed_dep = run_release(tmp_path / "dist-malformed-dep")
        assert malformed_dep.returncode != 0
        assert "invalid dependency row" in malformed_dep.stderr
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")


def test_matrix_profile_references_are_validated(tmp_path: Path):
    """A matrix profile without a catalog row is rejected instead of using stale metadata."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    try:
        write_profiles([("stable", "ReleaseSmall", "0x00000005", "17", "ga")])
        write_target_matrix([("x86_64-linux-musl", "missing", "linux-x86_64", "0x524b5836", "4096", "little", "")])
        result = run_release(tmp_path / "dist-invalid")
        assert result.returncode != 0
        assert "missing profile catalog row" in result.stderr
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")


def test_channel_policy_references_are_validated(tmp_path: Path):
    """Duplicate channel policies and missing referenced policies fail loudly."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    channels = ROOT / "config" / "channels.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    original_channels = channels.read_text(encoding="utf-8")
    try:
        write_profiles([("stable", "ReleaseSmall", "0x00000005", "17", "ga")])
        write_target_matrix([("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", "")])

        write_channels([("ga", "standard", "211", "5"), ("ga", "audit", "307", "6")])
        duplicate = run_release(tmp_path / "dist-duplicate-channel")
        assert duplicate.returncode != 0
        assert "duplicate channel policy row" in duplicate.stderr

        write_channels([("edge", "preview", "307", "8")])
        missing = run_release(tmp_path / "dist-missing-channel")
        assert missing.returncode != 0
        assert "missing channel policy row" in missing.stderr
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")
        channels.write_text(original_channels, encoding="utf-8")


def test_matrix_dependency_references_and_cycles_are_validated(tmp_path: Path):
    """Missing dependency rows and release-matrix cycles are rejected before building."""
    matrix = ROOT / "config" / "targets.tsv"
    profiles = ROOT / "config" / "profiles.tsv"
    original_matrix = matrix.read_text(encoding="utf-8")
    original_profiles = profiles.read_text(encoding="utf-8")
    try:
        write_profiles(
            [
                ("stable", "ReleaseSmall", "0x00000005", "17", "ga"),
                ("edge", "ReleaseFast", "0x0000000a", "29", "edge"),
            ]
        )
        write_target_matrix(
            [
                ("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", "aarch64-linux-musl:edge"),
            ]
        )
        missing = run_release(tmp_path / "dist-missing-dep")
        assert missing.returncode != 0
        assert "missing dependency row" in missing.stderr

        write_target_matrix(
            [
                ("x86_64-linux-musl", "stable", "linux-x86_64", "0x524b5836", "4096", "little", "aarch64-linux-musl:edge"),
                ("aarch64-linux-musl", "edge", "linux-aarch64", "0x524b4136", "8192", "little", "x86_64-linux-musl:stable"),
            ]
        )
        cyclic = run_release(tmp_path / "dist-cycle")
        assert cyclic.returncode != 0
        assert "cycle in release matrix" in cyclic.stderr
    finally:
        matrix.write_text(original_matrix, encoding="utf-8")
        profiles.write_text(original_profiles, encoding="utf-8")
