import json
import subprocess
from pathlib import Path

ROOT = Path("/app/relplan")
BIN = ROOT / "bin" / "relplan"


def build_tool():
    subprocess.run([str(ROOT / "scripts" / "build.sh")], cwd=ROOT, check=True)
    assert BIN.exists(), "build script did not create /app/relplan/bin/relplan"


def run_plan(tmp_path, manifest_text, entry="_start"):
    build_tool()
    manifest = tmp_path / "release.manifest"
    out_map = tmp_path / "map.json"
    diag = tmp_path / "diag.txt"
    manifest.write_text(manifest_text)
    before = manifest.read_text()
    subprocess.run(
        [
            str(BIN),
            "--manifest",
            str(manifest),
            "--entry",
            entry,
            "--map",
            str(out_map),
            "--diagnostics",
            str(diag),
        ],
        cwd=ROOT,
        check=True,
    )
    assert manifest.read_text() == before, "planner mutated its manifest input"
    return json.loads(out_map.read_text()), diag.read_text()


def symbols_by_name(plan):
    return {sym["name"]: sym for sym in plan["symbols"]}


def reloc_keyed(plan):
    return {
        (r["object"], r["section"], r["offset"], r["symbol"]): r
        for r in plan["relocations"]
    }


def test_archive_common_alignment_and_relocations(tmp_path):
    """Plan archive rescans, common allocation, padding, and both relocations."""
    manifest = """
object crt0.o
  section .text 21 16
  define _start .text 0 strong
  need main strong
  reloc .text 8 R_PC32 main -4
end

archive libutil.a
  member unused_weak.o
    section .text 5 1
    define optional .text 0 strong
  endmember
  member main.o
    section .text 13 4
    section .data 6 2
    define main .text 0 strong
    need helper strong
    need optional weak
    common shared_buf 12 8
    reloc .text 4 R_PC32 helper -4
    reloc .data 2 R_ABS32 shared_buf 3
  endmember
  member helper.o
    section .text 9 8
    section .rodata 7 4
    define helper .text 2 strong
    define optional .rodata 1 weak
    common shared_buf 20 16
    reloc .text 1 R_ABS32 optional 5
  endmember
endarchive

object plugin.o
  section .data 5 4
  define plugin .data 1 weak
end
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    assert plan["status"] == "ok"
    assert plan["entry"] == "_start"
    assert plan["included"] == [
        "crt0.o",
        "libutil.a(main.o)",
        "libutil.a(helper.o)",
        "plugin.o",
    ]
    assert [s["name"] for s in plan["sections"]] == [
        ".text",
        ".rodata",
        ".data",
        ".bss",
    ]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 0x31, "alignment": 16},
        {"name": ".rodata", "address": 0x1040, "size": 7, "alignment": 16},
        {"name": ".data", "address": 0x1050, "size": 13, "alignment": 16},
        {"name": ".bss", "address": 0x1060, "size": 20, "alignment": 16},
    ]

    syms = symbols_by_name(plan)
    assert list(syms) == sorted(syms), "symbols must be emitted in name order"
    assert syms["_start"]["address"] == 0x1000
    assert syms["main"]["address"] == 0x1018
    assert syms["helper"]["address"] == 0x102A
    assert syms["optional"]["binding"] == "weak"
    assert syms["optional"]["address"] == 0x1041
    assert syms["plugin"]["binding"] == "weak"
    assert syms["plugin"]["address"] == 0x1059
    assert syms["shared_buf"] == {
        "name": "shared_buf",
        "binding": "common",
        "section": ".bss",
        "address": 0x1060,
        "size": 20,
        "defined_by": "libutil.a(helper.o)",
    }

    relocs = reloc_keyed(plan)
    assert relocs[("crt0.o", ".text", 8, "main")]["value"] == 12
    assert relocs[("libutil.a(main.o)", ".text", 4, "helper")]["value"] == 10
    assert relocs[("libutil.a(main.o)", ".data", 2, "shared_buf")]["value"] == 0x1063
    assert relocs[("libutil.a(helper.o)", ".text", 1, "optional")]["value"] == 0x1046


def test_error_reports_are_stable_and_archives_are_not_rescanned(tmp_path):
    """Report stable errors without rescanning an earlier archive."""
    manifest = """
archive early.a
  member late_provider.o
    section .text 4 4
    define late .text 0 strong
  endmember
endarchive

object caller.o
  section .text 10 2
  define main .text 0 strong
  need late strong
  need late strong
  need dupe strong
  reloc .text 2 R_ABS32 late 0
end

