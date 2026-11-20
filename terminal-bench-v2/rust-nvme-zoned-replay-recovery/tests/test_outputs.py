import csv
import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path("/opt/nvmez-recover")
BIN = ROOT / "target" / "release" / "nvmez-recover"


def pack_record(rtype, flags, cid, nsid, path, status, lba, nlb, seq, payload=b""):
    header = b"NCAP" + struct.pack("<BBHHBBQIQI", rtype, flags, cid, nsid, path, status, lba, nlb, seq, len(payload))
    return header + payload + (b"\0" * ((4 - len(payload) % 4) % 4))


@dataclass
class Zone:
    nsid: int
    zslba: int
    capacity: int
    block_size: int
    max_open: int
    wp: int
    data: bytearray
    open: bool = False


def read_geometry(case_dir):
    zones = []
    with (case_dir / "geometry.tsv").open() as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    for row in rows:
        capacity = int(row["capacity"])
        block_size = int(row["block_size"])
        zslba = int(row["zslba"])
        zones.append(
            Zone(
                nsid=int(row["nsid"]),
                zslba=zslba,
                capacity=capacity,
                block_size=block_size,
                max_open=int(row["max_open"]),
                wp=zslba,
                data=bytearray(capacity * block_size),
            )
        )
    return sorted(zones, key=lambda z: (z.nsid, z.zslba))


def parse_records(stream_path):
    data = stream_path.read_bytes()
    pos = 0
    records = []
    while pos < len(data):
        if pos + 36 > len(data):
            break
        if data[pos : pos + 4] != b"NCAP":
            raise AssertionError("invalid capture framing")
        rtype, flags, cid, nsid, path, status, lba, nlb, seq, plen = struct.unpack(
            "<BBHHBBQIQI", data[pos + 4 : pos + 36]
        )
        start = pos + 36
        end = start + plen
        padded_end = end + ((4 - plen % 4) % 4)
        if padded_end > len(data):
            break
        records.append(
            {
                "rtype": rtype,
                "flags": flags,
                "cid": cid,
                "nsid": nsid,
                "path": path,
                "status": status,
                "lba": lba,
                "nlb": nlb,
                "seq": seq,
                "payload": data[start:end],
            }
        )
        pos = padded_end
    return records


def fnv64(data):
    h = 0xCBF29CE484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def nonzero_ranges(zone):
    out = []
    start = None
    for blk in range(zone.capacity):
        a = blk * zone.block_size
        present = any(zone.data[a : a + zone.block_size])
        if present and start is None:
            start = blk
        elif not present and start is not None:
            end = blk - 1
            out.append(str(zone.zslba + start) if start == end else f"{zone.zslba + start}-{zone.zslba + end}")
            start = None
    if start is not None:
        end = zone.capacity - 1
        out.append(str(zone.zslba + start) if start == end else f"{zone.zslba + start}-{zone.zslba + end}")
    return out


def find_zone(zones, nsid, lba):
    for i, zone in enumerate(zones):
        if zone.nsid == nsid and zone.zslba <= lba < zone.zslba + zone.capacity:
            return i
    return None


def reference_outputs(case_dir):
    zones = read_geometry(case_dir)
    persisted = None
    records = parse_records(case_dir / "stream.ncap")
    stats = {"records": 0, "applied": 0, "duplicates": 0, "ignored": 0, "flushes": 0, "cut_seq": None}
    audit = ["seq,path,cid,event,nsid,lba,nlb,result"]
    online = {}
    accepted = set()

    def snapshot():
        return [(z.wp, bytes(z.data), z.open) for z in zones]

    def restore(snap):
        for zone, (wp, data, opened) in zip(zones, snap):
            zone.wp = wp
            zone.data[:] = data
            zone.open = opened

    for rec in records:
        stats["records"] += 1
        common = f'{rec["seq"]},{rec["path"]},{rec["cid"]}'
        if rec["rtype"] == 6:
            stats["cut_seq"] = rec["seq"]
            audit.append(f'{common},crash,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},cut')
            if persisted is not None:
                restore(persisted)
            break
        if rec["rtype"] == 5:
            online[rec["path"]] = bool(rec["flags"] & 1)
            audit.append(f'{common},ana,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},ok')
            continue
        if rec["rtype"] == 4:
            stats["flushes"] += 1
            persisted = snapshot()
            audit.append(f'{common},flush,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},ok')
            continue
        if rec["status"] != 0 or not online.get(rec["path"], True):
            stats["ignored"] += 1
            audit.append(f'{common},ignored,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},path')
            continue
        key = (rec["nsid"], rec["cid"], rec["seq"], rec["rtype"], rec["lba"], rec["nlb"], rec["payload"])
        if key in accepted:
            stats["duplicates"] += 1
            audit.append(f'{common},duplicate,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},seen')
            continue
        start_lba = rec["lba"]
        if rec["rtype"] == 2:
            idx = find_zone(zones, rec["nsid"], start_lba)
            if idx is not None:
                start_lba = zones[idx].wp
        else:
            idx = find_zone(zones, rec["nsid"], start_lba)
        if idx is None:
            stats["ignored"] += 1
            audit.append(f'{common},ignored,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},range')
            continue
        zone = zones[idx]
        if rec["rtype"] == 3:
            zone.data[:] = b"\0" * len(zone.data)
            zone.wp = zone.zslba
            zone.open = False
            accepted.add(key)
            stats["applied"] += 1
            audit.append(f'{common},reset,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},ok')
            continue
        need = rec["nlb"] * zone.block_size
        open_count = sum(1 for z in zones if z.nsid == rec["nsid"] and z.open)
        if (
            rec["rtype"] not in (1, 2)
            or len(rec["payload"]) != need
            or start_lba != zone.wp
            or start_lba + rec["nlb"] > zone.zslba + zone.capacity
            or (not zone.open and open_count >= zone.max_open)
        ):
            stats["ignored"] += 1
            audit.append(f'{common},ignored,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},state')
            continue
        off = (start_lba - zone.zslba) * zone.block_size
        zone.data[off : off + need] = rec["payload"]
        zone.wp += rec["nlb"]
        zone.open = zone.wp < zone.zslba + zone.capacity
        accepted.add(key)
        stats["applied"] += 1
        audit.append(f'{common},applied,{rec["nsid"]},{rec["lba"]},{rec["nlb"]},ok')

    if stats["cut_seq"] is None and persisted is None:
        persisted = snapshot()
    zones_jsonl = "".join(
        json.dumps(
            {"nsid": z.nsid, "zslba": z.zslba, "wp": z.wp, "digest": fnv64(z.data), "ranges": nonzero_ranges(z)},
            separators=(",", ":"),
        )
        + "\n"
        for z in zones
    )
    manifest = {
        "case": case_dir.name,
        "records": stats["records"],
        "applied": stats["applied"],
        "duplicates": stats["duplicates"],
        "ignored": stats["ignored"],
        "flushes": stats["flushes"],
        "cut_seq": stats["cut_seq"],
        "zones": len(zones),
    }
    return {"zones.jsonl": zones_jsonl, "audit.csv": "\n".join(audit) + "\n", "manifest.json": json.dumps(manifest, separators=(",", ":")) + "\n"}


