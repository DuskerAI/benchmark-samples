import hashlib
import re
import subprocess
from pathlib import Path

APP = Path("/app/sysroot-plan")
BIN = APP / "bin" / "sysplan"
TARGET = "armv7-linux-gnueabihf"


def run_cmd(args, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check)


def build_tool():
    run_cmd([str(APP / "scripts" / "build.sh")])
    assert BIN.exists(), "build must create /app/sysroot-plan/bin/sysplan"


def parse_atom(atom):
    parts = atom.strip().split()
    if not parts or not parts[0]:
        return None
    if len(parts) == 1:
        return (parts[0], None, None)
    return (parts[0], parts[1], parts[2])


def ver_tuple(v):
    return tuple(int(x) for x in v.split("."))


def version_ok(have, op, want):
    if op is None:
        return True
    hv, wv = ver_tuple(have), ver_tuple(want)
    n = max(len(hv), len(wv))
    hv += (0,) * (n - len(hv))
    wv += (0,) * (n - len(wv))
    return {"=": hv == wv, ">=": hv >= wv, "<=": hv <= wv}[op]


def split_list(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def expand_vars(value, variables):
    return re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: variables.get(m.group(1), ""), value)


def logical_lines(text):
    lines = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.endswith("\\"):
            pending = (pending + " " + stripped[:-1].strip()).strip()
            continue
        line = (pending + " " + stripped).strip() if pending else stripped
        pending = ""
        lines.append(line)
    if pending:
        lines.append(pending)
    return lines


def load_pkgs(sysroot):
    pkgs = []
    for path in sorted((Path(sysroot) / "pkgconfig").glob("*.pc")):
        rec = {}
        variables = {}
        for line in logical_lines(path.read_text()):
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                rec[k.strip()] = expand_vars(v.strip(), variables)
            elif "=" in line:
                k, v = line.split("=", 1)
                variables[k.strip()] = expand_vars(v.strip(), variables)
        rec["_provides"] = [(rec["Name"], "=", rec["Version"])]
        rec["_provides"] += [parse_atom(x) for x in split_list(rec.get("Provides", ""))]
        rec["_provides"] = [p for p in rec["_provides"] if p]
        rec["_conflicts"] = [parse_atom(x) for x in split_list(rec.get("Conflicts", ""))]
        rec["_path"] = path.name
        pkgs.append(rec)
    return pkgs


def provides(pkg, atom):
    name, op, want = parse_atom(atom)
    for pname, _, pver in pkg["_provides"]:
        if pname != name:
            continue
        if pver is None:
            if op is None:
                return "0"
            continue
        if version_ok(pver, op, want):
            return pver
    return None


def excluded(pkg, atoms):
    return any(provides(pkg, atom) for atom in atoms)


def candidate_key(item):
    pkg, pver = item
    return (ver_tuple(pver), ver_tuple(pkg["Version"]), tuple(-ord(c) for c in pkg["Name"]))


def conflicts(a, b):
    def matches(conflict_atoms, provided_atoms):
        for cname, cop, cver in conflict_atoms:
            for pname, _, pver in provided_atoms:
                if cname == pname and (cop is None or (pver is not None and version_ok(pver, cop, cver))):
                    return True
        return False

    return matches(a["_conflicts"], b["_provides"]) or matches(b["_conflicts"], a["_provides"])


