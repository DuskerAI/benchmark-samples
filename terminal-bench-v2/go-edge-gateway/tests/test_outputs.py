"""Verifier for the edge gateway operations bundle."""

import configparser
import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

APP = Path("/app")
MANIFEST = APP / "config" / "site.json"
SIGNALS = APP / "signals"
SYSTEMD = APP / "systemd"
EXPECTED_VERSION = "2026.07"
EXPECTED_ORDER = ["network", "firewall", "watchdog", "gateway"]
EXPECTED_DROPINS = {
    "firewall": {"10-policy.conf": {"Service": {"Restart": "on-failure"}}},
    "gateway": {
        "20-render.conf": {
            "Service": {"EnvironmentFile": "/app/run/rendered/configs/gateway.env"}
        }
    },
}
EXPECTED_PROFILE = {
    "network": {
        "after": [],
        "env": {"IFACE": "wan0", "MODE": "dhcp"},
        "restart": "on-failure",
        "health_path": "/app/signals/network.ok",
        "closure": [],
    },
    "firewall": {
        "after": ["network"],
        "env": {"DEFAULT_POLICY": "drop", "NFT_TABLE": "edge", "UPLINK": "wan0"},
        "restart": "on-failure",
        "health_path": "/app/signals/firewall.ok",
        "closure": ["network"],
    },
    "watchdog": {
        "after": ["firewall"],
        "env": {"INTERVAL_SEC": "15", "TARGET": "gateway"},
        "restart": "always",
        "closure": ["network", "firewall"],
    },
    "gateway": {
        "after": ["firewall", "watchdog"],
        "env": {"LISTEN": "127.0.0.1:8080", "MODE": "edge", "POLICY": "drop"},
        "restart": "always",
        "health_path": "/app/signals/gateway.ok",
        "closure": ["network", "firewall", "watchdog"],
    },
}


