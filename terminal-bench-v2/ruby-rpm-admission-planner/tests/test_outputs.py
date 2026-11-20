import hashlib
import json
import subprocess
from pathlib import Path

APP = Path("/app/rpmgate")


def write_json(path, data):
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


def run_case(tmp_path, repo, request):
    repo_path = tmp_path / "repo.json"
    request_path = tmp_path / "request.json"
    lock_path = tmp_path / "out" / "lock.json"
    audit_path = tmp_path / "out" / "audit.json"
    write_json(repo_path, repo)
    write_json(request_path, request)
    repo_before = repo_path.read_bytes()
    request_before = request_path.read_bytes()
    subprocess.run([str(APP / "bin" / "build.sh")], check=True, cwd=APP)
    proc = subprocess.run(
        [
            str(APP / "bin" / "rpm-admit"),
            "--repo",
            str(repo_path),
            "--request",
            str(request_path),
            "--lock",
            str(lock_path),
            "--audit",
            str(audit_path),
        ],
        text=True,
        capture_output=True,
        check=False,
        cwd=APP,
    )
    assert repo_path.read_bytes() == repo_before
    assert request_path.read_bytes() == request_before
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else None
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
    return proc, lock, audit, lock_path, audit_path


def nevra(pkg):
    return f"{pkg['name']}-{pkg.get('epoch', 0)}:{pkg['version']}-{pkg.get('release', '')}.{pkg['arch']}"


def pkg(
    name,
    version,
    release,
    arch="x86_64",
    epoch=0,
    requires=None,
    provides=None,
    obsoletes=None,
    conflicts=None,
    module=None,
):
    row = {
        "name": name,
        "epoch": epoch,
        "version": version,
        "release": release,
        "arch": arch,
        "requires": requires or [],
        "provides": provides or [],
        "obsoletes": obsoletes or [],
        "conflicts": conflicts or [],
    }
    if module:
        row["module"] = module
    return row


def ordered_nevras(rows):
    return [nevra(row) for row in sorted(rows, key=lambda r: (r["name"], r["arch"], nevra(r)))]


def assert_lock_shape(lock, request, expected_nevras):
    assert lock["arch"] == request["arch"]
    got = [p["nevra"] for p in lock["packages"]]
    assert got == expected_nevras
    assert lock["checksum"] == hashlib.sha256("\n".join(sorted(got)).encode()).hexdigest()
    assert set(lock) == {"arch", "modules", "packages", "checksum"}
    for row in lock["packages"]:
        assert set(row) == {"name", "epoch", "version", "release", "arch", "nevra", "reason"}
        assert row["reason"] in {"requested", "dependency"}


def test_epoch_release_module_selection_and_canonical_files(tmp_path):
    """Admission uses active module streams, RPM EVR precedence, and deterministic JSON artifacts."""
    repo = {
        "module_defaults": {"nodejs": "18"},
        "packages": [
            pkg("web-stack", "1.0", "1.el9", requires=["nodejs-runtime >= 1:20.0-1", "openssl-libs >= 3:3.0.7-2"]),
            pkg("nodejs", "20.2.0", "4.el9", provides=["nodejs-runtime = 1:20.2.0-4"], module={"name": "nodejs", "stream": "20"}),
            pkg("nodejs", "18.19.0", "9.el9", provides=["nodejs-runtime = 1:18.19.0-9"], module={"name": "nodejs", "stream": "18"}),
            pkg("openssl-libs", "99.0", "1.el9", epoch=0),
            pkg("openssl-libs", "3.0.7", "2.el9", epoch=3),
        ],
    }
    request = {"arch": "x86_64", "enabled_streams": {"nodejs": "20"}, "install": ["web-stack"]}
    proc, lock, audit, lock_path, audit_path = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][1], repo["packages"][4]])
    assert_lock_shape(lock, request, expected)
    assert lock["modules"] == [{"name": "nodejs", "stream": "20", "source": "enabled"}]
    assert audit["status"] == "admitted"
    assert audit["requested"] == ["web-stack"]
    assert audit["rejected"] == []
    first_lock = lock_path.read_bytes()
    first_audit = audit_path.read_bytes()
    proc2, _, _, _, _ = run_case(tmp_path, {"packages": list(reversed(repo["packages"])), "module_defaults": repo["module_defaults"]}, request)
    assert proc2.returncode == 0
    assert lock_path.read_bytes() == first_lock
    assert audit_path.read_bytes() == first_audit


