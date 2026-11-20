
import json
import shutil
import subprocess
from functools import cmp_to_key
from pathlib import Path
import hashlib
import re

APP = Path("/app/aptplan")
OUT = Path("/app/out")
ROOT = Path("/var/lib/aptmirror")
REQUEST = Path("/etc/aptplan/request.json")
PREFS = Path("/etc/aptplan/preferences.d")
PROTECTED_HASHES = {
  "/etc/aptplan/preferences.d/00-default.pref": "9000aa40d44139c6a5ca667b43a9f544d090bc911220d5b9396fb48981e3ae0b",
  "/etc/aptplan/preferences.d/40-codec.pref": "fa4e7727734c53abc765871ea396a803f76035b32520079e878605d4d65f8a41",
  "/etc/aptplan/preferences.d/80-logs.pref": "40aeeb322e15b888a32dc906690e2cc1220fbbb3cb26349294dcd01a856b7f36",
  "/etc/aptplan/request.json": "de8bd366892a463d308ce9a28bfbfa1344ffa54ac1ff42aa35ed0d896e52d2ac",
  "/usr/share/aptplan/docs/output-format.md": "a542aa44eab3ef11f5835a20c72fbf3c34cd16a2b88a7b33f3c81c51a5772e08",
  "/usr/share/aptplan/docs/pins-and-requests.md": "b2c6c55ae014725b4da111be1bb188b3645183bd37086dbd1d17b9870b555e4a",
  "/usr/share/aptplan/docs/relationships.md": "a1d7409710134bbd2dde71e86dcaf0edcbbf704eb3702fd6c9290601cfeba823",
  "/usr/share/aptplan/docs/versioning.md": "a7c8e8f82dfaecc4eb1557097ad004c41f961c133f6d6d4edaab3acbc2a85834",
  "/var/lib/aptmirror/snapshots/experimental/Packages": "ba298107e4d7574c680f476f1768ac0a6e149265459c7cd5924023ef43570883",
  "/var/lib/aptmirror/snapshots/experimental/Release": "1fbd3f8ab4dd22a124a1c3f483214c1984eae99bea906ae5f0e6b0058989d7b5",
  "/var/lib/aptmirror/snapshots/quarantine/Packages": "299dc057e01a0366602e48d253653c532dafccfae70712a87e3c4d495af715c6",
  "/var/lib/aptmirror/snapshots/quarantine/Release": "0b73260327e9dfa2b85ac97e938d475e4d7d70e2b00d095ddee7fac43ced5836",
  "/var/lib/aptmirror/snapshots/stable/Packages": "3332a8fef1b8822131e84ec4d100ea787f3ffb99177067184aaad1a46ff2b0b8",
  "/var/lib/aptmirror/snapshots/stable/Release": "db8a25a90329fa94e6a42f77ba803ba0f8714b624fcda25eac3ab914d52a1156",
  "/var/lib/aptmirror/snapshots/updates/Packages": "5187084a98f142d43640e09049865e20d4c007820f66d4203690d8864c65318c",
  "/var/lib/aptmirror/snapshots/updates/Release": "6e1633cb07dea1cc62d633baf0fe16676d387a8fcd2c331e86f0d84781b9cd15"
}


def parse_stanzas(text):
    out = []
    cur = {}
    last = None
    for line in text.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
                cur = {}
                last = None
            continue
        if line[:1].isspace() and last:
            cur[last] += "\n" + line.strip()
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            last = k.strip()
            cur[last] = v.strip()
    if cur:
        out.append(cur)
    return out


def split_version(v):
    if ":" in v:
        ep, rest = v.split(":", 1)
        epoch = int(ep or "0")
    else:
        epoch, rest = 0, v
    if "-" in rest:
        upstream, rev = rest.rsplit("-", 1)
    else:
        upstream, rev = rest, ""
    return epoch, upstream, rev