def run_tool(case_dir, out_dir):
    subprocess.run(["cargo", "build", "--release"], cwd=ROOT, check=True)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    result = subprocess.run([str(BIN), "--case", str(case_dir), "--out", str(out_dir)], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout


def compare_case(case_dir, out_dir):
    expected = reference_outputs(case_dir)
    run_tool(case_dir, out_dir)
    assert sorted(p.name for p in out_dir.iterdir()) == ["audit.csv", "manifest.json", "zones.jsonl"]
    for name, content in expected.items():
        assert (out_dir / name).read_text() == content


def write_case(base, geometry, records, torn_tail=b""):
    base.mkdir(parents=True, exist_ok=True)
    (base / "geometry.tsv").write_text(geometry)
    (base / "stream.ncap").write_bytes(b"".join(records) + torn_tail)


def test_bundled_case_matches_replay_contract(tmp_path):
    """The shipped operator capture is rebuilt into the documented artifacts."""
    compare_case(ROOT / "cases" / "rack-a", tmp_path / "out-rack")


def test_generated_case_one_matches_contract(tmp_path):
    """A fresh capture is rebuilt according to the published contract."""
    case_dir = tmp_path / "case-alpha"
    geom = "nsid\tzslba\tcapacity\tblock_size\tmax_open\n7\t0\t8\t4\t2\n7\t8\t8\t4\t2\n"
    records = [
        pack_record(5, 1, 1, 0, 0, 0, 0, 0, 1),
        pack_record(1, 0, 44, 7, 0, 0, 0, 1, 2, b"aaaa"),
        pack_record(5, 0, 2, 0, 0, 0, 0, 0, 3),
        pack_record(1, 0, 45, 7, 0, 0, 1, 1, 4, b"bbbb"),
        pack_record(5, 1, 3, 0, 1, 0, 0, 0, 5),
        pack_record(1, 0, 44, 7, 1, 0, 0, 1, 2, b"aaaa"),
        pack_record(1, 0, 44, 7, 1, 0, 1, 1, 6, b"cccc"),
        pack_record(4, 0, 9, 0, 1, 0, 0, 0, 7),
    ]
    write_case(case_dir, geom, records, b"NCAP\x02\x00")
    compare_case(case_dir, tmp_path / "out-alpha")


def test_generated_case_two_matches_contract(tmp_path):
    """Another fresh capture is rebuilt according to the published contract."""
    case_dir = tmp_path / "case-beta"
    geom = "nsid\tzslba\tcapacity\tblock_size\tmax_open\n9\t0\t4\t4\t1\n9\t4\t4\t4\t1\n9\t8\t4\t4\t1\n"
    records = [
        pack_record(2, 0, 1, 9, 0, 0, 0, 1, 1, b"1111"),
        pack_record(1, 0, 2, 9, 0, 0, 4, 1, 2, b"2222"),
        pack_record(4, 0, 3, 0, 0, 0, 0, 0, 3),
        pack_record(3, 0, 4, 9, 0, 0, 0, 0, 4),
        pack_record(1, 0, 5, 9, 0, 0, 4, 1, 5, b"3333"),
        pack_record(1, 0, 6, 9, 0, 0, 8, 1, 6, b"4444"),
        pack_record(4, 0, 7, 0, 0, 0, 0, 0, 7),
        pack_record(1, 0, 8, 9, 0, 0, 5, 1, 8, b"5555"),
        pack_record(6, 0, 9, 0, 0, 0, 0, 0, 9),
    ]
    write_case(case_dir, geom, records)
    compare_case(case_dir, tmp_path / "out-beta")


def test_output_json_and_csv_are_stable(tmp_path):
    """The reports parse cleanly and remain byte-stable across repeated runs."""
    case_dir = ROOT / "cases" / "rack-a"
    out_dir = tmp_path / "out-repeat"
    run_tool(case_dir, out_dir)
    first = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    with (out_dir / "manifest.json").open() as f:
        manifest = json.load(f)
    assert set(manifest) == {"case", "records", "applied", "duplicates", "ignored", "flushes", "cut_seq", "zones"}
    with (out_dir / "audit.csv").open() as f:
        assert next(csv.reader(f)) == ["seq", "path", "cid", "event", "nsid", "lba", "nlb", "result"]
    run_tool(case_dir, out_dir)
    second = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    assert first == second
