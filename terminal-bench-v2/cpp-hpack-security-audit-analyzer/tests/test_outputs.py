import json
import pathlib
import subprocess


ROOT = pathlib.Path("/opt/hpackoscope")
OUT = pathlib.Path("/var/lib/hpackoscope/output")
CAPTURES = ROOT / "fixtures" / "captures"
CONF = pathlib.Path("/etc/hpackoscope/runtime.conf")
BUILT = False
GENERATED_CAPTURE_NAMES = {
    "y-compound.h2c",
    "y-late-size-update.h2c",
    "y-scope-reset.h2c",
    "z-extra.h2c",
    "y-ordinals.h2c",
    "y-capacity.h2c",
    "y-fragments.h2c",
    "y-json-lossless.h2c",
    "y-interleaved-fragments.h2c",
}


def run_tool():
    global BUILT
    if not BUILT:
        subprocess.run([str(ROOT / "scripts" / "build.sh")], check=True)
        BUILT = True
    subprocess.run([str(ROOT / "bin" / "hpackoscope"), str(CONF)], check=True)


def cleanup_generated_captures():
    for name in GENERATED_CAPTURE_NAMES:
        (CAPTURES / name).unlink(missing_ok=True)


def read_jsonl(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line]


def by_key(rows):
    return {(r["capture"], r["stream"], r["ordinal"]): r["headers"] for r in rows}


# These helpers build HPACK wire-format bytes for generated captures.
def hp_int(value, prefix_bits, prefix):
    limit = (1 << prefix_bits) - 1
    if value < limit:
        return bytes([prefix | value])
    out = bytearray([prefix | limit])
    value -= limit
    while value >= 128:
        out.append((value % 128) | 0x80)
        value //= 128
    out.append(value)
    return bytes(out)


def hp_string(value, *, huffman_hex=None):
    data = bytes.fromhex(huffman_hex) if huffman_hex else value.encode()
    return hp_int(len(data), 7, 0x80 if huffman_hex else 0x00) + data


def indexed(index):
    return hp_int(index, 7, 0x80)


def table_size_update(size):
    return hp_int(size, 5, 0x20)


def literal_indexed_name(index, value, *, huffman_hex=None):
    return hp_int(index, 6, 0x40) + hp_string(value, huffman_hex=huffman_hex)


def literal_indexed_new_name(name, value):
    return bytes([0x40]) + hp_string(name) + hp_string(value)


def literal_never_indexed_new_name(name, value):
    return bytes([0x10]) + hp_string(name) + hp_string(value)


def literal_without_indexing_name(index, value):
    return hp_int(index, 4, 0x00) + hp_string(value)


def test_original_corpus_outputs_are_complete_and_stable():
    """The bundled captures produce the documented files and stable decoded records."""
    cleanup_generated_captures()
    run_tool()
    assert sorted(p.name for p in OUT.iterdir()) == [
        "headers.jsonl",
        "security_findings.json",
        "stream_diagnostics.jsonl",
    ]
    rows = read_jsonl(OUT / "headers.jsonl")
    records = by_key(rows)
    assert records[("a-primary.h2c", 1, 0)] == [
        {"name": ":method", "value": "GET"},
        {"name": ":scheme", "value": "http"},
        {"name": ":path", "value": "/"},
        {"name": ":authority", "value": "www.example.com"},
        {"name": "user-agent", "value": "forensic/1.0"},
    ]
    assert records[("a-primary.h2c", 3, 0)][3:] == [
        {"name": ":authority", "value": "www.example.com"},
        {"name": "cache-control", "value": "no-cache"},
    ]
    assert records[("a-primary.h2c", 7, 1)] == [
        {"name": "marker-e", "value": "E"},
        {"name": "marker-e", "value": "E"},
    ]
    assert records[("b-isolation.h2c", 11, 0)][3] == {
        "name": ":authority",
        "value": "example.test",
    }
    assert records[("b-isolation.h2c", 17, 0)][3] == {
        "name": ":authority",
        "value": "beta.example",
    }


