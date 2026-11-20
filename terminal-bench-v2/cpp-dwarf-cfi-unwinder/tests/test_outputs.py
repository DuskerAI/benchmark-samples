"""Behavioral verifier for the CFI unwinder task."""

from __future__ import annotations

import json
import pathlib
import struct
import subprocess

APP = pathlib.Path("/app/task_file")
BIN = APP / "build" / "cfi_unwind"


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


def _sleb(value: int) -> bytes:
    out = bytearray()
    more = True
    while more:
        byte = value & 0x7F
        value >>= 7
        sign = byte & 0x40
        more = not ((value == 0 and not sign) or (value == -1 and sign))
        if more:
            byte |= 0x80
        out.append(byte)
    return bytes(out)


def _cie(ident: int, code_align: int, data_align: int, ret: int, ins: bytes) -> bytes:
    return (
        struct.pack("<I", ident)
        + _uleb(code_align)
        + _sleb(data_align)
        + _uleb(ret)
        + _uleb(3)
        + b"zR\0"
        + struct.pack("<H", len(ins))
        + ins
    )


def _encoded_u32_pcrel(current_len: int, target: int) -> bytes:
    field_start = current_len + 4 + 1
    return struct.pack("<i", target - field_start)


def _fde(cie_id: int, enc: int, initial: bytes, address_range: bytes, ins: bytes) -> bytes:
    return struct.pack("<I", cie_id) + bytes([enc]) + initial + address_range + struct.pack("<H", len(ins)) + ins


def _write_table(path: pathlib.Path, cies: list[bytes], fdes: list[bytes]) -> None:
    path.write_bytes(b"TCFI" + bytes([1]) + struct.pack("<HH", len(cies), len(fdes)) + b"".join(cies) + b"".join(fdes))


