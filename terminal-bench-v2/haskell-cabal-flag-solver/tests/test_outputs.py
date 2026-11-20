import hashlib
import json
import subprocess
from pathlib import Path


WORKSPACE = Path("/app/cabal-workspace")

EXPECTED_SOURCE_HASHES = {
    "apps/plan-report/app/Main.hs": "eca068c6eeb592e12b08b9f0d4ebe5b23304e908a81ec3800dca1803dce04956",
    "packages/api/src/Acme/Api.hs": "4408c2e07963d4c19579e188e7902fc5f6baa778bc9611f2bebb194ca52f633e",
    "packages/attestation/src-token/Acme/Attestation/Token.hs": "f26ca62c123065a4169f78f5a2b0c3aba4a691633b208dd33db5a32fc38dc9a5",
    "packages/attestation/src/Acme/Attestation.hs": "e8265681eb3f3296f009c9c8adb6ec06363254f634d063ff1530bc7af3dc7abe",
    "packages/codec/src/Acme/Codec.hs": "55b96657e77b2f5f8929e3b061e532fd48abb78bc580a00feab5cdb3d70be605",
    "packages/engine/src/Acme/Engine.hs": "db369c9a5c135c28837657713021c4c914bfd05cd217ab89f04fe06c66ae155c",
    "packages/manifest/src-fingerprint/Acme/Manifest/Fingerprint.hs": "07d013e59bd95491a143019cc0fe3f9989468d299490b4ecdd34ceb28f46bb0b",
    "packages/manifest/src/Acme/Manifest.hs": "9ab8ac094d9ac9b18dbc04ab4e0a52f199dab5156c8af5e4999ba135816c4b21",
    "packages/policy/src/Acme/Policy.hs": "74b6fc52603cc860d5a1ee9a0729483f30598cd47029862540064bd19101adcd",
    "packages/schema/src-internal/Acme/Schema/Fingerprint.hs": "a44882586f3e5d2f30674f4fdad9e6a028563bd150b252d7621768e58ee50cb7",
    "packages/schema/src/Acme/Schema.hs": "1f35fd246e238211bf9ef290933523770fa680cbe875ff373f0c83dafdbd085c",
    "tools/dependency-audit/app/Main.hs": "67aafe97298c3e2c4c6ba512e38d3bc092482c354f4b013e0605a4d78b9e384a",
    "tools/release-gate/app/Main.hs": "ebb70a2ce29fbbfdf5cdb1f502bf1b33f30d275c0d914ed4929f7c550e6e3815",
    "tools/workspace-probe/app/Main.hs": "be5cc65b93fe10b3e3faa5b6497e73d00b5da4cddcb405a79e5711aed6905eb3",
}

EXPECTED_VERSIONS = {
    "acme-api": "2.1.0",
    "acme-codec": "1.4.0",
    "acme-engine": "3.2.0",
    "acme-schema": "0.9.0",
    "acme-policy": "4.0.0",
    "acme-manifest": "5.2.0",
    "acme-attestation": "0.4.0",
    "plan-report": "1.0.0",
    "dependency-audit": "1.0.0",
    "workspace-probe": "1.0.0",
    "release-gate": "1.0.0",
}

EXPECTED_FLAGS = {
    "acme-codec": {"fast": True},
    "acme-engine": {"release": True},
    "acme-policy": {"strict": True, "signed": True},
    "acme-manifest": {"signed-metadata": True},
    "acme-attestation": {"local-kms": True, "reproducible": True},
}

EXPECTED_DEPENDENCY_EDGES = {
    "acme-engine": {"acme-api", "acme-codec"},
    "acme-policy": {"acme-schema", "acme-codec"},
    "acme-manifest": {"acme-schema"},
    "acme-attestation": {"acme-manifest", "acme-policy", "acme-codec"},
    "plan-report": {"acme-engine"},
    "dependency-audit": {"acme-api", "acme-codec"},
    "workspace-probe": {"acme-schema", "acme-policy"},
    "release-gate": {"acme-attestation", "acme-codec", "acme-manifest", "acme-policy"},
}


def cabal(*args, check=True):
    return subprocess.run(
        ["cabal", *args],
        cwd=WORKSPACE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
        timeout=600,
    )


def executable(component):
    path = cabal("list-bin", component, "--offline").stdout.strip()
    assert path
    return Path(path)