def test_provides_obsoletes_and_architecture_ties(tmp_path):
    """Admission accounts for replacement providers and native architecture preference in one closure."""
    repo = {
        "module_defaults": {"perl": "5.36"},
        "packages": [
            pkg("desktop-shell", "2.0", "1.el9", requires=["libjpeg >= 0:8-10", "theme-assets >= 0:5.1-1"]),
            pkg("libjpeg", "8", "9.el9"),
            pkg("libjpeg-turbo", "2.1.5", "7.el9", provides=["libjpeg = 0:8-10"], obsoletes=["libjpeg < 0:8-10"]),
            pkg("theme-assets", "5.1", "1.el9", arch="noarch"),
            pkg("theme-assets", "5.1", "1.el9", arch="x86_64"),
        ]
    }
    request = {"arch": "x86_64", "install": ["desktop-shell"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][2], repo["packages"][4]])
    assert_lock_shape(lock, request, expected)
    assert any(d["via"] == "provide" and d["selected"] == nevra(repo["packages"][2]) for d in audit["decisions"])
    assert all("libjpeg-0:8-9.el9.x86_64" != p["nevra"] for p in lock["packages"])
    assert lock["modules"] == [{"name": "perl", "stream": "5.36", "source": "default"}]


def test_requirement_operator_and_rpm_token_ordering_edges(tmp_path):
    """Admission applies every RPM requirement operator and EVR defaulting/token ordering rule."""
    repo = {
        "packages": [
            pkg(
                "policy-suite",
                "1.0",
                "1.el9",
                requires=[
                    "equal-lib = 1:2.0",
                    "greater-lib > 0:1.0-9",
                    "less-lib < 0:2.0-0",
                    "lte-lib <= 3.0-0",
                    "default-epoch = 4.2-1",
                    "token-lib >= 0:1.a-0",
                    "zero-lib = 0:1.007-0",
                ],
            ),
            pkg("equal-lib", "2.0", "", epoch=1),
            pkg("equal-lib", "2.0", "1.el9", epoch=1),
            pkg("greater-lib", "1.0", "10.el9"),
            pkg("greater-lib", "1.0", "9.el9"),
            pkg("less-lib", "1.9", "99.el9"),
            pkg("less-lib", "2.0", "1.el9"),
            pkg("lte-lib", "3.0", "0"),
            pkg("lte-lib", "3.0", "1"),
            pkg("default-epoch", "4.2", "1"),
            pkg("token-lib", "1.0", "0"),
            pkg("token-lib", "1.a", "0"),
            pkg("zero-lib", "1.7", "0"),
        ]
    }
    request = {"arch": "x86_64", "install": ["policy-suite"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected_rows = [
        repo["packages"][0],
        repo["packages"][1],
        repo["packages"][3],
        repo["packages"][5],
        repo["packages"][7],
        repo["packages"][9],
        repo["packages"][10],
        repo["packages"][12],
    ]
    assert_lock_shape(lock, request, ordered_nevras(expected_rows))
    selected_by_requirement = {d["requirement"]: d["selected"] for d in audit["decisions"]}
    assert selected_by_requirement["equal-lib = 1:2.0"] == nevra(repo["packages"][1])
    assert selected_by_requirement["greater-lib > 0:1.0-9"] == nevra(repo["packages"][3])
    assert selected_by_requirement["less-lib < 0:2.0-0"] == nevra(repo["packages"][5])
    assert selected_by_requirement["lte-lib <= 3.0-0"] == nevra(repo["packages"][7])
    assert selected_by_requirement["default-epoch = 4.2-1"] == nevra(repo["packages"][9])
    assert selected_by_requirement["token-lib >= 0:1.a-0"] == nevra(repo["packages"][10])
    assert selected_by_requirement["zero-lib = 0:1.007-0"] == nevra(repo["packages"][12])


def test_multilib_capabilities_are_admitted_without_collapsing_packages(tmp_path):
    """Admission keeps compatible multilib packages when requirements name both ABI widths."""
    repo = {
        "packages": [
            pkg("compat-runner", "1.4", "3.el9", requires=["libcodec.so.1()(64bit)", "libcodec.so.1()(32bit)"]),
            pkg("codec-libs", "4.0", "1.el9", arch="x86_64", provides=["libcodec.so.1()(64bit)"]),
            pkg("codec-libs", "4.0", "1.el9", arch="i686", provides=["libcodec.so.1()(32bit)"]),
            pkg("codec-libs", "4.0", "1.el9", arch="noarch", provides=["libcodec.so.1()(64bit)"]),
        ]
    }
    request = {"arch": "x86_64", "multilib": True, "install": ["compat-runner"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][1], repo["packages"][2]])
    assert_lock_shape(lock, request, expected)
    arches = sorted(p["arch"] for p in lock["packages"] if p["name"] == "codec-libs")
    assert arches == ["i686", "x86_64"]
    assert len([d for d in audit["decisions"] if d["selected"].startswith("codec-libs-")]) == 2


def test_checksum_and_selection_use_full_nevra_identity(tmp_path):
    """Admission can keep multiple same-name NEVRAs and hashes their lexical NEVRA order."""
    repo = {
        "packages": [
            pkg("bundle", "1.0", "1.el9", requires=["addon = 0:1.0-1.el9", "addon = 0:2.0-1.el9", "addon-abi()(32bit)"]),
            pkg("addon", "1.0", "1.el9", provides=["addon = 0:1.0-1.el9"]),
            pkg("addon", "2.0", "1.el9", provides=["addon = 0:2.0-1.el9"]),
            pkg("addon", "2.0", "1.el9", arch="i686", provides=["addon-abi()(32bit)"]),
            pkg("addon", "2.0", "1.el9", arch="noarch", provides=["addon = 0:2.0-1.el9"]),
        ]
    }
    request = {"arch": "x86_64", "multilib": True, "install": ["bundle"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][1], repo["packages"][2], repo["packages"][3]])
    assert_lock_shape(lock, request, expected)
    addon_rows = [p for p in lock["packages"] if p["name"] == "addon"]
    assert [p["nevra"] for p in addon_rows] == expected[:3]
    assert lock["checksum"] != hashlib.sha256("\n".join(expected).encode()).hexdigest()
    selected = {d["selected"] for d in audit["decisions"] if d["selected"].startswith("addon-")}
    assert selected == {nevra(repo["packages"][1]), nevra(repo["packages"][2]), nevra(repo["packages"][3])}