object first.o
  section .text 3 1
  define dupe .text 0 strong
end

object second.o
  section .text 3 1
  define dupe .text 0 strong
end
"""
    plan, diag = run_plan(tmp_path, manifest, entry="missing_entry")

    assert plan["status"] == "error"
    assert plan["included"] == ["caller.o", "first.o", "second.o"]
    lines = diag.splitlines()
    assert lines == sorted(lines)
    assert lines == [
        "duplicate strong symbol dupe in second.o (first first.o)",
        "undefined entry missing_entry",
        "undefined symbol late needed by caller.o",
    ]
    assert symbols_by_name(plan)["main"]["address"] == 0x1000
    assert reloc_keyed(plan)[("caller.o", ".text", 2, "late")]["value"] == 0


def test_weak_need_does_not_extract_archive_member_and_output_is_repeatable(tmp_path):
    """Keep weak-only archive references unresolved and output repeatable JSON."""
    manifest = """
object app.o
  section .text 8 4
  define main .text 0 strong
  need optional weak
  reloc .text 4 R_ABS32 optional 7
end

archive opt.a
  member optional.o
    section .text 4 4
    define optional .text 0 strong
  endmember
endarchive
"""
    plan1, diag1 = run_plan(tmp_path, manifest, entry="main")
    first_map = json.dumps(plan1, sort_keys=True)

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    plan2, diag2 = run_plan(second_dir, manifest, entry="main")
    assert json.dumps(plan2, sort_keys=True) == first_map
    assert diag1 == diag2 == "OK\n"
    assert plan1["included"] == ["app.o"]
    assert "optional" not in symbols_by_name(plan1)
    assert reloc_keyed(plan1)[("app.o", ".text", 4, "optional")]["value"] == 7


def test_comments_hex_symbol_precedence_and_extra_sections(tmp_path):
    """Honor comments, hex integers, precedence, and extra section ordering."""
    manifest = """
# parser should ignore full-line comments and blank lines
object boot.o
  section .text 0x10 0x10  # inline comments are ignored too
  section .zeta 0x3 1
  define entry .text 0x0 strong
  define weak_first .text 0x1 weak
  define common_winner .text 0x2 strong
  common common_winner 0x20 0x10
  need alt strong
end

archive libhex.a
  member provider.o
    section .data 0x4 0x4
    define weak_first .data 0x0 weak
    define alt .data 0x1 strong
    common heap 0x8 0x4
  endmember
endarchive

object later.o
  section .alpha 0x5 1
  section .extra 0x7 1
  define common_winner .alpha 0x0 weak
  define weak_first .extra 0x1 weak
  common aardvark 0x4 0x4
  common heap 0x10 0x8
end
"""
    plan, diag = run_plan(tmp_path, manifest, entry="entry")

    assert diag == "OK\n"
    assert plan["included"] == ["boot.o", "libhex.a(provider.o)", "later.o"]
    assert [s["name"] for s in plan["sections"]] == [
        ".text",
        ".data",
        ".bss",
        ".alpha",
        ".extra",
        ".zeta",
    ]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 0x10, "alignment": 16},
        {"name": ".data", "address": 0x1010, "size": 0x4, "alignment": 16},
        {"name": ".bss", "address": 0x1020, "size": 0x18, "alignment": 16},
        {"name": ".alpha", "address": 0x1040, "size": 0x5, "alignment": 16},
        {"name": ".extra", "address": 0x1050, "size": 0x7, "alignment": 16},
        {"name": ".zeta", "address": 0x1060, "size": 0x3, "alignment": 16},
    ]

    syms = symbols_by_name(plan)
    assert syms["alt"]["address"] == 0x1011
    assert syms["aardvark"]["address"] == 0x1020
    assert syms["common_winner"] == {
        "name": "common_winner",
        "binding": "strong",
        "section": ".text",
        "address": 0x1002,
        "size": 0,
        "defined_by": "boot.o",
    }
    assert syms["weak_first"] == {
        "name": "weak_first",
        "binding": "weak",
        "section": ".text",
        "address": 0x1001,
        "size": 0,
        "defined_by": "boot.o",
    }
    assert syms["heap"] == {
        "name": "heap",
        "binding": "common",
        "section": ".bss",
        "address": 0x1028,
        "size": 0x10,
        "defined_by": "later.o",
    }


def test_aliases_extract_archives_resolve_chains_and_report_cycles(tmp_path):
    """Resolve aliases after layout and diagnose alias cycles."""
    ok_manifest = """
