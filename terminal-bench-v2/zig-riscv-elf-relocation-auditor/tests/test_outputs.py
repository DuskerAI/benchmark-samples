import json
import subprocess
from pathlib import Path


APP = Path("/app/rvaudit")
BIN = APP / "zig-out/bin/rvaudit"


def run_case(tmp_path, case):
    inp = tmp_path / "case.json"
    out = tmp_path / "out.json"
    inp.write_text(json.dumps(case, separators=(",", ":")), encoding="utf-8")
    subprocess.run([str(BIN), str(inp), str(out)], cwd=APP, check=True)
    return json.loads(out.read_text(encoding="utf-8"))


def build_tool():
    subprocess.run(["make", "clean", "all"], cwd=APP, check=True)
    assert BIN.exists(), "rvaudit binary was not built"


def section(case, idx):
    for item in case["sections"]:
        if item["index"] == idx:
            return item
    raise KeyError(idx)


def symbol(case, idx):
    for item in case["symbols"]:
        if item["index"] == idx:
            return item
    raise KeyError(idx)


def sym_addr(case, sym):
    sec = sym["section"]
    if sec == "ABS":
        return sym["value"]
    if sec == "UND":
        if sym["binding"] == "WEAK":
            return 0
        raise ValueError("UNRESOLVED")
    return section(case, sec)["addr"] + sym["value"]


def sext(value, bits):
    mask = (1 << bits) - 1
    value &= mask
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


def is_pcrel(kind):
    return kind in ("R_RISCV_PCREL_HI20", "R_RISCV_PCREL_LO12_I", "R_RISCV_PCREL_LO12_S")


def is_hi(kind):
    return kind == "R_RISCV_PCREL_HI20"


def is_lo(kind):
    return kind in ("R_RISCV_PCREL_LO12_I", "R_RISCV_PCREL_LO12_S")


def width(kind):
    return {
        "R_RISCV_64": 8,
        "R_RISCV_PCREL_HI20": 4,
        "R_RISCV_PCREL_LO12_I": 4,
        "R_RISCV_PCREL_LO12_S": 4,
        "R_RISCV_BRANCH": 4,
        "R_RISCV_JAL": 4,
        "R_RISCV_RVC_BRANCH": 2,
    }.get(kind)


def admitted_pcrel(case, reloc):
    if not is_pcrel(reloc["type"]) or not reloc.get("pair"):
        return False
    try:
        reloc_section = section(case, reloc["section"])
    except KeyError:
        return False
    if reloc.get("relaxed", False):
        return False
    w = width(reloc["type"])
    return reloc["offset"] >= 0 and reloc["offset"] + w <= reloc_section["size"]


def pairmate(case, reloc):
    key = reloc.get("pair")
    if not key or not admitted_pcrel(case, reloc):
        return None
    matches = [(i, other) for i, other in enumerate(case["relocations"]) if other.get("pair") == key and admitted_pcrel(case, other)]
    if len(matches) != 2:
        return None
    kinds = [item[1]["type"] for item in matches]
    if sum(1 for kind in kinds if is_hi(kind)) != 1 or sum(1 for kind in kinds if is_lo(kind)) != 1:
        return None
    for i, other in matches:
        if other is not reloc:
            return i, other
    return None