def test_rejected_admission_has_no_lock_and_reports_requirement(tmp_path):
    """Rejected requests leave no lock artifact and record a policy-shaped audit."""
    repo = {"module_defaults": {"ruby": "3.1"}, "packages": [pkg("ruby", "3.1.4", "1.el9", module={"name": "ruby", "stream": "3.1"})]}
    request = {"arch": "x86_64", "enabled_streams": {"ruby": "3.3"}, "install": ["ruby >= 0:3.3-1"]}
    proc, lock, audit, lock_path, _ = run_case(tmp_path, repo, request)
    assert proc.returncode != 0
    assert lock is None
    assert not lock_path.exists()
    assert audit["status"] == "rejected"
    assert audit["requested"] == ["ruby >= 0:3.3-1"]
    assert audit["decisions"] == []
    assert audit["rejected"] and audit["rejected"][0]["requirement"] == "ruby >= 0:3.3-1"
    assert audit["rejected"][0]["reason"] == "unsatisfied"


def test_conflicting_duplicate_nevras_are_not_admission_candidates(tmp_path):
    """Admission ignores conflicting duplicate NEVRA records before dependency closure selection."""
    repo = {
        "packages": [
            pkg("render-host", "1.0", "1.el9", requires=["driver >= 0:1.0-1"]),
            pkg("driver", "2.0", "1.el9", requires=["missing-kmod"]),
            pkg("driver", "2.0", "1.el9", provides=["driver = 0:2.0-1"]),
            pkg("driver", "1.5", "3.el9"),
        ]
    }
    request = {"arch": "x86_64", "install": ["render-host"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][3]])
    assert_lock_shape(lock, request, expected)
    assert all("driver-0:2.0-1.el9.x86_64" != p["nevra"] for p in lock["packages"])
    assert any(d["requirement"] == "driver >= 0:1.0-1" and d["selected"] == nevra(repo["packages"][3]) for d in audit["decisions"])


