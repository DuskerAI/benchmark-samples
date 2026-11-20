"""Behavioral verifier for the crlink mini static linker.

The candidate's Crystal sources under /app are compiled, run against a battery
of link jobs, and the produced linked image is compared field by field against
an independent Python reference implementation of the contract in
/app/spec.md. Fixtures live beside this file and are never shipped into the
agent image.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

import reference

APP = "/app"
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

ALL_JOBS = [
    "weakstrong",
    "dupstrong",
    "comdat",
    "reloc_overflow",
    "archive_order",
    "nonlocal",
    "combined",
    "matrix_a",
    "matrix_b",
]


def _build_binary():
    """Compile the candidate linker; return the binary path or fail loudly."""
    subprocess.run(
        ["bash", "scripts/build.sh"],
        cwd=APP,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    binary = os.path.join(APP, "bin", "crlink")
    assert os.path.exists(binary), "build did not produce /app/bin/crlink"
    return binary


@pytest.fixture(scope="session")
def binary():
    """Session-scoped compiled candidate binary."""
    return _build_binary()


def _run_job(binary, job_name):
    """Run the candidate on a fixture job and return the parsed linked image."""
    src = os.path.join(FIXTURES, job_name)
    with tempfile.TemporaryDirectory() as tmp:
        job = os.path.join(tmp, job_name)
        shutil.copytree(src, job)
        out = os.path.join(tmp, "image.json")
        proc = subprocess.run(
            [binary, job, out],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, (
            f"crlink failed on {job_name}: {proc.stderr}"
        )
        with open(out) as fh:
            return json.load(fh)


def _expected(job_name):
    """Reference linked image for a fixture job."""
    return reference.link_job(os.path.join(FIXTURES, job_name))


def _reloc_key(r):
    """Return the stable identity fields for one emitted relocation."""
    return (r["object"], r["section"], r["offset"], r["kind"])


def _reloc_map(image):
    """Index relocation values by their stable identity fields."""
    return {_reloc_key(r): r["value"] for r in image["relocs"]}


def _section_multiset(image):
    """Normalize emitted sections for order-independent field comparison."""
    return sorted((s["name"], s["base"], s["size"]) for s in image["sections"])


# --------------------------------------------------------------------------
# Full-image equivalence across every job (integrated coverage).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("job", ALL_JOBS)
def test_symbols_match_reference(binary, job):
    """Resolved symbol addresses and bindings match the reference for every job."""
    got = _run_job(binary, job)
    exp = _expected(job)
    assert got["symbols"] == exp["symbols"]


@pytest.mark.parametrize("job", ALL_JOBS)
def test_sections_match_reference(binary, job):
    """Section layout (kept sections, bases, sizes) matches the reference."""
    got = _run_job(binary, job)
    exp = _expected(job)
    assert got["sections"] == exp["sections"]


@pytest.mark.parametrize("job", ALL_JOBS)
def test_relocs_match_reference(binary, job):
    """Every relocation value matches the reference for every job."""
    got = _run_job(binary, job)
    exp = _expected(job)
    assert got["relocs"] == exp["relocs"]


@pytest.mark.parametrize("job", ALL_JOBS)
def test_errors_match_reference(binary, job):
    """Diagnostic errors match the reference set for every job."""
    got = _run_job(binary, job)
    exp = _expected(job)
    assert got["errors"] == exp["errors"]


# --------------------------------------------------------------------------
# Independent rejection paths per defect layer.
# --------------------------------------------------------------------------

def test_strong_overrides_weak(binary):
    """A strong definition wins over a weak one regardless of link order."""
    img = _run_job(binary, "weakstrong")
    exp = _expected("weakstrong")
    assert img["symbols"]["gamma"]["binding"] == "STRONG"
    assert img["symbols"]["gamma"]["address"] == exp["symbols"]["gamma"]["address"]


def test_first_weak_wins(binary):
    """Among only weak definitions the first in link order provides the symbol."""
    img = _run_job(binary, "weakstrong")
    exp = _expected("weakstrong")
    assert img["symbols"]["beta"] == exp["symbols"]["beta"]


def test_duplicate_strong_keeps_first_and_reports(binary):
    """Two strong definitions collide: the first is kept and the error is recorded."""
    img = _run_job(binary, "dupstrong")
    exp = _expected("dupstrong")
    assert img["symbols"]["dup"] == exp["symbols"]["dup"]
    assert "duplicate strong symbol: dup" in img["errors"]


def test_comdat_keeps_single_copy(binary):
    """Duplicate COMDAT sections collapse to exactly one kept copy."""
    img = _run_job(binary, "comdat")
    inl = [s for s in img["sections"] if s["name"] == ".inl"]
    assert len(inl) == 1


def test_comdat_symbol_redirects_to_survivor(binary):
    """A symbol defined in a discarded COMDAT copy resolves to the survivor."""
    img = _run_job(binary, "comdat")
    exp = _expected("comdat")
    assert "inl_fn" in img["symbols"]
    assert img["symbols"]["inl_fn"] == exp["symbols"]["inl_fn"]
    got = _reloc_map(img)
    want = _reloc_map(exp)
    key = ("o2", ".text", 0, "ABS32")
    assert got.get(key) == want[key]


def test_relocation_signed_and_overflow(binary):
    """Relative relocations are signed and overflow cases are flagged."""
    img = _run_job(binary, "reloc_overflow")
    exp = _expected("reloc_overflow")
    got = _reloc_map(img)
    want = _reloc_map(exp)
    neg_key = ("m", ".text", 20, "REL32")
    assert got[neg_key] == want[neg_key]
    assert got[neg_key] < 0
    assert "REL32 overflow at m/.text+16" in img["errors"]
    assert "ABS16 overflow at m/.text+8" in img["errors"]
    assert "ABS32 overflow at m/.text+12" in img["errors"]


def test_archive_lazy_fixpoint(binary):
    """Archive members required only after a later pull are still included."""
    img = _run_job(binary, "archive_order")
    exp = _expected("archive_order")
    for name in ("top", "link", "base"):
        assert name in img["symbols"], f"missing {name}"
        assert img["symbols"][name] == exp["symbols"][name]
    assert img["errors"] == []


def test_archive_unneeded_member_excluded(binary):
    """An archive member that satisfies no undefined symbol is not pulled in."""
    img = _run_job(binary, "archive_order")
    assert "never" not in img["symbols"]


def test_nonlocal_resolution_feeds_relocation(binary):
    """A resolution/COMDAT fix changes a relocation value computed in a later pass."""
    img = _run_job(binary, "nonlocal")
    exp = _expected("nonlocal")
    got = _reloc_map(img)
    want = _reloc_map(exp)
    hook_key = ("caller", ".text", 4, "REL32")
    tbl_key = ("caller", ".text", 12, "REL32")
    assert got.get(hook_key) == want[hook_key]
    assert got.get(tbl_key) == want[tbl_key]
    assert img["errors"] == []


def test_combined_end_to_end(binary):
    """A job exercising the linker rules together matches the reference exactly."""
    img = _run_job(binary, "combined")
    exp = _expected("combined")
    assert img["symbols"] == exp["symbols"]
    assert img["sections"] == exp["sections"]
    assert img["relocs"] == exp["relocs"]
    assert img["errors"] == exp["errors"]


def test_common_precedence_coalescing_and_layout(binary):
    """Tentative definitions coalesce, obey precedence, and receive aligned storage."""
    img = _run_job(binary, "matrix_a")
    exp = _expected("matrix_a")
    assert img["symbols"]["cap"] == exp["symbols"]["cap"]
    assert img["symbols"]["arena"] == exp["symbols"]["arena"]
    assert img["symbols"]["fixed"] == exp["symbols"]["fixed"]
    assert img["symbols"]["cap"]["binding"] == "COMMON"
    assert img["symbols"]["arena"]["binding"] == "COMMON"
    assert img["symbols"]["fixed"]["binding"] == "STRONG"
    common = [section for section in img["sections"] if section["name"] == ".common"]
    assert common == [next(s for s in exp["sections"] if s["name"] == ".common")]
    assert _reloc_map(img) == _reloc_map(exp)


def test_common_archive_and_relocation_interaction(binary):
    """Archive-selected common storage participates in rescanning and later resolution."""
    img = _run_job(binary, "matrix_b")
    exp = _expected("matrix_b")
    assert img["symbols"]["pool"]["binding"] == "STRONG"
    assert img["symbols"]["leaf"]["binding"] == "COMMON"
    assert img["symbols"]["pool"] == exp["symbols"]["pool"]
    assert img["symbols"]["leaf"] == exp["symbols"]["leaf"]
    assert _reloc_map(img) == _reloc_map(exp)
    assert "dormant" not in img["symbols"]
    assert img["errors"] == ["unresolved symbol in reloc: dormant"]


def test_linked_image_has_only_the_contract_fields(binary):
    """The linked image uses the four public top-level fields and their JSON types."""
    img = _run_job(binary, "combined")
    assert set(img) == {"sections", "symbols", "relocs", "errors"}
    assert isinstance(img["sections"], list)
    assert isinstance(img["symbols"], dict)
    assert isinstance(img["relocs"], list)
    assert isinstance(img["errors"], list)


def test_diagnostics_are_nonfatal_but_malformed_input_is_fatal(binary):
    """Link diagnostics still emit an image, while malformed records fail the run."""
    diagnostic = os.path.join(FIXTURES, "dupstrong")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "diagnostic.json")
        proc = subprocess.run([binary, diagnostic, out], capture_output=True, text=True)
        assert proc.returncode == 0
        assert os.path.exists(out)
        with open(out) as fh:
            assert "duplicate strong symbol: dup" in json.load(fh)["errors"]

        bad_job = os.path.join(tmp, "bad")
        os.mkdir(bad_job)
        with open(os.path.join(bad_job, "link.list"), "w") as fh:
            fh.write("bad.cro\n")
        with open(os.path.join(bad_job, "bad.cro"), "w") as fh:
            fh.write("INVALID record\n")
        bad_out = os.path.join(tmp, "bad.json")
        bad = subprocess.run([binary, bad_job, bad_out], capture_output=True, text=True)
        assert bad.returncode != 0
        assert not os.path.exists(bad_out)

        missing_job = os.path.join(tmp, "missing")
        os.mkdir(missing_job)
        missing_out = os.path.join(tmp, "missing.json")
        missing = subprocess.run(
            [binary, missing_job, missing_out], capture_output=True, text=True
        )
        assert missing.returncode != 0
        assert not os.path.exists(missing_out)

        invalid = subprocess.run([binary], capture_output=True, text=True)
        assert invalid.returncode != 0
