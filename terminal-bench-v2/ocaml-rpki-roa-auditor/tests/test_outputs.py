import hashlib
import json
import subprocess
from pathlib import Path

import pytest

APP = Path("/app/rpki-audit")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _h(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True, scope="session")
def build_binary():
    subprocess.run(
        ["make", "-C", "/app", "clean", "all"],
        check=True,
        capture_output=True,
        text=True,
    )


def _run(tmp_path: Path, evidence: Path, routes: str, now: int = 1_700_000_000):
    trust = tmp_path / "anchor.txt"
    route_file = tmp_path / "routes.txt"
    out = tmp_path / "report.json"
    _write(trust, "trust|TA|ROOTSKI|1600000000|1800000000|10.0.0.0/8,203.0.113.0/24\n")
    _write(route_file, routes)
    proc = subprocess.run(
        [
            str(APP),
            "--trust",
            str(trust),
            "--evidence",
            str(evidence),
            "--routes",
            str(route_file),
            "--now",
            str(now),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == ""
    return json.loads(out.read_text(encoding="utf-8"))


def _case_main(root: Path) -> Path:
    ev = root / "ev"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP1|TA|2001|CHILDSKI|ROOTSKI|1690000000|1750000000|1|10.8.0.0/14\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")

    _write(ev / "child.crl", "crl|ISP1|CHILDSKI|1699999900|1700000500|3003\n")
    _write(
        ev / "alpha.roa",
        "roa|R-good|ISP1|3001|CHILDSKI|1690000000|1750000000|64496|24|10.8.0.0/16\n",
    )
    _write(
        ev / "beta.roa",
        "roa|R-alt|ISP1|3011|CHILDSKI|1690000000|1750000000|64496|24|10.8.0.0/17\n",
    )
    _write(
        ev / "zero.roa",
        "roa|R-zero|ISP1|3002|CHILDSKI|1690000000|1750000000|0|24|10.8.64.0/18\n",
    )
    _write(
        ev / "rev.roa",
        "roa|R-rev|ISP1|3003|CHILDSKI|1690000000|1750000000|64497|24|10.9.0.0/16\n",
    )
    _write(
        ev / "wide.roa",
        "roa|R-wide|ISP1|3004|CHILDSKI|1690000000|1750000000|64498|24|10.12.0.0/16\n",
    )
    _write(
        ev / "short.roa",
        "roa|R-short|ISP1|3005|CHILDSKI|1690000000|1750000000|64499|20|10.10.0.0/16\n",
    )
    _write(
        ev / "loose.roa",
        "roa|R-loose|ISP1|3006|CHILDSKI|1690000000|1750000000|64500|24|10.11.0.0/16\n",
    )
    _write(ev / "note.dat", "cms-noise\n")
    _write(
        ev / "rogue.cert",
        "cert|ROGUE|ROGUE|9001|ROGUESKI|ROGUESKI|1690000000|1750000000|1|10.60.0.0/16\n",
    )
    _write(ev / "rogue.crl", "crl|ROGUE|ROGUESKI|1699999900|1700000500|-\n")
    _write(
        ev / "rogue.roa",
        "roa|R-rogue|ROGUE|9002|ROGUESKI|1690000000|1750000000|64560|24|10.60.1.0/24\n",
    )
    rogue_entries = f"rogue.crl={_h(ev / 'rogue.crl')},rogue.roa={_h(ev / 'rogue.roa')}"
    _write(ev / "rogue.mft", f"mft|ROGUE|ROGUESKI|1699999900|1700000500|{rogue_entries}\n")
    child_names = [
        "child.crl",
        "alpha.roa",
        "beta.roa",
        "zero.roa",
        "rev.roa",
        "wide.roa",
        "short.roa",
        "note.dat",
    ]
    child_entries = ",".join(f"{name}={_h(ev / name)}" for name in child_names)
    _write(ev / "child.mft", f"mft|ISP1|CHILDSKI|1699999900|1700000500|{child_entries}\n")
    return ev


def test_admission_report_covers_interacting_policy_layers(tmp_path):
    """Route decisions, evidence isolation, malformed rows, and stable ordering interact correctly."""
    ev = _case_main(tmp_path)
    routes = (
        "route|r1|64496|10.8.7.0/24\n"
        "route|r2|64496|10.8.64.0/24\n"
        "route|r3|64497|10.9.1.0/24\n"
        "route|r4|64498|10.12.1.0/24\n"
        "route|r5|64499|10.10.1.0/24\n"
        "route|r6|64500|10.11.0.0/24\n"
        "route|r7|64501|203.0.113.0/24\n"
        "route|r8|64560|10.60.1.0/24\n"
        "broken"
    )
    report = _run(tmp_path, ev, routes)
    assert report["generatedAt"] == 1_700_000_000
    assert set(report) == {"generatedAt", "routes", "evidence"}
    assert [r["id"] for r in report["routes"]] == [
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
        "r8",
        "",
    ]
    assert [(r["id"], r["decision"], r["matched"]) for r in report["routes"]] == [
        ("r1", "accepted", ["R-alt", "R-good"]),
        ("r2", "rejected", ["R-zero"]),
        ("r3", "not-found", []),
        ("r4", "not-found", []),
        ("r5", "not-found", []),
        ("r6", "not-found", []),
        ("r7", "not-found", []),
        ("r8", "not-found", []),
        ("", "invalid", []),
    ]
    assert report["evidence"]["accepted"] == ["alpha.roa", "beta.roa", "short.roa", "zero.roa"]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert report["evidence"]["rejected"] == sorted(report["evidence"]["rejected"], key=lambda x: (x["path"], x["reason"]))
    assert ("note.dat", "malformed") in rejected
    assert ("rev.roa", "revoked") in rejected
    assert ("wide.roa", "overclaim") in rejected
    assert ("loose.roa", "not-on-manifest") in rejected
    assert ("rogue.roa", "untrusted") in rejected


def test_other_asn_covering_roa_rejects_with_matched_ids(tmp_path):
    """A valid non-AS0 ROA for another ASN rejects a covered route and records the driving ROA ids."""
    ev = _case_main(tmp_path)
    report = _run(tmp_path, ev, "route|other|64555|10.8.7.0/24\n")
    assert report["routes"] == [
        {
            "id": "other",
            "asn": 64555,
            "prefix": "10.8.7.0/24",
            "decision": "rejected",
            "matched": ["R-alt", "R-good"],
        }
    ]
    assert report["evidence"]["accepted"] == ["alpha.roa", "beta.roa", "short.roa", "zero.roa"]


def test_ca_admission_depends_on_parent_state_and_resources(tmp_path):
    """Defective CA certificates do not admit their ROAs."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|7202\n")
    cases = [
        ("expired", "7101", "OLD", "OLDSKI", "1600000000", "1699999999", "10.40.0.0/16"),
        ("revoked", "7202", "REV", "REVSKI", "1690000000", "1750000000", "10.41.0.0/16"),
        ("missing", "7303", "MISS", "MISSSKI", "1690000000", "1750000000", "10.42.0.0/16"),
        ("wideca", "7404", "WIDECA", "WIDESKI", "1690000000", "1750000000", "192.0.2.0/24"),
    ]
    root_names = ["root.crl"]
    for name, serial, subject, ski, nb, na, resources in cases:
        cert_text = f"cert|{subject}|TA|{serial}|{ski}|ROOTSKI|{nb}|{na}|1|{resources}\n"
        _write(ev / f"{name}.cert", cert_text)
        if name != "missing":
            root_names.append(f"{name}.cert")
        _write(ev / f"{name}.crl", f"crl|{subject}|{ski}|1699999900|1700000500|-\n")
        roa_text = f"roa|R-{name}|{subject}|8{serial}|{ski}|1690000000|1750000000|64540|24|10.40.1.0/24\n"
        _write(ev / f"{name}.roa", roa_text)
        crl_hash = _h(ev / f"{name}.crl")
        roa_hash = _h(ev / f"{name}.roa")
        entries = f"{name}.crl={crl_hash},{name}.roa={roa_hash}"
        _write(ev / f"{name}.mft", f"mft|{subject}|{ski}|1699999900|1700000500|{entries}\n")
    root_entries = ",".join(f"{name}={_h(ev / name)}" for name in root_names)
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")

    report = _run(tmp_path, ev, "route|r|64540|10.40.1.0/24\n")
    assert report["routes"] == [
        {"id": "r", "asn": 64540, "prefix": "10.40.1.0/24", "decision": "not-found", "matched": []}
    ]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    for name, *_ in cases:
        assert (f"{name}.roa", "untrusted") in rejected
    assert report["evidence"]["accepted"] == []


def test_repository_state_does_not_fail_open(tmp_path):
    """Stale issuer CRLs and manifest hash mismatches suppress affected ROAs."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP2|TA|2101|SKI2|ROOTSKI|1690000000|1750000000|1|10.20.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP2|SKI2|1699990000|1699999000|-\n")
    _write(
        ev / "stale.roa",
        "roa|R-stale|ISP2|4101|SKI2|1690000000|1750000000|64510|24|10.20.1.0/24\n",
    )
    _write(
        ev / "tamper.roa",
        "roa|R-tamper|ISP2|4102|SKI2|1690000000|1750000000|64511|24|10.20.2.0/24\n",
    )
    old_hash = _h(ev / "tamper.roa")
    _write(
        ev / "tamper.roa",
        "roa|R-tamper|ISP2|4102|SKI2|1690000000|1750000000|64511|24|10.20.3.0/24\n",
    )
    _write(
        ev / "absent.roa",
        "roa|R-absent|ISP2|4103|SKI2|1690000000|1750000000|64512|24|10.20.4.0/24\n",
    )
    entries = (
        f"child.crl={_h(ev / 'child.crl')},"
        f"stale.roa={_h(ev / 'stale.roa')},tamper.roa={old_hash}"
    )
    _write(ev / "child.mft", f"mft|ISP2|SKI2|1699999900|1700000500|{entries}\n")
    routes = (
        "route|a|64510|10.20.1.0/24\n"
        "route|b|64511|10.20.3.0/24\n"
        "route|c|64512|10.20.4.0/24\n"
    )
    report = _run(tmp_path, ev, routes)
    assert [r["decision"] for r in report["routes"]] == ["not-found", "not-found", "not-found"]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("stale.roa", "stale") in rejected
    assert ("tamper.roa", "hash-mismatch") in rejected
    assert ("absent.roa", "not-on-manifest") in rejected
    assert report["evidence"]["accepted"] == []


def test_authoritative_manifest_replaces_older_fresh_listing(tmp_path):
    """The newest fresh manifest controls nested paths and does not union older listings."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP4|TA|2301|SKI4|ROOTSKI|1690000000|1750000000|1|10.70.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP4|SKI4|1699999900|1700000500|-\n")
    _write(
        ev / "old.roa",
        "roa|R-old|ISP4|6101|SKI4|1690000000|1750000000|64570|24|10.70.1.0/24\n",
    )
    _write(
        ev / "nested" / "current.roa",
        "roa|R-current|ISP4|6102|SKI4|1690000000|1750000000|64571|24|10.70.2.0/24\n",
    )
    old_entries = f"child.crl={_h(ev / 'child.crl')},old.roa={_h(ev / 'old.roa')}"
    new_entries = (
        f"child.crl={_h(ev / 'child.crl')},"
        f"nested/current.roa={_h(ev / 'nested' / 'current.roa')}"
    )
    _write(ev / "child-old.mft", f"mft|ISP4|SKI4|1699999900|1700000500|{old_entries}\n")
    _write(ev / "child-new.mft", f"mft|ISP4|SKI4|1700000000|1700000600|{new_entries}\n")
    routes = "route|old|64570|10.70.1.0/24\nroute|new|64571|10.70.2.0/24\n"
    report = _run(tmp_path, ev, routes)
    assert [(r["id"], r["decision"], r["matched"]) for r in report["routes"]] == [
        ("old", "not-found", []),
        ("new", "accepted", ["R-current"]),
    ]
    assert report["evidence"]["accepted"] == ["nested/current.roa"]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("old.roa", "not-on-manifest") in rejected


def test_newest_manifest_hash_supersedes_older_good_hash(tmp_path):
    """A newer manifest entry with a bad hash suppresses an older fresh manifest entry."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP5|TA|2401|SKI5|ROOTSKI|1690000000|1750000000|1|10.80.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP5|SKI5|1699999900|1700000500|-\n")
    _write(
        ev / "target.roa",
        "roa|R-target|ISP5|6201|SKI5|1690000000|1750000000|64580|24|10.80.1.0/24\n",
    )
    good_hash = _h(ev / "target.roa")
    _write(
        ev / "target.roa",
        "roa|R-target|ISP5|6201|SKI5|1690000000|1750000000|64580|24|10.80.2.0/24\n",
    )
    old_entries = f"child.crl={_h(ev / 'child.crl')},target.roa={_h(ev / 'target.roa')}"
    new_entries = f"child.crl={_h(ev / 'child.crl')},target.roa={good_hash}"
    _write(ev / "child-a.mft", f"mft|ISP5|SKI5|1699999900|1700000500|{old_entries}\n")
    _write(ev / "child-b.mft", f"mft|ISP5|SKI5|1700000000|1700000600|{new_entries}\n")
    report = _run(tmp_path, ev, "route|r|64580|10.80.2.0/24\n")
    assert report["routes"][0]["decision"] == "not-found"
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("target.roa", "hash-mismatch") in rejected
    assert report["evidence"]["accepted"] == []