def test_diagnostics_and_security_report_match_contract():
    """Diagnostics are isolated and security findings use the requested aggregate schema."""
    run_tool()
    diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
    diag_streams = {(d["capture"], d["stream"], d["level"], d["code"]) for d in diagnostics}
    assert ("b-isolation.h2c", 9, "error", "decode-error") in diag_streams
    assert ("b-isolation.h2c", 15, "error", "decode-error") in diag_streams
    assert all(d["stream"] not in {11, 17} for d in diagnostics)
    report = json.loads((OUT / "security_findings.json").read_text())
    assert report["summary"] == {
        "duplicate-pseudo-header": 1,
        "forbidden-header": 1,
        "uppercase-name": 2,
    }
    assert [f["type"] for f in report["findings"]] == [
        "uppercase-name",
        "uppercase-name",
        "forbidden-header",
        "duplicate-pseudo-header",
    ]
    assert all(set(f) == {"capture", "stream", "ordinal", "type", "name"} for f in report["findings"])
    assert report["findings"][0]["name"] == "x-trace-name"
    assert report["findings"][2]["name"] == "connection"
    assert report["findings"][3]["name"] == ":method"


def test_same_schema_capture_added_at_runtime_is_decoded():
    """A new same-format capture is processed without relying on fixed fixture names."""
    extra = CAPTURES / "z-extra.h2c"
    extra.write_text(
        "# seq stream type flags payload\n"
        "1 21 HEADERS NONE 828684410861646465642e696f\n"
        "2 21 CONTINUATION END_HEADERS 400961646465642d6b6579076f6e652d74776f\n"
        "3 23 HEADERS END_HEADERS 828684be400a65787472612d6e616d6505782d76616c\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("z-extra.h2c", 21, 0)] == [
            {"name": ":method", "value": "GET"},
            {"name": ":scheme", "value": "http"},
            {"name": ":path", "value": "/"},
            {"name": ":authority", "value": "added.io"},
            {"name": "added-key", "value": "one-two"},
        ]
        assert records[("z-extra.h2c", 23, 0)] == [
            {"name": ":method", "value": "GET"},
            {"name": ":scheme", "value": "http"},
            {"name": ":path", "value": "/"},
            {"name": "added-key", "value": "one-two"},
            {"name": "extra-name", "value": "x-val"},
        ]
    finally:
        extra.unlink(missing_ok=True)


def test_generated_compound_hpack_edges_are_decoded_without_state_leaks():
    """Generated captures combine long integers, Huffman strings, rollback, and file scope."""
    cleanup_generated_captures()
    long_value = "x" * 260
    first_block = b"".join(
        [
            table_size_update(500),
            literal_indexed_name(
                1,
                "www.example.com",
                huffman_hex="f1e3c2e5f23a6ba0ab90f4ff",
            ),
            literal_indexed_new_name("long-token", long_value),
        ]
    )
    reuse_block = indexed(2) + indexed(6) + indexed(4) + indexed(62) + indexed(63)
    rejected_block = literal_indexed_new_name("leaky", "bad") + indexed(0)
    compound = CAPTURES / "y-compound.h2c"
    scope_reset = CAPTURES / "y-scope-reset.h2c"
    compound.write_text(
        "# seq stream type flags payload\n"
        f"1 31 HEADERS END_HEADERS {first_block.hex()}\n"
        f"2 33 HEADERS NONE {reuse_block[:2].hex()}\n"
        f"3 33 CONTINUATION END_HEADERS {reuse_block[2:].hex()}\n"
        f"4 35 HEADERS END_HEADERS {rejected_block.hex()}\n"
        f"5 37 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    scope_reset.write_text(
        "# seq stream type flags payload\n"
        f"1 41 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-compound.h2c", 31, 0)] == [
            {"name": ":authority", "value": "www.example.com"},
            {"name": "long-token", "value": long_value},
        ]
        assert records[("y-compound.h2c", 33, 0)] == [
            {"name": ":method", "value": "GET"},
            {"name": ":scheme", "value": "http"},
            {"name": ":path", "value": "/"},
            {"name": "long-token", "value": long_value},
            {"name": ":authority", "value": "www.example.com"},
        ]
        assert records[("y-compound.h2c", 37, 0)] == [
            {"name": "long-token", "value": long_value},
        ]
        assert ("y-compound.h2c", 35, 0) not in records
        assert ("y-scope-reset.h2c", 41, 0) not in records
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        diag_keys = {(d["capture"], d["stream"], d["code"]) for d in diagnostics}
        assert ("y-compound.h2c", 35, "decode-error") in diag_keys
        assert ("y-scope-reset.h2c", 41, "decode-error") in diag_keys
    finally:
        cleanup_generated_captures()


