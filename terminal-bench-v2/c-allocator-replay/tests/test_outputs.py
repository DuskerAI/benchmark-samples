"""Behavioral verifier for the arena replay tool."""

import json
import re
import subprocess
from pathlib import Path

APP = Path("/app/task_file")
BIN = APP / "build" / "arena_replay"
RUN_DIR = Path("/tmp/arena-replay-verifier")


def _token(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,31}", value))


def _hex(value):
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", value))


def _fnv(lines):
    h = 0xCBF29CE484222325
    for line in lines:
        for b in line.encode():
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def _parse_record(line):
    parts = line.split()
    if not parts:
        return None, None
    seen = {}
    for part in parts[1:]:
        if "=" not in part:
            return parts[0], None
        k, v = part.split("=", 1)
        if k in seen:
            return parts[0], None
        seen[k] = v
    return parts[0], seen


def _uint(text):
    if not re.fullmatch(r"[0-9]+", text or ""):
        return None
    value = int(text)
    return value if value > 0 else None


def _model(raw):
    last_nl = raw.rfind(b"\n")
    ignored_tail = last_nl != len(raw) - 1
    complete = b"" if last_nl < 0 else raw[: last_nl + 1]
    lines = complete.decode("utf-8").splitlines()
    arena = None
    free = []
    live = {}
    last_gen = {}
    queue = []
    applied = rejected = high_water = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        rec, fields = _parse_record(line)
        bad = fields is None
        if rec == "ARENA" and arena is None and not bad:
            if set(fields) != {"name", "size", "page", "quarantine"}:
                bad = True
            else:
                size = _uint(fields["size"])
                page = _uint(fields["page"])
                q = _uint(fields["quarantine"])
                if q is None and fields["quarantine"] == "0":
                    q = 0
                if not _token(fields["name"]) or not size or not page or size % page:
                    bad = True
                else:
                    arena = {
                        "name": fields["name"],
                        "size": size,
                        "page_size": page,
                        "quarantine_limit": q,
                    }
                    free = [{"addr": 0, "size": size}]
        elif rec == "ALLOC" and arena and not bad:
            if set(fields) != {"id", "gen", "size", "align", "tag"}:
                bad = True
            else:
                ident = fields["id"]
                gen = _uint(fields["gen"])
                size = _uint(fields["size"])
                align = _uint(fields["align"])
                tag = fields["tag"]
                if (
                    not _token(ident)
                    or not gen
                    or not size
                    or not align
                    or align & (align - 1)
                    or align > 64
                    or not _hex(tag)
                    or ident in live
                    or gen != last_gen.get(ident, 0) + 1
                    or size > arena["page_size"]
                ):
                    bad = True
                else:
                    chosen = None
                    for idx, block in enumerate(free):
                        start = block["addr"]
                        end = start + block["size"]
                        p = ((start + align - 1) // align) * align
                        while p + size <= end:
                            if p // arena["page_size"] == (p + size - 1) // arena["page_size"]:
                                chosen = (idx, p)
                                break
                            p = ((p // arena["page_size"]) + 1) * arena["page_size"]
                            p = ((p + align - 1) // align) * align
                        if chosen:
                            break
                    if not chosen:
                        bad = True
                    else:
                        idx, addr = chosen
                        block = free.pop(idx)
                        before = addr - block["addr"]
                        after_addr = addr + size
                        after = block["addr"] + block["size"] - after_addr
                        inserts = []
                        if before:
                            inserts.append({"addr": block["addr"], "size": before})
                        if after:
                            inserts.append({"addr": after_addr, "size": after})
                        free[idx:idx] = inserts
                        live[ident] = {
                            "id": ident,
                            "gen": gen,
                            "tag": tag,
                            "addr": addr,
                            "size": size,
                            "align": align,
                        }
                        last_gen[ident] = gen
                        high_water = max(high_water, addr + size)
                        applied += 1
        elif rec == "FREE" and arena and not bad:
            if set(fields) != {"id", "gen", "tag"}:
                bad = True
            else:
                ident = fields["id"]
                gen = _uint(fields["gen"])
                current = live.get(ident)
                if not _token(ident) or not gen or not current or current["gen"] != gen or current["tag"] != fields["tag"]:
                    bad = True
                else:
                    item = live.pop(ident)
                    queue.append({k: item[k] for k in ("id", "gen", "tag", "addr", "size")})
                    while len(queue) > arena["quarantine_limit"]:
                        old = queue.pop(0)
                        free.append({"addr": old["addr"], "size": old["size"]})
                        free.sort(key=lambda x: x["addr"])
                        merged = []
                        for b in free:
                            if merged and merged[-1]["addr"] + merged[-1]["size"] >= b["addr"]:
                                merged[-1]["size"] = max(
                                    merged[-1]["addr"] + merged[-1]["size"], b["addr"] + b["size"]
                                ) - merged[-1]["addr"]
                            else:
                                merged.append(dict(b))
                        free = merged
                    applied += 1
        else:
            bad = True
        if bad:
            rejected += 1
    if arena is None:
        raise AssertionError("trace fixture did not initialize an arena")
    live_list = sorted(live.values(), key=lambda x: (x["addr"], x["id"]))
    free.sort(key=lambda x: x["addr"])
    digest_lines = [f"A {arena['name']} {arena['size']} {arena['page_size']} {arena['quarantine_limit']}\n"]
    digest_lines += [
        f"L {x['id']} {x['gen']} {x['tag']} {x['addr']} {x['size']} {x['align']}\n" for x in live_list
    ]
    digest_lines += [f"F {x['addr']} {x['size']}\n" for x in free]
    digest_lines += [f"Q {x['id']} {x['gen']} {x['tag']} {x['addr']} {x['size']}\n" for x in queue]
    digest_lines.append(f"H {high_water} R {rejected} T {1 if ignored_tail else 0}\n")
    return {
        "arena": arena,
        "applied": applied,
        "rejected": rejected,
        "ignored_tail": ignored_tail,
        "live": live_list,
        "free": free,
        "quarantine": queue,
        "high_water": high_water,
        "digest": _fnv(digest_lines),
    }


def _run_trace(name, content):
    RUN_DIR.mkdir(exist_ok=True)
    trace = RUN_DIR / f"{name}.art"
    out = RUN_DIR / f"{name}.json"
    raw = content if isinstance(content, bytes) else content.encode()
    trace.write_bytes(raw)
    subprocess.run([str(BIN), str(trace), str(out)], cwd=APP, check=True)
    return json.loads(out.read_text()), _model(raw)


def test_builds_with_project_makefile():
    """The project should build the documented binary with the local makefile."""
    subprocess.run(["make", "clean"], cwd=APP, check=True)
    subprocess.run(["make"], cwd=APP, check=True)
    assert BIN.exists()


def test_replays_reference_scenarios():
    """Representative complete traces should match the replay contract exactly."""
    traces = {
        "alpha": """ARENA quarantine=1 page=64 size=256 name=alpha
ALLOC tag=aa align=8 size=13 gen=1 id=a
ALLOC id=b tag=bb gen=1 align=32 size=40
FREE tag=aa id=a gen=1
ALLOC id=c gen=1 tag=cc size=20 align=16
FREE id=b tag=bb gen=1
ALLOC id=a gen=2 size=12 align=8 tag=dd
FREE id=c gen=1 tag=bad
FREE tag=cc gen=1 id=c
ALLOC align=64 id=d tag=ee size=8 gen=1
""",
        "beta": """# generated operational sample
ARENA name=beta size=384 page=64 quarantine=2
ALLOC id=x gen=1 size=33 align=32 tag=10
ALLOC id=y gen=1 size=16 align=16 tag=20
ALLOC id=z gen=1 size=9 align=8 tag=30
FREE id=x gen=1 tag=10
ALLOC id=w gen=1 size=40 align=32 tag=40
FREE id=y gen=1 tag=20
FREE id=z gen=1 tag=30
ALLOC id=x gen=2 size=24 align=16 tag=50
ALLOC id=q gen=1 size=65 align=8 tag=60
ALLOC id=r gen=1 size=7 align=64 tag=70
""",
    }
    for name, text in traces.items():
        got, expected = _run_trace(name, text)
        assert got == expected


def test_generated_trace_family():
    """A longer generated trace should produce the same final report as the contract model."""
    lines = ["ARENA name=gamma size=512 page=128 quarantine=3"]
    for i in range(18):
        ident = f"h{i}"
        lines.append(f"ALLOC align={1 << (i % 6)} id={ident} tag={i + 10:x} size={7 + (i * 11) % 53} gen=1")
        if i % 3 == 2:
            old = f"h{i - 2}"
            lines.append(f"FREE tag={i + 8:x} gen=1 id={old}")
        if i % 5 == 4:
            lines.append(f"ALLOC id={ident} gen=2 size=12 align=8 tag=ff{i:x}")
    lines.extend([
        "FREE id=h9 gen=1 tag=13",
        "FREE id=h10 gen=1 tag=14",
        "ALLOC id=h0 gen=2 size=31 align=32 tag=abc",
        "ALLOC id=h12 gen=1 size=17 align=16 tag=22",
    ])
    got, expected = _run_trace("gamma", "\n".join(lines) + "\n")
    assert got == expected


def test_ignores_incomplete_final_record():
    """A trace with trailing bytes after the last newline should replay only complete records."""
    raw = (
        b"ARENA name=delta size=256 page=64 quarantine=0\n"
        b"ALLOC id=a gen=1 size=60 align=8 tag=aa\n"
        b"FREE id=a gen=1 tag=aa\n"
        b"ALLOC id=b gen=1 size=32 align=32 tag=bb"
    )
    got, expected = _run_trace("delta", raw)
    assert got == expected


def test_report_path_is_the_only_required_output():
    """The command should write valid JSON exactly to the requested output path."""
    trace = """ARENA name=epsilon size=128 page=64 quarantine=1
ALLOC id=a gen=1 size=16 align=16 tag=aa
"""
    RUN_DIR.mkdir(exist_ok=True)
    trace_path = RUN_DIR / "epsilon.art"
    nested = RUN_DIR / "nested" / "report.json"
    nested.parent.mkdir(exist_ok=True)
    trace_path.write_text(trace)
    subprocess.run([str(BIN), str(trace_path), str(nested)], cwd=APP, check=True)
    data = json.loads(nested.read_text())
    assert data == _model(trace.encode())
