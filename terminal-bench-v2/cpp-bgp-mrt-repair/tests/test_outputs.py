"""Behavioral verifier for the MRT replay task."""

from __future__ import annotations

import ipaddress
import json
import struct
import subprocess
from pathlib import Path


APP = Path("/app/task_file")
BIN = APP / "build" / "mrt_replay"


def be16(value: int) -> bytes:
    return struct.pack("!H", value)


def be32(value: int) -> bytes:
    return struct.pack("!I", value)


def mrt(timestamp: int, typ: int, subtype: int, payload: bytes) -> bytes:
    return be32(timestamp) + be16(typ) + be16(subtype) + be32(len(payload)) + payload


def attr(code: int, value: bytes, flags: int = 0x40) -> bytes:
    if len(value) > 255:
        return bytes([flags | 0x10, code]) + be16(len(value)) + value
    return bytes([flags, code, len(value)]) + value


def seg16(values: list[int], typ: int = 2) -> bytes:
    return bytes([typ, len(values)]) + b"".join(be16(v) for v in values)


def seg32(values: list[int], typ: int = 2) -> bytes:
    return bytes([typ, len(values)]) + b"".join(be32(v) for v in values)


def prefix_bytes(prefix: str) -> bytes:
    net = ipaddress.ip_network(prefix, strict=False)
    nbytes = (net.prefixlen + 7) // 8
    return bytes([net.prefixlen]) + net.network_address.packed[:nbytes]


def peer_table(peers: list[dict[str, object]]) -> bytes:
    body = be32(0) + be16(0) + be16(len(peers))
    for peer in peers:
        addr = ipaddress.ip_address(str(peer["address"]))
        bgp_id = ipaddress.ip_address(str(peer.get("bgp_id", "198.51.100.1"))).packed
        ptype = (1 if addr.version == 6 else 0) | (2 if peer.get("as4", False) else 0) | (4 if peer.get("ibgp", False) else 0)
        asn = int(peer["as"])
        body += bytes([ptype]) + bgp_id + addr.packed + (be32(asn) if ptype & 2 else be16(asn))
    return body


def rib(prefix: str, entries: list[tuple[int, int, bytes]], sequence: int = 1) -> tuple[int, bytes]:
    body = be32(sequence) + prefix_bytes(prefix) + be16(len(entries))
    for peer_index, timestamp, attrs in entries:
        body += be16(peer_index) + be32(timestamp) + be16(len(attrs)) + attrs
    subtype = 2 if ipaddress.ip_network(prefix, strict=False).version == 4 else 4
    return subtype, body


def update(peer_as: int, peer_addr: str, attrs: bytes, nlri: list[str] | None = None, withdrawn: list[str] | None = None) -> bytes:
    addr = ipaddress.ip_address(peer_addr)
    afi = 1 if addr.version == 4 else 2
    local = ipaddress.ip_address("10.0.0.1" if afi == 1 else "2001:db8::ffff")
    wd = b"".join(prefix_bytes(p) for p in (withdrawn or []))
    announced = b"".join(prefix_bytes(p) for p in (nlri or []))
    msg = bytes([2]) + be16(len(wd)) + wd + be16(len(attrs)) + attrs + announced
    return be32(peer_as) + be32(64512) + be16(0) + be16(afi) + addr.packed + local.packed + msg


def mp_reach(prefixes: list[str], nexthop: str) -> bytes:
    nh = ipaddress.ip_address(nexthop).packed
    afi = 2 if ipaddress.ip_address(nexthop).version == 6 else 1
    return be16(afi) + bytes([1, len(nh)]) + nh + bytes([0]) + b"".join(prefix_bytes(p) for p in prefixes)


def mp_unreach(prefixes: list[str]) -> bytes:
    afi = 2 if ipaddress.ip_network(prefixes[0], strict=False).version == 6 else 1
    return be16(afi) + bytes([1]) + b"".join(prefix_bytes(p) for p in prefixes)


def base_attrs(local_pref: int = 100, origin: int = 0, med: int | None = None, as_path: bytes | None = None, as4_path: bytes | None = None) -> bytes:
    parts = [attr(1, bytes([origin])), attr(5, be32(local_pref))]
    if med is not None:
        parts.append(attr(4, be32(med)))
    if as_path is not None:
        parts.append(attr(2, as_path))
    if as4_path is not None:
        parts.append(attr(17, as4_path))
    return b"".join(parts)


