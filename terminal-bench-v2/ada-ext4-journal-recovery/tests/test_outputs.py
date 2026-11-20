import json
import struct
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

APP = Path("/app/task_file")
BIN = APP / "bin" / "fsrecover"


@dataclass
class Inode:
    mode: int
    links: int
    size: int
    first: int
    blocks: int
    next_orphan: int = 0


def u16(value):
    return struct.pack("<H", value)


def u32(value):
    return struct.pack("<I", value)


def inode_payload(inode):
    return struct.pack(
        "<BBIHHH",
        inode.mode,
        inode.links,
        inode.size,
        inode.first,
        inode.blocks,
        inode.next_orphan,
    )


def bitmap_bytes(blocks, block_count):
    raw = bytearray((block_count + 7) // 8)
    for block in blocks:
        raw[block // 8] |= 1 << (block % 8)
    return bytes(raw)


def base_image(groups, inodes, allocated, dirs, block_count=64, orphan_head=0):
    out = bytearray(b"MFS1")
    out += u16(len(groups)) + u16(len(inodes)) + u16(block_count) + u16(len(dirs)) + u16(orphan_head)
    for gid, first, count, free_hint, used_hint in groups:
        out += u16(gid) + u16(first) + u16(count) + u16(free_hint) + u16(used_hint)
    for ino in sorted(inodes):
        out += u16(ino) + inode_payload(inodes[ino])
    out += bitmap_bytes(allocated, block_count)
    for parent, child, name in dirs:
        encoded = name.encode("ascii")
        out += u16(parent) + u16(child) + bytes([len(encoded)]) + encoded
    return bytes(out)


def upd(kind, key, payload):
    return bytes([kind]) + u16(key) + u16(len(payload)) + payload


def dir_payload(used, parent, child, name):
    encoded = name.encode("ascii")
    return bytes([1 if used else 0]) + u16(parent) + u16(child) + bytes([len(encoded)]) + encoded


def revoke_payload(kind, key):
    return bytes([kind]) + u16(key)


def transaction(txid, updates, valid=True, truncate=0):
    body = bytearray([0xD1]) + u32(txid) + u16(len(updates)) + b"".join(updates)
    checksum = sum(body) & 0xFFFFFFFF
    if not valid:
        checksum ^= 0x01020304
    full = bytes(body) + bytes([0xC0]) + u32(txid) + u32(checksum)
    if truncate:
        return full[:-truncate]
    return full


def journal_image(*txs):
    return b"JNL1" + b"".join(txs)


def name_ok(name):
    return 0 < len(name) <= 32 and "/" not in name and "\x00" not in name


def apply_update(state, kind, key, payload):
    if kind == 1 and len(payload) >= 12:
        mode, links, size, first, blocks, next_orphan = struct.unpack("<BBIHHH", payload[:12])
        if mode == 0:
            state["inodes"].pop(key, None)
        else:
            state["inodes"][key] = Inode(mode, links, size, first, blocks, next_orphan)
    elif kind == 2 and payload:
        if payload[0]:
            state["bitmap"].add(key)
        else:
            state["bitmap"].discard(key)
    elif kind == 3 and len(payload) >= 6:
        used = bool(payload[0])
        parent, child = struct.unpack("<HH", payload[1:5])
        length = payload[5]
        if 6 + length <= len(payload):
            name = payload[6 : 6 + length].decode("ascii")
            state["dirs"][key] = (used, parent, child, name)
    elif kind == 4 and len(payload) >= 8:
        first, count, free_hint, _used_hint = struct.unpack("<HHHH", payload[:8])
        state["groups"][key] = [key, first, count, free_hint, _used_hint]


def parse_base(raw):
    assert raw[:4] == b"MFS1"
    pos = 4
    group_count, inode_count, block_count, dir_count, orphan_head = struct.unpack_from("<HHHHH", raw, pos)
    pos += 10
    groups = {}
    for _ in range(group_count):
        gid, first, count, free_hint, used_hint = struct.unpack_from("<HHHHH", raw, pos)
        pos += 10
        groups[gid] = [gid, first, count, free_hint, used_hint]
    inodes = {}
    for _ in range(inode_count):
        ino = struct.unpack_from("<H", raw, pos)[0]
        mode, links, size, first, blocks, next_orphan = struct.unpack_from("<BBIHHH", raw, pos + 2)
        pos += 14
        if mode:
            inodes[ino] = Inode(mode, links, size, first, blocks, next_orphan)
    bitmap_len = (block_count + 7) // 8
    bitmap = {
        block
        for block in range(block_count)
        if raw[pos + block // 8] & (1 << (block % 8))
    }
    pos += bitmap_len
    dirs = {}
    for idx in range(1, dir_count + 1):
        parent, child = struct.unpack_from("<HH", raw, pos)
        length = raw[pos + 4]
        pos += 5
        name = raw[pos : pos + length].decode("ascii")
        pos += length
        dirs[idx] = (True, parent, child, name)
    return {
        "groups": groups,
        "inodes": inodes,
        "bitmap": bitmap,
        "dirs": dirs,
        "block_count": block_count,
        "orphan_head": orphan_head,
        "valid": 0,
        "ignored": 0,
    }


def replay(state, raw):
    assert raw[:4] == b"JNL1"
    pos = 4
    while pos < len(raw):
        try:
            pos, applied = replay_one_transaction(state, raw, pos)
        except (IndexError, struct.error, ValueError):
            state["ignored"] += 1
            break
        if not applied:
            state["ignored"] += 1
            break


def replay_one_transaction(state, raw, pos):
    parsed = parse_committed_transaction(raw, pos)
    if parsed is None:
        return pos, False
    next_pos, records = parsed
    apply_transaction(state, records)
    state["valid"] += 1
    return next_pos, True


def parse_committed_transaction(raw, pos):
    start = pos
    if raw[pos] != 0xD1:
        return None
    pos += 1
    txid = struct.unpack_from("<I", raw, pos)[0]
    pos += 4
    count = struct.unpack_from("<H", raw, pos)[0]
    pos += 2
    records = []
    for _ in range(count):
        kind = raw[pos]
        key = struct.unpack_from("<H", raw, pos + 1)[0]
        length = struct.unpack_from("<H", raw, pos + 3)[0]
        pos += 5
        if pos + length > len(raw):
            raise ValueError
        payload = raw[pos : pos + length]
        pos += length
        records.append((kind, key, payload))
    commit_pos = pos
    if pos + 9 > len(raw) or raw[pos] != 0xC0:
        raise ValueError
    pos += 1
    commit_txid = struct.unpack_from("<I", raw, pos)[0]
    pos += 4
    got = struct.unpack_from("<I", raw, pos)[0]
    pos += 4
    want = sum(raw[start:commit_pos]) & 0xFFFFFFFF
    if commit_txid != txid or got != want:
        return None
    return pos, records


def apply_transaction(state, records):
    revoked = {
        (payload[0], struct.unpack_from("<H", payload, 1)[0])
        for kind, _key, payload in records
        if kind == 5 and len(payload) >= 3
    }
    for kind, key, payload in records:
        if kind != 5 and (kind, key) not in revoked:
            apply_update(state, kind, key, payload)


def valid_dirs_and_refs(state):
    valid = {}
    refs = {ino: 0 for ino in state["inodes"]}
    if state["inodes"].get(1, Inode(0, 0, 0, 0, 0)).mode == 2:
        refs[1] = 1
    for idx, (used, parent, child, name) in state["dirs"].items():
        if (
            used
            and name_ok(name)
            and parent in state["inodes"]
            and child in state["inodes"]
            and state["inodes"][parent].mode == 2
        ):
            valid[idx] = (used, parent, child, name)
            refs[child] = refs.get(child, 0) + 1
    return valid, refs


def finish(state):
    state["dirs"], refs = valid_dirs_and_refs(state)
    seen = set()
    cur = state["orphan_head"]
    while cur and cur not in seen and cur in state["inodes"]:
        seen.add(cur)
        inode = state["inodes"][cur]
        nxt = inode.next_orphan
        if refs.get(cur, 0) == 0:
            for block in range(inode.first, inode.first + inode.blocks):
                state["bitmap"].discard(block)
            del state["inodes"][cur]
        cur = nxt
    for ino, inode in state["inodes"].items():
        for block in range(inode.first, inode.first + inode.blocks):
            state["bitmap"].add(block)
    state["dirs"], refs = valid_dirs_and_refs(state)
    for ino, inode in list(state["inodes"].items()):
        state["inodes"][ino] = replace(inode, links=refs.get(ino, 0))


def expected(base_raw, journal_raw):
    state = parse_base(base_raw)
    replay(state, journal_raw)
    finish(state)
    groups = []
    for gid in sorted(state["groups"]):
        _gid, first, count, _free_hint, _used_hint = state["groups"][gid]
        free = sum(1 for block in range(first, first + count) if block not in state["bitmap"])
        groups.append({"id": gid, "free_blocks": free})
    by_child = {}
    for _used, parent, child, name in state["dirs"].values():
        by_child.setdefault(child, []).append(f"{parent}/{name}")
    inodes = []
    for ino, inode in sorted(state["inodes"].items()):
        inodes.append(
            {
                "ino": ino,
                "mode": "dir" if inode.mode == 2 else "file",
                "links": inode.links,
                "size": inode.size,
                "blocks": list(range(inode.first, inode.first + inode.blocks)),
                "names": sorted(by_child.get(ino, [])),
            }
        )
    return {
        "summary": {
            "valid_transactions": state["valid"],
            "ignored_transactions": state["ignored"],
            "allocated_blocks": sorted(state["bitmap"]),
            "groups": groups,
        },
        "inodes": inodes,
    }


@pytest.fixture(scope="session", autouse=True)
def build_binary():
    subprocess.run(["make", "clean"], cwd=APP, check=True)
    subprocess.run(["make"], cwd=APP, check=True)


def run_case(tmp_path, base_raw, journal_raw):
    work = tmp_path / "case"
    out = tmp_path / "out"
    work.mkdir()
    (work / "base.img").write_bytes(base_raw)
    (work / "journal.img").write_bytes(journal_raw)
    subprocess.run(
        [str(BIN), "--base", str(work / "base.img"), "--journal", str(work / "journal.img"), "--out", str(out)],
        check=True,
        cwd=APP,
    )
    assert sorted(p.name for p in out.iterdir()) == ["inodes.ndjson", "summary.json"]
    summary = json.loads((out / "summary.json").read_text())
    inodes = [json.loads(line) for line in (out / "inodes.ndjson").read_text().splitlines() if line.strip()]
    assert summary == expected(base_raw, journal_raw)["summary"]
    assert inodes == expected(base_raw, journal_raw)["inodes"]


def test_rebuilds_report_after_valid_replay_and_metadata_reconciliation(tmp_path):
    """Committed metadata updates must feed final link, block, and group reconciliation."""
    base = base_image(
        [(0, 0, 32, 2, 0), (1, 32, 32, 31, 0)],
        {
            1: Inode(2, 9, 0, 0, 0),
            2: Inode(1, 7, 12, 5, 1),
            3: Inode(1, 0, 33, 6, 1),
        },
        {5, 6},
        [(1, 2, "alpha")],
        orphan_head=3,
    )
    journal = journal_image(
        transaction(
            11,
            [
                upd(1, 2, inode_payload(Inode(1, 1, 70, 8, 2))),
                upd(2, 5, b"\x00"),
                upd(2, 8, b"\x01"),
                upd(2, 9, b"\x01"),
            ],
        )
    )
    run_case(tmp_path, base, journal)


def test_stops_at_uncommitted_tail_without_using_its_records(tmp_path):
    """A bad journal tail must be counted and must not leak any staged records."""
    base = base_image(
        [(0, 0, 48, 40, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 1, 10, 4, 1)},
        {4},
        [(1, 2, "kept")],
        block_count=48,
    )
    good = transaction(3, [upd(1, 2, inode_payload(Inode(1, 1, 18, 4, 1)))])
    bad = transaction(
        4,
        [
            upd(1, 4, inode_payload(Inode(1, 1, 99, 12, 1))),
            upd(3, 2, dir_payload(True, 1, 4, "bad/name")),
        ],
        valid=False,
    )
    run_case(tmp_path, base, journal_image(good, bad))


def test_keeps_prior_commits_and_ignores_transactions_after_bad_record(tmp_path):
    """Valid commits before the first malformed transaction must survive, and later records must not replay."""
    base = base_image(
        [(0, 0, 40, 40, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 1, 8, 3, 1)},
        {3},
        [(1, 2, "original")],
        block_count=40,
    )
    first = transaction(
        70,
        [
            upd(1, 2, inode_payload(Inode(1, 1, 22, 4, 2))),
            upd(2, 3, b"\x00"),
            upd(2, 4, b"\x01"),
            upd(2, 5, b"\x01"),
            upd(3, 1, dir_payload(True, 1, 2, "after-first")),
        ],
    )
    bad = transaction(
        71,
        [
            upd(1, 2, inode_payload(Inode(1, 1, 99, 18, 1))),
            upd(3, 1, dir_payload(True, 1, 2, "bad-tail")),
        ],
        valid=False,
    )
    never = transaction(72, [upd(3, 1, dir_payload(True, 1, 2, "too-late"))])
    run_case(tmp_path, base, journal_image(first, bad, never))


def test_transaction_local_cancellation_and_reference_filtering(tmp_path):
    """Revoked records and unusable directory references must not appear in reports."""
    base = base_image(
        [(0, 0, 64, 60, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 1, 15, 7, 1)},
        {7},
        [(1, 2, "old")],
    )
    journal = journal_image(
        transaction(
            21,
            [
                upd(1, 5, inode_payload(Inode(1, 1, 44, 20, 1))),
                upd(3, 2, dir_payload(True, 1, 5, "ghost")),
                upd(5, 0, revoke_payload(1, 5)),
            ],
        )
    )
    run_case(tmp_path, base, journal)


def test_revocation_matches_kind_and_key_across_multiple_record_types(tmp_path):
    """Same-transaction revocations must cancel every matching update without canceling unrelated keys."""
    base = base_image(
        [(0, 0, 64, 60, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 1, 10, 6, 1), 3: Inode(1, 1, 11, 9, 1)},
        {6, 9},
        [(1, 2, "two"), (1, 3, "three")],
    )
    journal = journal_image(
        transaction(
            74,
            [
                upd(2, 6, b"\x00"),
                upd(2, 9, b"\x00"),
                upd(3, 1, dir_payload(True, 1, 2, "z-renamed")),
                upd(3, 2, dir_payload(True, 1, 3, "a-renamed")),
                upd(5, 0, revoke_payload(2, 6)),
                upd(5, 0, revoke_payload(3, 1)),
            ],
        )
    )
    run_case(tmp_path, base, journal)


def test_orphan_chain_truncation_is_deterministic(tmp_path):
    """A cyclic orphan chain is walked once before allocation and link reconciliation."""
    base = base_image(
        [(0, 0, 64, 60, 0)],
        {
            1: Inode(2, 1, 0, 0, 0),
            2: Inode(1, 1, 9, 3, 1),
            3: Inode(1, 0, 11, 10, 1, 4),
            4: Inode(1, 0, 12, 11, 1, 3),
        },
        {3, 10, 11},
        [(1, 2, "live")],
        orphan_head=3,
    )
    run_case(tmp_path, base, journal_image())


def test_directory_filtering_repeats_after_orphan_removal(tmp_path):
    """References through an orphaned directory must be removed before final link reconciliation."""
    base = base_image(
        [(0, 0, 64, 60, 0)],
        {
            1: Inode(2, 1, 0, 0, 0),
            2: Inode(2, 0, 0, 0, 0, 4),
            3: Inode(1, 1, 13, 12, 1),
            4: Inode(1, 0, 15, 20, 1, 0),
        },
        {12, 20},
        [(2, 3, "via-orphan")],
        orphan_head=2,
    )
    run_case(tmp_path, base, journal_image())


def test_group_identity_deletion_and_recreated_references(tmp_path):
    """Non-contiguous group ids, inode deletion, and directory rewrites must compose."""
    base = base_image(
        [(7, 30, 10, 8, 0), (2, 0, 30, 25, 0)],
        {
            1: Inode(2, 4, 0, 0, 0),
            2: Inode(1, 3, 12, 4, 1),
            3: Inode(1, 2, 9, 5, 1),
            4: Inode(1, 1, 18, 31, 2),
        },
        {4, 5, 31, 32, 38},
        [(1, 2, "zeta"), (1, 3, "gone"), (1, 4, "omega")],
        block_count=40,
    )
    journal = journal_image(
        transaction(
            31,
            [
                upd(1, 3, inode_payload(Inode(0, 0, 0, 0, 0))),
                upd(3, 2, dir_payload(False, 1, 3, "gone")),
                upd(3, 4, dir_payload(True, 1, 4, "beta")),
                upd(4, 5, struct.pack("<HHHH", 20, 10, 99, 0)),
                upd(4, 2, struct.pack("<HHHH", 0, 20, 99, 0)),
            ],
        ),
        transaction(32, [upd(2, 38, b"\x00")]),
        transaction(
            33,
            [
                upd(1, 9, inode_payload(Inode(1, 1, 80, 39, 1))),
                upd(4, 9, struct.pack("<HHHH", 35, 5, 0, 0)),
            ],
            valid=False,
        ),
    )
    run_case(tmp_path, base, journal)


def test_names_are_sorted_lexicographically_not_by_directory_slot(tmp_path):
    """All names for an inode must be emitted in lexical order regardless of slot insertion order."""
    base = base_image(
        [(0, 0, 32, 25, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(2, 1, 0, 0, 0), 3: Inode(1, 2, 18, 10, 1)},
        {10},
        [(2, 3, "zulu"), (1, 3, "beta"), (1, 3, "alpha"), (2, 3, "aardvark")],
        block_count=32,
    )
    journal = journal_image(transaction(76, [upd(3, 5, dir_payload(True, 1, 3, "middle"))]))
    run_case(tmp_path, base, journal)


def test_group_free_blocks_are_recomputed_from_final_allocations(tmp_path):
    """Group free counts must ignore stale hints and use final block allocation after replay and orphan cleanup."""
    base = base_image(
        [(4, 0, 8, 123, 0), (9, 8, 8, 456, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 0, 12, 2, 2, 3), 3: Inode(1, 0, 10, 9, 2, 0)},
        {2, 3, 9, 10, 15},
        [],
        block_count=16,
        orphan_head=2,
    )
    journal = journal_image(
        transaction(
            78,
            [
                upd(2, 15, b"\x00"),
                upd(1, 4, inode_payload(Inode(1, 1, 20, 6, 3))),
                upd(3, 1, dir_payload(True, 1, 4, "kept")),
            ],
        )
    )
    run_case(tmp_path, base, journal)


def test_interleaved_replay_recomputes_from_the_final_graph(tmp_path):
    """Later transactions must be applied before final directory and orphan cleanup."""
    base = base_image(
        [(3, 0, 16, 0, 0), (1, 16, 16, 0, 0)],
        {
            1: Inode(2, 1, 0, 0, 0),
            2: Inode(2, 1, 0, 2, 1),
            3: Inode(1, 0, 10, 10, 2, 5),
            5: Inode(1, 0, 12, 12, 1, 0),
            6: Inode(1, 1, 30, 20, 2),
        },
        {2, 10, 11, 12, 20, 21, 23},
        [(1, 2, "sub"), (2, 6, "late"), (1, 5, "keep")],
        block_count=32,
        orphan_head=3,
    )
    journal = journal_image(
        transaction(
            41,
            [
                upd(3, 4, dir_payload(True, 2, 6, "aardvark")),
                upd(3, 5, dir_payload(True, 99, 6, "badparent")),
                upd(2, 23, b"\x00"),
            ],
        ),
        transaction(
            42,
            [
                upd(1, 5, inode_payload(Inode(1, 1, 16, 14, 1))),
                upd(3, 6, dir_payload(True, 1, 5, "rescued")),
                upd(5, 0, revoke_payload(3, 6)),
            ],
        ),
        transaction(
            43,
            [
                upd(3, 3, dir_payload(False, 1, 5, "keep")),
                upd(3, 7, dir_payload(True, 1, 6, "alpha")),
                upd(3, 8, dir_payload(True, 2, 6, "beta")),
            ],
        ),
        transaction(44, [upd(1, 6, inode_payload(Inode(0, 0, 0, 0, 0)))], truncate=2),
    )
    run_case(tmp_path, base, journal)


def test_json_escaping_for_permitted_directory_name_bytes(tmp_path):
    """Permitted quote, backslash, and control bytes in names must remain valid JSON strings."""
    tricky = 'quote"and\\slash\nwith\ttab\x1fend'
    base = base_image(
        [(0, 0, 32, 30, 0)],
        {1: Inode(2, 1, 0, 0, 0), 2: Inode(1, 1, 19, 6, 1)},
        {6},
        [(1, 2, tricky)],
        block_count=32,
    )
    run_case(tmp_path, base, journal_image())


def test_wide_u16_fields_and_large_transaction_replay(tmp_path):
    """Unsigned 16-bit identifiers, counts, and transaction sizes must remain valid beyond small fixtures."""
    dirs = [(1, 2, "") for _ in range(2049)] + [(1, 1500, "wide")]
    base = base_image(
        [(129, 4090, 16, 0, 0), (2, 0, 32, 0, 0)],
        {
            1: Inode(2, 1, 0, 0, 0),
            1500: Inode(1, 1, 19, 4097, 1),
        },
        {4097},
        dirs,
        block_count=4112,
    )
    updates = [upd(2, 4097, b"\x01") for _ in range(258)]
    updates.append(upd(1, 1500, inode_payload(Inode(1, 1, 321, 4097, 2))))
    run_case(tmp_path, base, journal_image(transaction(61, updates)))


def test_all_stated_invalid_directory_names_are_filtered(tmp_path):
    """Empty, slash, NUL, and overlong directory names must not create references."""
    base = base_image(
        [(0, 0, 64, 50, 0)],
        {
            1: Inode(2, 1, 0, 0, 0),
            2: Inode(1, 1, 11, 4, 1),
            3: Inode(1, 1, 12, 5, 1),
            4: Inode(1, 1, 13, 6, 1),
            5: Inode(1, 1, 14, 7, 1),
            6: Inode(1, 1, 15, 8, 1),
        },
        {4, 5, 6, 7, 8},
        [(1, 2, "kept"), (1, 3, ""), (1, 4, "bad/name"), (1, 5, "nul\x00name"), (1, 6, "x" * 33)],
        block_count=64,
    )
    journal = journal_image(
        transaction(
            51,
            [
                upd(3, 2, dir_payload(True, 1, 3, "")),
                upd(3, 3, dir_payload(True, 1, 4, "bad/name")),
                upd(3, 4, dir_payload(True, 1, 5, "nul\x00name")),
                upd(3, 5, dir_payload(True, 1, 6, "x" * 33)),
            ],
        )
    )
    run_case(tmp_path, base, journal)