def test_authoritative_crl_replaces_older_revocation_state(tmp_path):
    """The newest usable CRL determines revocation when an older fresh CRL disagrees."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP6|TA|2501|SKI6|ROOTSKI|1690000000|1750000000|1|10.90.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "old.crl", "crl|ISP6|SKI6|1699999900|1700000500|6301\n")
    _write(ev / "new.crl", "crl|ISP6|SKI6|1700000000|1700000600|-\n")
    _write(
        ev / "target.roa",
        "roa|R-live|ISP6|6301|SKI6|1690000000|1750000000|64590|24|10.90.1.0/24\n",
    )
    entries = (
        f"old.crl={_h(ev / 'old.crl')},new.crl={_h(ev / 'new.crl')},"
        f"target.roa={_h(ev / 'target.roa')}"
    )
    _write(ev / "child.mft", f"mft|ISP6|SKI6|1699999900|1700000500|{entries}\n")
    report = _run(tmp_path, ev, "route|r|64590|10.90.1.0/24\n")
    assert report["routes"] == [
        {"id": "r", "asn": 64590, "prefix": "10.90.1.0/24", "decision": "accepted", "matched": ["R-live"]}
    ]
    assert report["evidence"]["accepted"] == ["target.roa"]


def test_report_schema_records_generation_time_and_roa_only_acceptance(tmp_path):
    """The report echoes the audit time and admits only valid ROA paths."""
    ev = _case_main(tmp_path)
    routes = "route|r1|64496|10.8.7.0/24\n"
    report = _run(tmp_path, ev, routes, now=1_700_000_123)
    assert set(report) == {"generatedAt", "routes", "evidence"}
    assert report["generatedAt"] == 1_700_000_123
    assert report["evidence"]["accepted"] == ["alpha.roa", "beta.roa", "short.roa", "zero.roa"]
    assert not {"root.crl", "root.mft", "child.cert", "child.crl", "child.mft"} & set(
        report["evidence"]["accepted"]
    )


def test_maxlength_same_asn_overrun_is_not_found(tmp_path):
    """A same-ASN ROA whose maxLength is shorter than the route prefix length does not reject the route."""
    ev = _case_main(tmp_path)
    report = _run(tmp_path, ev, "route|r5|64499|10.10.1.0/24\n")
    assert report["routes"] == [
        {"id": "r5", "asn": 64499, "prefix": "10.10.1.0/24", "decision": "not-found", "matched": []}
    ]


def test_stale_issuer_crl_takes_precedence_over_revocation(tmp_path):
    """When an issuer CRL is stale, dependent ROAs are stale even if that CRL lists their serial."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP3|TA|2201|SKI3|ROOTSKI|1690000000|1750000000|1|10.30.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP3|SKI3|1699990000|1699999000|5101\n")
    _write(
        ev / "target.roa",
        "roa|R-target|ISP3|5101|SKI3|1690000000|1750000000|64520|24|10.30.1.0/24\n",
    )
    entries = f"child.crl={_h(ev / 'child.crl')},target.roa={_h(ev / 'target.roa')}"
    _write(ev / "child.mft", f"mft|ISP3|SKI3|1699999900|1700000500|{entries}\n")
    report = _run(tmp_path, ev, "route|r|64520|10.30.1.0/24\n")
    assert report["routes"][0]["decision"] == "not-found"
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("target.roa", "stale") in rejected
    assert ("target.roa", "revoked") not in rejected