def source_hashes():
    return {
        str(path.relative_to(WORKSPACE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in WORKSPACE.rglob("*.hs")
        if "dist-newstyle" not in path.relative_to(WORKSPACE).parts
    }


def load_plan():
    cabal("build", "all", "--offline")
    plan_path = WORKSPACE / "dist-newstyle" / "cache" / "plan.json"
    assert plan_path.is_file(), "cabal did not write dist-newstyle/cache/plan.json"
    return json.loads(plan_path.read_text())


def local_units(plan):
    units = {}
    for unit in plan.get("install-plan", []):
        name = unit.get("pkg-name")
        if name in EXPECTED_VERSIONS and unit.get("type") == "configured":
            units.setdefault(name, []).append(unit)
    return units


def flag_enabled(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "+", "enabled", "1"}


def test_haskell_sources_remain_the_supplied_sources():
    """Agents must not replace or add Haskell sources to fake the reports."""
    assert source_hashes() == EXPECTED_SOURCE_HASHES


def test_resolved_cabal_plan_preserves_versions_flags_and_edges():
    """The resolved offline plan must keep the intended local graph and flags."""
    units = local_units(load_plan())
    assert set(units) == set(EXPECTED_VERSIONS)

    for name, version in EXPECTED_VERSIONS.items():
        versions = {unit.get("pkg-version") for unit in units[name]}
        assert versions == {version}

    for name, flags in EXPECTED_FLAGS.items():
        for flag_name, expected in flags.items():
            selected = [unit.get("flags", {}) for unit in units[name]]
            matches = [flags[flag_name] for flags in selected if flag_name in flags]
            assert matches, f"{name} is missing flag {flag_name}"
            assert all(flag_enabled(value) is expected for value in matches)

    for name, deps in EXPECTED_DEPENDENCY_EDGES.items():
        rendered_deps = json.dumps(
            [unit.get("depends", []) for unit in units[name]], sort_keys=True
        )
        for dep in deps:
            assert dep in rendered_deps, f"{name} plan does not depend on {dep}"


def test_complete_workspace_builds_offline():
    """Every declared local package must participate in one offline build."""
    cabal("build", "all", "--offline")
    assert executable("exe:dependency-audit").is_file()
    assert executable("exe:plan-report").is_file()
    assert executable("exe:release-gate").is_file()
    assert executable("exe:workspace-probe").is_file()


def test_all_local_components_are_reachable_offline():
    """The project must expose each local library, sublibrary, and executable."""
    cabal(
        "build",
        "acme-api",
        "acme-codec",
        "acme-engine",
        "acme-schema",
        "acme-schema:fingerprint",
        "acme-policy",
        "acme-manifest",
        "acme-manifest:fingerprint",
        "acme-attestation",
        "acme-attestation:token",
        "exe:plan-report",
        "exe:dependency-audit",
        "exe:workspace-probe",
        "exe:release-gate",
        "--offline",
    )


def test_release_plan_report_uses_the_local_package_graph():
    """The release executable must reflect versions and flags across local libraries."""
    cabal("build", "exe:plan-report", "--offline")
    result = subprocess.run(
        [executable("exe:plan-report")], text=True, capture_output=True, check=True
    )
    assert result.stdout == "plan:api-2.1|codec-fast|engine-release\n"
    assert result.stderr == ""


def test_dependency_audit_is_available_and_consistent():
    """The tool package must be in the project and use the selected local instances."""
    cabal("build", "exe:dependency-audit", "--offline")
    result = subprocess.run(
        [executable("exe:dependency-audit")], text=True, capture_output=True, check=True
    )
    assert result.stdout == "audit:api=2.1;codec=1.4;mode=fast\n"
    assert result.stderr == ""


def run_probe(*args, check=True):
    cabal("build", "exe:workspace-probe", "--offline")
    return subprocess.run(
        [executable("exe:workspace-probe"), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def test_workspace_probe_resolves_public_and_sublibrary_components():
    """The probe must link the selected local package graph and all release flags."""
    result = run_probe()
    assert result.stdout == "probe:schema-0.9|api-2.1/wire-7|strict-signed\n"
    assert result.stderr == ""


def test_workspace_probe_dispatches_each_graph_value():
    """Each supported query must expose its value from the same linked components."""
    expected = {
        "schema": "probe:schema-0.9\n",
        "wire": "probe:api-2.1/wire-7\n",
        "policy": "probe:strict-signed\n",
    }
    for query, output in expected.items():
        result = run_probe(query)
        assert result.stdout == output, (
            f"workspace-probe {query!r}: expected {output!r}, got {result.stdout!r}"
        )
        assert result.stderr == "", f"workspace-probe {query!r} wrote stderr"


def test_workspace_probe_rejects_unsupported_queries():
    """Unsupported probe requests must fail without producing a report."""
    for args in [("unknown",), ("schema", "wire")]:
        result = run_probe(*args, check=False)
        assert result.returncode != 0
        assert result.stdout == ""


def run_gate(*args, check=True):
    cabal("build", "exe:release-gate", "--offline")
    return subprocess.run(
        [executable("exe:release-gate"), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def test_release_gate_links_manifest_fingerprint_policy_and_codec():
    """The gate tool must compose the full selected release graph."""
    result = run_gate()
    assert result.stdout == (
        "gate:manifest-5.2|schema-0.9/api-2.1/wire-7#attest|"
        "strict-signed|codec-fast|metadata-signed|kms-local|reproducible\n"
    )
    assert result.stderr == ""


def test_release_gate_dispatches_each_release_value():
    """Each gate query must expose one value from the same linked graph."""
    expected = {
        "manifest": "gate:manifest-5.2\n",
        "fingerprint": "gate:schema-0.9/api-2.1/wire-7\n",
        "policy": "gate:strict-signed\n",
        "codec": "gate:codec-fast\n",
        "seal": "gate:metadata-signed\n",
        "token": "gate:schema-0.9/api-2.1/wire-7#attest\n",
        "authority": "gate:kms-local\n",
        "stamp": "gate:reproducible\n",
    }
    for query, output in expected.items():
        result = run_gate(query)
        assert result.stdout == output, (
            f"release-gate {query!r}: expected {output!r}, got {result.stdout!r}"
        )
        assert result.stderr == "", f"release-gate {query!r} wrote stderr"


def test_release_gate_rejects_unsupported_queries():
    """Unsupported gate requests must fail without producing a report."""
    for args in [("unknown",), ("manifest", "seal"), ("token", "stamp")]:
        result = run_gate(*args, check=False)
        assert result.returncode != 0
        assert result.stdout == ""