def order_char(c):
    if c == "":
        return 0
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def verrevcmp(a, b):
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        while (ia < len(a) and not a[ia].isdigit()) or (ib < len(b) and not b[ib].isdigit()):
            ca = a[ia] if ia < len(a) and not a[ia].isdigit() else ""
            cb = b[ib] if ib < len(b) and not b[ib].isdigit() else ""
            oa, ob = order_char(ca), order_char(cb)
            if oa != ob:
                return -1 if oa < ob else 1
            if ca:
                ia += 1
            if cb:
                ib += 1
        while ia < len(a) and a[ia] == "0":
            ia += 1
        while ib < len(b) and b[ib] == "0":
            ib += 1
        ja, jb = ia, ib
        while ja < len(a) and a[ja].isdigit():
            ja += 1
        while jb < len(b) and b[jb].isdigit():
            jb += 1
        da, db = a[ia:ja], b[ib:jb]
        if len(da) != len(db):
            return -1 if len(da) < len(db) else 1
        if da != db:
            return -1 if da < db else 1
        ia, ib = ja, jb
    return 0


def version_cmp(a, b):
    ea, ua, ra = split_version(a)
    eb, ub, rb = split_version(b)
    if ea != eb:
        return -1 if ea < eb else 1
    c = verrevcmp(ua, ub)
    if c:
        return c
    return verrevcmp(ra, rb)


def version_ok(ver, op, wanted):
    if not op:
        return True
    c = version_cmp(ver, wanted)
    return {">=": c >= 0, ">": c > 0, "=": c == 0, "<=": c <= 0, "<": c < 0}[op]


def parse_relation(text):
    text = text.strip()
    arch = ""
    m = re.search(r"\[([^\]]+)\]", text)
    if m:
        arch = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
    op = ver = ""
    m = re.search(r"\((>=|<=|=|>|<)\s*([^)]*)\)", text)
    if m:
        op, ver = m.group(1), m.group(2).strip()
        text = (text[:m.start()] + text[m.end():]).strip()
    return {"name": text.strip(), "op": op, "version": ver, "arch": arch}


def arch_applies(expr, arch):
    if not expr:
        return True
    tokens = expr.split()
    positive = [t for t in tokens if not t.startswith("!")]
    negative = [t[1:] for t in tokens if t.startswith("!")]
    if arch in negative:
        return False
    return not positive or arch in positive


def dep_groups(raw):
    groups = []
    if not raw:
        return groups
    for group in raw.split(","):
        alts = [parse_relation(x) for x in group.split("|")]
        alts = [x for x in alts if x["name"]]
        if alts:
            groups.append(alts)
    return groups


def name_set(raw):
    if not raw:
        return set()
    return {parse_relation(x)["name"] for x in raw.split(",") if parse_relation(x)["name"]}


def load_packages(root):
    packages = []
    for snapdir in sorted((root / "snapshots").iterdir()):
        if not snapdir.is_dir():
            continue
        release = parse_stanzas((snapdir / "Release").read_text())[0]
        for path in sorted(snapdir.glob("Packages")):
            for st in parse_stanzas(path.read_text()):
                packages.append({
                    "package": st.get("Package", ""),
                    "version": st.get("Version", ""),
                    "architecture": st.get("Architecture", ""),
                    "depends": st.get("Depends", ""),
                    "provides": st.get("Provides", ""),
                    "conflicts": st.get("Conflicts", ""),
                    "breaks": st.get("Breaks", ""),
                    "snapshot": release.get("Snapshot", snapdir.name),
                    "suite": release.get("Suite", snapdir.name),
                    "trusted": release.get("Trusted", "no").lower() == "yes",
                })
    return packages


def load_prefs(pref_dir):
    prefs = []
    order = 0
    for path in sorted(pref_dir.iterdir()):
        if not path.is_file():
            continue
        for st in parse_stanzas(path.read_text()):
            prefs.append({
                "package": st.get("Package", "*"),
                "snapshot": st.get("Snapshot", ""),
                "suite": st.get("Suite", ""),
                "priority": int(st.get("Pin-Priority", "500")),
                "order": order,
            })
            order += 1
    return prefs


def specificity(pref):
    return (4 if pref["package"] != "*" else 0) + (2 if pref["snapshot"] else 0) + (1 if pref["suite"] else 0)


