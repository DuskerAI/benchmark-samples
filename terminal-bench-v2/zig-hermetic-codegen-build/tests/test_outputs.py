"""Behavioral verification for the Relay release build graph."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path("/app/relay")
TARGETS = ("x86_64-linux-musl", "aarch64-linux-musl")


def run(command: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run a command with a stable locale and capture diagnostics."""
    env = os.environ.copy()
    env.update({"LANG": "C", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0"})
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Give each check an isolated source tree and cache."""
    destination = tmp_path / "relay"
    shutil.copytree(PROJECT, destination, ignore=shutil.ignore_patterns(".zig-cache", "zig-out"))
    return destination


def parse_assignments(line: str) -> dict[str, str]:
    """Parse one whitespace-separated key-value record."""
    if line.startswith("route "):
        line = line.removeprefix("route ")
    if line.startswith("capability "):
        line = line.removeprefix("capability ")
    fields = dict(part.split("=", 1) for part in line.split())
    assert all(fields.values())
    return fields


def manifest_scalars(data: bytes) -> dict[str, str]:
    """Return leading scalar fields from build-meta.txt before record sections."""
    scalars: dict[str, str] = {}
    for raw_line in data.decode("utf-8").splitlines():
        if raw_line.startswith(("component ", "route ", "capability ", "policy ", "handoff ", "anchor ", "guard ", "attestation ")):
            break
        if raw_line.startswith("component="):
            break
        key, value = raw_line.split("=", 1)
        scalars[key] = value
    return scalars


def assert_manifest_basics(project: Path, target: str, manifest: Path) -> dict[str, str]:
    """Check the manifest shape and scalar values that make the bundle auditable."""
    data = manifest.read_bytes()
    fields = manifest_scalars(data)
    revision, channel, schema, components, target_profile, routes, capabilities, policies, handoffs, anchors, guards, attestations, exclusions, digest = metadata(project, target)
    expected_scalars = {
        "revision": revision,
        "channel": channel,
        "schema": str(schema),
        "target": target,
        "profile": target_profile["profile"],
        "epoch": str(int(target_profile["epoch"])),
        "link_mode": target_profile["link_mode"],
        "digest": digest,
        "route_count": str(len(routes)),
        "route_weight_total": str(sum(int(route["weight"]) for route in routes)),
        "segment_count": str(len(dict.fromkeys(route["segment"] for route in routes))),
        "capacity_total": str(sum(int(route["capacity"]) for route in routes)),
        "capability_count": str(len(capabilities)),
        "policy_count": str(len(policies)),
        "handoff_count": str(len(handoffs)),
        "anchor_count": str(len(anchors)),
        "guard_count": str(len(guards)),
        "attestation_count": str(len(attestations)),
        "exclusion_count": str(len(exclusions)),
    }
    for key, value in expected_scalars.items():
        assert fields[key] == value
    assert data.endswith(b"\n")
    lines = data.splitlines()
    assert sum(line.startswith(b"component=") for line in lines) == len(components)
    assert sum(line.startswith(b"route target=") for line in lines) == len(routes)
    assert sum(line.startswith(b"capability target=") for line in lines) == len(capabilities)
    return fields


def release_values(project: Path) -> tuple[str, str, int, bytes]:
    """Independently derive release values and preserve raw file bytes."""
    raw = (project / "config/release.cfg").read_bytes()
    values: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values["revision"], values["channel"], int(values["schema"]), raw


def component_records(project: Path) -> tuple[list[dict[str, str]], bytes]:
    """Read component records in their contract-defined manifest order."""
    raw = (project / "config/components.cfg").read_bytes()
    records = [parse_assignments(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert len(records) == 2
    assert {record["component"] for record in records} == {"relay", "relay-inspect"}
    return records, raw


def target_records(project: Path) -> tuple[list[dict[str, str]], bytes]:
    """Read target profile records without trusting the generator."""
    raw = (project / "config/targets.cfg").read_bytes()
    records = [parse_assignments(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert len(records) == 2
    assert {record["target"] for record in records} == set(TARGETS)
    return records, raw


def route_records(project: Path, target: str) -> tuple[list[dict[str, str]], bytes, bytes]:
    """Read target route records and join their public segment records."""
    raw = (project / "config/routes.cfg").read_bytes()
    segments_raw = (project / "config/segments.cfg").read_bytes()
    records = [parse_assignments(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    segment_records = [parse_assignments(line) for line in segments_raw.decode("utf-8").splitlines() if line.strip()]
    components, _ = component_records(project)
    component_names = {record["component"] for record in components}
    assert {record["target"] for record in records}.issubset(set(TARGETS))
    assert {record["component"] for record in records}.issubset(component_names)
    segment_by_route: dict[tuple[str, str, str], dict[str, str]] = {}
    for record in segment_records:
        assert record["target"] in TARGETS
        assert record["component"] in component_names
        key = (record["target"], record["component"], record["lane"])
        assert key not in segment_by_route
        segment_by_route[key] = record
    routes: list[dict[str, str]] = []
    for record in records:
        if record["target"] != target:
            continue
        segment = segment_by_route[(record["target"], record["component"], record["lane"])]
        routes.append(record | {"segment": segment["segment"], "capacity": segment["capacity"]})
    return routes, raw, segments_raw


def capability_records(project: Path, target: str) -> tuple[list[dict[str, str]], bytes]:
    """Read selected capability records in component and ordinal order."""
    raw = (project / "config/capabilities.cfg").read_bytes()
    all_records = [parse_assignments(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    components, _ = component_records(project)
    component_order = {record["component"]: index for index, record in enumerate(components)}
    assert {record["target"] for record in all_records}.issubset(set(TARGETS))
    assert {record["component"] for record in all_records}.issubset(component_order)
    selected = [record | {"_index": str(index)} for index, record in enumerate(all_records) if record["target"] == target]
    selected.sort(key=lambda record: (component_order[record["component"]], int(record["ordinal"]), int(record["_index"])))
    return selected, raw


def policy_records(
    project: Path, target: str, target_profile: dict[str, str], routes: list[dict[str, str]], capabilities: list[dict[str, str]]
) -> tuple[list[dict[str, str]], bytes]:
    """Select policies by joining target routes, capabilities, and epoch rules."""
    raw = (project / "config/policies.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    route_rank = {(record["component"], record["lane"]): index for index, record in enumerate(routes)}
    cap_rank = {(record["component"], record["name"]): index for index, record in enumerate(capabilities)}
    selected: list[dict[str, str]] = []
    for record in all_records:
        assert record["target"] in TARGETS
        route_key = (record["component"], record["lane"])
        cap_key = (record["component"], record["capability"])
        if record["target"] != target or int(target_profile["epoch"]) < int(record["min_epoch"]):
            continue
        if route_key in route_rank and cap_key in cap_rank:
            route = next(item for item in routes if item["component"] == record["component"] and item["lane"] == record["lane"])
            if record["segment"] != route["segment"]:
                continue
            selected.append(record)
    selected.sort(key=lambda record: (route_rank[(record["component"], record["lane"])], cap_rank[(record["component"], record["capability"])], int(record["_index"])))
    return selected, raw


def exclusion_records(project: Path, target: str, target_profile: dict[str, str], policies: list[dict[str, str]]) -> tuple[list[dict[str, str]], bytes]:
    """Select policy exclusions by identity and inclusive epoch windows."""
    raw = (project / "config/exclusions.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    policy_keys = {
        (record["component"], record["lane"], record["capability"], record["segment"])
        for record in policies
    }
    selected: list[dict[str, str]] = []
    epoch = int(target_profile["epoch"])
    for record in all_records:
        assert record["target"] in TARGETS
        key = (record["component"], record["lane"], record["capability"], record["segment"])
        if record["target"] == target and key in policy_keys and int(record["not_before"]) <= epoch <= int(record["not_after"]):
            selected.append(record)
    return selected, raw


def guard_records(project: Path, target: str, target_profile: dict[str, str], policies: list[dict[str, str]]) -> tuple[list[dict[str, str]], bytes]:
    """Select guard records by policy identity and epoch congruence."""
    raw = (project / "config/guards.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    policy_rank = {
        (record["component"], record["lane"], record["capability"], record["segment"]): index
        for index, record in enumerate(policies)
    }
    selected: list[dict[str, str]] = []
    epoch = int(target_profile["epoch"])
    for record in all_records:
        assert record["target"] in TARGETS
        key = (record["component"], record["lane"], record["capability"], record["segment"])
        divisor = int(record["divisor"])
        assert divisor > 0
        if record["target"] == target and key in policy_rank and epoch % divisor == int(record["remainder"]):
            selected.append(record)
    selected.sort(key=lambda record: (policy_rank[(record["component"], record["lane"], record["capability"], record["segment"])], int(record["strength"]), int(record["_index"])))
    return selected, raw


def attestation_records(
    project: Path,
    target: str,
    target_profile: dict[str, str],
    policies: list[dict[str, str]],
    guards: list[dict[str, str]],
    handoffs: list[dict[str, str]],
    anchors: list[dict[str, str]],
) -> tuple[list[dict[str, str]], bytes]:
    """Select attestation records by policy identity and inclusive epoch windows."""
    raw = (project / "config/attestations.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    policy_rank = {
        (record["component"], record["lane"], record["capability"], record["segment"]): index
        for index, record in enumerate(policies)
    }
    guard_counts: dict[tuple[str, str, str, str], int] = {}
    for guard in guards:
        key = (guard["component"], guard["lane"], guard["capability"], guard["segment"])
        guard_counts[key] = guard_counts.get(key, 0) + 1
    handoff_counts: dict[tuple[str, str, str, str], int] = {}
    for handoff in handoffs:
        key = (handoff["component"], handoff["lane"], handoff["capability"], handoff["segment"])
        handoff_counts[key] = handoff_counts.get(key, 0) + 1
    anchor_counts: dict[tuple[str, str, str, str], int] = {}
    for anchor in anchors:
        for key in ((anchor["component"], anchor["lane"], anchor["capability"], anchor["segment"]), (anchor["peer_component"], anchor["peer_lane"], anchor["peer_capability"], anchor["peer_segment"])):
            anchor_counts[key] = anchor_counts.get(key, 0) + 1
    selected: list[dict[str, str]] = []
    epoch = int(target_profile["epoch"])
    for record in all_records:
        assert record["target"] in TARGETS
        key = (record["component"], record["lane"], record["capability"], record["segment"])
        if record["target"] == target and key in policy_rank and int(record["not_before"]) <= epoch <= int(record["not_after"]):
            risk = int(record["risk"])
            selected.append(record | {"effective_risk": str(risk * (1 + guard_counts.get(key, 0) + handoff_counts.get(key, 0) + anchor_counts.get(key, 0)))})
    selected.sort(key=lambda record: (policy_rank[(record["component"], record["lane"], record["capability"], record["segment"])], -int(record["risk"]), int(record["_index"])))
    return selected, raw


def anchor_records(project: Path, target: str, policies: list[dict[str, str]]) -> tuple[list[dict[str, str]], bytes]:
    """Select release anchor records after exclusions are applied."""
    raw = (project / "config/anchors.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    policy_rank = {
        (record["component"], record["lane"], record["capability"], record["segment"]): index
        for index, record in enumerate(policies)
    }
    selected: list[dict[str, str]] = []
    for record in all_records:
        assert record["target"] in TARGETS
        key = (record["component"], record["lane"], record["capability"], record["segment"])
        peer_key = (record["peer_component"], record["peer_lane"], record["peer_capability"], record["peer_segment"])
        if record["target"] == target and key in policy_rank and peer_key in policy_rank and key != peer_key:
            selected.append(record)
    selected.sort(
        key=lambda record: (
            policy_rank[(record["component"], record["lane"], record["capability"], record["segment"])],
            int(record["phase"]),
            policy_rank[(record["peer_component"], record["peer_lane"], record["peer_capability"], record["peer_segment"])],
            -int(record["weight"]),
            int(record["_index"]),
        )
    )
    return selected, raw


def handoff_records(project: Path, target: str, policies: list[dict[str, str]]) -> tuple[list[dict[str, str]], bytes]:
    """Select cross-policy handoff records after exclusions are applied."""
    raw = (project / "config/handoffs.cfg").read_bytes()
    all_records = [parse_assignments(line) | {"_index": str(index)} for index, line in enumerate(raw.decode("utf-8").splitlines()) if line.strip()]
    policy_rank = {
        (record["component"], record["lane"], record["capability"], record["segment"]): index
        for index, record in enumerate(policies)
    }
    selected: list[dict[str, str]] = []
    for record in all_records:
        assert record["target"] in TARGETS
        key = (record["component"], record["lane"], record["capability"], record["segment"])
        peer_key = (record["peer_component"], record["peer_lane"], record["peer_capability"], record["peer_segment"])
        if record["target"] == target and key in policy_rank and peer_key in policy_rank and key != peer_key:
            selected.append(record)
    selected.sort(
        key=lambda record: (
            policy_rank[(record["component"], record["lane"], record["capability"], record["segment"])],
            policy_rank[(record["peer_component"], record["peer_lane"], record["peer_capability"], record["peer_segment"])],
            -int(record["weight"]),
            int(record["_index"]),
        )
    )
    return selected, raw


def score_policies(
    policies: list[dict[str, str]], routes: list[dict[str, str]], capabilities: list[dict[str, str]], guards: list[dict[str, str]], handoffs: list[dict[str, str]], anchors: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Attach policy scores after guard and handoff selection is known."""
    route_by_key = {(record["component"], record["lane"]): record for record in routes}
    cap_by_key = {(record["component"], record["name"]): record for record in capabilities}
    guards_by_policy: dict[tuple[str, str, str, str], int] = {}
    for guard in guards:
        key = (guard["component"], guard["lane"], guard["capability"], guard["segment"])
        guards_by_policy[key] = guards_by_policy.get(key, 0) + int(guard["strength"])
    handoff_weights_by_policy: dict[tuple[str, str, str, str], int] = {}
    for handoff in handoffs:
        key = (handoff["component"], handoff["lane"], handoff["capability"], handoff["segment"])
        handoff_weights_by_policy[key] = handoff_weights_by_policy.get(key, 0) + int(handoff["weight"])
    anchor_weights_by_policy: dict[tuple[str, str, str, str], int] = {}
    for anchor in anchors:
        for key in ((anchor["component"], anchor["lane"], anchor["capability"], anchor["segment"]), (anchor["peer_component"], anchor["peer_lane"], anchor["peer_capability"], anchor["peer_segment"])):
            anchor_weights_by_policy[key] = anchor_weights_by_policy.get(key, 0) + int(anchor["weight"])
    out: list[dict[str, str]] = []
    for policy in policies:
        route = route_by_key[(policy["component"], policy["lane"])]
        cap = cap_by_key[(policy["component"], policy["capability"])]
        key = (policy["component"], policy["lane"], policy["capability"], policy["segment"])
        score = int(policy["severity"]) * (int(route["weight"]) + int(route["capacity"]) + int(cap["ordinal"]) + guards_by_policy.get(key, 0) + handoff_weights_by_policy.get(key, 0) + anchor_weights_by_policy.get(key, 0))
        out.append(policy | {"score": str(score)})
    return out


def metadata(
    project: Path, target: str
) -> tuple[str, str, int, list[dict[str, str]], dict[str, str], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], str]:
    """Compute expected metadata from all public configuration inputs."""
    revision, channel, schema, release_raw = release_values(project)
    components, components_raw = component_records(project)
    targets, targets_raw = target_records(project)
    routes, routes_raw, segments_raw = route_records(project, target)
    capabilities, capabilities_raw = capability_records(project, target)
    target_profile = next(record for record in targets if record["target"] == target)
    policies, policies_raw = policy_records(project, target, target_profile, routes, capabilities)
    exclusions, exclusions_raw = exclusion_records(project, target, target_profile, policies)
    excluded_keys = {(record["component"], record["lane"], record["capability"], record["segment"]) for record in exclusions}
    policies = [record for record in policies if (record["component"], record["lane"], record["capability"], record["segment"]) not in excluded_keys]
    handoffs, handoffs_raw = handoff_records(project, target, policies)
    anchors, anchors_raw = anchor_records(project, target, policies)
    guards, guards_raw = guard_records(project, target, target_profile, policies)
    policies = score_policies(policies, routes, capabilities, guards, handoffs, anchors)
    attestations, attestations_raw = attestation_records(project, target, target_profile, policies, guards, handoffs, anchors)
    digest_input = b"\0".join((release_raw, components_raw, targets_raw, routes_raw, segments_raw, capabilities_raw, policies_raw, exclusions_raw, handoffs_raw, anchors_raw, guards_raw, attestations_raw, target.encode()))
    digest = hashlib.sha256(digest_input).hexdigest()
    return revision, channel, schema, components, target_profile, routes, capabilities, policies, handoffs, anchors, guards, attestations, exclusions, digest


def build(project: Path, prefix: Path, target: str, cache: Path | None = None) -> None:
    """Invoke the documented release entry point."""
    command = [
        "zig",
        "build",
        "bundle",
        f"-Dtarget={target}",
        "-Doptimize=ReleaseSafe",
        "--prefix",
        str(prefix),
    ]
    if cache is not None:
        command.extend(["--cache-dir", str(cache)])
    result = run(command, project)
    assert result.returncode == 0, f"build failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def expected_manifest(project: Path, target: str) -> bytes:
    """Construct the public manifest from source inputs."""
    revision, channel, schema, components, target_profile, routes, capabilities, policies, handoffs, anchors, guards, attestations, exclusions, digest = metadata(project, target)
    component_lines = "".join(
        f"component={record['component']} role={record['role']} priority={int(record['priority'])}\n"
        for record in components
    )
    route_lines = "".join(
        f"route target={record['target']} component={record['component']} "
        f"lane={record['lane']} weight={int(record['weight'])} segment={record['segment']} capacity={int(record['capacity'])}\n"
        for record in routes
    )
    capability_lines = "".join(
        f"capability target={record['target']} component={record['component']} "
        f"name={record['name']} ordinal={int(record['ordinal'])}\n"
        for record in capabilities
    )
    policy_lines = "".join(
        f"policy target={record['target']} component={record['component']} lane={record['lane']} "
        f"segment={record['segment']} capability={record['capability']} severity={int(record['severity'])} score={int(record['score'])}\n"
        for record in policies
    )
    handoff_lines = "".join(
        f"handoff target={record['target']} component={record['component']} lane={record['lane']} "
        f"segment={record['segment']} capability={record['capability']} handoff={record['handoff']} "
        f"peer_component={record['peer_component']} peer_lane={record['peer_lane']} "
        f"peer_segment={record['peer_segment']} peer_capability={record['peer_capability']} weight={int(record['weight'])}\n"
        for record in handoffs
    )
    anchor_lines = "".join(
        f"anchor target={record['target']} component={record['component']} lane={record['lane']} "
        f"segment={record['segment']} capability={record['capability']} anchor={record['anchor']} "
        f"peer_component={record['peer_component']} peer_lane={record['peer_lane']} "
        f"peer_segment={record['peer_segment']} peer_capability={record['peer_capability']} "
        f"phase={int(record['phase'])} weight={int(record['weight'])}\n"
        for record in anchors
    )
    guard_lines = "".join(
        f"guard target={record['target']} component={record['component']} lane={record['lane']} "
        f"segment={record['segment']} capability={record['capability']} guard={record['guard']} strength={int(record['strength'])}\n"
        for record in guards
    )
    attestation_lines = "".join(
        f"attestation target={record['target']} component={record['component']} lane={record['lane']} "
        f"segment={record['segment']} capability={record['capability']} attestation={record['attestation']} "
        f"risk={int(record['risk'])} effective_risk={int(record['effective_risk'])}\n"
        for record in attestations
    )
    policy_score = sum(int(record["score"]) for record in policies)
    policy_score = max(0, policy_score - sum(int(record["penalty"]) for record in exclusions))
    guard_strength = sum(int(record["strength"]) for record in guards)
    risk_total = sum(int(record["effective_risk"]) for record in attestations)
    return (
        f"revision={revision}\n"
        f"channel={channel}\n"
        f"schema={schema}\n"
        f"target={target}\n"
        f"profile={target_profile['profile']}\n"
        f"epoch={int(target_profile['epoch'])}\n"
        f"link_mode={target_profile['link_mode']}\n"
        f"digest={digest}\n"
        f"route_count={len(routes)}\n"
        f"route_weight_total={sum(int(record['weight']) for record in routes)}\n"
        f"segment_count={len(dict.fromkeys(record['segment'] for record in routes))}\n"
        f"capacity_total={sum(int(record['capacity']) for record in routes)}\n"
        f"capability_count={len(capabilities)}\n"
        f"policy_count={len(policies)}\n"
        f"handoff_count={len(handoffs)}\n"
        f"handoff_weight_total={sum(int(record['weight']) for record in handoffs)}\n"
        f"anchor_count={len(anchors)}\n"
        f"anchor_weight_total={sum(int(record['weight']) for record in anchors)}\n"
        f"guard_count={len(guards)}\n"
        f"attestation_count={len(attestations)}\n"
        f"exclusion_count={len(exclusions)}\n"
        f"policy_score={policy_score}\n"
        f"guard_strength_total={guard_strength}\n"
        f"risk_total={risk_total}\n"
        f"{component_lines}"
        f"{route_lines}"
        f"{capability_lines}"
        f"{policy_lines}"
        f"{handoff_lines}"
        f"{anchor_lines}"
        f"{guard_lines}"
        f"{attestation_lines}"
    ).encode()


def expected_info(project: Path, target: str, component: str) -> str:
    """Construct the required --build-info line for one program."""
    revision, channel, schema, components, target_profile, routes, capabilities, policies, handoffs, anchors, guards, attestations, exclusions, digest = metadata(project, target)
    record = next(item for item in components if item["component"] == component)
    manifest_digest = hashlib.sha256(expected_manifest(project, target)).hexdigest()
    policy_score = max(0, sum(int(policy["score"]) for policy in policies) - sum(int(record["penalty"]) for record in exclusions))
    return (
        f"component={component} role={record['role']} priority={int(record['priority'])} "
        f"revision={revision} channel={channel} schema={schema} target={target} "
        f"profile={target_profile['profile']} epoch={int(target_profile['epoch'])} "
        f"link_mode={target_profile['link_mode']} digest={digest} "
        f"route_count={len(routes)} route_weight_total={sum(int(route['weight']) for route in routes)} "
        f"segment_count={len(dict.fromkeys(route['segment'] for route in routes))} "
        f"capacity_total={sum(int(route['capacity']) for route in routes)} "
        f"capability_count={len(capabilities)} policy_count={len(policies)} "
        f"handoff_count={len(handoffs)} handoff_weight_total={sum(int(record['weight']) for record in handoffs)} "
        f"anchor_count={len(anchors)} anchor_weight_total={sum(int(record['weight']) for record in anchors)} "
        f"guard_count={len(guards)} attestation_count={len(attestations)} "
        f"exclusion_count={len(exclusions)} policy_score={policy_score} "
        f"guard_strength_total={sum(int(guard['strength']) for guard in guards)} "
        f"risk_total={sum(int(record['effective_risk']) for record in attestations)} manifest_digest={manifest_digest}\n"
    )


def expected_audit(project: Path, target: str) -> str:
    """Construct the relay-inspect audit success line."""
    *_, routes, capabilities, policies, handoffs, anchors, guards, attestations, exclusions, digest = metadata(project, target)
    policy_score = max(0, sum(int(policy["score"]) for policy in policies) - sum(int(record["penalty"]) for record in exclusions))
    return (
        f"manifest=ok target={target} digest={digest} route_count={len(routes)} "
        f"route_weight_total={sum(int(route['weight']) for route in routes)} "
        f"segment_count={len(dict.fromkeys(route['segment'] for route in routes))} "
        f"capacity_total={sum(int(route['capacity']) for route in routes)} "
        f"capability_count={len(capabilities)} policy_count={len(policies)} "
        f"handoff_count={len(handoffs)} handoff_weight_total={sum(int(record['weight']) for record in handoffs)} "
        f"anchor_count={len(anchors)} anchor_weight_total={sum(int(record['weight']) for record in anchors)} "
        f"guard_count={len(guards)} attestation_count={len(attestations)} "
        f"exclusion_count={len(exclusions)} policy_score={policy_score} "
        f"guard_strength_total={sum(int(guard['strength']) for guard in guards)} "
        f"risk_total={sum(int(record['effective_risk']) for record in attestations)}\n"
    )


def artifacts(prefix: Path, target: str) -> tuple[Path, Path, Path]:
    """Return the three documented installed artifact paths."""
    root = prefix / target
    return (
        root / "bin/relay",
        root / "bin/relay-inspect",
        root / "share/relay/build-meta.txt",
    )


def artifact_bytes(prefix: Path, target: str) -> tuple[bytes, bytes, bytes]:
    """Capture all installed artifact bytes for a target subtree."""
    return tuple(path.read_bytes() for path in artifacts(prefix, target))


def test_release_matrix(project: Path, tmp_path: Path) -> None:
    """Both target bundles coexist with exact layout, architecture, and auditable manifest shape."""
    prefix = tmp_path / "install"
    for target in TARGETS:
        build(project, prefix, target)

    machines = {
        "x86_64-linux-musl": "Advanced Micro Devices X86-64",
        "aarch64-linux-musl": "AArch64",
    }
    for target in TARGETS:
        relay, inspect, manifest = artifacts(prefix, target)
        assert relay.is_file() and os.access(relay, os.X_OK)
        assert inspect.is_file() and os.access(inspect, os.X_OK)
        assert_manifest_basics(project, target, manifest)
        target_root = prefix / target
        assert {path.relative_to(target_root) for path in target_root.rglob("*") if path.is_file()} == {
            Path("bin/relay"),
            Path("bin/relay-inspect"),
            Path("share/relay/build-meta.txt"),
        }
        for binary in (relay, inspect):
            header = run(["readelf", "-h", str(binary)], project)
            assert header.returncode == 0
            machine = next(
                (
                    line.partition(":")[2].strip()
                    for line in header.stdout.splitlines()
                    if line.partition(":")[0].strip() == "Machine"
                ),
                None,
            )
            assert machine == machines[target]


def test_installed_program_contract_and_audit(project: Path, tmp_path: Path) -> None:
    """The runnable x86 bundle reports parseable identity and audits the installed manifest bytes."""
    target = TARGETS[0]
    prefix = tmp_path / "install"
    build(project, prefix, target)
    relay, inspect, manifest = artifacts(prefix, target)
    manifest_fields = assert_manifest_basics(project, target, manifest)
    for binary, component in ((relay, "relay"), (inspect, "relay-inspect")):
        result = run([str(binary), "--build-info"], project)
        assert result.returncode == 0
        assert result.stderr == ""
        fields = parse_assignments(result.stdout.strip())
        assert fields["component"] == component
        assert fields["target"] == target
        assert fields["digest"] == manifest_fields["digest"]
        assert fields["manifest_digest"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        for key in ("revision", "channel", "schema", "profile", "epoch", "link_mode", "route_count", "capability_count", "policy_count"):
            assert fields[key] == manifest_fields[key]

    result = run([str(inspect), "--audit-manifest", str(manifest)], project)
    assert result.returncode == 0
    assert result.stderr == ""
    audit_fields = parse_assignments(result.stdout.strip().removeprefix("manifest=ok "))
    assert audit_fields["target"] == target
    assert audit_fields["digest"] == manifest_fields["digest"]
    for key in ("route_count", "capability_count", "policy_count", "guard_count", "attestation_count", "exclusion_count"):
        assert audit_fields[key] == manifest_fields[key]

    tampered = tmp_path / "tampered.txt"
    tampered.write_bytes(manifest.read_bytes() + b"extra\n")
    mismatch = run([str(inspect), "--audit-manifest", str(tampered)], project)
    assert mismatch.returncode != 0
    assert mismatch.stdout == ""


def test_rebuild_tracks_all_config_inputs(project: Path, tmp_path: Path) -> None:
    """A same-cache rebuild updates one target from all config files without touching another."""
    prefix = tmp_path / "install"
    cache = tmp_path / "cache"
    for target in TARGETS:
        build(project, prefix, target, cache)
    before_x86 = artifact_bytes(prefix, TARGETS[0])
    before_arm = artifact_bytes(prefix, TARGETS[1])

    (project / "config/release.cfg").write_text(
        "revision=2026.08.1\nchannel=candidate\nschema=23\n",
        encoding="utf-8",
    )
    (project / "config/components.cfg").write_text(
        "component=relay-inspect role=audit priority=30\n"
        "component=relay role=forwarder priority=5\n",
        encoding="utf-8",
    )
    (project / "config/targets.cfg").write_text(
        "target=aarch64-linux-musl profile=longhaul epoch=100 link_mode=static\n"
        "target=x86_64-linux-musl profile=staging epoch=101 link_mode=mostly-static\n",
        encoding="utf-8",
    )
    (project / "config/routes.cfg").write_text(
        "target=aarch64-linux-musl component=relay lane=field weight=70\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit weight=9\n"
        "target=x86_64-linux-musl component=relay lane=lab-forward weight=11\n",
        encoding="utf-8",
    )
    (project / "config/segments.cfg").write_text(
        "target=aarch64-linux-musl component=relay lane=field segment=field capacity=13\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capacity=17\n"
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capacity=19\n",
        encoding="utf-8",
    )
    (project / "config/capabilities.cfg").write_text(
        "target=x86_64-linux-musl component=relay name=egress ordinal=50\n"
        "target=x86_64-linux-musl component=relay-inspect name=scan ordinal=10\n"
        "target=aarch64-linux-musl component=relay name=field-egress ordinal=5\n",
        encoding="utf-8",
    )
    (project / "config/policies.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress min_epoch=100 severity=6\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan min_epoch=101 severity=4\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress min_epoch=100 severity=7\n",
        encoding="utf-8",
    )
    (project / "config/exclusions.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress exclusion=egress-freeze not_before=101 not_after=101 penalty=12\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan exclusion=stale not_before=1 not_after=100 penalty=99\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress exclusion=field-freeze not_before=100 not_after=100 penalty=4\n",
        encoding="utf-8",
    )
    (project / "config/handoffs.cfg").write_text(
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan handoff=audit-forward peer_component=relay peer_lane=lab-forward peer_segment=lab peer_capability=egress weight=31\n"
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress handoff=forward-audit peer_component=relay-inspect peer_lane=lab-audit peer_segment=lab peer_capability=scan weight=17\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress handoff=field-only peer_component=relay-inspect peer_lane=missing peer_segment=field peer_capability=missing weight=99\n",
        encoding="utf-8",
    )
    (project / "config/anchors.cfg").write_text(
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan anchor=audit-forward peer_component=relay peer_lane=lab-forward peer_segment=lab peer_capability=egress phase=2 weight=23\n"
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress anchor=forward-audit peer_component=relay-inspect peer_lane=lab-audit peer_segment=lab peer_capability=scan phase=1 weight=19\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress anchor=field-only peer_component=relay-inspect peer_lane=missing peer_segment=field peer_capability=missing phase=1 weight=99\n",
        encoding="utf-8",
    )
    (project / "config/guards.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress guard=burst divisor=3 remainder=2 strength=8\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan guard=trace divisor=4 remainder=1 strength=5\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress guard=field divisor=2 remainder=0 strength=13\n",
        encoding="utf-8",
    )
    (project / "config/attestations.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=lab-forward segment=lab capability=egress attestation=egress-window not_before=100 not_after=105 risk=6\n"
        "target=x86_64-linux-musl component=relay-inspect lane=lab-audit segment=lab capability=scan attestation=audit-window not_before=101 not_after=101 risk=9\n"
        "target=aarch64-linux-musl component=relay lane=field segment=field capability=field-egress attestation=field-window not_before=100 not_after=100 risk=12\n",
        encoding="utf-8",
    )
    build(project, prefix, TARGETS[0], cache)

    relay, inspect, manifest = artifacts(prefix, TARGETS[0])
    manifest_fields = assert_manifest_basics(project, TARGETS[0], manifest)
    assert artifact_bytes(prefix, TARGETS[0]) != before_x86
    assert artifact_bytes(prefix, TARGETS[1]) == before_arm
    for binary, component in ((relay, "relay"), (inspect, "relay-inspect")):
        result = run([str(binary), "--build-info"], project)
        assert result.returncode == 0
        assert result.stderr == ""
        fields = parse_assignments(result.stdout.strip())
        assert fields["component"] == component
        assert fields["target"] == TARGETS[0]
        assert fields["revision"] == manifest_fields["revision"]
        assert fields["digest"] == manifest_fields["digest"]
        assert fields["manifest_digest"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    result = run([str(inspect), "--audit-manifest", str(manifest)], project)
    assert result.returncode == 0
    audit_fields = parse_assignments(result.stdout.strip().removeprefix("manifest=ok "))
    assert audit_fields["target"] == TARGETS[0]
    assert audit_fields["digest"] == manifest_fields["digest"]


def test_target_profile_routes_and_capabilities_use_requested_triple(project: Path, tmp_path: Path) -> None:
    """Target profile, routes, and capabilities follow TRIPLE and the documented ordering rules."""
    (project / "config/components.cfg").write_text(
        "component=relay-inspect role=audit priority=30\n"
        "component=relay role=forwarder priority=5\n",
        encoding="utf-8",
    )
    (project / "config/targets.cfg").write_text(
        "target=aarch64-linux-musl profile=carrier epoch=77 link_mode=static\n"
        "target=x86_64-linux-musl profile=lab epoch=88 link_mode=relocatable\n",
        encoding="utf-8",
    )
    (project / "config/routes.cfg").write_text(
        "target=aarch64-linux-musl component=relay lane=arm-first weight=1\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit weight=2\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward weight=3\n",
        encoding="utf-8",
    )
    (project / "config/segments.cfg").write_text(
        "target=aarch64-linux-musl component=relay lane=arm-first segment=arm capacity=100\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capacity=7\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capacity=5\n",
        encoding="utf-8",
    )
    (project / "config/capabilities.cfg").write_text(
        "target=x86_64-linux-musl component=relay name=late-forward ordinal=50\n"
        "target=x86_64-linux-musl component=relay-inspect name=second-audit ordinal=20\n"
        "target=x86_64-linux-musl component=relay-inspect name=first-audit ordinal=10\n"
        "target=aarch64-linux-musl component=relay-inspect name=arm-only ordinal=1\n"
        "target=x86_64-linux-musl component=relay name=tie-forward ordinal=50\n",
        encoding="utf-8",
    )
    (project / "config/policies.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward min_epoch=80 severity=9\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit min_epoch=88 severity=2\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=wrong capability=tie-forward min_epoch=88 severity=99\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward min_epoch=88 severity=3\n"
        "target=x86_64-linux-musl component=relay lane=missing segment=forward capability=late-forward min_epoch=88 severity=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only min_epoch=1 severity=7\n",
        encoding="utf-8",
    )
    (project / "config/exclusions.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward exclusion=tie-freeze not_before=88 not_after=88 penalty=13\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit exclusion=expired not_before=1 not_after=10 penalty=99\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=wrong capability=late-forward exclusion=ignored not_before=88 not_after=88 penalty=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only exclusion=ignored not_before=1 not_after=100 penalty=99\n",
        encoding="utf-8",
    )
    (project / "config/handoffs.cfg").write_text(
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit handoff=audit-late peer_component=relay peer_lane=x86-forward peer_segment=forward peer_capability=late-forward weight=12\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward handoff=late-audit peer_component=relay-inspect peer_lane=x86-audit peer_segment=audit peer_capability=first-audit weight=5\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward handoff=excluded-source peer_component=relay-inspect peer_lane=x86-audit peer_segment=audit peer_capability=first-audit weight=99\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward handoff=missing-peer peer_component=relay peer_lane=x86-forward peer_segment=forward peer_capability=missing weight=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only handoff=arm-ignored peer_component=relay peer_lane=arm-first peer_segment=arm peer_capability=arm-only weight=99\n",
        encoding="utf-8",
    )
    (project / "config/anchors.cfg").write_text(
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit anchor=audit-late peer_component=relay peer_lane=x86-forward peer_segment=forward peer_capability=late-forward phase=2 weight=21\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward anchor=late-audit peer_component=relay-inspect peer_lane=x86-audit peer_segment=audit peer_capability=first-audit phase=1 weight=13\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward anchor=excluded-source peer_component=relay-inspect peer_lane=x86-audit peer_segment=audit peer_capability=first-audit phase=1 weight=99\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward anchor=missing-peer peer_component=relay peer_lane=x86-forward peer_segment=forward peer_capability=missing phase=3 weight=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only anchor=arm-ignored peer_component=relay peer_lane=arm-first peer_segment=arm peer_capability=arm-only phase=1 weight=99\n",
        encoding="utf-8",
    )
    (project / "config/guards.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward guard=small divisor=11 remainder=0 strength=1\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward guard=late divisor=8 remainder=0 strength=4\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit guard=audit divisor=4 remainder=0 strength=6\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=wrong capability=late-forward guard=ignored divisor=8 remainder=0 strength=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only guard=ignored divisor=1 remainder=0 strength=99\n",
        encoding="utf-8",
    )
    (project / "config/attestations.cfg").write_text(
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=late-forward attestation=late-window not_before=80 not_after=90 risk=4\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward attestation=tie-window not_before=88 not_after=88 risk=9\n"
        "target=x86_64-linux-musl component=relay-inspect lane=x86-audit segment=audit capability=first-audit attestation=audit-window not_before=88 not_after=88 risk=6\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=forward capability=tie-forward attestation=lower-risk not_before=88 not_after=88 risk=1\n"
        "target=x86_64-linux-musl component=relay lane=x86-forward segment=wrong capability=tie-forward attestation=ignored not_before=88 not_after=88 risk=99\n"
        "target=aarch64-linux-musl component=relay-inspect lane=arm-first segment=arm capability=arm-only attestation=ignored not_before=1 not_after=100 risk=99\n",
        encoding="utf-8",
    )
    prefix = tmp_path / "install"
    build(project, prefix, TARGETS[0], tmp_path / "cache")

    relay, inspect, manifest = artifacts(prefix, TARGETS[0])
    manifest_bytes = manifest.read_bytes()
    assert manifest_bytes == expected_manifest(project, TARGETS[0])
    assert b"arm-first" not in manifest_bytes
    assert b"arm-only" not in manifest_bytes
    assert manifest_bytes.index(b"name=first-audit") < manifest_bytes.index(b"name=second-audit")
    assert manifest_bytes.index(b"component=relay-inspect name=second-audit") < manifest_bytes.index(b"component=relay name=late-forward")
    assert manifest_bytes.index(b"name=late-forward") < manifest_bytes.index(b"name=tie-forward")
    assert b"component=relay lane=missing" not in manifest_bytes
    assert b"segment=wrong" not in manifest_bytes
    assert b"segment_count=2\ncapacity_total=12\n" in manifest_bytes
    assert b"policy_count=2\n" in manifest_bytes
    assert b"handoff_count=2\nhandoff_weight_total=17\n" in manifest_bytes
    assert b"anchor_count=2\nanchor_weight_total=34\n" in manifest_bytes
    assert b"guard_count=2\n" in manifest_bytes
    assert b"attestation_count=2\nexclusion_count=1\n" in manifest_bytes
    assert b"attestation=tie-window" not in manifest_bytes
    assert b"attestation=lower-risk" not in manifest_bytes
    assert b"effective_risk=20" in manifest_bytes
    assert b"guard=small" not in manifest_bytes
    assert b"handoff=excluded-source" not in manifest_bytes
    assert b"handoff=missing-peer" not in manifest_bytes
    assert b"anchor=excluded-source" not in manifest_bytes
    assert b"anchor=missing-peer" not in manifest_bytes
    assert manifest_bytes.index(b"handoff=audit-late") < manifest_bytes.index(b"handoff=late-audit")
    assert manifest_bytes.index(b"anchor=audit-late") < manifest_bytes.index(b"anchor=late-audit")
    assert manifest_bytes.index(b"guard=audit") < manifest_bytes.index(b"guard=late")
    assert manifest_bytes.index(b"capability=first-audit") < manifest_bytes.index(b"capability=late-forward")
    for binary, component in ((relay, "relay"), (inspect, "relay-inspect")):
        result = run([str(binary), "--build-info"], project)
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout == expected_info(project, TARGETS[0], component)


def test_clean_builds_are_reproducible(project: Path, tmp_path: Path) -> None:
    """Independent empty-cache builds install byte-identical bundles."""
    target = TARGETS[0]
    first_prefix = tmp_path / "one" / "prefix"
    second_prefix = tmp_path / "a-different-root" / "prefix"
    build(project, first_prefix, target, tmp_path / "one" / "cache")
    build(project, second_prefix, target, tmp_path / "another-cache")
    first = artifacts(first_prefix, target)
    second = artifacts(second_prefix, target)
    assert [hashlib.sha256(path.read_bytes()).digest() for path in first] == [
        hashlib.sha256(path.read_bytes()).digest() for path in second
    ]
