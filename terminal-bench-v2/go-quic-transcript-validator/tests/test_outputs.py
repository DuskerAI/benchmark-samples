"""Behavioral verifier for the qtrace validator CLI."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

APP = Path("/app/quicval")
BIN = APP / "bin" / "quicval"
PUBLIC_TRACE = APP / "fixtures" / "customer-alpha.qtr"
PUBLIC_OUT = APP / "out" / "transcript-report.json"
WORK = Path("/tmp/qtrace-verifier")


@dataclass(frozen=True)
class Param:
    """A transport parameter entry used by verifier-created traces."""

    ident: int
    value: bytes


def encode_varint(value: int) -> bytes:
    """Encode an integer using the trace contract's varint format."""
    if value < 64:
        return bytes([value])
    if value < 16384:
        return bytes([0x40 | (value >> 8), value & 0xFF])
    if value < 1073741824:
        return bytes(
            [0x80 | (value >> 24), (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
        )
    return bytes(
        [
            0xC0 | (value >> 56),
            (value >> 48) & 0xFF,
            (value >> 40) & 0xFF,
            (value >> 32) & 0xFF,
            (value >> 24) & 0xFF,
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
    )


def decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode one varint and return the value plus the next offset."""
    first = data[offset]
    size = 1 << (first >> 6)
    raw = bytearray(data[offset : offset + size])
    raw[0] &= 0x3F
    value = int.from_bytes(raw, "big")
    return value, offset + size


def payload(endpoint: int, params: list[Param]) -> bytes:
    """Create a plaintext payload for a generated Initial packet."""
    out = bytearray([endpoint])
    out += encode_varint(len(params))
    for param in params:
        out += encode_varint(param.ident)
        out += encode_varint(len(param.value))
        out += param.value
    return bytes(out)


def initial(dcid: bytes, scid: bytes, token: bytes, packet_number: int, pn_len: int, body: bytes) -> bytes:
    """Create an Initial packet for generated verifier traces."""
    first = 0xC0 | (pn_len - 1)
    pn = packet_number.to_bytes(pn_len, "big")
    out = bytearray([first])
    out += (1).to_bytes(4, "big")
    out += bytes([len(dcid)]) + dcid
    out += bytes([len(scid)]) + scid
    out += encode_varint(len(token)) + token
    out += encode_varint(len(pn) + len(body)) + pn + body
    return bytes(out)


def retry(odcid: bytes, dcid: bytes, scid: bytes, token: bytes, *, corrupt: bool = False) -> bytes:
    """Create a Retry packet for generated verifier traces."""
    out = bytearray([0xF0])
    out += (1).to_bytes(4, "big")
    out += bytes([len(dcid)]) + dcid
    out += bytes([len(scid)]) + scid
    out += encode_varint(len(token)) + token
    tag = hashlib.sha256(b"retry-v1" + bytes([len(odcid)]) + odcid + bytes(out)).digest()[:16]
    if corrupt:
        tag = bytes([tag[0] ^ 0x80]) + tag[1:]
    return bytes(out) + tag


def bundle(packets: list[bytes]) -> bytes:
    """Create a trace bundle from generated packet records."""
    out = bytearray(b"QTV1")
    out += encode_varint(len(packets))
    for packet in packets:
        out += encode_varint(len(packet))
        out += packet
    return bytes(out)


def read_bundle(data: bytes) -> list[bytes]:
    """Read packet records from a trace bundle."""
    assert data[:4] == b"QTV1"
    count, offset = decode_varint(data, 4)
    packets = []
    for _ in range(count):
        length, offset = decode_varint(data, offset)
        packets.append(data[offset : offset + length])
        offset += length
    return packets


def parse_packet(index: int, raw: bytes) -> dict[str, object]:
    """Parse a packet record according to the local trace contract."""
    packet_type = (raw[0] >> 4) & 0x03
    pn_len = (raw[0] & 0x03) + 1
    offset = 5
    dcid_len = raw[offset]
    offset += 1
    dcid = raw[offset : offset + dcid_len]
    offset += dcid_len
    scid_len = raw[offset]
    offset += 1
    scid = raw[offset : offset + scid_len]
    offset += scid_len
    token_len, offset = decode_varint(raw, offset)
    token = raw[offset : offset + token_len]
    offset += token_len
    if packet_type == 3:
        return {
            "index": index,
            "type": "retry",
            "dcid": dcid,
            "scid": scid,
            "token": token,
            "raw": raw,
            "tag": raw[-16:],
        }
    length, offset = decode_varint(raw, offset)
    truncated = int.from_bytes(raw[offset : offset + pn_len], "big")
    offset += pn_len
    body = raw[offset : offset + length - pn_len]
    return {
        "index": index,
        "type": "initial",
        "dcid": dcid,
        "scid": scid,
        "token": token,
        "pn_len": pn_len,
        "truncated": truncated,
        "payload": body,
    }


def reconstruct(truncated: int, pn_len: int, largest: int | None) -> int:
    """Reconstruct a full packet number from a truncated value."""
    if largest is None:
        return truncated
    expected = largest + 1
    window = 1 << (pn_len * 8)
    half = window // 2
    candidate = (expected & ~(window - 1)) | truncated
    if candidate <= expected - half and candidate < (1 << 62) - window:
        candidate += window
    elif candidate > expected + half and candidate >= window:
        candidate -= window
    return candidate


def parse_params(body: bytes) -> tuple[str, list[dict[str, str]], list[dict[str, object]]]:
    """Parse payload parameters and return endpoint, entries, and local problems."""
    endpoint = "client" if body[0] == 1 else "server"
    count, offset = decode_varint(body, 1)
    params = []
    problems = []
    seen = set()
    for _ in range(count):
        ident, offset = decode_varint(body, offset)
        length, offset = decode_varint(body, offset)
        value = body[offset : offset + length]
        offset += length
        key = f"0x{ident:04x}"
        if key in seen:
            problems.append({"code": "duplicate_transport_parameter", "detail": key})
        seen.add(key)
        params.append({"id": key, "value": value.hex()})
    return endpoint, params, problems


def expected_report(trace_path: Path) -> dict[str, object]:
    """Build the expected JSON report for a trace bundle."""
    parsed = [parse_packet(i, raw) for i, raw in enumerate(read_bundle(trace_path.read_bytes()))]
    states: dict[str, dict[str, object]] = {}
    total_problems = 0
    for packet in parsed:
        if packet["type"] == "retry":
            key = bytes(packet["dcid"]).hex()
            endpoint = "server"
        else:
            body = bytes(packet["payload"])
            endpoint = "client" if body[0] == 1 else "server"
            key = bytes(packet["scid" if endpoint == "client" else "dcid"]).hex()
        state = states.setdefault(
            key,
            {
                "odcid": packet["dcid"],
                "largest": {"client": None, "server": None},
                "seen": set(),
                "packets": [],
                "params": {"client": [], "server": []},
                "problems": [],
                "had_retry": False,
                "bad_retry": False,
            },
        )
        if packet["type"] == "retry":
            state["had_retry"] = True
            raw_without_tag = bytes(packet["raw"])[:-16]
            odcid = bytes(state["odcid"])
            tag = hashlib.sha256(b"retry-v1" + bytes([len(odcid)]) + odcid + raw_without_tag).digest()[:16]
            ok = tag == packet["tag"]
            if not ok:
                state["bad_retry"] = True
                state["problems"].append(
                    {"code": "bad_retry_integrity", "packet_index": packet["index"], "detail": "retry"}
                )
                total_problems += 1
            state["packets"].append(
                {
                    "index": packet["index"],
                    "type": "retry",
                    "endpoint": endpoint,
                    "dcid": bytes(packet["dcid"]).hex(),
                    "scid": bytes(packet["scid"]).hex(),
                    "packet_number": None,
                    "token": bytes(packet["token"]).hex(),
                }
            )
            continue
        pn = reconstruct(int(packet["truncated"]), int(packet["pn_len"]), state["largest"][endpoint])
        seen_key = (endpoint, pn)
        if seen_key in state["seen"]:
            state["problems"].append(
                {"code": "duplicate_packet", "packet_index": packet["index"], "detail": endpoint}
            )
            total_problems += 1
            continue
        state["seen"].add(seen_key)
        if state["largest"][endpoint] is None or pn > state["largest"][endpoint]:
            state["largest"][endpoint] = pn
        endpoint_from_payload, params, problems = parse_params(bytes(packet["payload"]))
        assert endpoint_from_payload == endpoint
        state["params"][endpoint].extend(params)
        for problem in problems:
            problem["packet_index"] = packet["index"]
            state["problems"].append(problem)
            total_problems += 1
        entry = {
            "index": packet["index"],
            "type": "initial",
            "endpoint": endpoint,
            "dcid": bytes(packet["dcid"]).hex(),
            "scid": bytes(packet["scid"]).hex(),
            "packet_number": pn,
        }
        if packet["token"]:
            entry["token"] = bytes(packet["token"]).hex()
        state["packets"].append(entry)
    connections = []
    for key in sorted(states):
        state = states[key]
        connections.append(
            {
                "connection_id": key,
                "retry_valid": bool(state["had_retry"] and not state["bad_retry"]),
                "packet_count": len(state["packets"]),
                "packets": state["packets"],
                "transport_parameters": {
                    "client": sorted(state["params"]["client"], key=lambda item: (int(item["id"], 16), item["value"])),
                    "server": sorted(state["params"]["server"], key=lambda item: (int(item["id"], 16), item["value"])),
                },
                "problems": state["problems"],
            }
        )
    return {
        "version": "qtrace-1",
        "summary": {
            "trace_count": 1,
            "packet_count": len(parsed),
            "connection_count": len(connections),
            "problem_count": total_problems,
            "valid": total_problems == 0,
        },
        "connections": connections,
    }


def run_cli(trace: Path, output: Path) -> dict[str, object]:
    """Build and run the Go CLI, then return the parsed JSON report."""
    subprocess.run(["go", "build", "-o", str(BIN), str(APP / "cmd" / "quicval")], cwd=APP, check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BIN), "--trace", str(trace), "--out", str(output)], cwd=APP, check=True)
    return json.loads(output.read_text())


def write_case_a(path: Path) -> None:
    """Write a generated trace bundle used by the verifier."""
    odcid = bytes.fromhex("0102030405060708")
    client = bytes.fromhex("1112131415161718")
    server = bytes.fromhex("2122232425262728")
    token = bytes(range(90))
    packets = [
        initial(odcid, client, b"", 0xFE, 1, payload(1, [Param(0x40, b"a"), Param(0x07, b"first")])),
        retry(odcid, client, server, token),
        initial(server, client, token, 0xFF, 1, payload(1, [Param(0x39, b"b" * 65)])),
        initial(server, client, token, 0x00, 1, payload(1, [Param(0x3A, b"c")])),
        initial(client, server, b"", 0x0100, 2, payload(2, [Param(0x40, b"sv"), Param(0, b"z")])),
    ]
    path.write_bytes(bundle(packets))


def write_case_b(path: Path) -> None:
    """Write a second generated trace bundle used by the verifier."""
    odcid = bytes.fromhex("9081726354453627")
    client = bytes.fromhex("333435363738393a")
    server = bytes.fromhex("4445464748494a4b")
    token = b"token-token-token-token-token-token-token-token-token-token"
    packets = [
        initial(odcid, client, b"", 7, 1, payload(1, [Param(1, b"x"), Param(1, b"y")])),
        retry(odcid, client, server, token, corrupt=True),
        initial(server, client, token, 8, 1, payload(1, [Param(2, bytes(range(64)))])),
        initial(server, client, token, 8, 1, payload(1, [Param(3, b"dup")])),
        initial(client, server, b"", 63, 1, payload(2, [Param(0x4000, b"long-id")])),
    ]
    path.write_bytes(bundle(packets))


def write_case_c(path: Path) -> None:
    """Write a generated trace with no Retry packet."""
    odcid = bytes.fromhex("a0a1a2a3a4a5a6a7")
    client = bytes.fromhex("b0b1b2b3b4b5b6b7")
    server = bytes.fromhex("c0c1c2c3c4c5c6c7")
    packets = [
        initial(odcid, client, b"", 0x10, 1, payload(1, [Param(4, b"noretry")])),
        initial(client, server, b"", 0x20, 1, payload(2, [Param(5, b"server")])),
    ]
    path.write_bytes(bundle(packets))


def write_case_d(path: Path) -> None:
    """Write a generated trace with several Retry packets on one connection."""
    odcid = bytes.fromhex("0d0e0f1011121314")
    client = bytes.fromhex("15161718191a1b1c")
    server_a = bytes.fromhex("25262728292a2b2c")
    server_b = bytes.fromhex("35363738393a3b3c")
    token_a = b"retry-token-a" * 5
    token_b = b"retry-token-b" * 5
    packets = [
        initial(odcid, client, b"", 0x2FFE, 2, payload(1, [Param(0x10, b"first")])),
        retry(odcid, client, server_a, token_a, corrupt=True),
        retry(odcid, client, server_b, token_b),
        initial(server_b, client, token_b, 0x2FFF, 2, payload(1, [Param(0x11, b"after")])),
        initial(client, server_b, b"", 0x01, 1, payload(2, [Param(0x12, b"reply")])),
    ]
    path.write_bytes(bundle(packets))


def write_case_e(path: Path) -> None:
    """Write an interleaved trace with several independent connections."""
    odcid_a = bytes.fromhex("4142434445464748")
    client_a = bytes.fromhex("5152535455565758")
    server_a1 = bytes.fromhex("6162636465666768")
    server_a2 = bytes.fromhex("7172737475767778")
    token_a1 = bytes(range(1, 72))
    token_a2 = bytes(range(72, 150))
    odcid_b = bytes.fromhex("8182838485868788")
    client_b = bytes.fromhex("9192939495969798")
    server_b = bytes.fromhex("a1a2a3a4a5a6a7a8")
    packets = [
        initial(odcid_a, client_a, b"", 1, 1, payload(1, [Param(0x21, b"a-client")])),
        initial(odcid_b, client_b, b"", 2, 1, payload(1, [Param(0x20, b"b-client")])),
        retry(odcid_a, client_a, server_a1, token_a1),
        initial(client_b, server_b, b"", 3, 1, payload(2, [Param(0x1F, b"b-server")])),
        retry(odcid_a, client_a, server_a2, token_a2),
        initial(server_a2, client_a, token_a2, 4, 1, payload(1, [Param(0x22, b"a-after")])),
        initial(client_a, server_a2, b"", 5, 1, payload(2, [Param(0x23, b"a-server")])),
    ]
    path.write_bytes(bundle(packets))


def write_case_f(path: Path) -> None:
    """Write a trace that exercises packet number reconstruction near a window boundary."""
    odcid = bytes.fromhex("d0d1d2d3d4d5d6d7")
    client = bytes.fromhex("e0e1e2e3e4e5e6e7")
    packets = [
        initial(odcid, client, b"", 300, 2, payload(1, [Param(0x0100, b"high")])),
        initial(odcid, client, b"", 250, 1, payload(1, [Param(0x00FF, b"older")])),
        initial(odcid, client, b"", 45, 1, payload(1, [Param(0x0101, b"wrapped")])),
    ]
    path.write_bytes(bundle(packets))


def assert_report_matches(trace: Path, output: Path) -> None:
    """Assert that CLI output matches the report contract for one trace."""
    assert run_cli(trace, output) == expected_report(trace)


def test_public_fixture_report_matches_contract() -> None:
    """Verify the bundled trace produces the documented report."""
    assert_report_matches(PUBLIC_TRACE, PUBLIC_OUT)


def test_generated_trace_report_matches_contract() -> None:
    """Verify the CLI handles an additional valid trace bundle."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-a.qtr"
    output = WORK / "case-a.json"
    write_case_a(trace)
    assert_report_matches(trace, output)


def test_generated_problem_report_matches_contract() -> None:
    """Verify the CLI reports contract problems without changing the schema."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-b.qtr"
    output = WORK / "case-b.json"
    write_case_b(trace)
    report = run_cli(trace, output)
    expected = expected_report(trace)
    assert report == expected
    assert report["summary"]["valid"] is False
    assert report["summary"]["problem_count"] >= 3


def test_generated_no_retry_report_matches_contract() -> None:
    """Verify a connection without Retry keeps retry_valid false."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-c.qtr"
    output = WORK / "case-c.json"
    write_case_c(trace)
    report = run_cli(trace, output)
    expected = expected_report(trace)
    assert report == expected
    assert report["connections"][0]["retry_valid"] is False


def test_generated_multi_retry_report_matches_contract() -> None:
    """Verify all Retry tags on a connection control retry_valid."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-d.qtr"
    output = WORK / "case-d.json"
    write_case_d(trace)
    report = run_cli(trace, output)
    expected = expected_report(trace)
    assert report == expected
    assert report["connections"][0]["retry_valid"] is False
    assert any(
        problem["code"] == "bad_retry_integrity" for problem in report["connections"][0]["problems"]
    )


def test_generated_interleaved_connections_match_contract() -> None:
    """Verify interleaved connections keep independent retry and parameter state."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-e.qtr"
    output = WORK / "case-e.json"
    write_case_e(trace)
    report = run_cli(trace, output)
    expected = expected_report(trace)
    assert report == expected
    retry_states = {conn["connection_id"]: conn["retry_valid"] for conn in report["connections"]}
    assert retry_states["5152535455565758"] is True
    assert retry_states["9192939495969798"] is False


def test_generated_packet_number_window_match_contract() -> None:
    """Verify packet-number decoding keeps the largest observed value."""
    WORK.mkdir(parents=True, exist_ok=True)
    trace = WORK / "case-f.qtr"
    output = WORK / "case-f.json"
    write_case_f(trace)
    report = run_cli(trace, output)
    expected = expected_report(trace)
    assert report == expected
    numbers = [packet["packet_number"] for packet in report["connections"][0]["packets"]]
    assert numbers == [300, 250, 301]