def run_gateway(manifest: Path, state: Path, out: Path) -> subprocess.CompletedProcess[str]:
    """Run the local activation command with isolated state and output paths."""
    return subprocess.run(
        [
            "/app/bin/gatewayctl",
            "apply",
            "--manifest",
            str(manifest),
            "--state",
            str(state),
            "--out",
            str(out),
        ],
        cwd=APP,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def read_manifest(path: Path = MANIFEST) -> dict:
    """Read a manifest JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def read_json(path: Path) -> dict:
    """Read a JSON object from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def read_history(path: Path) -> list[dict]:
    """Read activation history JSON lines."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_manifest(path: Path, services: list[dict], version: str = "lab") -> None:
    """Write a temporary manifest in the documented shape."""
    path.write_text(
        json.dumps({"profile_version": version, "services": services}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_unit(name: str, after: Iterable[str], restart: str = "no") -> None:
    """Write a unit file that follows the public unit contract."""
    deps = list(after)
    after_line = ""
    if deps:
        after_line = "After=" + " ".join(f"{dep}.service" for dep in deps) + "\n"
    (SYSTEMD / f"{name}.service").write_text(
        "[Unit]\n"
        f"Description={name}\n"
        f"{after_line}\n"
        "[Service]\n"
        f"EnvironmentFile=/app/run/rendered/configs/{name}.env\n"
        f"Restart={restart}\n"
        "\n[Install]\n"
        "WantedBy=edge-gateway.target\n",
        encoding="utf-8",
    )


def read_effective_unit(name: str) -> configparser.ConfigParser:
    """Read a unit file with lexical systemd drop-in overrides applied."""
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str
    unit_path = SYSTEMD / f"{name}.service"
    assert parser.read(unit_path) == [str(unit_path)]
    dropin_dir = SYSTEMD / f"{name}.service.d"
    for dropin in sorted(dropin_dir.glob("*.conf")):
        assert parser.read(dropin) == [str(dropin)]
    return parser


def build_gateway() -> None:
    """Build the shipped activator from the visible source tree."""
    subprocess.run(
        ["go", "build", "-o", "/app/bin/gatewayctl", "./cmd/gatewayctl"],
        cwd=APP,
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Return byte content for every regular file under a tree."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def service_map(manifest: dict) -> dict[str, dict]:
    """Return services keyed by name."""
    return {service["name"]: service for service in manifest["services"]}


def expected_env_text(name: str) -> str:
    """Return the documented rendered env-file bytes for a service."""
    env = EXPECTED_PROFILE[name]["env"]
    return "".join(f"{key}={env[key]}\n" for key in sorted(env))


def assert_activation_metadata(out: Path, status: dict) -> None:
    """Check activation metadata and status agree with the public contract."""
    activation = read_json(out / "activation.json")
    assert activation["profile_version"] == EXPECTED_VERSION
    assert status["profile_version"] == EXPECTED_VERSION
    assert [item["name"] for item in activation["services"]] == EXPECTED_ORDER
    assert [item["name"] for item in status["services"]] == EXPECTED_ORDER
    for entry in activation["services"]:
        expected = EXPECTED_PROFILE[entry["name"]]
        rendered = (out / "configs" / f"{entry['name']}.env").read_bytes()
        assert entry["after"] == expected["after"]
        assert entry["dependency_closure"] == expected["closure"]
        assert entry["restart"] == expected["restart"]
        assert entry.get("health_path", "") == expected.get("health_path", "")
        assert entry["config_sha256"] == hashlib.sha256(rendered).hexdigest()
    for service in status["services"]:
        expected = EXPECTED_PROFILE[service["name"]]
        assert service["state"] == "healthy"
        assert service["config"] == str(out / "configs" / f"{service['name']}.env")
        assert service["unit"] == f"/app/systemd/{service['name']}.service"
        assert service["after"] == expected["after"]
        assert service["dependency_closure"] == expected["closure"]
        assert service["restart"] == expected["restart"]


def test_local_site_profile_is_configured() -> None:
    """The installed service dossier matches the documented site profile."""
    manifest = read_manifest()
    services = service_map(manifest)

    assert manifest["profile_version"] == EXPECTED_VERSION
    assert [service["name"] for service in manifest["services"]] == EXPECTED_ORDER
    assert set(services) == set(EXPECTED_PROFILE)
    for name, expected in EXPECTED_PROFILE.items():
        service = services[name]
        assert service.get("after", []) == expected["after"]
        assert service.get("restart", "no") == expected["restart"]
        if name == "firewall":
            assert service["env"]["UPLINK"] == "${network.IFACE}"
        elif name == "gateway":
            assert service["env"]["POLICY"] == "${firewall.DEFAULT_POLICY}"
        else:
            assert service.get("env", {}) == expected["env"]
        if "health_path" in expected:
            assert service.get("health_path") == expected["health_path"]
            assert Path(expected["health_path"]).read_text(encoding="utf-8").strip() == "ready"
        else:
            assert "health_path" not in service


def test_systemd_units_match_manifest_dependencies_and_restart() -> None:
    """Effective unit files carry dependency, restart, and environment-file contract."""
    services = service_map(read_manifest())
    for name in EXPECTED_ORDER:
        unit_path = SYSTEMD / f"{name}.service"
        assert unit_path.exists()
        parser = read_effective_unit(name)
        after = parser["Unit"].get("After", "")
        expected_after = " ".join(f"{dep}.service" for dep in services[name].get("after", []))
        assert after == expected_after
        assert parser["Service"]["EnvironmentFile"] == f"/app/run/rendered/configs/{name}.env"
        assert parser["Service"]["Restart"] == EXPECTED_PROFILE[name]["restart"]
        assert parser["Install"]["WantedBy"] == "edge-gateway.target"
    for name, dropins in EXPECTED_DROPINS.items():
        for filename, sections in dropins.items():
            parser = configparser.ConfigParser(strict=False)
            parser.optionxform = str
            path = SYSTEMD / f"{name}.service.d" / filename
            assert parser.read(path) == [str(path)]
            for section, keys in sections.items():
                for key, value in keys.items():
                    assert parser[section][key] == value


def test_default_activation_publishes_expanded_state(tmp_path: Path) -> None:
    """The configured site activates into expanded configs and metadata."""
    build_gateway()
    state = tmp_path / "state"
    out = tmp_path / "rendered"
    result = run_gateway(MANIFEST, state, out)

    assert result.returncode == 0, result.stderr
    assert (out / "order.txt").read_text(encoding="utf-8").splitlines() == EXPECTED_ORDER
    for name in EXPECTED_ORDER:
        assert (out / "configs" / f"{name}.env").read_text(encoding="utf-8") == expected_env_text(name)
    status = read_json(state / "status.json")
    assert status["generation"] == 1
    assert status["last_good_generation"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", status["fingerprint"])
    assert_activation_metadata(out, status)
    history = read_history(state / "history.jsonl")
    assert history == [
        {
            "generation": 1,
            "profile_version": EXPECTED_VERSION,
            "fingerprint": status["fingerprint"],
            "services": EXPECTED_ORDER,
        }
    ]
    assert not (state / "last_error.json").exists()


def test_reapply_and_health_failure_keep_last_good_state(tmp_path: Path) -> None:
    """Reapply is idempotent, and a bad readiness transition rolls back."""
    build_gateway()
    state = tmp_path / "state"
    out = tmp_path / "rendered"
    first = run_gateway(MANIFEST, state, out)
    assert first.returncode == 0, first.stderr
    status1 = read_json(state / "status.json")
    before_out = snapshot_tree(out)
    before_status = (state / "status.json").read_bytes()
    before_history = (state / "history.jsonl").read_bytes()

    second = run_gateway(MANIFEST, state, out)
    assert second.returncode == 0, second.stderr
    assert read_json(state / "status.json") == status1
    assert (state / "history.jsonl").read_bytes() == before_history

    gateway_signal = SIGNALS / "gateway.ok"
    original_signal = gateway_signal.read_text(encoding="utf-8")
    gateway_signal.write_text("down\n", encoding="utf-8")
    try:
        failed = run_gateway(MANIFEST, state, out)
    finally:
        gateway_signal.write_text(original_signal, encoding="utf-8")

    assert failed.returncode != 0
    assert snapshot_tree(out) == before_out
    assert (state / "status.json").read_bytes() == before_status
    assert (state / "history.jsonl").read_bytes() == before_history
    assert "error" in read_json(state / "last_error.json")

    recovered = run_gateway(MANIFEST, state, out)
    assert recovered.returncode == 0, recovered.stderr
    assert not (state / "last_error.json").exists()
    assert read_json(state / "status.json") == status1
    assert (state / "history.jsonl").read_bytes() == before_history


def test_reference_expansion_and_dependency_closure_are_semantic(tmp_path: Path) -> None:
    """References resolve only through dependency closure and feed metadata."""
    build_gateway()
    manifest = tmp_path / "reference-manifest.json"
    state = tmp_path / "state"
    out = tmp_path / "rendered"
    services = [
        {"name": "root", "env": {"REGION": "north", "ZONE": "7"}, "restart": "no"},
        {"name": "cache", "after": ["root"], "env": {"SCOPE": "${root.REGION}"}, "restart": "on-failure"},
        {"name": "api", "after": ["cache"], "env": {"ROUTE": "${root.REGION}-${cache.SCOPE}-${root.ZONE}"}, "restart": "always"},
        {"name": "audit", "after": ["root"], "env": {}, "restart": "no"},
    ]
    write_manifest(manifest, services)
    for svc in services:
        write_unit(svc["name"], svc.get("after", []), svc.get("restart", "no"))

    result = run_gateway(manifest, state, out)
    assert result.returncode == 0, result.stderr
    assert (out / "order.txt").read_text(encoding="utf-8").splitlines() == ["root", "cache", "api", "audit"]
    assert (out / "configs" / "api.env").read_text(encoding="utf-8") == "ROUTE=north-north-7\n"
    assert not (out / "configs" / "audit.env").read_text(encoding="utf-8")
    activation = read_json(out / "activation.json")
    by_name = {entry["name"]: entry for entry in activation["services"]}
    assert by_name["api"]["dependency_closure"] == ["root", "cache"]
    assert by_name["audit"]["dependency_closure"] == ["root"]


def test_unit_restart_validation_and_fingerprint_are_restart_safe(tmp_path: Path) -> None:
    """Unit restart drift is validated before publish and participates in generations."""
    build_gateway()
    state = tmp_path / "state"
    out = tmp_path / "rendered"
    first = run_gateway(MANIFEST, state, out)
    assert first.returncode == 0, first.stderr
    status1 = read_json(state / "status.json")
    before_out = snapshot_tree(out)
    before_status = (state / "status.json").read_bytes()

    dropin = SYSTEMD / "gateway.service.d" / "20-render.conf"
    original_dropin = dropin.read_text(encoding="utf-8")
    dropin.write_text(original_dropin + "Restart=on-failure\n", encoding="utf-8")
    try:
        failed = run_gateway(MANIFEST, state, out)
    finally:
        dropin.write_text(original_dropin, encoding="utf-8")
    assert failed.returncode != 0
    assert snapshot_tree(out) == before_out
    assert (state / "status.json").read_bytes() == before_status
    error = read_json(state / "last_error.json")["error"]
    assert "Restart" in error and "gateway" in error

    unit = SYSTEMD / "gateway.service"
    original_unit = unit.read_text(encoding="utf-8")
    unit.write_text(original_unit + "# rotated by site operator\n", encoding="utf-8")
    try:
        changed = run_gateway(MANIFEST, state, out)
    finally:
        unit.write_text(original_unit, encoding="utf-8")
    assert changed.returncode == 0, changed.stderr
    status2 = read_json(state / "status.json")
    assert status2["generation"] == status1["generation"] + 1
    assert status2["fingerprint"] != status1["fingerprint"]
    history = read_history(state / "history.jsonl")
    assert [entry["generation"] for entry in history] == [1, 2]
    assert history[-1]["profile_version"] == EXPECTED_VERSION
    assert history[-1]["fingerprint"] == status2["fingerprint"]
    assert history[-1]["services"] == EXPECTED_ORDER


def test_unit_dropins_are_effective_and_fingerprinted(tmp_path: Path) -> None:
    """Systemd drop-ins participate in validation, rollback, and generation changes."""
    build_gateway()
    state = tmp_path / "state"
    out = tmp_path / "rendered"
    first = run_gateway(MANIFEST, state, out)
    assert first.returncode == 0, first.stderr
    status1 = read_json(state / "status.json")
    before_out = snapshot_tree(out)
    before_status = (state / "status.json").read_bytes()

    dropin = SYSTEMD / "gateway.service.d" / "20-render.conf"
    original_dropin = dropin.read_text(encoding="utf-8")
    dropin.write_text("[Service]\nEnvironmentFile=/tmp/gateway.env\n", encoding="utf-8")
    try:
        failed = run_gateway(MANIFEST, state, out)
    finally:
        dropin.write_text(original_dropin, encoding="utf-8")
    assert failed.returncode != 0
    assert snapshot_tree(out) == before_out
    assert (state / "status.json").read_bytes() == before_status
    error = read_json(state / "last_error.json")["error"]
    assert "EnvironmentFile" in error and "gateway" in error

    policy = SYSTEMD / "firewall.service.d" / "10-policy.conf"
    original_policy = policy.read_text(encoding="utf-8")
    policy.write_text(original_policy + "# rotated by site operator\n", encoding="utf-8")
    try:
        changed = run_gateway(MANIFEST, state, out)
    finally:
        policy.write_text(original_policy, encoding="utf-8")
    assert changed.returncode == 0, changed.stderr
    status2 = read_json(state / "status.json")
    assert status2["generation"] == status1["generation"] + 1
    assert status2["fingerprint"] != status1["fingerprint"]
    assert read_history(state / "history.jsonl")[-1]["generation"] == status2["generation"]


def test_invalid_admin_edits_abort_without_publishing(tmp_path: Path) -> None:
    """Invalid graph, restart, env, and reference edits are rejected before publishing."""
    build_gateway()
    outside_health = tmp_path / "ready.txt"
    outside_health.write_text("ready\n", encoding="utf-8")
    cases = [
        ([{"name": "a", "after": ["b"], "env": {"OK": "1"}}, {"name": "b", "after": ["a"], "env": {"OK": "1"}}], {}),
        ([{"name": "orphan", "after": ["missing"], "env": {"OK": "1"}}], {}),
        ([{"name": "bad_name", "env": {"OK": "1"}}], {}),
        ([{"name": "bad-restart", "env": {"OK": "1"}, "restart": "sometimes"}], {}),
        ([{"name": "lower-env", "env": {"lower": "1"}}], {}),
        ([{"name": "digit-env", "env": {"1BAD": "1"}}], {}),
        ([{"name": "dup", "env": {"OK": "1"}}, {"name": "dup", "env": {"OK": "2"}}], {}),
        ([{"name": "bad-env", "env": {"BAD-KEY": "1"}}], {}),
        ([{"name": "bad-value", "env": {"OK": "line1\nline2"}}], {}),
        ([{"name": "self", "after": ["self"], "env": {"OK": "1"}}], {}),
        ([{"name": "repeat", "after": ["dep", "dep"], "env": {"OK": "1"}}, {"name": "dep", "env": {"OK": "1"}}], {}),
        ([{"name": "escape", "env": {"OK": "1"}, "health_path": str(outside_health)}], {}),
        ([{"name": "a", "env": {"OK": "${b.OK}"}}, {"name": "b", "env": {"OK": "1"}}], {"a": []}),
        ([{"name": "a", "env": {"OK": "${a.OK}"}}], {"a": []}),
    ]
    for idx, (services, units) in enumerate(cases):
        manifest = tmp_path / f"manifest-{idx}.json"
        state = tmp_path / f"state-{idx}"
        out = tmp_path / f"out-{idx}"
        write_manifest(manifest, services)
        for name, after in units.items():
            write_unit(name, after)
        result = run_gateway(manifest, state, out)
        assert result.returncode != 0, f"case {idx} unexpectedly succeeded"
        assert not (out / "order.txt").exists()
        assert not (state / "status.json").exists()
        assert "error" in read_json(state / "last_error.json")


def teardown_module() -> None:
    """Remove any accidental run directory created by manual contract experiments."""
    shutil.rmtree("/app/run", ignore_errors=True)