def reference(case):
    errors = []
    results = []
    for i, reloc in enumerate(case["relocations"]):
        kind = reloc["type"]
        if kind == "R_RISCV_RELAX":
            continue
        try:
            reloc_section = section(case, reloc["section"])
            place = reloc_section["addr"] + reloc["offset"]
        except KeyError:
            errors.append({"reloc": i, "code": "BAD_SECTION"})
            continue
        if reloc.get("relaxed", False):
            continue
        w = width(kind)
        if w is not None and (reloc["offset"] < 0 or reloc["offset"] + w > reloc_section["size"]):
            errors.append({"reloc": i, "code": "WRITE_BOUNDS"})
            continue
        if is_pcrel(kind):
            match = pairmate(case, reloc)
            if match is None:
                errors.append({"reloc": i, "code": "PAIR"})
                continue
            mate_idx, mate = match
            hi = reloc if is_hi(kind) else mate
            lo = mate if is_hi(kind) else reloc
            try:
                hs = symbol(case, hi["symbol"])
                base = sym_addr(case, hs)
            except KeyError:
                errors.append({"reloc": i, "code": "BAD_SYMBOL"})
                continue
            except ValueError:
                errors.append({"reloc": i, "code": "UNRESOLVED"})
                continue
            hplace = section(case, hi["section"])["addr"] + hi["offset"]
            delta = base + hi.get("addend", 0) + lo.get("addend", 0) - hplace
            value = (delta + 0x800) // 0x1000 if is_hi(kind) else sext(delta, 12)
            results.append({"reloc": i, "value": value, "target": base + hi.get("addend", 0) + lo.get("addend", 0), "paired": mate_idx})
            continue
        sym = None
        try:
            if "symbol" in reloc:
                sym = symbol(case, reloc["symbol"])
                target = sym_addr(case, sym) + reloc.get("addend", 0)
            else:
                target = reloc.get("addend", 0)
        except KeyError:
            errors.append({"reloc": i, "code": "BAD_SYMBOL"})
            continue
        except ValueError:
            errors.append({"reloc": i, "code": "UNRESOLVED"})
            continue
        if kind == "R_RISCV_64":
            results.append({"reloc": i, "value": target, "target": target, "paired": None})
        elif kind in ("R_RISCV_BRANCH", "R_RISCV_JAL", "R_RISCV_RVC_BRANCH"):
            delta = target - place
            limit = {"R_RISCV_BRANCH": 4096, "R_RISCV_JAL": 1048576, "R_RISCV_RVC_BRANCH": 256}[kind]
            if delta % 2:
                errors.append({"reloc": i, "code": "MISALIGNED"})
            elif delta < -limit or delta > limit - 2:
                errors.append({"reloc": i, "code": "RANGE"})
            else:
                results.append({"reloc": i, "value": delta, "target": target, "paired": None})
        else:
            errors.append({"reloc": i, "code": "UNSUPPORTED"})
    exports = [s["name"] for s in case["symbols"] if s["section"] != "UND" and s["binding"] in ("GLOBAL", "WEAK") and s["visibility"] in ("DEFAULT", "PROTECTED")]
    return {"ok": not errors, "errors": errors, "relocations": results, "exports": exports}


def assert_report(case, report):
    expected = reference(case)
    assert report == expected


def test_auditor_builds_and_writes_requested_path(tmp_path):
    """Checks that the project builds and the command writes a valid report to the requested file."""
    build_tool()
    case = {
        "sections": [{"name": ".text", "index": 3, "addr": 0x1000, "size": 64}, {"name": ".data", "index": 9, "addr": 0x6000, "size": 32}],
        "symbols": [{"name": "start", "index": 11, "section": 3, "value": 0, "binding": "GLOBAL", "visibility": "DEFAULT"}, {"name": "cell", "index": 29, "section": 9, "value": 12, "binding": "LOCAL", "visibility": "DEFAULT"}],
        "relocations": [{"section": 3, "offset": 8, "type": "R_RISCV_64", "symbol": 29, "addend": -4}],
    }
    report = run_case(tmp_path, case)
    assert_report(case, report)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["case.json", "out.json"]