def test_path_length_constraints_limit_subordinate_ca_admission(tmp_path):
    """Finite CA path-length constraints allow ROA issuance but block excess CA delegation."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "limit.cert",
        "cert|LIMIT|TA|8101|LIMITSKI|ROOTSKI|1690000000|1750000000|1|10.120.0.0/16|0\n",
    )
    _write(
        ev / "one.cert",
        "cert|ONE|TA|8201|ONESKI|ROOTSKI|1690000000|1750000000|1|10.130.0.0/16|1\n",
    )
    root_entries = ",".join(f"{name}={_h(ev / name)}" for name in ["root.crl", "limit.cert", "one.cert"])
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")

    _write(ev / "limit.crl", "crl|LIMIT|LIMITSKI|1699999900|1700000500|-\n")
    _write(
        ev / "limit-direct.roa",
        "roa|R-limit-direct|LIMIT|8102|LIMITSKI|1690000000|1750000000|64620|24|10.120.1.0/24\n",
    )
    _write(
        ev / "blocked.cert",
        "cert|BLOCKED|LIMIT|8103|BLOCKEDSKI|LIMITSKI|1690000000|1750000000|1|10.120.2.0/24\n",
    )
    limit_entries = ",".join(
        f"{name}={_h(ev / name)}" for name in ["limit.crl", "limit-direct.roa", "blocked.cert"]
    )
    _write(ev / "limit.mft", f"mft|LIMIT|LIMITSKI|1699999900|1700000500|{limit_entries}\n")
    _write(ev / "blocked.crl", "crl|BLOCKED|BLOCKEDSKI|1699999900|1700000500|-\n")
    _write(
        ev / "blocked.roa",
        "roa|R-blocked|BLOCKED|8104|BLOCKEDSKI|1690000000|1750000000|64621|24|10.120.2.0/24\n",
    )
    blocked_entries = f"blocked.crl={_h(ev / 'blocked.crl')},blocked.roa={_h(ev / 'blocked.roa')}"
    _write(ev / "blocked.mft", f"mft|BLOCKED|BLOCKEDSKI|1699999900|1700000500|{blocked_entries}\n")

    _write(ev / "one.crl", "crl|ONE|ONESKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|CHILD|ONE|8202|CHILDSKI|ONESKI|1690000000|1750000000|1|10.130.1.0/24|1\n",
    )
    one_entries = f"one.crl={_h(ev / 'one.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "one.mft", f"mft|ONE|ONESKI|1699999900|1700000500|{one_entries}\n")
    _write(ev / "child.crl", "crl|CHILD|CHILDSKI|1699999900|1700000500|-\n")
    _write(
        ev / "grand.cert",
        "cert|GRAND|CHILD|8203|GRANDSKI|CHILDSKI|1690000000|1750000000|1|10.130.1.128/25\n",
    )
    _write(
        ev / "child.roa",
        "roa|R-child|CHILD|8204|CHILDSKI|1690000000|1750000000|64630|24|10.130.1.0/24\n",
    )
    child_entries = ",".join(f"{name}={_h(ev / name)}" for name in ["child.crl", "grand.cert", "child.roa"])
    _write(ev / "child.mft", f"mft|CHILD|CHILDSKI|1699999900|1700000500|{child_entries}\n")
    _write(ev / "grand.crl", "crl|GRAND|GRANDSKI|1699999900|1700000500|-\n")
    _write(
        ev / "grand.roa",
        "roa|R-grand|GRAND|8205|GRANDSKI|1690000000|1750000000|64631|25|10.130.1.128/25\n",
    )
    grand_entries = f"grand.crl={_h(ev / 'grand.crl')},grand.roa={_h(ev / 'grand.roa')}"
    _write(ev / "grand.mft", f"mft|GRAND|GRANDSKI|1699999900|1700000500|{grand_entries}\n")

    routes = (
        "route|direct|64620|10.120.1.0/24\n"
        "route|blocked|64621|10.120.2.0/24\n"
        "route|child|64630|10.130.1.0/24\n"
        "route|grand|64631|10.130.1.128/25\n"
    )
    report = _run(tmp_path, ev, routes)
    assert [(r["id"], r["decision"], r["matched"]) for r in report["routes"]] == [
        ("direct", "accepted", ["R-limit-direct"]),
        ("blocked", "not-found", []),
        ("child", "accepted", ["R-child"]),
        ("grand", "not-found", []),
    ]
    assert report["evidence"]["accepted"] == ["child.roa", "limit-direct.roa"]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("blocked.roa", "untrusted") in rejected
    assert ("grand.roa", "untrusted") in rejected


def test_multi_prefix_roa_requires_all_prefixes_within_issuer_resources(tmp_path):
    """A ROA with any prefix outside issuer resources is isolated before route use."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP7|TA|2601|SKI7|ROOTSKI|1690000000|1750000000|1|10.100.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP7|SKI7|1699999900|1700000500|-\n")
    _write(
        ev / "mixed.roa",
        "roa|R-mixed|ISP7|6401|SKI7|1690000000|1750000000|64640|24|10.100.1.0/24,203.0.113.0/24\n",
    )
    entries = f"child.crl={_h(ev / 'child.crl')},mixed.roa={_h(ev / 'mixed.roa')}"
    _write(ev / "child.mft", f"mft|ISP7|SKI7|1699999900|1700000500|{entries}\n")

    report = _run(tmp_path, ev, "route|mixed|64640|10.100.1.0/24\n")
    assert report["routes"] == [
        {"id": "mixed", "asn": 64640, "prefix": "10.100.1.0/24", "decision": "not-found", "matched": []}
    ]
    assert report["evidence"]["accepted"] == []
    assert {("mixed.roa", "overclaim")} <= {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}