def priority(pkg, prefs):
    best = None
    for pref in prefs:
        if pref["package"] not in ("*", pkg["package"]):
            continue
        if pref["snapshot"] and pref["snapshot"] != pkg["snapshot"]:
            continue
        if pref["suite"] and pref["suite"] != pkg["suite"]:
            continue
        if best is None or (specificity(pref), pref["order"]) >= (specificity(best), best["order"]):
            best = pref
    return best["priority"] if best else 500


def provides(pkg):
    vals = set()
    for item in pkg.get("provides", "").split(","):
        rel = parse_relation(item)
        if rel["name"]:
            vals.add(rel["name"])
    return vals


def satisfies_pkg(pkg, rel, arch):
    if not arch_applies(rel["arch"], arch):
        return True
    if pkg["package"] == rel["name"]:
        return version_ok(pkg["version"], rel["op"], rel["version"])
    if rel["name"] in provides(pkg):
        return True
    return False


def package_order_key(pkg, prefs):
    return (priority(pkg, prefs), cmp_to_key(version_cmp)(pkg["version"]), pkg["snapshot"], pkg["package"], pkg["architecture"])


def reference_plan(root=ROOT, request_path=REQUEST, pref_dir=PREFS):
    request = json.loads(Path(request_path).read_text())
    arch = request["architecture"]
    prefs = load_prefs(Path(pref_dir))
    packages = load_packages(Path(root))
    selected = {}
    depnames = {}
    rejected_keys = set()

    def eligible(pkg):
        if pkg["architecture"] not in (arch, "all"):
            return False
        if not pkg["trusted"]:
            return False
        if pkg["suite"] in request.get("forbidden_suites", []):
            return False
        if pkg["package"] in request.get("held", []):
            return False
        return priority(pkg, prefs) >= 0

    def conflicts_with_selected(pkg):
        bad = name_set(pkg.get("conflicts", "")) | name_set(pkg.get("breaks", ""))
        for other in selected.values():
            other_bad = name_set(other.get("conflicts", "")) | name_set(other.get("breaks", ""))
            if other["package"] in bad or pkg["package"] in other_bad:
                return True
        return False

    def candidates(rel):
        out = [p for p in packages if eligible(p) and satisfies_pkg(p, rel, arch)]
        out.sort(key=cmp_to_key(lambda a, b: (
            -1 if priority(a, prefs) > priority(b, prefs) else
            1 if priority(a, prefs) < priority(b, prefs) else
            -version_cmp(a["version"], b["version"]) or
            ((a["snapshot"], a["package"], a["architecture"]) > (b["snapshot"], b["package"], b["architecture"])) - ((a["snapshot"], a["package"], a["architecture"]) < (b["snapshot"], b["package"], b["architecture"]))
        )))
        return out

    def resolve_rel(rel, stack):
        if not arch_applies(rel["arch"], arch):
            return None
        for name, pkg in selected.items():
            if satisfies_pkg(pkg, rel, arch):
                return name
        for cand in candidates(rel):
            if cand["package"] in stack:
                continue
            before_sel = dict(selected)
            before_dep = {k: set(v) for k, v in depnames.items()}
            if try_select(cand, rel["name"] == cand["package"] and "requested" or "dependency", stack | {cand["package"]}):
                return cand["package"]
            selected.clear()
            selected.update(before_sel)
            depnames.clear()
            depnames.update(before_dep)
            rejected_keys.add(key(cand))
        return None

    def try_select(pkg, reason, stack):
        if conflicts_with_selected(pkg):
            rejected_keys.add(key(pkg))
            return False
        selected[pkg["package"]] = pkg
        deps = set()
        for group in dep_groups(pkg.get("depends", "")):
            resolved = None
            for rel in group:
                if not arch_applies(rel["arch"], arch):
                    resolved = None
                    break
                resolved = resolve_rel(rel, stack)
                if resolved:
                    break
            if resolved:
                deps.add(resolved)
            elif group:
                rejected_keys.add(key(pkg))
                selected.pop(pkg["package"], None)
                return False
        depnames[pkg["package"]] = deps
        return True

    for req in request["requested"]:
        assert resolve_rel({"name": req, "op": "", "version": "", "arch": ""}, set()), f"unresolved request {req}"

    selected_records = []
    for name in sorted(selected):
        p = selected[name]
        selected_records.append({
            "package": p["package"],
            "version": p["version"],
            "architecture": p["architecture"],
            "source_snapshot": p["snapshot"],
            "reason": "requested" if p["package"] in request["requested"] else "dependency",
            "dependencies": sorted(depnames.get(name, set())),
        })
    return {
        "request_id": request["request_id"],
        "architecture": arch,
        "selected": selected_records,
        "summary": {
            "selected_count": len(selected_records),
            "snapshot_count": len({p["snapshot"] for p in packages}),
            "has_conflicts": False,
        },
    }