def run_capture(tmp_path: Path, capture: bytes) -> dict[str, object]:
    subprocess.run(["make", "clean"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(["make"], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    in_path = tmp_path / "input.mrt"
    out_path = tmp_path / "report.json"
    in_path.write_bytes(capture)
    subprocess.run([str(BIN), str(in_path), str(out_path)], cwd=APP, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return json.loads(out_path.read_text(encoding="utf-8"))


def routes_by_prefix(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {route["prefix"]: route for route in report["routes"]}  # type: ignore[index]


def malformed_update_after_withdraw(peer_as: int, peer_addr: str, withdrawn: list[str]) -> bytes:
    addr = ipaddress.ip_address(peer_addr)
    local = ipaddress.ip_address("10.0.0.1")
    wd = b"".join(prefix_bytes(p) for p in withdrawn)
    bad_nlri = bytes([24, 203])
    msg = bytes([2]) + be16(len(wd)) + wd + be16(0) + bad_nlri
    return be32(peer_as) + be32(64512) + be16(0) + be16(1) + addr.packed + local.packed + msg


def test_replays_mixed_table_and_update_stream(tmp_path: Path) -> None:
    """The report should reflect table entries, incremental updates, withdrawals, AS4 values, IPv6 MP attributes, and quarantined bad records."""
    peers = [
        {"as": 64500, "address": "192.0.2.1", "bgp_id": "198.51.100.1"},
        {"as": 64501, "address": "192.0.2.2", "bgp_id": "198.51.100.2"},
        {"as": 65551, "address": "2001:db8::10", "bgp_id": "198.51.100.3", "as4": True, "ibgp": True},
        {"as": 64500, "address": "192.0.2.1", "bgp_id": "198.51.100.4"},
    ]
    rib4_a = base_attrs(local_pref=100, origin=0, med=50, as_path=seg16([64520, 64530]))
    rib4_b = base_attrs(local_pref=100, origin=0, med=200, as_path=seg16([64520]))
    subtype4, rib4 = rib("198.51.100.0/24", [(0, 100, rib4_a), (1, 101, rib4_b)])
    rib6_attrs = base_attrs(local_pref=150, origin=0, as_path=seg16([23456, 64560]), as4_path=seg32([65551, 64560]))
    subtype6, rib6 = rib("2001:db8:100::/48", [(2, 110, rib6_attrs)], sequence=2)
    upd_a = base_attrs(local_pref=200, origin=2, as_path=seg16([23456, 65001]), as4_path=seg32([4_200_000_001, 65001]))
    upd_b = base_attrs(local_pref=200, origin=0, as_path=seg16([65010]))
    upd_v6 = base_attrs(local_pref=250, origin=0, as_path=seg16([64570])) + attr(14, mp_reach(["2001:db8:200::/48"], "2001:db8::10"))
    wdr_v6 = attr(15, mp_unreach(["2001:db8:100::/48"]))
    bad_payload = be32(3) + prefix_bytes("10.10.0.0/16") + be16(1) + be16(99)
    capture = b"".join(
        [
            mrt(1, 13, 1, peer_table(peers)),
            mrt(2, 13, subtype4, rib4),
            mrt(3, 13, subtype6, rib6),
            mrt(4, 16, 1, update(64500, "192.0.2.1", b"", withdrawn=["198.51.100.0/24"])),
            mrt(5, 16, 1, update(64500, "192.0.2.1", upd_a, nlri=["203.0.113.0/24"])),
            mrt(6, 16, 1, update(64501, "192.0.2.2", upd_b, nlri=["203.0.113.0/24"])),
            mrt(7, 16, 1, update(65551, "2001:db8::10", upd_v6)),
            mrt(8, 16, 1, update(65551, "2001:db8::10", wdr_v6)),
            mrt(9, 99, 1, b"ignored"),
            mrt(10, 13, 2, bad_payload),
            be32(11) + be16(13) + be16(2) + be32(20) + b"short",
        ]
    )
    report = run_capture(tmp_path, capture)
    by_prefix = routes_by_prefix(report)
    assert report["records"] == {"seen": 11, "applied": 8, "ignored": 1, "malformed": 2}
    assert [peer["index"] for peer in report["peers"]] == [0, 1, 2, 3]
    assert set(by_prefix) == {"198.51.100.0/24", "203.0.113.0/24", "2001:0db8:0200:0000:0000:0000:0000:0000/48"}
    assert by_prefix["198.51.100.0/24"]["selected"]["peer_address"] == "192.0.2.2"
    selected_v4 = by_prefix["203.0.113.0/24"]["selected"]
    assert selected_v4["peer_as"] == 64501
    other = by_prefix["203.0.113.0/24"]["candidates"][1]
    assert other["as_path"] == [4_200_000_001, 65001]
    selected_v6 = by_prefix["2001:0db8:0200:0000:0000:0000:0000:0000/48"]["selected"]
    assert selected_v6["source"] == "ibgp"
    assert selected_v6["local_pref"] == 250


def test_boundaries_and_path_comparison_are_stable(tmp_path: Path) -> None:
    """A later peer table should replace route state, and best-path ordering should match the documented comparison rules."""
    first_table = peer_table([{"as": 64496, "address": "192.0.2.200"}])
    stale_attrs = base_attrs(as_path=seg16([64497]))
    stale_subtype, stale_rib = rib("172.16.0.0/16", [(0, 50, stale_attrs)])
    peers = [
        {"as": 64510, "address": "192.0.2.1"},
        {"as": 64511, "address": "192.0.2.2"},
    ]
    diff_a = base_attrs(local_pref=100, origin=0, med=200, as_path=seg16([65020]))
    diff_b = base_attrs(local_pref=100, origin=0, med=1, as_path=seg16([65030]))
    same_a = base_attrs(local_pref=100, origin=0, med=200, as_path=seg16([65040]))
    same_b = base_attrs(local_pref=100, origin=0, med=1, as_path=seg16([65040]))
    p1_sub, p1_rib = rib("10.0.0.0/24", [(0, 100, diff_a), (1, 101, diff_b)], sequence=10)
    p2_sub, p2_rib = rib("10.0.1.0/24", [(0, 102, same_a), (1, 103, same_b)], sequence=11)
    p3_sub, p3_rib = rib("2001:db8:10::/48", [(1, 104, base_attrs(local_pref=150, as_path=seg16([65050])))], sequence=12)
    capture = b"".join(
        [
            mrt(1, 13, 1, first_table),
            mrt(2, 13, stale_subtype, stale_rib),
            mrt(3, 13, 1, peer_table(peers)),
            mrt(4, 13, p1_sub, p1_rib),
            mrt(5, 13, p2_sub, p2_rib),
            mrt(6, 13, p3_sub, p3_rib),
        ]
    )
    report = run_capture(tmp_path, capture)
    by_prefix = routes_by_prefix(report)
    assert "172.16.0.0/16" not in by_prefix
    assert by_prefix["10.0.0.0/24"]["selected"]["peer_address"] == "192.0.2.1"
    assert by_prefix["10.0.1.0/24"]["selected"]["peer_address"] == "192.0.2.2"
    assert [route["prefix"] for route in report["routes"]] == [
        "10.0.0.0/24",
        "10.0.1.0/24",
        "2001:0db8:0010:0000:0000:0000:0000:0000/48",
    ]
    assert report["selected_count"] == 3


def test_update_replacement_and_malformed_update_are_atomic(tmp_path: Path) -> None:
    """A peer's later announcement replaces its candidate, and a malformed update must not partially apply withdrawals."""
    peers = [{"as": 64500, "address": "192.0.2.1"}]
    stable_subtype, stable_rib = rib("10.20.0.0/16", [(0, 100, base_attrs(as_path=seg16([64550])))])
    first = base_attrs(local_pref=100, origin=0, as_path=seg16([64560]))
    replacement = base_attrs(local_pref=250, origin=0, as_path=seg16([64560]))
    capture = b"".join(
        [
            mrt(1, 13, 1, peer_table(peers)),
            mrt(2, 13, stable_subtype, stable_rib),
            mrt(3, 16, 1, update(64500, "192.0.2.1", first, nlri=["203.0.113.0/24"])),
            mrt(4, 16, 1, update(64500, "192.0.2.1", replacement, nlri=["203.0.113.0/24"])),
            mrt(5, 16, 1, malformed_update_after_withdraw(64500, "192.0.2.1", ["10.20.0.0/16"])),
        ]
    )
    report = run_capture(tmp_path, capture)
    by_prefix = routes_by_prefix(report)
    assert report["records"] == {"seen": 5, "applied": 4, "ignored": 0, "malformed": 1}
    assert set(by_prefix) == {"10.20.0.0/16", "203.0.113.0/24"}
    route = by_prefix["203.0.113.0/24"]
    assert len(route["candidates"]) == 1
    assert route["selected"]["local_pref"] == 250


def test_rib_entry_atomicity_and_numeric_peer_tiebreak(tmp_path: Path) -> None:
    """RIB records apply atomically, and the peer-address tiebreak compares address bytes rather than text."""
    peers = [
        {"as": 64500, "address": "192.0.2.2"},
        {"as": 64501, "address": "192.0.2.10"},
    ]
    attrs_a = base_attrs(local_pref=100, origin=0, as_path=seg16([64570]))
    attrs_b = base_attrs(local_pref=100, origin=0, as_path=seg16([64580]))
    good_subtype, good_rib = rib("198.51.101.0/24", [(1, 100, attrs_b), (0, 101, attrs_a)])
    bad_subtype, bad_rib = rib("203.0.114.0/24", [(0, 102, attrs_a), (9, 103, attrs_b)])
    capture = b"".join(
        [
            mrt(1, 13, 1, peer_table(peers)),
            mrt(2, 13, good_subtype, good_rib),
            mrt(3, 13, bad_subtype, bad_rib),
        ]
    )
    report = run_capture(tmp_path, capture)
    by_prefix = routes_by_prefix(report)
    assert report["records"] == {"seen": 3, "applied": 2, "ignored": 0, "malformed": 1}
    assert "203.0.114.0/24" not in by_prefix
    assert by_prefix["198.51.101.0/24"]["selected"]["peer_address"] == "192.0.2.2"
    assert report["selected_count"] == 1