def test_generated_late_table_update_and_non_indexed_literals_do_not_leak_state():
    """Generated captures verify table update ordering, non-indexed literals, rollback, and eviction."""
    cleanup_generated_captures()
    capture = CAPTURES / "y-late-size-update.h2c"
    seed = literal_indexed_new_name("alpha-token", "one")
    late_update = literal_indexed_new_name("should-not-stick", "bad") + table_size_update(64)
    never_indexed = literal_never_indexed_new_name("secret-token", "zero")
    shrink_and_literal = table_size_update(32) + literal_without_indexing_name(1, "after-shrink")
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 61 HEADERS END_HEADERS {seed.hex()}\n"
        f"2 63 HEADERS END_HEADERS {late_update.hex()}\n"
        f"3 65 HEADERS END_HEADERS {indexed(62).hex()}\n"
        f"4 67 HEADERS END_HEADERS {never_indexed.hex()}\n"
        f"5 69 HEADERS END_HEADERS {indexed(62).hex()}\n"
        f"6 71 HEADERS END_HEADERS {shrink_and_literal.hex()}\n"
        f"7 73 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-late-size-update.h2c", 61, 0)] == [{"name": "alpha-token", "value": "one"}]
        assert ("y-late-size-update.h2c", 63, 0) not in records
        assert records[("y-late-size-update.h2c", 65, 0)] == [{"name": "alpha-token", "value": "one"}]
        assert records[("y-late-size-update.h2c", 67, 0)] == [{"name": "secret-token", "value": "zero"}]
        assert records[("y-late-size-update.h2c", 69, 0)] == [{"name": "alpha-token", "value": "one"}]
        assert records[("y-late-size-update.h2c", 71, 0)] == [{"name": ":authority", "value": "after-shrink"}]
        assert ("y-late-size-update.h2c", 73, 0) not in records
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        diag_keys = {(d["capture"], d["stream"], d["code"]) for d in diagnostics}
        assert ("y-late-size-update.h2c", 63, "decode-error") in diag_keys
        assert ("y-late-size-update.h2c", 73, "decode-error") in diag_keys
    finally:
        cleanup_generated_captures()


def test_ordinal_is_zero_based_and_skips_rejected_blocks():
    """Ordinals must be zero-based and only increment on successful decode."""
    capture = CAPTURES / "y-ordinals.h2c"
    valid = indexed(2)  # :method: GET
    rejected = literal_indexed_new_name("broken", "x") + indexed(0)
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 55 HEADERS END_HEADERS {valid.hex()}\n"
        f"2 55 HEADERS END_HEADERS {rejected.hex()}\n"
        f"3 55 HEADERS END_HEADERS {valid.hex()}\n"
        f"4 57 HEADERS END_HEADERS {valid.hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-ordinals.h2c", 55, 0)] == [
            {"name": ":method", "value": "GET"},
        ]
        assert records[("y-ordinals.h2c", 55, 1)] == [
            {"name": ":method", "value": "GET"},
        ]
        assert ("y-ordinals.h2c", 55, 2) not in records
        assert records[("y-ordinals.h2c", 57, 0)] == [
            {"name": ":method", "value": "GET"},
        ]
    finally:
        capture.unlink(missing_ok=True)


def test_configured_capacity_eviction_and_rejected_updates_are_transactional():
    """Dynamic-table capacity changes evict deterministically and failed blocks roll back."""
    cleanup_generated_captures()
    capture = CAPTURES / "y-capacity.h2c"
    seed = b"".join(
        [
            table_size_update(96),
            literal_indexed_new_name("marker-a", "A"),
            literal_indexed_new_name("marker-b", "B"),
            literal_indexed_new_name("marker-c", "C"),
        ]
    )
    oversize_then_clear = table_size_update(32) + literal_indexed_new_name("too-large", "value")
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 81 HEADERS END_HEADERS {seed.hex()}\n"
        f"2 81 HEADERS END_HEADERS {(indexed(62) + indexed(63)).hex()}\n"
        f"3 81 HEADERS END_HEADERS {oversize_then_clear.hex()}\n"
        f"4 81 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-capacity.h2c", 81, 1)] == [
            {"name": "marker-c", "value": "C"},
            {"name": "marker-b", "value": "B"},
        ]
        assert records[("y-capacity.h2c", 81, 2)] == [{"name": "too-large", "value": "value"}]
        assert ("y-capacity.h2c", 81, 3) not in records
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        assert ("y-capacity.h2c", 81, "decode-error") in {
            (d["capture"], d["stream"], d["code"]) for d in diagnostics
        }
    finally:
        cleanup_generated_captures()


