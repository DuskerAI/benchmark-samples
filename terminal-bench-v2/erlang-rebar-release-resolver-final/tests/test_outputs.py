import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path("/opt/rebar_resolver")


def run_cmd(cmd, **kwargs):
    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    return subprocess.run(cmd, text=True, capture_output=True, env=env, check=False, **kwargs)


def build_tool():
    result = run_cmd(["make", "-C", str(ROOT)])
    assert result.returncode == 0, result.stderr + result.stdout
    exe = ROOT / "bin" / "r3rel"
    assert exe.exists() and os.access(exe, os.X_OK)
    assert_selfcheck_silent()
    return exe


def assert_selfcheck_silent():
    selfcheck = run_cmd([str(ROOT / "bin" / "r3rel"), "--selfcheck"])
    assert selfcheck.returncode == 0, selfcheck.stderr + selfcheck.stdout
    assert selfcheck.stdout == ""
    assert selfcheck.stderr == ""


def test_selfcheck_silent():
    """The required selfcheck exits successfully without writing either stream."""
    build_tool()


def write_workspace(tmp_path, body):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "rebar.config").write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return ws


def run_resolver(exe, ws, profile, out):
    out.mkdir(parents=True, exist_ok=True)
    return run_cmd([str(exe), "--workspace", str(ws), "--profile", profile, "--out", str(out)])


def load_manifest(out):
    with (out / "manifest.json").open(encoding="utf-8") as f:
        data = json.load(f)
    lock_lines = (out / "manifest.lock").read_text(encoding="utf-8").splitlines()
    return data, lock_lines


def names(data):
    return [p["name"] for p in data["packages"]]


def by_name(data):
    return {p["name"]: p for p in data["packages"]}


def assert_before(data, left, right):
    order = names(data)
    assert order.index(left) < order.index(right), order