object start.o
  section .text 12 4
  define _start .text 0 strong
  need api strong
  reloc .text 4 R_PC32 public_api -4
end

archive libalias.a
  member facade.o
    section .rodata 3 1
    alias api impl strong
    alias public_api api strong
    need impl strong
  endmember
  member impl.o
    section .text 9 8
    define impl .text 1 strong
    alias maybe_absent optional weak
    reloc .text 2 R_ABS32 maybe_absent 11
  endmember
endarchive
"""
    plan, diag = run_plan(tmp_path, ok_manifest)

    assert diag == "OK\n"
    assert plan["included"] == ["start.o", "libalias.a(facade.o)", "libalias.a(impl.o)"]
    syms = symbols_by_name(plan)
    assert syms["impl"]["address"] == 0x1011
    assert syms["api"] == {
        "name": "api",
        "binding": "strong",
        "section": ".text",
        "address": 0x1011,
        "size": 0,
        "defined_by": "libalias.a(facade.o)",
    }
    assert syms["public_api"]["address"] == 0x1011
    assert syms["maybe_absent"] == {
        "name": "maybe_absent",
        "binding": "weak",
        "section": "",
        "address": 0,
        "size": 0,
        "defined_by": "libalias.a(impl.o)",
    }
    relocs = reloc_keyed(plan)
    assert relocs[("start.o", ".text", 4, "public_api")]["value"] == 9
    assert relocs[("libalias.a(impl.o)", ".text", 2, "maybe_absent")]["value"] == 11

    bad_manifest = """
object loops.o
  section .text 4 4
  define _start .text 0 strong
  alias a b strong
  alias b a strong
  alias c missing strong
end
"""
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_plan, bad_diag = run_plan(bad_dir, bad_manifest)
    assert bad_plan["status"] == "error"
    assert bad_diag.splitlines() == [
        "alias cycle a in loops.o",
        "alias cycle b in loops.o",
        "undefined symbol missing needed by loops.o",
    ]


def test_archive_scan_boundaries_and_signed_pc_relative_relocations(tmp_path):
    """Preserve archive scan boundaries and signed PC-relative relocation values."""
    manifest = """
archive too_early.a
  member provider.o
    section .text 3 1
    define late_target .text 0 strong
  endmember
endarchive

object entry.o
  section .text 8 4
  define _start .text 0 strong
  need bridge strong
  reloc .text 4 R_PC32 late_target -4
end

archive bridge.a
  member bridge.o
    section .text 6 2
    define bridge .text 0 strong
    need late_target strong
  endmember
endarchive
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert plan["status"] == "error"
    assert plan["included"] == ["entry.o", "bridge.a(bridge.o)"]
    assert diag.splitlines() == [
        "undefined symbol late_target needed by bridge.a(bridge.o)"
    ]
    relocs = reloc_keyed(plan)
    assert relocs[("entry.o", ".text", 4, "late_target")]["value"] == -0x1008


def test_common_tie_break_and_alias_common_suppression(tmp_path):
    """Apply common tie-breaks and suppress common storage behind strong aliases."""
    manifest = """
object boot.o
  section .text 16 8
  define _start .text 0 strong
  define real_buf .text 8 strong
  alias shared real_buf strong
  common shared 64 32
  common scratch 16 4
end

object tie_a.o
  common scratch 24 8
end

object tie_b.o
  common scratch 24 16
end

object tie_c.o
  common scratch 24 16
end
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    syms = symbols_by_name(plan)
    assert syms["shared"] == {
        "name": "shared",
        "binding": "strong",
        "section": ".text",
        "address": 0x1008,
        "size": 0,
        "defined_by": "boot.o",
    }
    assert syms["scratch"] == {
        "name": "scratch",
        "binding": "common",
        "section": ".bss",
        "address": 0x1010,
        "size": 24,
        "defined_by": "tie_b.o",
    }
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 16, "alignment": 16},
        {"name": ".bss", "address": 0x1010, "size": 24, "alignment": 16},
    ]


def test_interleaved_archive_aliases_commons_and_section_padding(tmp_path):
    """Combine archive fixed-points, alias precedence, common JSON binding, and PC-relative bases."""
    manifest = """
object entry.o
  section .text 5 1
  define _start .text 0 strong
  need dispatch strong
  reloc .text 1 R_PC32 api 0
end

archive core.a
  member dispatch.o
    section .text 7 4
    define dispatch .text 0 strong
    need api strong
    need heap weak
    reloc .text 3 R_ABS32 heap 2
  endmember
  member api_alias.o
    section .rodata 3 1
    alias api impl strong
    need impl strong
  endmember
  member impl.o
    section .text 11 8
    define impl .text 2 strong
    common heap 9 8
  endmember
