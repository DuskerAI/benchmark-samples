"""Behavioral verifier for the offline io replay tool."""

import json
import re
import subprocess
from pathlib import Path

APP = Path("/app/task_file")
BIN = APP / "build" / "uring_replay"
RUN_DIR = Path("/tmp/uring-replay-verifier")
MASK32 = 0xFFFFFFFF
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,63}\Z")


def _fnv(data: bytes) -> str:
    h = 0xCBF29CE484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def _fields(line: str) -> tuple[str, dict[str, str]] | None:
    parts = line.split()
    if not parts:
        return None
    values = {}
    for part in parts[1:]:
        if "=" not in part:
            return None
        k, v = part.split("=", 1)
        if not k:
            return None
        values[k] = v
    return parts[0], values


def _present(spec: str) -> set[int] | None:
    if not spec:
        return set()
    pages = set()
    for part in spec.split(","):
        if not part:
            return None
        if "-" in part:
            bounds = part.split("-", 1)
            if not all(x.isdecimal() for x in bounds):
                return None
            lo, hi = (int(x) for x in bounds)
            if hi < lo:
                return None
            pages.update(range(lo, hi + 1))
        else:
            if not part.isdecimal():
                return None
            pages.add(int(part))
    return pages


def _uint(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    return int(value)


def _sint(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"-?[0-9]+", value):
        return None
    return int(value)


def _valid_name(value: str | None) -> bool:
    return value is not None and NAME_RE.fullmatch(value) is not None


def _valid_ref(value: str | None) -> bool:
    return value == "-" or _valid_name(value)


def _hex(value: str | None) -> bytearray | None:
    if value is None or len(value) % 2 or re.fullmatch(r"[0-9A-Fa-f]*", value) is None:
        return None
    return bytearray.fromhex(value)


def _power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _resident_span(mapping: dict, start: int, limit: int, page_size: int) -> int:
    if start >= mapping["size"]:
        return 0
    limit = min(limit, mapping["size"] - start)
    n = 0
    while n < limit:
        page = (start + n) // page_size
        if page not in mapping["present"]:
            break
        page_end = min(((start + n) // page_size + 1) * page_size, mapping["size"])
        n = min(limit, page_end - start)
    return n


def _model(text: str) -> dict:
    init = None
    files: dict[str, bytearray] = {}
    maps: dict[str, dict] = {}
    subs: dict[int, dict] = {}
    cqs = []
    for order, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _fields(line)
        if parsed is None:
            continue
        rec, f = parsed
        if rec == "INIT":
            if init is not None:
                continue
            entries = _uint(f.get("entries"))
            page = _uint(f.get("page"))
            head = _uint(f.get("head"))
            if entries is None or page is None or head is None:
                continue
            if not _power_of_two(entries) or page == 0 or head > MASK32:
                continue
            init = {"head": head, "page": page, "entries": entries}
        elif init is None:
            continue
        elif rec == "FILE":
            data = _hex(f.get("hex"))
            if _valid_name(f.get("name")) and data is not None:
                files[f["name"]] = data
        elif rec == "MAP":
            size = _uint(f.get("size"))
            present = _present(f.get("present", ""))
            data = _hex(f.get("data"))
            if _valid_name(f.get("name")) and size is not None and present is not None and data is not None and len(data) == size:
                maps[f["name"]] = {"size": size, "present": present, "data": data}
        elif rec == "SUB":
            sub_id = _uint(f.get("id"))
            off = _uint(f.get("off"))
            mapoff = _uint(f.get("mapoff"))
            length = _uint(f.get("len"))
            link = f.get("link")
            link_id = None if link == "none" else _uint(link)
            if (
                sub_id is not None
                and sub_id > 0
                and f.get("op") in {"READ", "WRITE", "NOP", "TIMEOUT"}
                and _valid_ref(f.get("file"))
                and _valid_ref(f.get("map"))
                and off is not None
                and mapoff is not None
                and length is not None
                and (link == "none" or (link_id is not None and link_id > 0))
            ):
                subs[sub_id] = {
                    "id": sub_id,
                    "op": f["op"],
                    "file": f["file"],
                    "map": f["map"],
                    "off": off,
                    "mapoff": mapoff,
                    "len": length,
                    "link": link_id,
                }
        elif rec == "CQ":
            sub_id = _uint(f.get("id"))
            pos = _uint(f.get("pos"))
            res = _sint(f.get("res"))
            if sub_id is not None and sub_id > 0 and pos is not None and pos <= MASK32 and res is not None:
                cqs.append({"id": sub_id, "pos": pos, "res": res, "order": order})

    if init is None:
        init = {"head": 0, "page": 1, "entries": 1}

    completed: dict[int, int] = {}
    completions = []
    cancelled = 0
    short_io = 0
    for cq in sorted(cqs, key=lambda x: (((x["pos"] - init["head"]) & MASK32), x["order"])):
        if ((cq["pos"] - init["head"]) & MASK32) >= init["entries"]:
            continue
        if cq["id"] in completed:
            continue
        sub = subs.get(cq["id"])
        if sub is None:
            continue
        res = cq["res"]
        if sub["op"] == "TIMEOUT" and sub["link"] in completed and completed[sub["link"]] >= 0:
            res = -125
            cancelled += 1
        copied = 0
        if res >= 0 and sub["op"] in {"READ", "WRITE"}:
            file_data = files.get(sub["file"])
            mapping = maps.get(sub["map"])
            if file_data is not None and mapping is not None and sub["off"] < len(file_data):
                n = min(res, sub["len"], len(file_data) - sub["off"])
                n = min(n, _resident_span(mapping, sub["mapoff"], n, init["page"]))
                if sub["op"] == "READ":
                    mapping["data"][sub["mapoff"] : sub["mapoff"] + n] = file_data[sub["off"] : sub["off"] + n]
                else:
                    file_data[sub["off"] : sub["off"] + n] = mapping["data"][sub["mapoff"] : sub["mapoff"] + n]
                copied = n
            if copied < sub["len"]:
                short_io += 1
        completed[sub["id"]] = res
        completions.append({"id": sub["id"], "op": sub["op"], "res": res, "bytes": copied})

    file_items = [
        {"name": name, "size": len(data), "hex": data.hex(), "digest": _fnv(bytes(data))}
        for name, data in sorted(files.items())
    ]
    map_items = [
        {"name": name, "size": item["size"], "hex": item["data"].hex(), "digest": _fnv(bytes(item["data"]))}
        for name, item in sorted(maps.items())
    ]
    digest_lines = [f"A {len(completions)} {cancelled} {short_io}\n"]
    digest_lines.extend(f"C {c['id']} {c['op']} {c['res']} {c['bytes']}\n" for c in completions)
    digest_lines.extend(f"F {x['name']} {x['size']} {x['hex']}\n" for x in file_items)
    digest_lines.extend(f"M {x['name']} {x['size']} {x['hex']}\n" for x in map_items)
    return {
        "schema": "uring-replay-v1",
        "applied": len(completions),
        "cancelled": cancelled,
        "short_io": short_io,
        "completions": completions,
        "files": file_items,
        "maps": map_items,
        "digest": _fnv("".join(digest_lines).encode()),
    }


def _run_case(name: str, trace: str) -> tuple[dict, dict]:
    RUN_DIR.mkdir(exist_ok=True)
    trace_path = RUN_DIR / f"{name}.trace"
    out_path = RUN_DIR / f"{name}.json"
    trace_path.write_text(trace)
    subprocess.run([str(BIN), str(trace_path), str(out_path)], cwd=APP, check=True)
    return json.loads(out_path.read_text()), _model(trace)


def _hex_bytes(count: int, mul: int = 1, add: int = 0) -> str:
    return bytes(((i * mul + add) % 256 for i in range(count))).hex()


def test_project_builds_documented_binary():
    """The documented make command should produce the replay executable."""
    subprocess.run(["make", "clean"], cwd=APP, check=True)
    subprocess.run(["make"], cwd=APP, check=True)
    assert BIN.exists()


def test_replays_basic_contract_sample():
    """A compact valid trace should produce the documented JSON shape and state."""
    trace = "\n".join(
        [
            "INIT entries=8 page=16 head=4",
            f"FILE name=base hex={_hex_bytes(48)}",
            f"MAP name=buf size=32 present=0,1 data={_hex_bytes(32, 7, 3)}",
            "SUB id=1 op=READ file=base map=buf off=4 mapoff=8 len=12 link=none",
            "SUB id=2 op=WRITE file=base map=buf off=24 mapoff=8 len=10 link=none",
            "SUB id=3 op=NOP file=- map=- off=0 mapoff=0 len=0 link=none",
            "CQ id=1 pos=4 res=12",
            "CQ id=2 pos=5 res=10",
            "CQ id=3 pos=6 res=0",
            "",
        ]
    )
    got, expected = _run_case("basic", trace)
    assert got == expected
    assert re.fullmatch(r"[0-9a-f]{16}", got["digest"])


def test_replays_repaired_sequence_family():
    """A trace family with interleaved records should match the replay model."""
    trace = "\n".join(
        [
            "INIT entries=8 page=16 head=4294967294",
            f"FILE name=alpha hex={_hex_bytes(64, 5, 1)}",
            f"MAP name=work size=48 present=0,1,2 data={bytes([0xAA] * 48).hex()}",
            "SUB id=10 op=READ file=alpha map=work off=0 mapoff=0 len=12 link=none",
            "SUB id=11 op=WRITE file=alpha map=work off=24 mapoff=0 len=8 link=none",
            "SUB id=12 op=WRITE file=alpha map=work off=40 mapoff=4 len=10 link=none",
            "CQ id=12 pos=0 res=10",
            "CQ id=10 pos=4294967294 res=12",
            "CQ id=11 pos=4294967295 res=5",
            "",
        ]
    )
    got, expected = _run_case("sequence", trace)
    assert got == expected


def test_replays_linked_and_bounded_operations():
    """Linked completions and bounded byte movement should be reflected in the report."""
    trace = "\n".join(
        [
            "INIT entries=16 page=8 head=21",
            f"FILE name=beta hex={_hex_bytes(80, 11, 9)}",
            f"MAP name=mapa size=40 present=0,2,3,4 data={_hex_bytes(40, 13, 7)}",
            "SUB id=20 op=READ file=beta map=mapa off=0 mapoff=6 len=12 link=none",
            "SUB id=21 op=TIMEOUT file=- map=- off=0 mapoff=0 len=0 link=20",
            "SUB id=22 op=WRITE file=beta map=mapa off=32 mapoff=16 len=9 link=none",
            "CQ id=21 pos=22 res=-62",
            "CQ id=22 pos=23 res=3",
            "CQ id=20 pos=21 res=10",
            "",
        ]
    )
    got, expected = _run_case("linked", trace)
    assert got == expected


def test_replays_generated_mixed_trace():
    """A generated mixed trace should remain deterministic across many operations."""
    lines = [
        "INIT entries=32 page=16 head=100",
        f"FILE name=main hex={_hex_bytes(160, 17, 4)}",
        f"MAP name=x size=96 present=0,1,3,4,5 data={_hex_bytes(96, 19, 2)}",
        f"MAP name=y size=64 present=0,2,3 data={_hex_bytes(64, 23, 5)}",
    ]
    pos = 100
    cq_lines = []
    for i in range(18):
        op = "READ" if i % 3 != 1 else "WRITE"
        mapping = "x" if i % 2 == 0 else "y"
        lines.append(
            f"SUB id={100 + i} op={op} file=main map={mapping} "
            f"off={(i * 7) % 120} mapoff={(i * 11) % 72} len={5 + (i * 5) % 19} link=none"
        )
        cq_lines.append(f"CQ id={100 + i} pos={pos + i} res={2 + (i * 7) % 21}")
        if i in {5, 11, 14}:
            lines.append(f"SUB id={200 + i} op=TIMEOUT file=- map=- off=0 mapoff=0 len=0 link={100 + i}")
            cq_lines.append(f"CQ id={200 + i} pos={pos + i + 1} res=-62")
    trace = "\n".join(lines + list(reversed(cq_lines)) + [""])
    got, expected = _run_case("mixed", trace)
    assert got == expected


def test_writes_only_the_requested_json_report():
    """The command should write valid JSON at the exact requested output path."""
    trace = "\n".join(
        [
            "INIT entries=4 page=16 head=0",
            f"FILE name=solo hex={_hex_bytes(16)}",
            f"MAP name=one size=16 present=0 data={_hex_bytes(16, 3)}",
            "SUB id=1 op=READ file=solo map=one off=0 mapoff=0 len=4 link=none",
            "CQ id=1 pos=0 res=4",
            "",
        ]
    )
    RUN_DIR.mkdir(exist_ok=True)
    trace_path = RUN_DIR / "path.trace"
    out_path = RUN_DIR / "nested" / "report.json"
    out_path.parent.mkdir(exist_ok=True)
    trace_path.write_text(trace)
    subprocess.run([str(BIN), str(trace_path), str(out_path)], cwd=APP, check=True)
    assert json.loads(out_path.read_text()) == _model(trace)


def test_replays_replaced_records_and_range_residency():
    """Later definitions and range resident pages should drive replay state."""
    trace = "\n".join(
        [
            "INIT entries=16 page=8 head=77",
            f"FILE name=dup hex={bytes([0x11] * 40).hex()}",
            f"MAP name=buf size=32 present=0 data={bytes([0x22] * 32).hex()}",
            f"FILE name=dup hex={_hex_bytes(64, 29, 1)}",
            f"MAP name=buf size=40 present=0-1,3-4 data={_hex_bytes(40, 31, 7)}",
            "SUB id=7 op=READ file=dup map=buf off=2 mapoff=6 len=18 link=none",
            "SUB id=7 op=WRITE file=dup map=buf off=24 mapoff=24 len=12 link=none",
            "SUB id=8 op=READ file=dup map=buf off=0 mapoff=12 len=20 link=none",
            "CQ id=8 pos=78 res=20",
            "CQ id=7 pos=77 res=12",
            "",
        ]
    )
    got, expected = _run_case("replaced_range", trace)
    assert got == expected


def test_replays_layered_redefinitions_with_wraparound():
    """Replacement semantics should compose with ring ordering and cancellation."""
    lines = [
        "INIT entries=32 page=4 head=4294967288",
        f"FILE name=a hex={_hex_bytes(96, 3, 5)}",
        f"FILE name=b hex={_hex_bytes(96, 7, 11)}",
        f"MAP name=m size=64 present=0-3,8-10,12 data={_hex_bytes(64, 5, 19)}",
        f"MAP name=m size=64 present=1-6,9,11-13 data={_hex_bytes(64, 9, 23)}",
    ]
    cqs = []
    for i in range(12):
        op = "READ" if i % 2 == 0 else "WRITE"
        file_name = "a" if i % 3 else "b"
        lines.append(
            f"SUB id={300 + i} op={op} file={file_name} map=m "
            f"off={(i * 13) % 70} mapoff={(i * 5 + 3) % 50} len={6 + (i * 7) % 17} link=none"
        )
        if i in {3, 8}:
            lines.append(
                f"SUB id={300 + i} op={op} file={file_name} map=m "
                f"off={(i * 17) % 74} mapoff={(i * 9 + 1) % 48} len={9 + i} link=none"
            )
        cqs.append(f"CQ id={300 + i} pos={(4294967288 + i * 3) & MASK32} res={4 + (i * 11) % 19}")
        if i in {4, 9}:
            lines.append(f"SUB id={500 + i} op=TIMEOUT file=- map=- off=0 mapoff=0 len=0 link={300 + i}")
            cqs.append(f"CQ id={500 + i} pos={(4294967289 + i * 3) & MASK32} res=-62")
    trace = "\n".join(lines + list(reversed(cqs)) + [""])
    got, expected = _run_case("layered_redefs", trace)
    assert got == expected


def test_filters_outside_ring_and_duplicate_completions():
    """Ring residency and duplicate completion filtering should affect all counters."""
    trace = "\n".join(
        [
            "INIT entries=8 page=8 head=100",
            f"FILE name=ring hex={_hex_bytes(80, 37, 5)}",
            f"MAP name=slots size=48 present=0-2,4-5 data={_hex_bytes(48, 41, 9)}",
            "SUB id=1 op=READ file=ring map=slots off=4 mapoff=6 len=18 link=none",
            "SUB id=2 op=WRITE file=ring map=slots off=24 mapoff=16 len=12 link=none",
            "SUB id=3 op=TIMEOUT file=- map=- off=0 mapoff=0 len=0 link=1",
            "SUB id=4 op=READ file=ring map=slots off=0 mapoff=0 len=4 link=none",
            "CQ id=4 pos=108 res=4",
            "CQ id=2 pos=100 res=9",
            "CQ id=99 pos=100 res=0",
            "CQ id=1 pos=101 res=18",
            "CQ id=1 pos=102 res=2",
            "CQ id=3 pos=103 res=-62",
            "",
        ]
    )
    got, expected = _run_case("ring_duplicates", trace)
    assert got == expected
    assert [c["id"] for c in got["completions"]] == [2, 1, 3]


def test_ignores_prefix_and_invalid_records_without_mutating_state():
    """Invalid records and pre-init noise must not replace earlier valid state."""
    trace = "\n".join(
        [
            f"FILE name=ghost hex={_hex_bytes(8)}",
            "SUB id=44 op=NOP file=- map=- off=0 mapoff=0 len=0 link=none",
            "CQ id=44 pos=0 res=0",
            "INIT entries=6 page=4 head=19",
            "INIT entries=16 page=8 head=19",
            "INIT entries=32 page=4 head=4000",
            f"FILE name={'x' * 64} hex=00",
            f"FILE name=good.name-1 hex={_hex_bytes(32, 43, 2)}",
            "FILE name=good.name-1 hex=abc",
            f"MAP name=wide_map size=64 present=0-2,4-7 data={_hex_bytes(64, 47, 3)}",
            f"MAP name=wide_map size=64 present=7-4 data={bytes([0xEE] * 64).hex()}",
            "SUB id=-3 op=NOP file=- map=- off=0 mapoff=0 len=0 link=none",
            "SUB id=1 op=READ file=good.name-1 map=wide_map off=3 mapoff=15 len=20 link=none",
            "SUB id=2 op=TIMEOUT file=- map=- off=0 mapoff=0 len=0 link=1",
            "SUB id=2 op=BOGUS file=- map=- off=0 mapoff=0 len=0 link=none",
            "CQ id=1 pos=19 res=20",
            "CQ id=2 pos=20 res=-62",
            "CQ id=44 pos=21 res=0",
            "CQ id=1 pos=22 res=5",
            "CQ id=2 pos=35 res=0",
            "",
        ]
    )
    got, expected = _run_case("invalid_prefix", trace)
    assert got == expected
    assert got["cancelled"] == 1
    assert "ghost" not in {item["name"] for item in got["files"]}


def test_replays_large_sparse_ring_and_page_ranges():
    """Large traces should not depend on the small capacities in the seed source."""
    file_name = "long.file-name_0123456789abcdefghijklmnopqrstuv"
    map_name = "wide.map-name_0123456789abcdefghijklmnopqrstuv"
    file_size = 2300
    map_size = 1800
    lines = [
        "INIT entries=1024 page=4 head=4294967000",
        f"FILE name={file_name} hex={_hex_bytes(file_size, 53, 7)}",
        f"MAP name={map_name} size={map_size} present=0-80,96-191,260-449 data={_hex_bytes(map_size, 59, 11)}",
    ]
    cqs = []
    expected_ids = []
    for i in range(180):
        sub_id = 1000 + i
        op = "READ" if i % 4 in {0, 3} else "WRITE"
        lines.append(
            f"SUB id={sub_id} op={op} file={file_name} map={map_name} "
            f"off={(i * 37) % 2100} mapoff={(i * 29) % 1680} len={9 + (i * 13) % 41} link=none"
        )
        if i % 31 == 0:
            lines.append(
                f"SUB id={sub_id} op={op} file={file_name} map={map_name} "
                f"off={(i * 41 + 5) % 2100} mapoff={(i * 17 + 9) % 1680} len={17 + i % 23} link=none"
            )
        pos = (4294967000 + i * 3) & MASK32
        cqs.append(f"CQ id={sub_id} pos={pos} res={4 + (i * 19) % 57}")
        expected_ids.append(sub_id)
        if i % 29 == 7:
            cqs.append(f"CQ id={sub_id} pos={(pos + 1) & MASK32} res=1")
        if i % 37 == 12:
            cqs.append(f"CQ id={9000 + i} pos={(pos + 2) & MASK32} res=0")
    for i in range(35):
        cqs.append(f"CQ id={7000 + i} pos={(4294967000 + 2000 + i) & MASK32} res=0")
    trace = "\n".join(lines + list(reversed(cqs)) + [""])
    got, expected = _run_case("large_sparse", trace)
    assert got == expected
    assert got["applied"] == len(expected_ids)
    assert got["files"][0]["name"] == file_name