def test_crl_must_be_listed_by_authoritative_manifest(tmp_path):
    """A fresh CRL outside the authoritative manifest cannot make issued ROAs usable."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP8|TA|2701|SKI8|ROOTSKI|1690000000|1750000000|1|10.110.0.0/16\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP8|SKI8|1699999900|1700000500|-\n")
    _write(
        ev / "target.roa",
        "roa|R-unlisted-crl|ISP8|6501|SKI8|1690000000|1750000000|64650|24|10.110.1.0/24\n",
    )
    _write(ev / "child.mft", f"mft|ISP8|SKI8|1699999900|1700000500|target.roa={_h(ev / 'target.roa')}\n")

    report = _run(tmp_path, ev, "route|r|64650|10.110.1.0/24\n")
    assert report["routes"] == [
        {"id": "r", "asn": 64650, "prefix": "10.110.1.0/24", "decision": "not-found", "matched": []}
    ]
    assert report["evidence"]["accepted"] == []
    assert {("target.roa", "stale")} <= {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}


def test_ca_resource_sets_require_every_prefix_within_parent(tmp_path):
    """A subordinate CA with mixed covered and uncovered resources is not admitted."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "mixed.cert",
        "cert|MIXED|TA|2801|MIXEDSKI|ROOTSKI|1690000000|1750000000|1|10.0.8.0/24,192.0.2.0/24\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},mixed.cert={_h(ev / 'mixed.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "mixed.crl", "crl|MIXED|MIXEDSKI|1699999900|1700000500|-\n")
    _write(
        ev / "mixed.roa",
        "roa|R-mixed-ca|MIXED|2802|MIXEDSKI|1690000000|1750000000|64660|24|10.0.8.0/24\n",
    )
    child_entries = f"mixed.crl={_h(ev / 'mixed.crl')},mixed.roa={_h(ev / 'mixed.roa')}"
    _write(ev / "mixed.mft", f"mft|MIXED|MIXEDSKI|1699999900|1700000500|{child_entries}\n")

    report = _run(tmp_path, ev, "route|mixed|64660|10.0.8.0/24\n")
    assert report["routes"] == [
        {"id": "mixed", "asn": 64660, "prefix": "10.0.8.0/24", "decision": "not-found", "matched": []}
    ]
    assert report["evidence"]["accepted"] == []
    assert {("mixed.roa", "untrusted")} <= {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}