def resolve(sysroot, target, roots, link, host_tools=(), exclude=(), locks=None, variant=""):
    pkgs = load_pkgs(sysroot)
    selected = {}
    locks = locks or {}

    def lock_for(atom, role):
        name = parse_atom(atom)[0]
        return locks.get(f"{name}@{role}") or locks.get(name)

    def variant_ok(pkg):
        variants = split_list(pkg.get("Variants", ""))
        return not variants or variant in variants

    def choices(atom, role, selected_pkgs):
        allowed = {"any", "host"} if role == "host" else {"any", target}
        locked_name = lock_for(atom, role)
        out = [
            (p, provides(p, atom))
            for p in pkgs
            if p.get("Target") in allowed
            and provides(p, atom)
            and variant_ok(p)
            and not excluded(p, exclude)
            and (locked_name is None or p["Name"] == locked_name)
        ]
        out.sort(key=candidate_key, reverse=True)
        return [(pkg, pver) for pkg, pver in out if all(not conflicts(pkg, old) for old in selected_pkgs.values())]

    def choose(atom, role):
        for pkg, _ in choices(atom, role, selected):
            selected[pkg["Name"]] = pkg
            return pkg
        raise AssertionError(f"no viable candidate for {atom}")

    host_rows = [(atom.split()[0], choose(atom, "host")) for atom in host_tools]

    def expand_sequence(atoms, selected_pkgs, emitted, visiting):
        if not atoms:
            yield selected_pkgs, emitted
            return
        first, rest = atoms[0], atoms[1:]
        for selected_next, emitted_next in expand_atom(first, selected_pkgs, emitted, visiting):
            yield from expand_sequence(rest, selected_next, emitted_next, visiting)

    def expand_atom(atom, selected_pkgs, emitted, visiting):
        for pkg, _ in choices(atom, "target", selected_pkgs):
            name = pkg["Name"]
            if name in visiting:
                continue
            next_selected = dict(selected_pkgs)
            next_selected[name] = pkg
            if name in emitted:
                yield next_selected, emitted
                continue
            deps = split_list(pkg.get("Requires", ""))
            if link == "static":
                deps += split_list(pkg.get("Requires.private", ""))
            for selected_after, emitted_after in expand_sequence(deps, next_selected, emitted, visiting | {name}):
                yield selected_after, emitted_after | {name}

    try:
        selected, _ = next(expand_sequence(list(roots), selected, set(), set()))
    except StopIteration as exc:
        raise AssertionError("no viable dependency closure") from exc
    target_pkgs = [p for p in selected.values() if p.get("Target") != "host"]

    def selected_target(atom):
        allowed = {"any", target}
        out = [(p, provides(p, atom)) for p in target_pkgs if p.get("Target") in allowed and provides(p, atom)]
        out.sort(key=candidate_key, reverse=True)
        for pkg, _ in out:
            if all(not conflicts(pkg, old) for old in selected.values() if old is not pkg):
                return pkg
        raise AssertionError(f"selected closure does not satisfy {atom}")

    deps_by_name = {}
    for p in target_pkgs:
        deps = split_list(p.get("Requires", ""))
        if link == "static":
            deps += split_list(p.get("Requires.private", ""))
        deps_by_name[p["Name"]] = {selected_target(d)["Name"] for d in deps}
    ordered = []
    remaining = {p["Name"]: p for p in target_pkgs}
    while remaining:
        ready = sorted(n for n in remaining if not (deps_by_name[n] & set(remaining)))
        assert ready, "dependency graph must be acyclic"
        ordered.extend(remaining.pop(name) for name in ready)
    return host_rows, ordered


def rewrite_token(tok, sysroot):
    tok = tok.replace("${pc_sysrootdir}", sysroot)
    if tok.startswith(("-I/", "-L/")):
        return tok[:2] + sysroot + tok[2:]
    return tok


def expected_plan(sysroot, target, request):
    req = {}
    for line in Path(request).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            k, v = line.split(":", 1)
            req[k.strip()] = v.strip()
    roots = split_list(req["roots"])
    tools = split_list(req.get("host-tools", ""))
    excluded_atoms = split_list(req.get("exclude", ""))
    locks = {}
    if req.get("lock"):
        lock_path = Path(req["lock"])
        if not lock_path.is_absolute():
            lock_path = Path(request).parent / lock_path
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                k, v = line.split(":", 1)
                locks[k.strip()] = v.strip()
    hosts, targets = resolve(sysroot, target, roots, req["link"], tools, excluded_atoms, locks, req.get("variant", ""))
    rows = ["role\trequest\tpackage\tversion\ttarget"]
    for request_name, pkg in sorted(hosts, key=lambda x: x[0]):
        rows.append(f"host-tool\t{request_name}\t{pkg['Name']}\t{pkg['Version']}\t{pkg['Target']}")
    rows.extend(f"target-lib\t{pkg['Name']}\t{pkg['Name']}\t{pkg['Version']}\t{pkg['Target']}" for pkg in targets)
    cflags = []
    libs = []
    for pkg in targets:
        flag_fields = [pkg.get("Cflags", "")]
        if req["link"] == "static":
            flag_fields.append(pkg.get("Cflags.private", ""))
        for field in flag_fields:
            cflags += [rewrite_token(x, sysroot) for x in field.split()]
    for pkg in reversed(targets):
        flag_fields = [pkg.get("Libs", "")]
        if req["link"] == "static":
            flag_fields.append(pkg.get("Libs.private", ""))
        for field in flag_fields:
            libs += [rewrite_token(x, sysroot) for x in field.split()]
    return "\n".join(rows) + "\n", " ".join(cflags + libs) + "\n"