def test_release_a(tmp_path):
    """The bundled workspace yields the requested offline release manifest."""
    exe = build_tool()
    out = tmp_path / "out"
    result = run_resolver(exe, ROOT / "fixtures" / "workspace", "prod", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, lock_lines = load_manifest(out)
    pkgs = by_name(data)
    assert data["release"] == "ship"
    assert data["profile"] == "prod"
    assert set(pkgs) == {"gateway", "core", "audit", "codec"}
    assert pkgs["core"]["version"] == "2.1.0"
    assert pkgs["core"]["provenance"] == "override"
    assert pkgs["codec"]["version"] == "1.1.0"
    assert pkgs["codec"]["provenance"] == "selected"
    assert_before(data, "codec", "audit")
    assert_before(data, "codec", "core")
    assert_before(data, "core", "gateway")
    assert_before(data, "audit", "gateway")
    assert lock_lines == [
        f"{p['name']} {p['version']} {p['source']} {p['checksum']} {p['provenance']}"
        for p in data["packages"]
    ]


def test_release_b(tmp_path):
    """Profile stacks and retained locks agree with the manifest contract."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [gateway]}.
        {app, gateway, "1.0.0", [{lib, ">=1.0.0,<3.0.0"}], []}.
        {hex, lib, "1.4.0", [], "l140"}.
        {hex, lib, "1.5.0", [], "l150"}.
        {hex, lib, "2.0.0", [], "l200"}.
        {lock, [{lib, "1.4.0", hex, "l140"}]}.
        {profile, green, [{override, lib, "1.5.0"}]}.
        {profile, blue, [{override, lib, "2.0.0"}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "green,blue", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, _ = load_manifest(out)
    pkgs = by_name(data)
    assert pkgs["lib"]["version"] == "2.0.0"
    assert pkgs["lib"]["provenance"] == "override"
    out2 = tmp_path / "out2"
    result2 = run_resolver(exe, ws, "default", out2)
    assert result2.returncode == 0, result2.stderr + result2.stdout
    data2, _ = load_manifest(out2)
    assert by_name(data2)["lib"]["version"] == "1.4.0"
    assert by_name(data2)["lib"]["provenance"] == "lock"


def test_release_boundaries_and_override_conflict(tmp_path):
    """Inclusive upper bounds and invalid overrides follow the same constraint contract."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [gateway]}.
        {app, gateway, "1.0.0", [{leaf, ">=1.0.0,<=1.2.0"}], []}.
        {hex, leaf, "1.1.0", [], "l110"}.
        {hex, leaf, "1.2.0", [], "l120"}.
        {hex, leaf, "1.3.0", [], "l130"}.
        {profile, forced, [{override, leaf, "1.3.0"}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, _ = load_manifest(out)
    assert by_name(data)["leaf"]["version"] == "1.2.0"
    assert by_name(data)["leaf"]["provenance"] == "selected"

    bad = run_resolver(exe, ws, "forced", out)
    assert bad.returncode == 21
    assert sorted(p.name for p in out.iterdir()) == ["conflict.json"]
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict == {"package": "leaf", "constraints": [">=1.0.0,<=1.2.0"]}


def test_release_c(tmp_path):
    """Optional applications, exclusions, and cycles settle to a stable release."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, edge, [api]}.
        {app, api, "1.0.0", [{auth, ">=1.0.0,<2.0.0"}], [audit]}.
        {hex, auth, "1.0.0", [{core, ">=1.0.0,<2.0.0"}], "auth100"}.
        {hex, core, "1.0.0", [{auth, ">=1.0.0,<2.0.0"}], "core100"}.
        {hex, audit, "1.0.0", [{core, ">=1.0.0"}], "audit100"}.
        {profile, prod, [{include_optional, [audit]}, {exclude, [audit]}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "prod", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, _ = load_manifest(out)
    assert names(data) == ["auth", "core", "api"]


def test_release_d(tmp_path):
    """Version ordering and lock reconciliation stay deterministic."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, pre, [client]}.
        {app, client, "1.0.0", [{codec, ">=1.2.0-rc.1,<1.2.0"}, {plug, ">=1.0.0,<2.0.0"}], []}.
        {hex, codec, "1.2.0-rc.1", [], "c-rc1"}.
        {hex, codec, "1.2.0-rc.2", [], "c-rc2"}.
        {hex, codec, "1.2.0", [], "c-final"}.
        {hex, plug, "1.0.0", [], "p100"}.
        {hex, plug, "1.1.0", [], "p110"}.
        {lock, [{codec, "1.2.0-rc.1", hex, "c-rc1"}, {plug, "1.0.0", hex, "stale"}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, _ = load_manifest(out)
    pkgs = by_name(data)
    assert pkgs["codec"]["version"] == "1.2.0-rc.1"
    assert pkgs["codec"]["provenance"] == "lock"
    assert pkgs["plug"]["version"] == "1.1.0"
    assert pkgs["plug"]["provenance"] == "selected"
    assert_before(data, "codec", "client")
    assert_before(data, "plug", "client")


def test_release_e(tmp_path):
    """Conflicting bounds use the documented release failure contract."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, bad, [left, right]}.
        {app, left, "1.0.0", [{shared, ">=1.0.0,<2.0.0"}], []}.
        {app, right, "1.0.0", [{shared, ">=2.0.0,<3.0.0"}], []}.
        {hex, shared, "1.5.0", [], "s150"}.
        {hex, shared, "2.5.0", [], "s250"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 21
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict["package"] == "shared"
    assert conflict["constraints"] == [">=1.0.0,<2.0.0", ">=2.0.0,<3.0.0"]


def test_release_f(tmp_path):
    """Running the resolver twice leaves the same release artifacts."""
    exe = build_tool()
    ws_src = ROOT / "fixtures" / "workspace"
    ws = tmp_path / "ws"
    shutil.copytree(ws_src, ws)
    out = tmp_path / "out"
    first = run_resolver(exe, ws, "prod", out)
    assert first.returncode == 0, first.stderr + first.stdout
    snapshot = {
        "json": (out / "manifest.json").read_bytes(),
        "lock": (out / "manifest.lock").read_bytes(),
    }
    second = run_resolver(exe, ws, "prod", out)
    assert second.returncode == 0, second.stderr + second.stdout
    assert snapshot["json"] == (out / "manifest.json").read_bytes()
    assert snapshot["lock"] == (out / "manifest.lock").read_bytes()


def test_release_output_replacement(tmp_path):
    """Output replacement and original conflict ranges survive satisfiable/conflict reruns."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, bad, [left, right]}.
        {release, ok, [root]}.
        {app, root, "1.0.0", [{leaf, ">=1.0.0,<2.0.0"}], []}.
        {app, left, "1.0.0", [{leaf, ">=1.1.0,<1.2.0"}], []}.
        {app, right, "1.0.0", [{leaf, ">=1.2.0,<1.3.0"}], []}.
        {hex, leaf, "1.0.5", [], "l105"}.
        {hex, leaf, "1.1.5", [], "l115"}.
        {hex, leaf, "1.2.5", [], "l125"}.
        {profile, badcase, [{release, bad, [left, right]}]}.
        """,
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.tmp").write_text("old", encoding="utf-8")

    ok = run_resolver(exe, ws, "default", out)
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "manifest.lock"]
    assert by_name(load_manifest(out)[0])["leaf"]["version"] == "1.2.5"

    bad = run_resolver(exe, ws, "badcase", out)
    assert bad.returncode == 21
    assert sorted(p.name for p in out.iterdir()) == ["conflict.json"]
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict["package"] == "leaf"
    assert conflict["constraints"] == [">=1.1.0,<1.2.0", ">=1.2.0,<1.3.0"]


def test_release_g(tmp_path):
    """Resolution backtracks when the newest local choice breaks transitive bounds."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [gateway]}.
        {app, gateway, "1.0.0", [{alpha, ">=1.0.0,<3.0.0"}, {beta, ">=1.0.0,<2.0.0"}], []}.
        {hex, alpha, "1.0.0", [{codec, ">=1.0.0,<2.0.0"}], "a100"}.
        {hex, alpha, "2.0.0", [{codec, ">=2.0.0,<3.0.0"}], "a200"}.
        {hex, beta, "1.0.0", [{codec, ">=1.5.0,<2.0.0"}], "b100"}.
        {hex, codec, "1.4.0", [], "c140"}.
        {hex, codec, "1.5.0", [], "c150"}.
        {hex, codec, "2.1.0", [], "c210"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 0, result.stderr + result.stdout
    data, _ = load_manifest(out)
    pkgs = by_name(data)
    assert pkgs["alpha"]["version"] == "1.0.0"
    assert pkgs["codec"]["version"] == "1.5.0"
    assert_before(data, "codec", "alpha")
    assert_before(data, "codec", "beta")
    assert_before(data, "alpha", "gateway")
    assert_before(data, "beta", "gateway")


def test_release_h(tmp_path):
    """Conflict reports preserve original multi-bound range strings exactly."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, bad, [left, right, anchor]}.
        {app, left, "1.0.0", [{shared, ">=1.0.0, <2.0.0"}], []}.
        {app, right, "1.0.0", [{shared, ">=2.0.0,<3.0.0"}], []}.
        {app, anchor, "1.0.0", [{shared, "=3.1.0"}], []}.
        {hex, shared, "1.5.0", [], "s150"}.
        {hex, shared, "2.5.0", [], "s250"}.
        {hex, shared, "3.1.0", [], "s310"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 21
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict == {
        "package": "shared",
        "constraints": [">=1.0.0, <2.0.0", ">=2.0.0,<3.0.0", "=3.1.0"],
    }


def test_conflict_collects_ranges_past_first_unsatisfied_bound(tmp_path):
    """Conflict reports include incoming ranges from later reachable dependents."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, bad, [gateway]}.
        {app, gateway, "1.0.0", [{left, ">=1.0.0,<2.0.0"}, {zz_anchor, ">=1.0.0,<2.0.0"}], []}.
        {hex, left, "1.0.0", [{shared, ">=9.0.0, <10.0.0"}], "left100"}.
        {hex, zz_anchor, "1.0.0", [{shared, "=1.0.0"}], "anchor100"}.
        {hex, shared, "1.0.0", [], "shared100"}.
        {hex, shared, "2.0.0", [], "shared200"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 21
    assert sorted(p.name for p in out.iterdir()) == ["conflict.json"]
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict == {
        "package": "shared",
        "constraints": [">=9.0.0, <10.0.0", "=1.0.0"],
    }


def test_profile_package_definition_replaces_stale_lock(tmp_path):
    """Later profile package definitions win and stale locks stop matching."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [root]}.
        {app, root, "1.0.0", [{lib, ">=1.0.0,<2.0.0"}], []}.
        {hex, lib, "1.0.0", [], "old-checksum"}.
        {lock, [{lib, "1.0.0", hex, "old-checksum"}]}.
        {profile, refresh, [{hex, lib, "1.0.0", [], "new-checksum"}]}.
        """,
    )

    default_out = tmp_path / "default-out"
    default = run_resolver(exe, ws, "default", default_out)
    assert default.returncode == 0, default.stderr + default.stdout
    default_lib = by_name(load_manifest(default_out)[0])["lib"]
    assert default_lib["checksum"] == "old-checksum"
    assert default_lib["provenance"] == "lock"

    refresh_out = tmp_path / "refresh-out"
    refresh = run_resolver(exe, ws, "refresh", refresh_out)
    assert refresh.returncode == 0, refresh.stderr + refresh.stdout
    refresh_lib = by_name(load_manifest(refresh_out)[0])["lib"]
    assert refresh_lib["checksum"] == "new-checksum"
    assert refresh_lib["provenance"] == "selected"


def test_semver_prerelease_hyphens_and_case_are_identifiers(tmp_path):
    """SemVer prerelease comparison separates only on dots and preserves case."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [root]}.
        {app, root, "1.0.0", [{lib, ">1.0.0-alpha.1,<1.0.0"}, {tool, ">=1.0.0-RC,<1.0.0-rc"}], []}.
        {hex, lib, "1.0.0-alpha-1", [], "hyphen"}.
        {hex, lib, "1.0.0-alpha.1", [], "dot"}.
        {hex, tool, "1.0.0-RC", [], "upper"}.
        {hex, tool, "1.0.0-rc", [], "lower"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 0, result.stderr + result.stdout
    pkgs = by_name(load_manifest(out)[0])
    assert pkgs["lib"]["version"] == "1.0.0-alpha-1"
    assert pkgs["tool"]["version"] == "1.0.0-RC"


def test_standalone_greater_than_and_lock_invalidation(tmp_path):
    """Standalone greater-than ranges and stale source/bounds locks are honored."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [root]}.
        {app, root, "1.0.0", [{lib, ">1.0.0"}, {plug, ">=2.0.0"}], []}.
        {app, lib, "1.1.0", [], []}.
        {hex, lib, "1.2.0", [], "hex-120"}.
        {hex, plug, "1.5.0", [], "plug-150"}.
        {hex, plug, "2.1.0", [], "plug-210"}.
        {lock, [{lib, "1.1.0", hex, "local"}, {plug, "1.5.0", hex, "plug-150"}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 0, result.stderr + result.stdout
    pkgs = by_name(load_manifest(out)[0])
    assert pkgs["lib"] == {
        "name": "lib",
        "version": "1.2.0",
        "source": "hex",
        "checksum": "hex-120",
        "provenance": "selected",
    }
    assert pkgs["plug"]["version"] == "2.1.0"
    assert pkgs["plug"]["provenance"] == "selected"


def test_optional_defaults_and_root_optional_exclusion(tmp_path):
    """Optional apps are omitted by default, while rooted optional apps survive exclusion."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [root, addon]}.
        {app, root, "1.0.0", [], [addon, skipped]}.
        {hex, addon, "1.0.0", [], "addon-100"}.
        {hex, skipped, "1.0.0", [], "skipped-100"}.
        {profile, prod, [{exclude, [addon, skipped]}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "prod", out)
    assert result.returncode == 0, result.stderr + result.stdout
    pkgs = by_name(load_manifest(out)[0])
    assert set(pkgs) == {"root", "addon"}
    assert pkgs["addon"]["source"] == "hex"
    assert pkgs["addon"]["checksum"] == "addon-100"


def test_conflict_cycle_collects_range_from_dependent_revisiting_target(tmp_path):
    """Conflict collection continues through the target to collect cyclic dependent ranges."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, bad, [root]}.
        {app, root, "1.0.0", [{shared, ">=2.0.0"}], []}.
        {hex, shared, "2.1.0", [{helper, ">=1.0.0"}], "shared-210"}.
        {hex, helper, "1.0.0", [{shared, "<2.0.0"}], "helper-100"}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "default", out)
    assert result.returncode == 21
    conflict = json.loads((out / "conflict.json").read_text(encoding="utf-8"))
    assert conflict == {"package": "shared", "constraints": [">=2.0.0", "<2.0.0"]}


def test_profile_package_definition_replaces_different_source(tmp_path):
    """A later package definition with the same version replaces an earlier different source."""
    exe = build_tool()
    ws = write_workspace(
        tmp_path,
        """
        {release, rel, [root]}.
        {app, root, "1.0.0", [{lib, "=1.0.0"}], []}.
        {hex, lib, "1.0.0", [], "hex-old"}.
        {lock, [{lib, "1.0.0", hex, "hex-old"}]}.
        {profile, localize, [{app, lib, "1.0.0", [], []}]}.
        """,
    )
    out = tmp_path / "out"
    result = run_resolver(exe, ws, "localize", out)
    assert result.returncode == 0, result.stderr + result.stdout
    lib = by_name(load_manifest(out)[0])["lib"]
    assert lib["source"] == "local"
    assert lib["checksum"] == "local"
    assert lib["provenance"] == "selected"