def test_same_asn_roa_takes_precedence_over_other_asn_cover(tmp_path):
    """A route covered by same-ASN and other-ASN ROAs is accepted with only same-ASN matches."""
    ev = tmp_path / "bundle"
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|ISP9|TA|2901|SKI9|ROOTSKI|1690000000|1750000000|1|10.0.0.0/8\n",
    )
    root_entries = f"root.crl={_h(ev / 'root.crl')},child.cert={_h(ev / 'child.cert')}"
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "child.crl", "crl|ISP9|SKI9|1699999900|1700000500|-\n")
    _write(
        ev / "other.roa",
        "roa|R-other|ISP9|6601|SKI9|1690000000|1750000000|64661|24|10.1.0.0/16\n",
    )
    _write(
        ev / "same-b.roa",
        "roa|R-same-b|ISP9|6602|SKI9|1690000000|1750000000|64662|24|10.1.2.0/24\n",
    )
    _write(
        ev / "same-a.roa",
        "roa|R-same-a|ISP9|6603|SKI9|1690000000|1750000000|64662|24|10.1.0.0/16\n",
    )
    child_names = ["child.crl", "other.roa", "same-b.roa", "same-a.roa"]
    child_entries = ",".join(f"{name}={_h(ev / name)}" for name in child_names)
    _write(ev / "child.mft", f"mft|ISP9|SKI9|1699999900|1700000500|{child_entries}\n")

    report = _run(tmp_path, ev, "route|both|64662|10.1.2.0/24\n")
    assert report["routes"] == [
        {
            "id": "both",
            "asn": 64662,
            "prefix": "10.1.2.0/24",
            "decision": "accepted",
            "matched": ["R-same-a", "R-same-b"],
        }
    ]
    assert report["evidence"]["accepted"] == ["other.roa", "same-a.roa", "same-b.roa"]