endarchive

object final.o
  section .data 6 2
  common heap 9 16
  common tail 4 4
end
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    assert plan["included"] == [
        "entry.o",
        "core.a(dispatch.o)",
        "core.a(api_alias.o)",
        "core.a(impl.o)",
        "final.o",
    ]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 27, "alignment": 16},
        {"name": ".rodata", "address": 0x1020, "size": 3, "alignment": 16},
        {"name": ".data", "address": 0x1030, "size": 6, "alignment": 16},
        {"name": ".bss", "address": 0x1040, "size": 16, "alignment": 16},
    ]

    syms = symbols_by_name(plan)
    assert syms["api"] == {
        "name": "api",
        "binding": "strong",
        "section": ".text",
        "address": 0x1012,
        "size": 0,
        "defined_by": "core.a(api_alias.o)",
    }
    assert syms["heap"] == {
        "name": "heap",
        "binding": "common",
        "section": ".bss",
        "address": 0x1040,
        "size": 9,
        "defined_by": "final.o",
    }
    assert syms["tail"] == {
        "name": "tail",
        "binding": "common",
        "section": ".bss",
        "address": 0x104C,
        "size": 4,
        "defined_by": "final.o",
    }
    relocs = reloc_keyed(plan)
    assert relocs[("entry.o", ".text", 1, "api")]["value"] == 17
    assert relocs[("core.a(dispatch.o)", ".text", 3, "heap")]["value"] == 0x1042


def test_weak_alias_cannot_satisfy_strong_archive_need(tmp_path):
    """Reject weak alias extraction for strong archive needs."""
    manifest = """
object boot.o
  section .text 8 4
  define _start .text 0 strong
  need svc strong
  reloc .text 4 R_ABS32 svc 0
end

archive libsvc.a
  member shim.o
    section .rodata 3 1
    alias svc missing weak
  endmember
  member svc.o
    section .text 5 4
    define svc .text 1 strong
  endmember
endarchive
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    assert plan["included"] == ["boot.o", "libsvc.a(svc.o)"]
    syms = symbols_by_name(plan)
    assert syms["svc"] == {
        "name": "svc",
        "binding": "strong",
        "section": ".text",
        "address": 0x1009,
        "size": 0,
        "defined_by": "libsvc.a(svc.o)",
    }
    assert reloc_keyed(plan)[("boot.o", ".text", 4, "svc")]["value"] == 0x1009


def test_duplicate_strong_keeps_first_symbol_for_map_and_relocations(tmp_path):
    """Keep the first strong definition selected after duplicate diagnostics."""
    manifest = """
object first.o
  section .text 10 2
  define _start .text 0 strong
  define api .text 2 strong
  reloc .text 4 R_PC32 api 0
end

object second.o
  section .text 8 8
  define api .text 6 strong
end

object caller.o
  section .data 4 4
  alias entry_alias api strong
  reloc .data 0 R_ABS32 entry_alias 1
end
"""
    plan, diag = run_plan(tmp_path, manifest, entry="entry_alias")

    assert plan["status"] == "error"
    assert diag.splitlines() == [
        "duplicate strong symbol api in second.o (first first.o)"
    ]
    syms = symbols_by_name(plan)
    assert syms["api"] == {
        "name": "api",
        "binding": "strong",
        "section": ".text",
        "address": 0x1002,
        "size": 0,
        "defined_by": "first.o",
    }
    assert syms["entry_alias"]["address"] == 0x1002
    relocs = reloc_keyed(plan)
    assert relocs[("first.o", ".text", 4, "api")]["value"] == -2
    assert relocs[("caller.o", ".data", 0, "entry_alias")]["value"] == 0x1003


def test_group_archives_reach_fixed_point_across_archive_order(tmp_path):
    """Rescan archive groups across archive order and preserve layout interactions."""
    manifest = """
object entry.o
  section .text 6 2
  define _start .text 0 strong
  need boot strong
  reloc .text 2 R_PC32 helper -4
end

group release_group
  archive libhelpers.a
    member helper.o
      section .text 5 4
      define helper .text 1 strong
      common shared 6 4
      reloc .text 0 R_ABS32 shared 0
    endmember
  endarchive
  archive libboot.a
    member boot.o
      section .text 7 8
      alias boot helper strong
      need helper strong
    endmember
  endarchive
endgroup

object tail.o
  section .data 3 1
  common shared 9 8