def test_conflicts_force_non_greedy_closure_with_dependency_cycle(tmp_path):
    """Admission backtracks from a newer candidate whose later dependency conflicts with the closure."""
    repo = {
        "packages": [
            pkg("control-plane", "1.0", "1.el9", requires=["engine >= 0:1.0-1", "policy-core = 0:3.0-1.el9"]),
            pkg("engine", "2.0", "1.el9", requires=["fast-path = 0:1.0-1"]),
            pkg("engine", "1.5", "4.el9", requires=["engine-loop = 0:1.5-4.el9"]),
            pkg("engine-loop", "1.5", "4.el9", provides=["engine-loop = 0:1.5-4.el9"], requires=["engine = 0:1.5-4.el9"]),
            pkg("fast-path", "1.0", "1.el9", conflicts=["policy-core >= 0:3.0-1.el9"]),
            pkg("policy-core", "3.0", "1.el9"),
        ]
    }
    request = {"arch": "x86_64", "install": ["control-plane"]}
    proc, lock, audit, _, _ = run_case(tmp_path, repo, request)
    assert proc.returncode == 0, proc.stderr
    expected = ordered_nevras([repo["packages"][0], repo["packages"][2], repo["packages"][3], repo["packages"][5]])
    assert_lock_shape(lock, request, expected)
    assert nevra(repo["packages"][1]) not in [p["nevra"] for p in lock["packages"]]
    assert nevra(repo["packages"][4]) not in [p["nevra"] for p in lock["packages"]]
    selected_by_requirement = {d["requirement"]: d["selected"] for d in audit["decisions"]}
    assert selected_by_requirement["engine >= 0:1.0-1"] == nevra(repo["packages"][2])
    assert selected_by_requirement["engine-loop = 0:1.5-4.el9"] == nevra(repo["packages"][3])


def test_direct_conflict_rejects_without_partial_lock(tmp_path):
    """Rejected admissions report conflicts and do not leave stale lock artifacts."""
    repo = {
        "packages": [
            pkg("storage-node", "4.0", "1.el9", requires=["journal-driver >= 0:2.0-1"]),
            pkg("journal-driver", "2.0", "1.el9", conflicts=["debug-shim"]),
            pkg("debug-shim", "1.0", "1.el9"),
        ]
    }
    request = {"arch": "x86_64", "install": ["storage-node", "debug-shim"]}
    proc, lock, audit, lock_path, _ = run_case(tmp_path, repo, request)
    assert proc.returncode != 0
    assert lock is None
    assert not lock_path.exists()
    assert audit["status"] == "rejected"
    assert audit["requested"] == ["storage-node", "debug-shim"]
    assert any(row["reason"] == "unsatisfied" for row in audit["rejected"])