def assert_success(sysroot, request, outdir):
    run_cmd([str(BIN), "--sysroot", str(sysroot), "--target", TARGET, "--request", str(request), "--out", str(outdir)])
    assert sorted(p.name for p in outdir.iterdir()) == ["build.env", "link.flags", "packages.tsv"]
    want_pkg, want_flags = expected_plan(str(sysroot), TARGET, request)
    assert (outdir / "packages.tsv").read_text() == want_pkg
    assert (outdir / "link.flags").read_text() == want_flags
    assert (outdir / "build.env").read_text() == f"status=ok\ntarget={TARGET}\nsysroot={sysroot}\n"


def snapshot_inputs(root):
    return {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_release_case_static_plan_matches_contract(tmp_path):
    """The bundled release case exercises static transitive closure, host tools, virtual providers, ordering, and sysroot rewriting."""
    build_tool()
    case = APP / "cases" / "release"
    before = snapshot_inputs(case)
    out = tmp_path / "out"
    out.mkdir()
    (out / "old-artifact").write_text("stale")
    assert_success(case / "sysroot", case / "request.plan", out)
    assert snapshot_inputs(case) == before


def test_generated_shared_plan_keeps_private_libraries_out(tmp_path):
    """Shared linking must resolve public dependencies without pulling private-only target libraries into the plan."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: viewer >= 1\nlink: shared\nhost-tools: packer >= 4\n")
    (pc / "viewer.pc").write_text("Name: viewer\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: surface >= 2\nRequires.private: trace-only\nCflags: -I/usr/include/viewer\nLibs: -L/usr/lib -lviewer\n")
    (pc / "surface.pc").write_text("Name: surface\nVersion: 2.0\nTarget: any\nCflags: -I/usr/include/surface\nLibs: -lsurface\n")
    (pc / "trace-only.pc").write_text("Name: trace-only\nVersion: 9.0\nTarget: armv7-linux-gnueabihf\nLibs: -ltraceonly\n")
    (pc / "packer.pc").write_text("Name: packer\nVersion: 4.1\nTarget: host\nLibs: -lpacker\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    assert "trace-only" not in (tmp_path / "out" / "packages.tsv").read_text()


def test_provider_tiebreak_and_conflict_backtracking(tmp_path):
    """A resolver must prefer the best viable provider while avoiding conflicts introduced by the selected closure."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: compositor\nlink: static\n")
    (pc / "compositor.pc").write_text("Name: compositor\nVersion: 5.0\nTarget: armv7-linux-gnueabihf\nRequires: tls >= 3\nConflicts: tls-fast\nLibs: -lcomp\n")
    (pc / "tls-fast.pc").write_text("Name: tls-fast\nVersion: 3.4\nTarget: armv7-linux-gnueabihf\nProvides: tls = 3.4\nLibs: -ltlsfast\n")
    (pc / "tls-safe.pc").write_text("Name: tls-safe\nVersion: 3.2\nTarget: armv7-linux-gnueabihf\nProvides: tls = 3.2\nLibs: -ltlssafe\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "tls-safe" in text and "tls-fast" not in text


def test_unsatisfied_request_writes_only_status(tmp_path):
    """Unsatisfied dependencies must fail without leaving partial success artifacts."""
    build_tool()
    root = tmp_path / "bad"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: missing >= 8\nlink: static\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("stale")
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(root / "request.plan"), "--out", str(out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in out.iterdir()) == ["status.txt"]
    assert (out / "status.txt").read_text().startswith("ERROR\tunsatisfied\tmissing")


def test_conflict_and_bad_request_errors_are_reported(tmp_path):
    """Conflict and malformed-request failures must use their public status codes and avoid success artifacts."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (pc / "alpha.pc").write_text("Name: alpha\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nConflicts: beta\nLibs: -la\n")
    (pc / "beta.pc").write_text("Name: beta\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -lb\n")
    conflict_request = root / "conflict.plan"
    conflict_request.write_text("roots: alpha, beta\nlink: static\n")
    conflict_out = tmp_path / "conflict-out"
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(conflict_request), "--out", str(conflict_out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in conflict_out.iterdir()) == ["status.txt"]
    assert (conflict_out / "status.txt").read_text().startswith("ERROR\tconflict\tbeta")

    bad_request = root / "bad.plan"
    bad_request.write_text("roots: alpha\nlink: maybe\n")
    bad_out = tmp_path / "bad-out"
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(bad_request), "--out", str(bad_out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in bad_out.iterdir()) == ["status.txt"]
    assert (bad_out / "status.txt").read_text() == "ERROR\tbad-request\trequest\n"


def test_unversioned_provides_comments_and_less_equal_atoms(tmp_path):
    """Bare provided capabilities, comments, blank lines, and <= version atoms must follow the public metadata grammar."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("# staged request\n\nroots: facade <= 2.0\nlink: static\n")
    (pc / "facade.pc").write_text("# facade package\n\nName: facade\nVersion: 2.0\nTarget: armv7-linux-gnueabihf\nRequires.private: compression\nLibs: -lfacade\n")
    (pc / "zip-lite.pc").write_text("Name: zip-lite\nVersion: 7.3\nTarget: armv7-linux-gnueabihf\nProvides: compression\nLibs: -lzip\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "zip-lite" in text


def test_package_lexicographic_ties_and_host_sorting(tmp_path):
    """Provider ties must fall through to package version, package name, and sorted host-tool request names."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: codec = 1.0, raster = 1.0\nlink: shared\nhost-tools: zebra, alpha\n")
    (pc / "codec-old.pc").write_text("Name: codec-old\nVersion: 1.5\nTarget: armv7-linux-gnueabihf\nProvides: codec = 1.0\nLibs: -lcodecold\n")
    (pc / "codec-new.pc").write_text("Name: codec-new\nVersion: 2.0\nTarget: armv7-linux-gnueabihf\nProvides: codec = 1.0\nLibs: -lcodecnew\n")
    (pc / "araster.pc").write_text("Name: araster\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\nProvides: raster = 1.0\nLibs: -laraster\n")
    (pc / "braster.pc").write_text("Name: braster\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\nProvides: raster = 1.0\nLibs: -lbraster\n")
    (pc / "zebra-host.pc").write_text("Name: zebra-host\nVersion: 1.0\nTarget: host\nProvides: zebra\nLibs: -lzebra\n")
    (pc / "alpha-host.pc").write_text("Name: alpha-host\nVersion: 1.0\nTarget: host\nProvides: alpha\nLibs: -lalpha\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    lines = (tmp_path / "out" / "packages.tsv").read_text().splitlines()
    assert "codec-new" in "\n".join(lines) and "codec-old" not in "\n".join(lines)
    assert "araster" in "\n".join(lines) and "braster" not in "\n".join(lines)
    assert lines[1].startswith("host-tool\talpha\t")
    assert lines[2].startswith("host-tool\tzebra\t")


def test_transitive_conflict_rejects_provider_inside_static_closure(tmp_path):
    """A resolver must reject a higher virtual provider that conflicts with the selected transitive closure."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: compositor\nlink: static\n")
    (pc / "compositor.pc").write_text("Name: compositor\nVersion: 4.0\nTarget: armv7-linux-gnueabihf\nRequires: renderer\nLibs: -lcompositor\n")
    (pc / "renderer.pc").write_text("Name: renderer\nVersion: 2.0\nTarget: armv7-linux-gnueabihf\nRequires: audit, image-codec >= 2\nLibs: -lrenderer\n")
    (pc / "codec-fast.pc").write_text("Name: codec-fast\nVersion: 8.0\nTarget: armv7-linux-gnueabihf\nProvides: image-codec = 3.0\nConflicts: audit\nLibs: -lcodecfast\n")
    (pc / "codec-safe.pc").write_text("Name: codec-safe\nVersion: 2.5\nTarget: armv7-linux-gnueabihf\nProvides: image-codec = 2.5\nLibs: -lcodecsafe\n")
    (pc / "audit.pc").write_text("Name: audit\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -laudit\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "codec-safe" in text
    assert "codec-fast" not in text


def test_target_host_scope_and_version_padding_interact(tmp_path):
    """Host and target resolution scopes, any-target packages, and padded numeric versions must interact deterministically."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: launcher <= 2.0\nlink: shared\nhost-tools: launcher >= 5\n")
    (pc / "launcher-target.pc").write_text("Name: launcher-target\nVersion: 2\nTarget: any\nProvides: launcher = 2\nRequires: shim\nCflags: -I${pc_sysrootdir}/include/launcher\nLibs: -L/usr/lib/launcher -llauncher\n")
    (pc / "launcher-too-new.pc").write_text("Name: launcher-too-new\nVersion: 2.0.1\nTarget: armv7-linux-gnueabihf\nProvides: launcher = 2.0.1\nLibs: -lnew\n")
    (pc / "launcher-host.pc").write_text("Name: launcher-host\nVersion: 5.1\nTarget: host\nProvides: launcher = 5.1\nLibs: -lhostlauncher\n")
    (pc / "shim.pc").write_text("Name: shim\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -lshim\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    lines = (tmp_path / "out" / "packages.tsv").read_text().splitlines()
    assert lines[1].startswith("host-tool\tlauncher\tlauncher-host\t5.1\thost")
    assert any("target-lib\tlauncher-target\tlauncher-target\t2\tany" == line for line in lines)
    assert "launcher-too-new" not in "\n".join(lines)


def test_metadata_variables_and_versioned_conflicts_shape_static_choice(tmp_path):
    """Metadata variables and version-scoped conflicts must feed the same provider search and link planning rules."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: compositor\nlink: static\n")
    (pc / "compositor.pc").write_text(
        "Name: compositor\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\n"
        "Requires: codec >= 2, abi = 1.5\nCflags: -I/usr/include/compositor\nLibs: -lcompositor\n"
    )
    (pc / "codec-fast.pc").write_text(
        "api=2.0\ninc=/usr/include/codec-fast\nName: codec-fast\nVersion: ${api}\n"
        "Target: armv7-linux-gnueabihf\nProvides: codec = ${api}\nConflicts: abi >= 2\n"
        "Cflags: -I${inc}\nLibs: -lcodecfast\n"
    )
    (pc / "codec-safe.pc").write_text(
        "Name: codec-safe\nVersion: 1.9\nTarget: armv7-linux-gnueabihf\nProvides: codec = 1.9\nLibs: -lcodecsafe\n"
    )
    (pc / "abi15.pc").write_text(
        "Name: abi15\nVersion: 1.5\nTarget: armv7-linux-gnueabihf\nProvides: abi = 1.5\nLibs: -labi15\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "codec-fast\t2.0" in text
    assert "codec-safe" not in text
    assert f"-I{root / 'sysroot'}/usr/include/codec-fast" in (tmp_path / "out" / "link.flags").read_text()


def test_versioned_conflict_blocks_only_matching_providers(tmp_path):
    """A conflict atom with an operator must not reject lower versions of the same capability."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: renderer\nlink: static\n")
    (pc / "renderer.pc").write_text(
        "Name: renderer\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: gfx, driver = 1.5\nLibs: -lrenderer\n"
    )
    (pc / "gfx.pc").write_text(
        "Name: gfx\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\nConflicts: driver >= 2\nLibs: -lgfx\n"
    )
    (pc / "driver-old.pc").write_text(
        "Name: driver-old\nVersion: 1.5\nTarget: armv7-linux-gnueabihf\nProvides: driver = 1.5\nLibs: -ldriverold\n"
    )
    (pc / "driver-new.pc").write_text(
        "Name: driver-new\nVersion: 2.1\nTarget: armv7-linux-gnueabihf\nProvides: driver = 2.1\nLibs: -ldrivernew\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "driver-old" in text
    assert "driver-new" not in text


def test_lockfile_and_exclude_jointly_constrain_provider_search(tmp_path):
    """Request locks and excludes must be applied before provider ranking and transitive static expansion."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    lock = root / "release.lock"
    lock.write_text("# release waiver\ncodec: codec-audit\ncompression: zip-safe\n")
    (root / "request.plan").write_text(
        f"roots: renderer\nlink: static\nexclude: telemetry\nlock: {lock}\n"
    )
    (pc / "renderer.pc").write_text(
        "Name: renderer\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\n"
        "Requires: codec >= 2\nRequires.private: compression\nLibs: -lrenderer\n"
    )
    (pc / "codec-fast.pc").write_text(
        "Name: codec-fast\nVersion: 9.0\nTarget: armv7-linux-gnueabihf\nProvides: codec = 9.0\nLibs: -lfast\n"
    )
    (pc / "codec-audit.pc").write_text(
        "Name: codec-audit\nVersion: 2.5\nTarget: armv7-linux-gnueabihf\nProvides: codec = 2.5\nLibs: -laudit\n"
    )
    (pc / "zip-fast.pc").write_text(
        "Name: zip-fast\nVersion: 4.0\nTarget: armv7-linux-gnueabihf\nProvides: compression = 4.0, telemetry\nLibs: -lzipfast\n"
    )
    (pc / "zip-safe.pc").write_text(
        "Name: zip-safe\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\nProvides: compression = 3.0\nLibs: -lzipsafe\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "codec-audit" in text and "codec-fast" not in text
    assert "zip-safe" in text and "zip-fast" not in text


def test_lockfile_mismatch_reports_unsatisfied_without_success_artifacts(tmp_path):
    """A lock that names a nonmatching provider must turn that dependency into an unsatisfied request."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    lock = root / "release.lock"
    lock.write_text("codec: missing-provider\n")
    (root / "request.plan").write_text(f"roots: codec >= 2\nlink: shared\nlock: {lock}\n")
    (pc / "codec-real.pc").write_text(
        "Name: codec-real\nVersion: 2.5\nTarget: armv7-linux-gnueabihf\nProvides: codec = 2.5\nLibs: -lcodec\n"
    )
    out = tmp_path / "out"
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(root / "request.plan"), "--out", str(out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in out.iterdir()) == ["status.txt"]
    assert (out / "status.txt").read_text().startswith("ERROR\tunsatisfied\tcodec")


def test_relative_lock_path_is_anchored_to_request_file(tmp_path):
    """Relative lock paths must be resolved from the request file directory before provider search begins."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "locks").mkdir()
    (root / "locks" / "toolchain.lock").write_text("codec: codec-pinned\nhelper: helper-safe\n")
    (root / "request.plan").write_text("roots: app\nlink: static\nlock: locks/toolchain.lock\n")
    (pc / "app.pc").write_text(
        "Name: app\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: codec >= 1\nRequires.private: helper\nLibs: -lapp\n"
    )
    (pc / "codec-fast.pc").write_text(
        "Name: codec-fast\nVersion: 9.0\nTarget: armv7-linux-gnueabihf\nProvides: codec = 9.0\nLibs: -lfast\n"
    )
    (pc / "codec-pinned.pc").write_text(
        "Name: codec-pinned\nVersion: 1.5\nTarget: armv7-linux-gnueabihf\nProvides: codec = 1.5\nLibs: -lpinned\n"
    )
    (pc / "helper-fast.pc").write_text(
        "Name: helper-fast\nVersion: 7.0\nTarget: armv7-linux-gnueabihf\nProvides: helper = 7.0\nLibs: -lhelperfast\n"
    )
    (pc / "helper-safe.pc").write_text(
        "Name: helper-safe\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nProvides: helper = 1.0\nLibs: -lhelpersafe\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "codec-pinned" in text and "codec-fast" not in text
    assert "helper-safe" in text and "helper-fast" not in text


def test_malformed_request_line_is_bad_request(tmp_path):
    """A nonblank request line without a key separator must be reported as a bad request."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: app\nthis line is not a request field\nlink: static\n")
    (pc / "app.pc").write_text("Name: app\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -lapp\n")
    out = tmp_path / "out"
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(root / "request.plan"), "--out", str(out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in out.iterdir()) == ["status.txt"]
    assert (out / "status.txt").read_text() == "ERROR\tbad-request\trequest\n"


def test_duplicate_request_keys_are_bad_request(tmp_path):
    """Request keys are unique, so a duplicate field must not be silently overwritten."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: app\nlink: shared\nroots: other\n")
    (pc / "app.pc").write_text("Name: app\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -lapp\n")
    (pc / "other.pc").write_text("Name: other\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nLibs: -lother\n")
    out = tmp_path / "out"
    proc = run_cmd([str(BIN), "--sysroot", str(root / "sysroot"), "--target", TARGET, "--request", str(root / "request.plan"), "--out", str(out)], check=False)
    assert proc.returncode != 0
    assert sorted(p.name for p in out.iterdir()) == ["status.txt"]
    assert (out / "status.txt").read_text() == "ERROR\tbad-request\trequest\n"


def test_variant_filtering_precedes_lock_and_provider_ranking(tmp_path):
    """Request variants must filter packages before locks and provider tie-breaks are applied."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    lock = root / "pins.lock"
    lock.write_text("codec: codec-lts\n")
    (root / "request.plan").write_text("roots: compositor\nlink: static\nvariant: lts\nlock: pins.lock\n")
    (pc / "compositor.pc").write_text("Name: compositor\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: codec >= 2\nLibs: -lcompositor\n")
    (pc / "codec-main.pc").write_text("Name: codec-main\nVersion: 9.0\nTarget: armv7-linux-gnueabihf\nProvides: codec = 9.0\nVariants: mainline\nLibs: -lmain\n")
    (pc / "codec-lts.pc").write_text("Name: codec-lts\nVersion: 2.4\nTarget: armv7-linux-gnueabihf\nProvides: codec = 2.4\nVariants: lts, maintenance\nLibs: -llts\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    text = (tmp_path / "out" / "packages.tsv").read_text()
    assert "codec-lts" in text and "codec-main" not in text


def test_role_qualified_locks_separate_host_and_target_providers(tmp_path):
    """Role-qualified lock rows must not force host tools and target libraries to use the same provider."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    lock = root / "roles.lock"
    lock.write_text("shader@host: shader-host\nshader@target: shader-target-safe\n")
    (root / "request.plan").write_text("roots: renderer\nlink: static\nhost-tools: shader >= 1\nlock: roles.lock\n")
    (pc / "renderer.pc").write_text("Name: renderer\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: shader >= 1\nLibs: -lrenderer\n")
    (pc / "shader-host.pc").write_text("Name: shader-host\nVersion: 5.0\nTarget: host\nProvides: shader = 5.0\nLibs: -lshaderhost\n")
    (pc / "shader-target-fast.pc").write_text("Name: shader-target-fast\nVersion: 9.0\nTarget: armv7-linux-gnueabihf\nProvides: shader = 9.0\nLibs: -lfast\n")
    (pc / "shader-target-safe.pc").write_text("Name: shader-target-safe\nVersion: 1.5\nTarget: armv7-linux-gnueabihf\nProvides: shader = 1.5\nLibs: -lsafe\n")
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    lines = (tmp_path / "out" / "packages.tsv").read_text().splitlines()
    assert any(line.startswith("host-tool\tshader\tshader-host") for line in lines)
    assert any(line.startswith("target-lib\tshader-target-safe\tshader-target-safe") for line in lines)
    assert "shader-target-fast" not in "\n".join(lines)


def test_static_private_flags_follow_dependency_order_and_scope(tmp_path):
    """Static plans include private flags in package order, while host-tool flags stay out of link.flags."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: app\nlink: static\nhost-tools: signer\n")
    (pc / "app.pc").write_text(
        "Name: app\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: ui\nRequires.private: trace\n"
        "Cflags: -I/usr/include/app\nCflags.private: -DAPP_STATIC\nLibs: -lapp\nLibs.private: -pthread\n"
    )
    (pc / "ui.pc").write_text(
        "Name: ui\nVersion: 2.0\nTarget: armv7-linux-gnueabihf\nRequires: core\n"
        "Cflags: -I/usr/include/ui\nCflags.private: -DUI_STATIC\nLibs: -lui\nLibs.private: -Wl,--whole-archive\n"
    )
    (pc / "core.pc").write_text(
        "Name: core\nVersion: 3.0\nTarget: armv7-linux-gnueabihf\nCflags: -I/usr/include/core\n"
        "Cflags.private: -DCORE_STATIC\nLibs: -lcore\nLibs.private: -lm\n"
    )
    (pc / "trace.pc").write_text(
        "Name: trace\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nCflags: -I/usr/include/trace\n"
        "Cflags.private: -DTRACE_STATIC\nLibs.private: -ltracepriv\nLibs: -ltrace\n"
    )
    (pc / "signer.pc").write_text(
        "Name: signer\nVersion: 1.0\nTarget: host\nProvides: signer\nCflags.private: -DHOST_ONLY\nLibs.private: -lhostonly\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    flags = (tmp_path / "out" / "link.flags").read_text().strip().split()
    assert flags[:7] == [
        f"-I{root / 'sysroot'}/usr/include/core",
        "-DCORE_STATIC",
        f"-I{root / 'sysroot'}/usr/include/trace",
        "-DTRACE_STATIC",
        f"-I{root / 'sysroot'}/usr/include/ui",
        "-DUI_STATIC",
        f"-I{root / 'sysroot'}/usr/include/app",
    ]
    assert "-DHOST_ONLY" not in flags and "-lhostonly" not in flags


def test_shared_plan_suppresses_private_flags_after_backtracking(tmp_path):
    """Private flags are excluded from shared output even when provider search must backtrack through conflicts."""
    build_tool()
    root = tmp_path / "case"
    pc = root / "sysroot" / "pkgconfig"
    pc.mkdir(parents=True)
    (root / "request.plan").write_text("roots: shell\nlink: shared\n")
    (pc / "shell.pc").write_text(
        "Name: shell\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nRequires: render-api >= 2, policy\n"
        "Cflags: -I/usr/include/shell\nCflags.private: -DSHELL_STATIC\nLibs: -lshell\nLibs.private: -lshellpriv\n"
    )
    (pc / "render-fast.pc").write_text(
        "Name: render-fast\nVersion: 4.0\nTarget: armv7-linux-gnueabihf\nProvides: render-api = 4.0\nConflicts: policy\n"
        "Cflags: -I/usr/include/fast\nCflags.private: -DFAST_STATIC\nLibs: -lfast\nLibs.private: -lfastpriv\n"
    )
    (pc / "render-safe.pc").write_text(
        "Name: render-safe\nVersion: 2.1\nTarget: armv7-linux-gnueabihf\nProvides: render-api = 2.1\n"
        "Cflags: -I/usr/include/safe\nCflags.private: -DSAFE_STATIC\nLibs: -lsafe\nLibs.private: -lsafepriv\n"
    )
    (pc / "policy.pc").write_text(
        "Name: policy\nVersion: 1.0\nTarget: armv7-linux-gnueabihf\nCflags: -I/usr/include/policy\n"
        "Cflags.private: -DPOLICY_STATIC\nLibs: -lpolicy\nLibs.private: -lpolicypriv\n"
    )
    assert_success(root / "sysroot", root / "request.plan", tmp_path / "out")
    flags = (tmp_path / "out" / "link.flags").read_text()
    assert "render-safe" in (tmp_path / "out" / "packages.tsv").read_text()
    assert "render-fast" not in (tmp_path / "out" / "packages.tsv").read_text()
    assert "STATIC" not in flags and "priv" not in flags