end
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    assert plan["included"] == [
        "entry.o",
        "libboot.a(boot.o)",
        "libhelpers.a(helper.o)",
        "tail.o",
    ]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 0x15, "alignment": 16},
        {"name": ".data", "address": 0x1020, "size": 3, "alignment": 16},
        {"name": ".bss", "address": 0x1030, "size": 9, "alignment": 16},
    ]
    syms = symbols_by_name(plan)
    assert syms["boot"] == {
        "name": "boot",
        "binding": "strong",
        "section": ".text",
        "address": 0x1011,
        "size": 0,
        "defined_by": "libboot.a(boot.o)",
    }
    assert syms["helper"]["address"] == 0x1011
    assert syms["shared"] == {
        "name": "shared",
        "binding": "common",
        "section": ".bss",
        "address": 0x1030,
        "size": 9,
        "defined_by": "tail.o",
    }
    relocs = reloc_keyed(plan)
    assert relocs[("entry.o", ".text", 2, "helper")]["value"] == 11
    assert relocs[("libhelpers.a(helper.o)", ".text", 0, "shared")]["value"] == 0x1030



def test_comdat_sections_and_got_slots_interact_with_layout(tmp_path):
    """Discard duplicate COMDAT contributions while allocating GOT slots deterministically."""
    manifest = """
object boot.o
  section .text 8 4
  define _start .text 0 strong
  need api strong
  got api
  reloc .text 4 R_ABS32 __got_api 0
end

object feature_v1.o
  section .text 12 4 feature
  define api .text 4 strong
  reloc .text 0 R_ABS32 __got_api 1
end

object feature_v2.o
  section .text 20 16 feature
  define api .text 8 strong
  reloc .text 0 R_ABS32 api 0
end

object data.o
  section .data 4 4
  got missing
  reloc .data 0 R_ABS32 __got_missing 0
end
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert diag == "OK\n"
    assert plan["included"] == ["boot.o", "feature_v1.o", "feature_v2.o", "data.o"]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 20, "alignment": 16},
        {"name": ".data", "address": 0x1020, "size": 4, "alignment": 16},
        {"name": ".got", "address": 0x1030, "size": 16, "alignment": 16},
    ]
    syms = symbols_by_name(plan)
    assert syms["api"] == {
        "name": "api",
        "binding": "strong",
        "section": ".text",
        "address": 0x100C,
        "size": 0,
        "defined_by": "feature_v1.o",
    }
    assert syms["__got_api"] == {
        "name": "__got_api",
        "binding": "strong",
        "section": ".got",
        "address": 0x1030,
        "size": 8,
        "defined_by": "boot.o",
    }
    assert syms["__got_missing"]["address"] == 0x1038
    relocs = reloc_keyed(plan)
    assert relocs[("boot.o", ".text", 4, "__got_api")]["value"] == 0x1030
    assert relocs[("feature_v1.o", ".text", 0, "__got_api")]["value"] == 0x1031
    assert relocs[("data.o", ".data", 0, "__got_missing")]["value"] == 0x1038
    assert ("feature_v2.o", ".text", 0, "api") not in relocs


def test_archive_member_can_be_included_then_lose_discarded_comdat_symbols(tmp_path):
    """Report strong needs that only a discarded COMDAT contribution would satisfy."""
    manifest = """
object root.o
  section .text 4 4
  define _start .text 0 strong
  need svc strong
end

object vendor.o
  section .text 6 2 plugin
  define anchor .text 0 strong
end

archive patch.a
  member svcdup.o
    section .text 5 1 plugin
    define svc .text 0 strong
    got anchor
    reloc .text 0 R_ABS32 __got_anchor 0
  endmember
endarchive
"""
    plan, diag = run_plan(tmp_path, manifest)

    assert plan["status"] == "error"
    assert plan["included"] == ["root.o", "vendor.o", "patch.a(svcdup.o)"]
    assert diag.splitlines() == ["undefined symbol svc needed by root.o"]
    assert plan["sections"] == [
        {"name": ".text", "address": 0x1000, "size": 10, "alignment": 16},
        {"name": ".got", "address": 0x1010, "size": 8, "alignment": 16},
    ]
    syms = symbols_by_name(plan)
    assert "svc" not in syms
    assert syms["anchor"]["address"] == 0x1004
    assert syms["__got_anchor"] == {
        "name": "__got_anchor",
        "binding": "strong",
        "section": ".got",
        "address": 0x1010,
        "size": 8,
        "defined_by": "patch.a(svcdup.o)",
    }
    assert reloc_keyed(plan) == {}