def test_mixed_relocation_report_matches_reference(tmp_path):
    """Validates a mixed report with relative addressing, discarded records, and exported symbols."""
    build_tool()
    case = {
        "sections": [{"name": ".init", "index": 5, "addr": 0x80000000, "size": 128}, {"name": ".sdata", "index": 12, "addr": 0x80003800, "size": 96}],
        "symbols": [
            {"name": "entry", "index": 4, "section": 5, "value": 0, "binding": "GLOBAL", "visibility": "DEFAULT"},
            {"name": "near_data", "index": 18, "section": 12, "value": 0x7f4, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "svc", "index": 21, "section": "ABS", "value": 0x80000102, "binding": "WEAK", "visibility": "PROTECTED"},
            {"name": "private_hook", "index": 23, "section": 5, "value": 32, "binding": "GLOBAL", "visibility": "HIDDEN"},
        ],
        "relocations": [
            {"section": 5, "offset": 4, "type": "R_RISCV_PCREL_HI20", "symbol": 18, "addend": -2048, "pair": "a"},
            {"section": 5, "offset": 8, "type": "R_RISCV_RELAX"},
            {"section": 5, "offset": 12, "type": "R_RISCV_PCREL_LO12_I", "symbol": 4, "addend": 14, "pair": "a"},
            {"section": 5, "offset": 16, "type": "R_RISCV_64", "symbol": 21, "addend": -2},
            {"section": 5, "offset": 20, "type": "R_RISCV_64", "symbol": 23, "addend": 0, "relaxed": True},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_error_and_short_form_cases_are_stable(tmp_path):
    """Checks RVC_BRANCH limits, weak undefineds, unresolved strong symbols, and exports."""
    build_tool()
    case = {
        "sections": [{"name": ".text", "index": 1, "addr": 0x4000, "size": 512}, {"name": ".plt", "index": 4, "addr": 0x5000, "size": 128}],
        "symbols": [
            {"name": "root", "index": 1, "section": 1, "value": 0, "binding": "GLOBAL", "visibility": "DEFAULT"},
            {"name": "ok_edge", "index": 6, "section": 1, "value": 0x104, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "far_edge", "index": 8, "section": 4, "value": 0x40, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "missing", "index": 9, "section": "UND", "value": 0, "binding": "GLOBAL", "visibility": "DEFAULT"},
            {"name": "weak_missing", "index": 10, "section": "UND", "value": 0, "binding": "WEAK", "visibility": "DEFAULT"},
        ],
        "relocations": [
            {"section": 1, "offset": 6, "type": "R_RISCV_RVC_BRANCH", "symbol": 6, "addend": 0},
            {"section": 1, "offset": 10, "type": "R_RISCV_RVC_BRANCH", "symbol": 8, "addend": 0},
            {"section": 1, "offset": 14, "type": "R_RISCV_64", "symbol": 9, "addend": 4},
            {"section": 1, "offset": 18, "type": "R_RISCV_64", "symbol": 10, "addend": 12},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_explicit_reference_and_pair_errors_are_reported(tmp_path):
    """Exercises BAD_SECTION, BAD_SYMBOL, PAIR, and non-adjacent LO12_S pairing."""
    build_tool()
    case = {
        "sections": [
            {"name": ".text", "index": 30, "addr": 0x20000, "size": 256},
            {"name": ".rodata", "index": 44, "addr": 0x22800, "size": 256},
            {"name": ".trap", "index": 58, "addr": 0x20100, "size": 64},
        ],
        "symbols": [
            {"name": "anchor", "index": 301, "section": 30, "value": 0x20, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "table", "index": 777, "section": 44, "value": 0x70, "binding": "GLOBAL", "visibility": "PROTECTED"},
            {"name": "weak_abs", "index": 901, "section": "ABS", "value": 0x20006, "binding": "WEAK", "visibility": "DEFAULT"},
        ],
        "relocations": [
            {"section": 99, "offset": 0, "type": "R_RISCV_64", "symbol": 777, "addend": 1},
            {"section": 30, "offset": 4, "type": "R_RISCV_64", "symbol": 123456, "addend": 0},
            {"section": 30, "offset": 8, "type": "R_RISCV_PCREL_LO12_S", "symbol": 301, "addend": -8, "pair": "stray"},
            {"section": 30, "offset": 12, "type": "R_RISCV_PCREL_LO12_S", "symbol": 301, "addend": -12, "pair": "late"},
            {"section": 58, "offset": 2, "type": "R_RISCV_RVC_BRANCH", "symbol": 901, "addend": 0},
            {"section": 30, "offset": 16, "type": "R_RISCV_PCREL_HI20", "symbol": 777, "addend": 0x123, "pair": "late"},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_order_sensitive_indices_addends_and_discarded_records(tmp_path):
    """Combines sparse indexes, signed low parts, relaxed records, and export filtering."""
    build_tool()
    case = {
        "sections": [
            {"name": ".vectors", "index": 70, "addr": 0x100000, "size": 512},
            {"name": ".payload", "index": 15, "addr": 0x102ffc, "size": 512},
        ],
        "symbols": [
            {"name": "skip_hidden", "index": 2, "section": 70, "value": 0, "binding": "GLOBAL", "visibility": "HIDDEN"},
            {"name": "payload", "index": 480, "section": 15, "value": 0x1ff, "binding": "GLOBAL", "visibility": "DEFAULT"},
            {"name": "local_abs", "index": 17, "section": "ABS", "value": 0x111111, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "weak_payload", "index": 512, "section": 15, "value": 0x80, "binding": "WEAK", "visibility": "PROTECTED"},
        ],
        "relocations": [
            {"section": 70, "offset": 0x34, "type": "R_RISCV_PCREL_HI20", "symbol": 480, "addend": -0x201, "pair": "neglo"},
            {"section": 15, "offset": 0x20, "type": "R_RISCV_64", "symbol": 512, "addend": 5, "relaxed": True},
            {"section": 70, "offset": 0x38, "type": "R_RISCV_PCREL_LO12_I", "symbol": 2, "addend": -0x55, "pair": "neglo"},
            {"section": 70, "offset": 0x3c, "type": "R_RISCV_64", "symbol": 17, "addend": -0x10},
            {"section": 15, "offset": 0x22, "type": "R_RISCV_RELAX"},
            {"section": 70, "offset": 0x40, "type": "R_RISCV_RVC_BRANCH", "symbol": 480, "addend": -0x2bbf},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_pcrel_pairs_use_hi_context_with_decoy_lo_symbols(tmp_path):
    """Checks non-adjacent PCREL pairs whose LO12 symbols disagree with the HI20 symbols."""
    build_tool()
    case = {
        "sections": [
            {"name": ".text", "index": 7, "addr": 0x100000, "size": 512},
            {"name": ".data", "index": 13, "addr": 0x102200, "size": 512},
            {"name": ".got", "index": 31, "addr": 0x200000, "size": 256},
        ],
        "symbols": [
            {"name": "real_a", "index": 700, "section": 13, "value": 0x321, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "real_b", "index": 701, "section": 31, "value": 0x10, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "abs_decoy", "index": 702, "section": "ABS", "value": 0x7FFFFFFF, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "text_decoy", "index": 703, "section": 7, "value": 0x10, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "keep_order", "index": 704, "section": 31, "value": 0x20, "binding": "WEAK", "visibility": "PROTECTED"},
        ],
        "relocations": [
            {"section": 7, "offset": 0x24, "type": "R_RISCV_PCREL_LO12_I", "symbol": 702, "addend": -0x46, "pair": "left"},
            {"section": 7, "offset": 0x28, "type": "R_RISCV_64", "symbol": 704, "addend": 0x11},
            {"section": 7, "offset": 0x20, "type": "R_RISCV_PCREL_HI20", "symbol": 700, "addend": 0x8FF, "pair": "left"},
            {"section": 7, "offset": 0x80, "type": "R_RISCV_PCREL_HI20", "symbol": 701, "addend": -1, "pair": "right"},
            {"section": 13, "offset": 0x10, "type": "R_RISCV_PCREL_LO12_S", "symbol": 703, "addend": 0x7FE, "pair": "right"},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_priority_and_discard_ordering_are_observable(tmp_path):
    """Verifies branch error priority and the distinction between RELAX and relaxed records."""
    build_tool()
    case = {
        "sections": [
            {"name": ".text", "index": 10, "addr": 0x1000, "size": 256},
            {"name": ".far", "index": 20, "addr": 0x5000, "size": 256},
        ],
        "symbols": [
            {"name": "odd_far", "index": 88, "section": 20, "value": 1, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "near_even", "index": 89, "section": 10, "value": 0x30, "binding": "GLOBAL", "visibility": "DEFAULT"},
        ],
        "relocations": [
            {"section": 999, "offset": 0, "type": "R_RISCV_RELAX"},
            {"section": 10, "offset": 0, "type": "R_RISCV_RVC_BRANCH", "symbol": 88, "addend": 0},
            {"section": 999, "offset": 4, "type": "R_RISCV_64", "symbol": 89, "addend": 0, "relaxed": True},
            {"section": 10, "offset": 0x20, "type": "R_RISCV_RVC_BRANCH", "symbol": 89, "addend": 0},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_admission_rejects_out_of_section_writes_before_symbol_checks(tmp_path):
    """Rejects active writes outside section storage while preserving discard and error priority."""
    build_tool()
    case = {
        "sections": [{"name": ".text", "index": 6, "addr": 0x8000, "size": 16}],
        "symbols": [
            {"name": "entry", "index": 40, "section": 6, "value": 0, "binding": "GLOBAL", "visibility": "DEFAULT"},
        ],
        "relocations": [
            {"section": 6, "offset": 12, "type": "R_RISCV_64", "symbol": 999, "addend": 0},
            {"section": 6, "offset": -2, "type": "R_RISCV_RVC_BRANCH", "symbol": 40, "addend": 0},
            {"section": 6, "offset": 15, "type": "R_RISCV_RVC_BRANCH", "symbol": 999, "addend": 0, "relaxed": True},
            {"section": 6, "offset": 16, "type": "VENDOR_PRIVATE", "symbol": 40, "addend": 0},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_shipped_smoke_manifest_is_still_a_valid_audit_input(tmp_path):
    """Runs the packaged smoke case so the build-time case path and simple manifest stay live."""
    build_tool()
    smoke = json.loads(Path("/var/lib/rvaudit/cases/smoke.json").read_text(encoding="utf-8"))
    assert_report(smoke, run_case(tmp_path, smoke))


def test_ambiguous_pcrel_groups_and_branch_families(tmp_path):
    """Covers exact active PCREL grouping plus 4-byte branch and jump relocation ranges."""
    build_tool()
    case = {
        "sections": [
            {"name": ".text", "index": 1, "addr": 0x10000, "size": 4096},
            {"name": ".cold", "index": 2, "addr": 0x18000, "size": 2048},
            {"name": ".far", "index": 3, "addr": 0x101000, "size": 128},
        ],
        "symbols": [
            {"name": "near", "index": 10, "section": 1, "value": 0x900, "binding": "GLOBAL", "visibility": "DEFAULT"},
            {"name": "edge", "index": 11, "section": 1, "value": 0xFFE, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "far", "index": 12, "section": 3, "value": 0, "binding": "LOCAL", "visibility": "DEFAULT"},
            {"name": "weak_abs", "index": 13, "section": "ABS", "value": 0x180fe, "binding": "WEAK", "visibility": "PROTECTED"},
            {"name": "ghost", "index": 14, "section": "UND", "value": 0, "binding": "WEAK", "visibility": "DEFAULT"},
        ],
        "relocations": [
            {"section": 1, "offset": 0x20, "type": "R_RISCV_BRANCH", "symbol": 10, "addend": 0},
            {"section": 1, "offset": 0x24, "type": "R_RISCV_BRANCH", "symbol": 11, "addend": 1},
            {"section": 1, "offset": 0x28, "type": "R_RISCV_JAL", "symbol": 12, "addend": 0},
            {"section": 2, "offset": 0x10, "type": "R_RISCV_JAL", "symbol": 13, "addend": 1},
            {"section": 1, "offset": 0x40, "type": "R_RISCV_PCREL_HI20", "symbol": 10, "addend": 0x7ff, "pair": "tri"},
            {"section": 1, "offset": 0x44, "type": "R_RISCV_PCREL_LO12_I", "symbol": 9999, "addend": -0x12, "pair": "tri"},
            {"section": 1, "offset": 0x48, "type": "R_RISCV_PCREL_LO12_S", "symbol": 14, "addend": 0x33, "pair": "tri"},
            {"section": 1, "offset": 0x4c, "type": "R_RISCV_PCREL_HI20", "symbol": 10, "addend": 0, "pair": "relaxed-mate"},
            {"section": 1, "offset": 0x50, "type": "R_RISCV_PCREL_LO12_I", "symbol": 10, "addend": 0, "pair": "relaxed-mate", "relaxed": True},
            {"section": 1, "offset": 0x54, "type": "R_RISCV_PCREL_LO12_I", "symbol": 9999, "addend": 0xabc, "pair": "valid"},
            {"section": 1, "offset": 0x58, "type": "R_RISCV_PCREL_HI20", "symbol": 13, "addend": -0x81, "pair": "valid"},
        ],
    }
    assert_report(case, run_case(tmp_path, case))


def test_generated_order_sensitive_relocation_matrix(tmp_path):
    """Uses generated manifests to mix pair admission, discards, sparse indexes, and range checks."""
    build_tool()
    cases = []
    for seed in range(7):
        text = 0x20000 + seed * 0x3000
        data = text + 0x7000 + seed * 0x400
        cases.append({
            "sections": [
                {"name": f".text{seed}", "index": 100 + seed * 11, "addr": text, "size": 768},
                {"name": f".data{seed}", "index": 700 + seed * 13, "addr": data, "size": 512},
                {"name": f".plt{seed}", "index": 900 + seed * 17, "addr": text - 0x300 + seed * 2, "size": 384},
            ],
            "symbols": [
                {"name": f"entry_{seed}", "index": 5000 + seed * 101, "section": 100 + seed * 11, "value": 0x30 + seed * 2, "binding": "GLOBAL", "visibility": "DEFAULT"},
                {"name": f"cell_{seed}", "index": 6000 + seed * 103, "section": 700 + seed * 13, "value": 0x80 + seed * 9, "binding": "WEAK", "visibility": "PROTECTED"},
                {"name": f"abs_{seed}", "index": 7000 + seed * 107, "section": "ABS", "value": text + 0x100 + seed * 3, "binding": "LOCAL", "visibility": "DEFAULT"},
                {"name": f"miss_{seed}", "index": 8000 + seed * 109, "section": "UND", "value": 0, "binding": "WEAK", "visibility": "DEFAULT"},
                {"name": f"hidden_{seed}", "index": 9000 + seed * 113, "section": 900 + seed * 17, "value": 0x20, "binding": "GLOBAL", "visibility": "HIDDEN"},
            ],
            "relocations": [
                {"section": 100 + seed * 11, "offset": 0x10, "type": "R_RISCV_PCREL_LO12_I", "symbol": 9000 + seed * 113, "addend": -0x701 + seed, "pair": f"p{seed}"},
                {"section": 100 + seed * 11, "offset": 0x14, "type": "R_RISCV_BRANCH", "symbol": 5000 + seed * 101, "addend": 2 * seed},
                {"section": 100 + seed * 11, "offset": 0x18, "type": "R_RISCV_PCREL_HI20", "symbol": 6000 + seed * 103, "addend": 0x123 - seed * 17, "pair": f"p{seed}"},
                {"section": 700 + seed * 13, "offset": 0x20, "type": "R_RISCV_64", "symbol": 7000 + seed * 107, "addend": -seed * 5},
                {"section": 100 + seed * 11, "offset": 0x1c, "type": "R_RISCV_RVC_BRANCH", "symbol": 8000 + seed * 109, "addend": text + 0x1e + seed * 2},
                {"section": 900 + seed * 17, "offset": 0x24, "type": "R_RISCV_JAL", "symbol": 6000 + seed * 103, "addend": -0x10000 + seed},
                {"section": 100 + seed * 11, "offset": 765, "type": "R_RISCV_BRANCH", "symbol": 5000 + seed * 101, "addend": 0},
                {"section": 404040 + seed, "offset": 0, "type": "R_RISCV_RELAX"},
                {"section": 404040 + seed, "offset": 0, "type": "R_RISCV_64", "symbol": 5000 + seed * 101, "relaxed": True},
            ],
        })
    for case in cases:
        assert_report(case, run_case(tmp_path, case))
