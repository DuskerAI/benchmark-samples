"""Verifier for the ELF note summarizer task."""

import json
import struct
import subprocess
from pathlib import Path

ROOT = Path("/app/task_file")
BIN = ROOT / "build" / "note_summary"
NT_FILE = 0x46494C45
NT_SIGINFO = 0x53494749
PN_XNUM = 0xFFFF
U64_MAX = (1 << 64) - 1


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _i32(value: int) -> bytes:
    return struct.pack("<i", value)


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _pad4(blob: bytes) -> bytes:
    return blob + (b"\x00" * ((4 - (len(blob) % 4)) % 4))


def _note(name: str, note_type: int, desc: bytes, *, truncate: int = 0) -> bytes:
    name_b = name.encode() + b"\x00"
    note = _u32(len(name_b)) + _u32(len(desc)) + _u32(note_type)
    note += _pad4(name_b) + _pad4(desc)
    if truncate:
        return note[:-truncate]
    return note


def _note_bytes(name: bytes, note_type: int, desc: bytes) -> bytes:
    note = _u32(len(name)) + _u32(len(desc)) + _u32(note_type)
    return note + _pad4(name) + _pad4(desc)


def _prstatus(pid: int, signal: int, comm: str, *, extra: bytes = b"") -> bytes:
    comm_b = comm.encode()
    assert len(comm_b) <= 15
    return _u32(pid) + _u32(signal) + comm_b + (b"\x00" * (16 - len(comm_b))) + extra


def _siginfo(signo: int, errno: int, code: int, *, extra: bytes = b"") -> bytes:
    return _i32(signo) + _i32(errno) + _i32(code) + extra


def _process(pid: int, ppid: int, name: str, state: str, *, extra: bytes = b"") -> bytes:
    name_b = name.encode()
    state_b = state.encode()
    assert len(name_b) <= 16
    assert len(state_b) <= 16
    return _u32(pid) + _u32(ppid) + name_b.ljust(16, b"\x00") + state_b.ljust(16, b"\x00") + extra


def _auxv(pairs: list[tuple[int, int]], *, terminated: bool = True) -> bytes:
    blob = b"".join(_u64(t) + _u64(v) for t, v in pairs)
    if terminated:
        blob += _u64(0) + _u64(0)
    return blob


def _nt_file(entries: list[tuple[int, int, int, str]], page_size: int = 0x1000) -> bytes:
    table = _u64(len(entries)) + _u64(page_size)
    for start, end, file_pages, _path in entries:
        table += _u64(start) + _u64(end) + _u64(file_pages)
    names = b"".join(path.encode() + b"\x00" for *_triple, path in entries)
    return table + names


def _phdr(p_type: int, offset: int, size: int) -> bytes:
    return (
        _u32(p_type)
        + _u32(0)
        + _u64(offset)
        + _u64(0)
        + _u64(0)
        + _u64(size)
        + _u64(size)
        + _u64(4)
    )


def _shdr0(program_count: int) -> bytes:
    return (
        _u32(0)
        + _u32(0)
        + _u64(0)
        + _u64(0)
        + _u64(0)
        + _u64(0)
        + _u32(0)
        + _u32(program_count)
        + _u64(0)
        + _u64(0)
    )


def _elf_with_entries(path: Path, entries: list[dict]) -> Path:
    ehdr_size = 64
    phdr_size = 56
    phoff = ehdr_size
    data_off = ehdr_size + phdr_size * len(entries)
    payloads = [entry.get("data", b"") for entry in entries]

    cursor = data_off
    actual_offsets = []
    for payload in payloads:
        actual_offsets.append(cursor)
        cursor += len(payload)

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    ehdr = (
        ident
        + _u16(4)
        + _u16(62)
        + _u32(1)
        + _u64(0)
        + _u64(phoff)
        + _u64(0)
        + _u32(0)
        + _u16(ehdr_size)
        + _u16(phdr_size)
        + _u16(len(entries))
        + _u16(0)
        + _u16(0)
        + _u16(0)
    )
    phdrs = []
    for entry, actual_offset, payload in zip(entries, actual_offsets, payloads):
        p_type = entry.get("type", 4)
        offset = entry.get("offset", actual_offset)
        size = entry.get("size", len(payload))
        phdrs.append(_phdr(p_type, offset, size))

    path.write_bytes(ehdr + b"".join(phdrs) + b"".join(payloads))
    return path


def _elf_with_notes(path: Path, segments: list[bytes], p_types: list[int] | None = None) -> Path:
    if p_types is None:
        p_types = [4] * len(segments)
    assert len(p_types) == len(segments)
    return _elf_with_entries(path, [{"type": p_type, "data": seg} for p_type, seg in zip(p_types, segments)])