def key(pkg):
    return f"{pkg['package']}={pkg['version']}@{pkg['snapshot']}/{pkg['architecture']}"


def run_cli(root=ROOT, request=REQUEST, prefs=PREFS, out=OUT):
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(APP / "bin/build.sh")], check=True, cwd="/app")
    subprocess.run([
        "java", "-jar", str(APP / "build/aptplan.jar"),
        "--root", str(root), "--request", str(request),
        "--preferences", str(prefs), "--out", str(out)
    ], check=True, cwd="/app")
    return json.loads((out / "install_plan.json").read_text())


def normalized_actual(plan):
    return {
        "request_id": plan.get("request_id"),
        "architecture": plan.get("architecture"),
        "selected": sorted(plan.get("selected", []), key=lambda x: x["package"]),
        "summary": {
            "selected_count": plan.get("summary", {}).get("selected_count"),
            "snapshot_count": plan.get("summary", {}).get("snapshot_count"),
            "has_conflicts": plan.get("summary", {}).get("has_conflicts"),
        },
    }


def test_build_and_documented_cli_command_succeed():
    """Verify the documented Java build and resolver command run offline and emit both required files."""
    run_cli()
    assert (APP / "build/aptplan.jar").exists()
    assert (OUT / "install_plan.json").exists()
    assert (OUT / "audit.ndjson").exists()


def test_install_plan_schema_matches_instruction():
    """Verify install_plan.json uses the exact top-level and selected/rejected/summary schema."""
    plan = run_cli()
    assert set(plan) == {"request_id", "architecture", "selected", "rejected", "summary"}
    assert isinstance(plan["selected"], list) and plan["selected"], "selected must be a non-empty array"
    for item in plan["selected"]:
        assert set(item) == {"package", "version", "architecture", "source_snapshot", "reason", "dependencies"}
        assert isinstance(item["dependencies"], list)
    assert isinstance(plan["rejected"], list)
    for item in plan["rejected"]:
        assert set(item) == {"package", "version", "architecture", "source_snapshot", "reason"}
    assert set(plan["summary"]) == {"selected_count", "rejected_count", "snapshot_count", "has_conflicts"}


def test_selected_plan_matches_reference_for_shipped_corpus():
    """Compare selected package decisions against an independent reference resolver."""
    actual = normalized_actual(run_cli())
    expected = reference_plan()
    assert actual == expected


def test_audit_ndjson_is_valid_deterministic_and_explains_decisions():
    """Verify audit.ndjson is valid NDJSON with stable selected and rejected decision records."""
    run_cli()
    first = (OUT / "audit.ndjson").read_text()
    run_cli()
    second = (OUT / "audit.ndjson").read_text()
    assert first == second
    lines = [json.loads(line) for line in first.splitlines() if line.strip()]
    assert lines, "audit.ndjson must contain decision records"
    for row in lines:
        assert set(row) == {"package", "decision", "reason", "details"}
        assert row["package"] and row["decision"] and row["reason"] and row["details"]
    plan = json.loads((OUT / "install_plan.json").read_text())
    selected_names = {item["package"] for item in plan["selected"]}
    rejected_names = {item["package"] for item in plan["rejected"]}
    audited_selected = {row["package"] for row in lines if row["decision"] == "selected"}
    audited_rejected = {row["package"] for row in lines if row["decision"] == "rejected"}
    assert selected_names <= audited_selected
    assert rejected_names <= audited_rejected