def _run(record: pathlib.Path, output: pathlib.Path) -> dict:
    subprocess.run(["make", "-C", str(APP), "clean", "all"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run([str(BIN), str(record), str(output)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return json.loads(output.read_text(encoding="utf-8"))


def test_smoke_contract_still_passes(tmp_path: pathlib.Path) -> None:
    """Verify the checked-in smoke path remains compatible with the documented command."""
    subprocess.run(["make", "-C", str(APP), "test"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    smoke = json.loads((APP / "build" / "smoke.json").read_text(encoding="utf-8"))
    assert smoke["threads"][0]["frames"][0]["status"] == "root"
    assert smoke["summary"] == {"thread_count": 1, "complete_threads": 1, "frame_count": 1}
    assert pathlib.Path(tmp_path).exists()


def test_nested_frames_from_generated_record(tmp_path: pathlib.Path) -> None:
    """Verify a generated crash record unwinds multiple frames and preserves the report schema."""
    table = tmp_path / "a.tcfi"
    cfa = bytes([0x0C]) + _uleb(7) + _uleb(16) + bytes([0x90]) + _uleb(1)
    cies = [_cie(1, 1, -8, 16, cfa)]
    prefix_len = 4 + 1 + 2 + 2 + len(cies[0])
    first_initial = _encoded_u32_pcrel(prefix_len, 0x401000)
    fde1 = _fde(1, 0x13, first_initial, struct.pack("<I", 0x40), b"")
    second_initial = struct.pack("<i", 0x400F00 - 0x400000)
    fde2 = _fde(1, 0x33, second_initial, struct.pack("<I", 0x80), b"")
    _write_table(table, cies, [fde1, fde2])
    record = tmp_path / "a.crash"
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x400000\n"
        "THREAD worker\n"
        "START 0x401020\n"
        "REG 7 0x8000\n"
        "MEM 0x8008 0x400f20\n"
        "MEM 0x8018 0x0\n"
        "END\n",
        encoding="utf-8",
    )
    data = _run(record, tmp_path / "a.json")
    frames = data["threads"][0]["frames"]
    assert [frame["index"] for frame in frames] == list(range(len(frames)))
    assert [frame["status"] for frame in frames] == ["ok", "root"]
    assert frames[0]["return_address"] == hex(0x400F20)
    assert frames[1]["pc"] == hex(0x400F20)
    assert frames[1]["cfa"] == hex(0x8020)
    assert data["summary"]["complete_threads"] == 1


def test_row_state_and_boundaries(tmp_path: pathlib.Path) -> None:
    """Verify documented row-state operations and half-open FDE ranges on generated data."""
    table = tmp_path / "b.tcfi"
    base_cfa = bytes([0x0C]) + _uleb(7) + _uleb(8) + bytes([0x90]) + _uleb(1)
    cies = [_cie(2, 1, -8, 16, base_cfa)]
    stream = (
        bytes([0x50, 0x13])
        + _sleb(-3)
        + bytes([0x0A, 0x11])
        + _uleb(16)
        + _sleb(-2)
        + bytes([0x50, 0x0B])
    )
    fde = _fde(2, 0x00, struct.pack("<Q", 0x5000), struct.pack("<Q", 0x30), stream)
    _write_table(table, cies, [fde])
    record = tmp_path / "b.crash"
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x0\n"
        "THREAD inner\n"
        "START 0x5018\n"
        "REG 7 0x9000\n"
        "MEM 0x9028 0x0\n"
        "END\n"
        "THREAD edge\n"
        "START 0x5030\n"
        "REG 7 0xa000\n"
        "END\n",
        encoding="utf-8",
    )
    data = _run(record, tmp_path / "b.json")
    assert [thread["id"] for thread in data["threads"]] == ["inner", "edge"]
    assert data["threads"][0]["frames"] == [
        {"index": 0, "pc": hex(0x5018), "cfa": hex(0x9018), "return_address": hex(0), "status": "root"}
    ]
    assert data["threads"][1]["frames"] == [
        {"index": 0, "pc": hex(0x5030), "cfa": None, "return_address": None, "status": "no_fde"}
    ]
    assert data["summary"] == {"thread_count": 2, "complete_threads": 1, "frame_count": 2}


def test_register_value_rules_and_stable_json(tmp_path: pathlib.Path) -> None:
    """Verify non-memory return rules and stable JSON output across repeated runs."""
    table = tmp_path / "c.tcfi"
    initial = bytes([0x0C]) + _uleb(130) + _uleb(300) + bytes([0x14]) + _uleb(16) + _uleb(2)
    cies = [_cie(9, 2, -8, 16, initial)]
    fde = _fde(9, 0x00, struct.pack("<Q", 0x7000), struct.pack("<Q", 0x20), b"")
    _write_table(table, cies, [fde])
    record = tmp_path / "c.crash"
    base_reg = 0xA000
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x0\n"
        "THREAD first\n"
        "START 0x7004\n"
        f"REG 130 {hex(base_reg)}\n"
        "END\n",
        encoding="utf-8",
    )
    one = _run(record, tmp_path / "c1.json")
    two = _run(record, tmp_path / "c2.json")
    assert json.dumps(one, sort_keys=False) == json.dumps(two, sort_keys=False)
    frames = one["threads"][0]["frames"]
    expected_cfa = base_reg + 300
    assert frames[0]["cfa"] == hex(expected_cfa)
    assert frames[0]["return_address"] == hex(expected_cfa - 16)
    assert frames[1]["status"] == "no_fde"


def test_overlapping_fdes_propagate_recovered_registers(tmp_path: pathlib.Path) -> None:
    """Verify FDE selection, factored CFA rules, and cross-frame register recovery."""
    table = tmp_path / "d.tcfi"
    first_cie = (
        bytes([0x0C])
        + _uleb(7)
        + _uleb(16)
        + bytes([0x90])
        + _uleb(1)
        + bytes([0x89])
        + _uleb(2)
    )
    second_cie = bytes([0x12]) + _uleb(9) + _sleb(-4) + bytes([0x90]) + _uleb(1)
    cies = [_cie(10, 1, -8, 16, first_cie), _cie(11, 1, -8, 16, second_cie)]
    broad = _fde(10, 0x00, struct.pack("<Q", 0x1100), struct.pack("<Q", 0x80), b"")
    narrow = _fde(11, 0x00, struct.pack("<Q", 0x1120), struct.pack("<Q", 0x20), b"")
    _write_table(table, cies, [broad, narrow])
    record = tmp_path / "d.crash"
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x0\n"
        "THREAD overlap\n"
        "START 0x1108\n"
        "REG 7 0x9000\n"
        "MEM 0x9000 0xa000\n"
        "MEM 0x9008 0x1128\n"
        "MEM 0xa018 0x0\n"
        "END\n",
        encoding="utf-8",
    )
    data = _run(record, tmp_path / "d.json")
    frames = data["threads"][0]["frames"]
    assert [frame["status"] for frame in frames] == ["ok", "root"]
    assert frames[0]["return_address"] == hex(0x1128)
    assert frames[1]["pc"] == hex(0x1128)
    assert frames[1]["cfa"] == hex(0xA020)
    assert data["summary"] == {"thread_count": 1, "complete_threads": 1, "frame_count": 2}


def test_cycle_stop_status_is_reported(tmp_path: pathlib.Path) -> None:
    """Verify documented cycle termination emits a deterministic final frame."""
    table = tmp_path / "e.tcfi"
    cfa = bytes([0x0C]) + _uleb(7) + _uleb(16) + bytes([0x90]) + _uleb(1)
    cies = [_cie(12, 1, -8, 16, cfa)]
    fde = _fde(12, 0x00, struct.pack("<Q", 0x6200), struct.pack("<Q", 0x80), b"")
    _write_table(table, cies, [fde])
    record = tmp_path / "e.crash"
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x0\n"
        "THREAD loop\n"
        "START 0x6210\n"
        "REG 7 0xa000\n"
        "MEM 0xa008 0x6230\n"
        "MEM 0xa018 0x6230\n"
        "END\n",
        encoding="utf-8",
    )
    data = _run(record, tmp_path / "e.json")
    frames = data["threads"][0]["frames"]
    assert [frame["index"] for frame in frames] == [0, 1, 2]
    assert [frame["status"] for frame in frames] == ["ok", "ok", "cycle"]
    assert frames[2] == {"index": 2, "pc": hex(0x6230), "cfa": None, "return_address": None, "status": "cycle"}
    assert data["summary"] == {"thread_count": 1, "complete_threads": 0, "frame_count": 3}


def test_self_cycle_with_signed_encoding_and_sf_rules(tmp_path: pathlib.Path) -> None:
    """Verify a self-referential recovered PC is a cycle across signed encodings and factored rules."""
    table = tmp_path / "f.tcfi"
    cfa = bytes([0x0C]) + _uleb(4) + _uleb(32) + bytes([0x11]) + _uleb(31) + _sleb(-1)
    cies = [_cie(21, 1, -16, 31, cfa)]
    fde = _fde(21, 0x0B, struct.pack("<i", 0x7200), struct.pack("<i", 0x100), b"")
    _write_table(table, cies, [fde])
    record = tmp_path / "f.crash"
    record.write_text(
        f"CFI {table}\n"
        "BASE 0x0\n"
        "THREAD self\n"
        "START 0x7204\n"
        "REG 4 0x9000\n"
        "MEM 0x9030 0x7204\n"
        "END\n",
        encoding="utf-8",
    )
    data = _run(record, tmp_path / "f.json")
    frames = data["threads"][0]["frames"]
    assert frames == [
        {"index": 0, "pc": hex(0x7204), "cfa": hex(0x9020), "return_address": hex(0x7204), "status": "ok"},
        {"index": 1, "pc": hex(0x7204), "cfa": None, "return_address": None, "status": "cycle"},
    ]
    assert data["summary"] == {"thread_count": 1, "complete_threads": 0, "frame_count": 2}