def test_fragment_failures_clear_only_the_affected_stream_and_recovery_continues():
    """Fragmentation failures are diagnosed per stream without leaking ordinals."""
    cleanup_generated_captures()
    capture = CAPTURES / "y-fragments.h2c"
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 91 HEADERS NONE {indexed(2).hex()}\n"
        f"2 95 CONTINUATION END_HEADERS {indexed(4).hex()}\n"
        f"3 91 HEADERS END_HEADERS {indexed(6).hex()}\n"
        f"4 93 HEADERS END_HEADERS {indexed(4).hex()}\n"
        f"5 91 HEADERS END_HEADERS {indexed(2).hex()}\n"
        f"6 97 HEADERS NONE {indexed(7).hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-fragments.h2c", 93, 0)] == [{"name": ":path", "value": "/"}]
        assert records[("y-fragments.h2c", 91, 0)] == [{"name": ":method", "value": "GET"}]
        assert ("y-fragments.h2c", 91, 1) not in records
        assert ("y-fragments.h2c", 97, 0) not in records
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        diag_keys = {(d["capture"], d["stream"], d["code"]) for d in diagnostics}
        assert ("y-fragments.h2c", 91, "decode-error") in diag_keys
        assert ("y-fragments.h2c", 95, "decode-error") in diag_keys
        assert ("y-fragments.h2c", 97, "decode-error") in diag_keys
    finally:
        cleanup_generated_captures()


def test_json_values_are_lossless_and_invalid_huffman_blocks_roll_back():
    """Decoded control characters use the requested JSON spelling and bad Huffman rolls back."""
    cleanup_generated_captures()
    capture = CAPTURES / "y-json-lossless.h2c"
    tab_value = "a\tb"
    bad_huffman_value = bytes([0x81, 0x00])
    rejected = literal_indexed_new_name("leaky", "bad") + bytes([0x40]) + hp_string("bad-huff") + bad_huffman_value
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 101 HEADERS END_HEADERS {literal_indexed_new_name('tab-token', tab_value).hex()}\n"
        f"2 101 HEADERS END_HEADERS {rejected.hex()}\n"
        f"3 101 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    try:
        run_tool()
        raw_headers = (OUT / "headers.jsonl").read_text()
        assert '"value":"a\\u0009b"' in raw_headers
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[("y-json-lossless.h2c", 101, 0)] == [{"name": "tab-token", "value": "a\tb"}]
        assert records[("y-json-lossless.h2c", 101, 1)] == [{"name": "tab-token", "value": "a\tb"}]
        assert all({"name": "leaky", "value": "bad"} not in headers for headers in records.values())
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        assert ("y-json-lossless.h2c", 101, "decode-error") in {
            (d["capture"], d["stream"], d["code"]) for d in diagnostics
        }
    finally:
        cleanup_generated_captures()


def test_interleaved_fragmented_table_update_survives_other_stream_failures():
    """A split dynamic-table update commits only after its block completes despite unrelated stream errors."""
    cleanup_generated_captures()
    capture = CAPTURES / "y-interleaved-fragments.h2c"
    split_block = table_size_update(128) + literal_indexed_new_name("split-key", "split-value")
    capture.write_text(
        "# seq stream type flags payload\n"
        f"1 121 HEADERS NONE {split_block[:1].hex()}\n"
        f"2 123 HEADERS END_HEADERS {indexed(4).hex()}\n"
        f"3 125 CONTINUATION END_HEADERS {indexed(2).hex()}\n"
        f"4 121 CONTINUATION END_HEADERS {split_block[1:].hex()}\n"
        f"5 121 HEADERS END_HEADERS {indexed(62).hex()}\n"
    )
    try:
        run_tool()
        records = by_key(read_jsonl(OUT / "headers.jsonl"))
        assert records[(capture.name, 123, 0)] == [{"name": ":path", "value": "/"}]
        assert records[(capture.name, 121, 0)] == [{"name": "split-key", "value": "split-value"}]
        assert records[(capture.name, 121, 1)] == [{"name": "split-key", "value": "split-value"}]
        diagnostics = read_jsonl(OUT / "stream_diagnostics.jsonl")
        assert {(d["stream"], d["code"]) for d in diagnostics if d["capture"] == capture.name} == {
            (125, "decode-error")
        }
    finally:
        cleanup_generated_captures()


def test_run_recreates_output_directory():
    """The tool removes stale output files before writing the reports."""
    cleanup_generated_captures()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = OUT / "stale.txt"
    stale.write_text("old")
    run_tool()
    assert not stale.exists()
    assert (OUT / "headers.jsonl").read_text().endswith("\n")