def _elf_with_extended_phnum_and_decoy(path: Path, note_segment: bytes, decoy_segment: bytes) -> Path:
    ehdr_size = 64
    phdr_size = 56
    phoff = ehdr_size
    data_off = ehdr_size + (2 * phdr_size)
    fake_phdr_off = data_off
    note_off = fake_phdr_off + phdr_size
    decoy_off = note_off + len(note_segment)
    shoff = decoy_off + len(decoy_segment)
    fake_phdr = _phdr(4, decoy_off, len(decoy_segment))

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    ehdr = (
        ident
        + _u16(4)
        + _u16(62)
        + _u32(1)
        + _u64(0)
        + _u64(phoff)
        + _u64(shoff)
        + _u32(0)
        + _u16(ehdr_size)
        + _u16(phdr_size)
        + _u16(PN_XNUM)
        + _u16(64)
        + _u16(1)
        + _u16(0)
    )
    phdrs = _phdr(1, fake_phdr_off, len(fake_phdr)) + _phdr(4, note_off, len(note_segment))
    path.write_bytes(ehdr + phdrs + fake_phdr + note_segment + decoy_segment + _shdr0(2))
    return path


def _build() -> None:
    result = subprocess.run(["make", "clean", "all"], cwd=ROOT, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert BIN.exists()


def _run(input_path: Path, output_path: Path) -> dict:
    result = subprocess.run([str(BIN), str(input_path), str(output_path)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output_path.read_text())


def _assert_empty_schema_lists(data: dict, *names: str) -> None:
    for name in names:
        assert data[name] == []


def test_project_unit_tests_still_pass() -> None:
    """The shipped C++ unit test target should still pass after the parser change."""
    result = subprocess.run(["make", "clean", "test"], cwd=ROOT, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_multiple_pt_note_segments_and_aligned_records(tmp_path: Path) -> None:
    """The summarizer should parse every PT_NOTE segment and honor ELF note padding."""
    _build()
    first = b"".join(
        [
            _note("GNU", 3, bytes.fromhex("00112233445566778899aabbccddeeff10203040")),
            _note("LINUX", 0x303, b"abcde"),
        ]
    )
    second = b"".join(
        [
            _note("CORE", 1, _prstatus(4321, 11, "worker-A")),
            _note("CORE", 6, _auxv([(3, 0x400040), (9, 0x401000), (25, 0x7FFF0000)])),
        ]
    )
    input_path = _elf_with_notes(tmp_path / "core.sample", [first, second])

    data = _run(input_path, tmp_path / "summary.json")

    assert data["input"] == "core.sample"
    assert data["note_count"] == 4
    assert data["gnu_build_ids"] == ["00112233445566778899aabbccddeeff10203040"]
    assert data["threads"] == [{"pid": 4321, "signal": 11, "comm": "worker-A"}]
    assert data["auxv"] == [
        {"type": "AT_PHDR", "value": "0x400040"},
        {"type": "AT_ENTRY", "value": "0x401000"},
        {"type": "AT_RANDOM", "value": "0x7fff0000"},
    ]
    assert data["mapped_files"] == []
    assert data["unknown_notes"] == [{"name": "LINUX", "type": 771, "desc_size": 5}]


def test_malformed_tail_only_stops_its_own_segment(tmp_path: Path) -> None:
    """A torn note should not prevent later PT_NOTE segments from being summarized."""
    _build()
    torn = _note("GNU", 3, b"\xaa\xbb\xcc\xdd") + _note("CORE", 1, _prstatus(999, 5, "bad"), truncate=9)
    good = _note("CORE", 1, _prstatus(1001, 7, "resumed")) + _note("CORE", 6, _auxv([(7, 0x700000)]))
    input_path = _elf_with_notes(tmp_path / "core.torn", [torn, good])

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 3
    assert data["gnu_build_ids"] == ["aabbccdd"]
    assert data["threads"] == [{"pid": 1001, "signal": 7, "comm": "resumed"}]
    assert data["auxv"] == [{"type": "AT_BASE", "value": "0x700000"}]
    _assert_empty_schema_lists(data, "mapped_files", "unknown_notes")


def test_unterminated_auxv_is_unknown_and_non_note_headers_are_ignored(tmp_path: Path) -> None:
    """Auxv descriptors need a terminator, and only PT_NOTE program headers are summarized."""
    _build()
    decoy = _note("GNU", 3, b"\xff\xee")
    unterminated = _note("CORE", 6, _auxv([(3, 0x11111111)], terminated=False))
    valid = _note("GNU", 3, b"\x12\x34\x56\x78") + _note("LINUX", 0x404, b"xyz")
    input_path = _elf_with_notes(tmp_path / "core.auxv", [decoy, unterminated, valid], p_types=[1, 4, 4])

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 3
    assert data["gnu_build_ids"] == ["12345678"]
    assert data["threads"] == []
    assert data["auxv"] == []
    assert data["mapped_files"] == []
    assert data["unknown_notes"] == [
        {"name": "CORE", "type": 6, "desc_size": 16},
        {"name": "LINUX", "type": 1028, "desc_size": 3},
    ]


def test_out_of_bounds_ranges_exact_prstatus_and_json_escaping(tmp_path: Path) -> None:
    """Bad segment ranges are skipped while malformed known descriptors become unknown notes."""
    _build()
    oversized_prstatus = _note("CORE", 1, _prstatus(77, 12, "too-long", extra=b"xxxx"))
    odd_unknown = _note_bytes(b'ODD"\\\x00', 0x5150, b"payload")
    valid = _note("GNU", 3, b"\x99\x88") + oversized_prstatus + odd_unknown
    input_path = _elf_with_entries(
        tmp_path / "core.ranges",
        [
            {"type": 4, "data": b"", "offset": 0xFFFF_FFFF_FFFF_FFF0, "size": 0x80},
            {"type": 4, "data": b"", "offset": 0x200000, "size": 0x20},
            {"type": 1, "data": _note("GNU", 3, b"\x00")},
            {"type": 4, "data": valid},
        ],
    )

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 3
    assert data["gnu_build_ids"] == ["9988"]
    _assert_empty_schema_lists(data, "threads", "auxv", "mapped_files")
    assert data["unknown_notes"] == [
        {"name": "CORE", "type": 1, "desc_size": 28},
        {"name": 'ODD"\\', "type": 20816, "desc_size": 7},
    ]


def test_owner_field_prefix_empty_names_and_strict_auxv_end(tmp_path: Path) -> None:
    """Owner fields are nul-prefix strings, and auxv terminators must end the descriptor."""
    _build()
    empty_owner = _note_bytes(b"", 0x7777, b"empty")
    padded_gnu = _note_bytes(b"GNU\x00kept-out", 3, b"\x01\x23\x45")
    padded_core = _note_bytes(b"CORE\x00shadow", 1, _prstatus(2026, 31, "nul-owner"))
    aux_with_tail = _note("CORE", 6, _auxv([(3, 0x44)]) + _u64(9) + _u64(0x99))
    input_path = _elf_with_notes(tmp_path / "core.names", [empty_owner + padded_gnu + padded_core + aux_with_tail])

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 4
    assert data["gnu_build_ids"] == ["012345"]
    assert data["threads"] == [{"pid": 2026, "signal": 31, "comm": "nul-owner"}]
    assert data["auxv"] == []
    assert data["mapped_files"] == []
    assert data["unknown_notes"] == [
        {"name": "", "type": 0x7777, "desc_size": 5},
        {"name": "CORE", "type": 6, "desc_size": 48},
    ]


def test_extended_phnum_and_core_process_notes(tmp_path: Path) -> None:
    """Extended program-header counts should not let payload bytes become phantom headers."""
    _build()
    notes = b"".join(
        [
            _note("CORE", NT_SIGINFO, _siginfo(11, 0, -6)),
            _note("CORE", 3, _process(7331, 1, "svc\x00shadow", "idle\x00x")),
            _note("GNU", 3, b"\x01\x02\x03\x04"),
        ]
    )
    decoy = _note("GNU", 3, b"\xff\xff") + _note("CORE", 1, _prstatus(9999, 9, "phantom"))
    input_path = _elf_with_extended_phnum_and_decoy(tmp_path / "core.extended", notes, decoy)

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 3
    assert data["gnu_build_ids"] == ["01020304"]
    assert data["signals"] == [{"signo": 11, "errno": 0, "code": -6}]
    assert data["processes"] == [{"pid": 7331, "ppid": 1, "name": "svc", "state": "idle"}]
    _assert_empty_schema_lists(data, "threads", "auxv", "mapped_files", "unknown_notes")


def test_malformed_new_core_descriptors_and_json_strings(tmp_path: Path) -> None:
    """New CORE descriptor classes should be all-or-unknown and still escape JSON strings."""
    _build()
    bad_siginfo = _siginfo(4, -1, 2, extra=b"x")
    bad_process = _process(44, 7, "bad", "state", extra=b"tail")
    good_process = _process(45, 7, 'quo"te', "line\nfeed")
    mapped = _nt_file([(0x20, 0x30, 1, '/tmp/a"b\\c')], page_size=0x100)
    input_path = _elf_with_notes(
        tmp_path / "core.newtypes",
        [
            _note("CORE", NT_SIGINFO, bad_siginfo)
            + _note("CORE", 3, bad_process)
            + _note("CORE", 3, good_process)
            + _note("CORE", NT_FILE, mapped)
        ],
    )

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 4
    assert data["processes"] == [{"pid": 45, "ppid": 7, "name": 'quo"te', "state": "line\nfeed"}]
    assert data["mapped_files"] == [
        {"start": "0x20", "end": "0x30", "offset": "0x100", "path": '/tmp/a"b\\c'}
    ]
    _assert_empty_schema_lists(data, "gnu_build_ids", "threads", "signals", "auxv")
    assert data["unknown_notes"] == [
        {"name": "CORE", "type": NT_SIGINFO, "desc_size": len(bad_siginfo)},
        {"name": "CORE", "type": 3, "desc_size": len(bad_process)},
    ]


def test_core_file_note_records_and_numeric_auxv_types(tmp_path: Path) -> None:
    """Mapped-file notes use their table, page size, and path string area together."""
    _build()
    mapped = _note(
        "CORE",
        NT_FILE,
        _nt_file(
            [
                (0x400000, 0x401000, 0, "/usr/bin/app"),
                (0x7F0000001000, 0x7F0000003000, 0x123, "/lib/libm.so"),
            ]
        ),
    )
    aux = _note("CORE", 6, _auxv([(17, 0x55), (25, 0xABCDEF)]))
    input_path = _elf_with_notes(tmp_path / "core.files", [mapped + aux])

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 2
    assert data["mapped_files"] == [
        {"start": "0x400000", "end": "0x401000", "offset": "0x0", "path": "/usr/bin/app"},
        {"start": "0x7f0000001000", "end": "0x7f0000003000", "offset": "0x123000", "path": "/lib/libm.so"},
    ]
    assert data["auxv"] == [
        {"type": "17", "value": "0x55"},
        {"type": "AT_RANDOM", "value": "0xabcdef"},
    ]
    _assert_empty_schema_lists(data, "gnu_build_ids", "threads", "unknown_notes")


def test_strict_mapped_file_descriptor_boundaries(tmp_path: Path) -> None:
    """Mapped-file descriptors should reject bad tables, ranges, and trailing bytes."""
    _build()
    valid = _nt_file([(0x1000, 0x2000, 3, "/ok/one")], page_size=0x2000)
    zero_page = _nt_file([(0x10, 0x20, 1, "/bad/page")], page_size=0)
    reversed_range = _nt_file([(0x3000, 0x2000, 0, "/bad/range")])
    trailing_bytes = _nt_file([(0x40, 0x50, 0, "/bad/trailing")]) + b"extra"
    input_path = _elf_with_notes(
        tmp_path / "core.strictfiles",
        [
            _note("CORE", NT_FILE, valid)
            + _note("CORE", NT_FILE, zero_page)
            + _note("CORE", NT_FILE, reversed_range)
            + _note("CORE", NT_FILE, trailing_bytes)
        ],
    )

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 4
    assert data["mapped_files"] == [
        {"start": "0x1000", "end": "0x2000", "offset": "0x6000", "path": "/ok/one"}
    ]
    _assert_empty_schema_lists(data, "gnu_build_ids", "threads", "auxv")
    assert data["unknown_notes"] == [
        {"name": "CORE", "type": NT_FILE, "desc_size": len(zero_page)},
        {"name": "CORE", "type": NT_FILE, "desc_size": len(reversed_range)},
        {"name": "CORE", "type": NT_FILE, "desc_size": len(trailing_bytes)},
    ]


def test_malformed_file_notes_are_reported_as_unknown(tmp_path: Path) -> None:
    """Mapped-file descriptors with invalid path or offset data should not be partially emitted."""
    _build()
    missing_path_nul = _u64(1) + _u64(0x1000) + _u64(0x1) + _u64(0x2) + _u64(0x3) + b"/tmp/no-terminator"
    overflowing_offset = _nt_file([(0x10, 0x20, U64_MAX, "/overflow")], page_size=2)
    input_path = _elf_with_notes(
        tmp_path / "core.badfiles",
        [_note("CORE", NT_FILE, missing_path_nul) + _note("CORE", NT_FILE, overflowing_offset)],
    )

    data = _run(input_path, tmp_path / "summary.json")

    assert data["note_count"] == 2
    _assert_empty_schema_lists(data, "gnu_build_ids", "threads", "auxv", "mapped_files")
    assert data["unknown_notes"] == [
        {"name": "CORE", "type": NT_FILE, "desc_size": len(missing_path_nul)},
        {"name": "CORE", "type": NT_FILE, "desc_size": len(overflowing_offset)},
    ]
