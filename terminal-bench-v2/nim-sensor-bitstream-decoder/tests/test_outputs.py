"""Behavioral verifier for the SARC sensor-archive decoder.

The decoder under /app is built from source, then run against deterministically
generated archives spanning the full public contract. Every expected result is
recomputed independently by sarlib.reference_decode, which shares no code with
the Nim binary, so a wrong decoder is rejected on sample values rather than on
output shape.
"""

import subprocess
from pathlib import Path

import gen_fixtures
import pytest
import sarlib

APP = Path("/app")
BIN = APP / "bin" / "sardecode"
WORK = Path("/tmp/sar_work")


def _build():
    """Build the decoder from source using the task's own build script."""
    subprocess.run(
        ["bash", str(APP / "scripts" / "build.sh")],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixtures():
    return gen_fixtures.fixtures()


@pytest.fixture(scope="session")
def built():
    """Compile the decoder once for the whole session."""
    _build()
    assert BIN.exists(), "decoder binary was not produced by build.sh"
    WORK.mkdir(parents=True, exist_ok=True)
    return True


def _run_decoder(name, archive):
    arc_path = WORK / f"{name}.sar"
    out_path = WORK / f"{name}.out"
    arc_path.write_bytes(archive)
    if out_path.exists():
        out_path.unlink()
    proc = subprocess.run(
        [str(BIN), str(arc_path), str(out_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc, out_path


def _fixture_map():
    return {name: (counts, blocks) for name, counts, blocks in _fixtures()}


@pytest.mark.parametrize("name", [n for n, _, _ in gen_fixtures.fixtures()])
def test_reconstructs_exact_samples(built, name):
    """Each conforming archive decodes to exactly the independently computed
    per-channel sample stream and text layout."""
    counts, blocks = _fixture_map()[name]
    archive = sarlib.build_archive(counts, blocks)
    expected = sarlib.expected_output_text(archive, counts)

    proc, out_path = _run_decoder(name, archive)
    assert proc.returncode == 0, (
        f"decoder exited {proc.returncode} on {name}: {proc.stderr}"
    )
    assert out_path.exists(), f"decoder produced no output for {name}"
    actual = out_path.read_text()
    assert actual == expected, (
        f"reconstructed samples for {name} differ from the recorded stream"
    )


def test_happy_single_block(built):
    """A small single-block archive with unique code lengths decodes correctly
    (the baseline that already worked)."""
    counts, blocks = _fixture_map()["happy_single"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("happy_single_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_equal_length_symbols_tiebreak(built):
    """When several symbols share a code length, the canonical ascending-symbol
    tie-break must be honored so literals map to the recorded samples."""
    counts, blocks = _fixture_map()["equal_len"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("equal_len_chk", archive)
    assert proc.returncode == 0, proc.stderr
    got = out_path.read_text()
    expected = sarlib.expected_output_text(archive, counts)
    assert got == expected, "equal-length symbol table decoded to wrong samples"


def test_codes_spanning_byte_boundaries(built):
    """Codes whose bits continue into a following byte must be read in order, so
    a table whose codes routinely cross byte boundaries still decodes exactly."""
    counts, blocks = _fixture_map()["byte_span_pure"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("byte_span_pure_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_backreference_reaching_block_start(built):
    """A back-reference whose source reaches exactly the first sample of a freshly
    reset block window must copy from that sample."""
    counts, blocks = _fixture_map()["ref_start_pure"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("ref_start_pure_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_overlapping_backreference(built):
    """An overlapping back-reference extends a run by copying sample-by-sample
    from the region being written."""
    counts, blocks = _fixture_map()["ref_overlap"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("ref_overlap_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_multi_block_window_reset(built):
    """Across a block boundary the window resets, so block two's back-references
    resolve within block two rather than reading block one's tail."""
    counts, blocks = _fixture_map()["multi_block"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("multi_block_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_multi_channel_partition(built):
    """The decoded stream is partitioned into channels by declared count, each
    labeled with its channel index."""
    counts, blocks = _fixture_map()["multi_channel"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("multi_channel_chk", archive)
    assert proc.returncode == 0, proc.stderr
    got = out_path.read_text()
    expected = sarlib.expected_output_text(archive, counts)
    assert got == expected
    assert got.count("channel ") == len(counts), "wrong number of channel labels"


def test_truncated_final_block(built):
    """A final block that stops before its end-of-block symbol contributes the
    samples it fully produced, without error, and a channel short of its declared
    count receives fewer samples."""
    counts, blocks = _fixture_map()["truncated_final"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("truncated_final_chk", archive)
    assert proc.returncode == 0, f"truncated final block must not crash: {proc.stderr}"
    got = out_path.read_text()
    expected = sarlib.expected_output_text(archive, counts)
    assert got == expected
    produced = len(sarlib.reference_decode(archive))
    assert produced < sum(counts), "fixture should exercise truncation"


def test_large_multi_block_archive(built):
    """A larger deterministic multi-block archive combining every path decodes
    exactly to the independently computed stream."""
    counts, blocks = _fixture_map()["big_multi"]
    archive = sarlib.build_archive(counts, blocks)
    proc, out_path = _run_decoder("big_multi_chk", archive)
    assert proc.returncode == 0, proc.stderr
    assert out_path.read_text() == sarlib.expected_output_text(archive, counts)


def test_deterministic_across_runs(built):
    """Decoding the same archive twice yields byte-identical output."""
    counts, blocks = _fixture_map()["big_multi"]
    archive = sarlib.build_archive(counts, blocks)
    _, out1 = _run_decoder("det1", archive)
    first = out1.read_text()
    _, out2 = _run_decoder("det2", archive)
    second = out2.read_text()
    assert first == second, "decoder output is not deterministic"
    assert first == sarlib.expected_output_text(archive, counts)