def test_tie_breaks_validity_boundaries_and_empty_evidence_files(tmp_path):
    """Tie-breaking, time boundaries, end-entity CA flags, and empty evidence files follow the public profile."""
    ev = tmp_path / "bundle"
    _write(ev / "empty.dat", "")
    _write(ev / "root.crl", "crl|TA|ROOTSKI|1699999900|1700000500|-\n")
    _write(
        ev / "child.cert",
        "cert|TIE|TA|3001|TIESKI|ROOTSKI|1690000000|1750000000|1|10.2.0.0/16\n",
    )
    _write(
        ev / "ee.cert",
        "cert|EE|TA|3002|EESKI|ROOTSKI|1690000000|1750000000|0|10.3.0.0/16\n",
    )
    root_entries = ",".join(f"{name}={_h(ev / name)}" for name in ["root.crl", "child.cert", "ee.cert"])
    _write(ev / "root.mft", f"mft|TA|ROOTSKI|1699999900|1700000500|{root_entries}\n")
    _write(ev / "b.crl", "crl|TIE|TIESKI|1699999900|1700000500|6701\n")
    _write(ev / "a.crl", "crl|TIE|TIESKI|1699999900|1700000500|-\n")
    _write(
        ev / "listed.roa",
        "roa|R-listed|TIE|6701|TIESKI|1700000000|1700000000|64670|24|10.2.1.0/24\n",
    )
    _write(
        ev / "only-b.roa",
        "roa|R-only-b|TIE|6702|TIESKI|1690000000|1750000000|64671|24|10.2.2.0/24\n",
    )
    _write(
        ev / "ee.roa",
        "roa|R-ee|EE|6703|EESKI|1690000000|1750000000|64672|24|10.3.1.0/24\n",
    )
    a_entries = ",".join(f"{name}={_h(ev / name)}" for name in ["a.crl", "b.crl", "listed.roa"])
    b_entries = ",".join(f"{name}={_h(ev / name)}" for name in ["a.crl", "b.crl", "only-b.roa"])
    _write(ev / "a.mft", f"mft|TIE|TIESKI|1699999900|1700000500|{a_entries}\n")
    _write(ev / "b.mft", f"mft|TIE|TIESKI|1699999900|1700000500|{b_entries}\n")
    _write(ev / "ee.crl", "crl|EE|EESKI|1699999900|1700000500|-\n")
    ee_entries = f"ee.crl={_h(ev / 'ee.crl')},ee.roa={_h(ev / 'ee.roa')}"
    _write(ev / "ee.mft", f"mft|EE|EESKI|1699999900|1700000500|{ee_entries}\n")

    routes = (
        "route|listed|64670|10.2.1.0/24\n"
        "route|only-b|64671|10.2.2.0/24\n"
        "route|ee|64672|10.3.1.0/24\n"
    )
    report = _run(tmp_path, ev, routes)
    assert [(r["id"], r["decision"], r["matched"]) for r in report["routes"]] == [
        ("listed", "accepted", ["R-listed"]),
        ("only-b", "not-found", []),
        ("ee", "not-found", []),
    ]
    assert report["evidence"]["accepted"] == ["listed.roa"]
    rejected = {(x["path"], x["reason"]) for x in report["evidence"]["rejected"]}
    assert ("empty.dat", "malformed") not in rejected
    assert ("only-b.roa", "not-on-manifest") in rejected
    assert ("ee.roa", "untrusted") in rejected