def test_mutated_request_and_metadata_are_not_hardcoded(tmp_path):
    """Run against a copied mirror with an added request to prevent static shipped-output solutions."""
    mirror = tmp_path / "mirror"
    shutil.copytree(ROOT, mirror)
    request = tmp_path / "request.json"
    data = json.loads(REQUEST.read_text())
    data["request_id"] = "aptplan-mutated-diagnostic"
    data["requested"] = data["requested"] + ["diagnostic-shell"]
    request.write_text(json.dumps(data, indent=2) + "\n")
    out = tmp_path / "out"
    actual = normalized_actual(run_cli(mirror, request, PREFS, out))
    expected = reference_plan(mirror, request, PREFS)
    assert actual == expected
    assert "diagnostic-shell" in {x["package"] for x in actual["selected"]}


def test_generated_backtracking_provider_graph_avoids_unsafe_state_iteration(tmp_path):
    """Generate nested alternatives so resolver backtracking must restore state safely."""
    mirror = tmp_path / "mirror"
    shutil.copytree(ROOT, mirror)
    stable_packages = mirror / "snapshots" / "stable" / "Packages"
    stable_packages.write_text(stable_packages.read_text() + """

Package: field-hospital-suite
Version: 1.0-1
Architecture: amd64
Depends: rescue-console, triage-dashboard, diagnostic-shell
Description: generated compound request that forces recursive resolver state changes

Package: triage-dashboard
Version: 2.0-1
Architecture: amd64
Depends: log-router | journald-provider, audit-probe
Description: generated dependency with unsafe first logging alternative

Package: audit-probe
Version: 1.0-1
Architecture: amd64
Depends: zlib1g (>= 1:1.2.13), virtual-capture
Description: generated dependency that adds a second provider-choice layer

Package: capture-heavy
Version: 3.0-1
Architecture: amd64
Provides: virtual-capture
Conflicts: rescue-console
Description: generated preferred provider that must be rejected because it conflicts

Package: capture-lite
Version: 1.0-1
Architecture: all
Provides: virtual-capture
Description: generated fallback provider that keeps the selected set consistent
""".lstrip())

    request = tmp_path / "request.json"
    data = json.loads(REQUEST.read_text())
    data["request_id"] = "aptplan-generated-backtracking"
    data["requested"] = data["requested"] + ["field-hospital-suite"]
    request.write_text(json.dumps(data, indent=2) + "\n")
    out = tmp_path / "out"

    actual = normalized_actual(run_cli(mirror, request, PREFS, out))
    expected = reference_plan(mirror, request, PREFS)
    assert actual == expected
    selected = {x["package"]: x for x in actual["selected"]}
    assert "field-hospital-suite" in selected
    assert "triage-dashboard" in selected
    assert "audit-probe" in selected
    assert "mini-journald" in selected
    assert "capture-lite" in selected
    assert "log-router" not in selected
    assert "capture-heavy" not in selected


def test_selected_plan_respects_relationship_invariants():
    """Check holds, forbidden suites, conflicts, epoch ordering, tilde ordering, and pin overrides."""
    plan = run_cli()
    selected = {x["package"]: x for x in plan["selected"]}
    request = json.loads(REQUEST.read_text())
    assert not (set(selected) & set(request["held"]))
    assert all(x["architecture"] in (request["architecture"], "all") for x in selected.values())
    assert all(x["source_snapshot"] not in {"experimental", "quarantine"} for x in selected.values())
    assert "log-router" not in selected, "log-router conflicts with a requested package; a safe provider should be used"
    assert selected["libcore"]["version"].startswith("2:"), "epoch-aware version comparison is required"
    assert selected["libui"]["version"] == "1.4-1", "tilde prerelease must sort below the final release"
    assert selected["libcodec-ng"]["source_snapshot"] == "stable", "package-specific pin must override the newer update"


def test_protected_inputs_are_not_modified():
    """Verify mirror metadata, request files, preferences, and local docs remain immutable."""
    run_cli()
    for path, expected in PROTECTED_HASHES.items():
        p = Path(path)
        assert p.exists(), f"protected path missing: {path}"
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, f"protected path modified: {path}"


def test_repeated_runs_are_byte_identical():
    """Verify deterministic byte-for-byte install_plan.json output across repeated runs."""
    run_cli()
    first = (OUT / "install_plan.json").read_bytes()
    run_cli()
    second = (OUT / "install_plan.json").read_bytes()
    assert first == second
