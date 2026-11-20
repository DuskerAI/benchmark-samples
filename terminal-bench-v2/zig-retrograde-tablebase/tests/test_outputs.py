import json
import subprocess
from pathlib import Path

CELLS = "ABCDEFG"
ADJ = {
    0: (1, 3, 4),
    1: (0, 2, 4),
    2: (1, 4, 5),
    3: (0, 4, 6),
    4: (0, 1, 2, 3, 5, 6),
    5: (2, 4, 6),
    6: (3, 4, 5),
}
MIRROR = {0: 2, 1: 1, 2: 0, 3: 5, 4: 4, 5: 3, 6: 6}


def run_build():
    subprocess.run(["/app/scripts/build.sh"], cwd="/app", check=True)
    assert Path("/app/bin/hexbase").exists()


def bit(i):
    return 1 << i


def raw_string(state):
    turn, r, b, blocked = state
    letters = "".join(CELLS[i] for i in range(7) if blocked & bit(i))
    return f"turn={'R' if turn == 0 else 'B'};R={CELLS[r]};B={CELLS[b]};blocked={letters}"


def mirror_state(state):
    turn, r, b, blocked = state
    mask = 0
    for i in range(7):
        if blocked & bit(i):
            mask |= bit(MIRROR[i])
    return (turn, MIRROR[r], MIRROR[b], mask)


def canonical(state):
    return min(raw_string(state), raw_string(mirror_state(state)))


def parse_position(text):
    parts = text.split(";")
    values = {}
    for part in parts:
        if "=" not in part:
            raise ValueError(text)
        key, value = part.split("=", 1)
        if key in values:
            raise ValueError(text)
        values[key] = value
    turn = {"R": 0, "B": 1}[values["turn"]]
    r = CELLS.index(values["R"])
    b = CELLS.index(values["B"])
    blocked = 0
    previous = ""
    for ch in values["blocked"]:
        if ch <= previous:
            raise ValueError(text)
        previous = ch
        blocked |= bit(CELLS.index(ch))
    state = (turn, r, b, blocked)
    assert is_valid(state)
    return state


def is_valid(state):
    turn, r, b, blocked = state
    return turn in (0, 1) and r != b and not (blocked & bit(r)) and not (blocked & bit(b))


def all_states():
    for turn in (0, 1):
        for r in range(7):
            for b in range(7):
                for blocked in range(128):
                    state = (turn, r, b, blocked)
                    if is_valid(state):
                        yield state


def successors(state):
    turn, r, b, blocked = state
    at = r if turn == 0 else b
    other = b if turn == 0 else r
    out = []
    for dest in ADJ[at]:
        if dest == other or (blocked & bit(dest)):
            continue
        if turn == 0:
            out.append((1, dest, b, blocked))
        else:
            out.append((0, r, dest, blocked))
    return out


def build_reference():
    states = list(all_states())
    status = {state: "unknown" for state in states}
    changed = True
    while changed:
        changed = False
        for state in states:
            if status[state] != "unknown":
                continue
            kids = successors(state)
            if not kids:
                status[state] = "loss"
                changed = True
            elif any(status[child] == "loss" for child in kids):
                status[state] = "win"
                changed = True
            elif all(status[child] == "win" for child in kids):
                status[state] = "loss"
                changed = True
    for state in states:
        if status[state] == "unknown":
            status[state] = "draw"
    table = {}
    for state in states:
        key = canonical(state)
        table.setdefault(key, status[state])
        assert table[key] == status[state]
    return table


def canonical_successors(text):
    return {canonical(child) for child in successors(parse_position(text))}


def run_json(*args, output):
    subprocess.run(["/app/bin/hexbase", *args, str(output)], cwd="/app", check=True)
    return json.loads(output.read_text())


def assert_certificate(entry, expected):
    cert = entry["certificate"]
    assert isinstance(cert, list)
    status = entry["status"]
    start = entry["canonical"]
    assert expected[start] == status
    if status == "loss":
        assert cert == []
    elif status == "win":
        assert len(cert) == 1
        assert cert[0] in canonical_successors(start)
        assert expected[cert[0]] == "loss"
    else:
        assert len(cert) >= 2
        assert cert[0] == start
        seen = {cert[0]}
        repeated = False
        for before, after in zip(cert, cert[1:]):
            assert after in canonical_successors(before)
            assert expected[after] == "draw"
            if after in seen:
                repeated = True
                assert after == cert[-1]
            seen.add(after)
        assert repeated


def test_tablebase_is_complete_and_ordered(tmp_path):
    """The generated table contains the full canonical game graph in stable order."""
    run_build()
    expected = build_reference()
    report = run_json("table", output=tmp_path / "table.json")
    positions = report["positions"]
    keys = [entry["canonical"] for entry in positions]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    assert set(keys) == set(expected)
    for entry in positions:
        assert entry["valid"] is True
        assert entry["status"] == expected[entry["canonical"]]


def test_certificates_replay_against_the_game_graph(tmp_path):
    """Outcome certificates are legal witnesses for wins, losses, and recurring play."""
    run_build()
    expected = build_reference()
    report = run_json("table", output=tmp_path / "table.json")
    statuses_seen = {entry["status"] for entry in report["positions"]}
    assert {"win", "loss", "draw"}.issubset(statuses_seen)
    for entry in report["positions"]:
        assert_certificate(entry, expected)


def test_adjudication_uses_canonical_positions(tmp_path):
    """Individual adjudications agree with the table and preserve mirror equivalence."""
    run_build()
    expected = build_reference()
    samples = [
        "turn=R;R=A;B=G;blocked=",
        "turn=B;R=E;B=A;blocked=CG",
        "turn=R;R=D;B=F;blocked=AB",
        "turn=B;R=C;B=D;blocked=EFG",
    ]
    for idx, pos in enumerate(samples):
        entry = run_json("adjudicate", pos, output=tmp_path / f"one-{idx}.json")
        assert entry["valid"] is True
        assert entry["canonical"] == canonical(parse_position(pos))
        assert entry["status"] == expected[entry["canonical"]]
        assert_certificate(entry, expected)
    left = run_json("adjudicate", "turn=R;R=A;B=G;blocked=DF", output=tmp_path / "left.json")
    right = run_json("adjudicate", "turn=R;R=C;B=G;blocked=DF", output=tmp_path / "right.json")
    assert left["canonical"] == right["canonical"]
    assert left["status"] == right["status"]


def test_invalid_positions_are_rejected(tmp_path):
    """Malformed and unreachable board descriptions are rejected without a table entry."""
    run_build()
    bad_positions = [
        "turn=R;R=A;B=A;blocked=",
        "turn=B;R=H;B=C;blocked=",
        "turn=R;R=B;B=C;blocked=B",
        "turn=R;R=A;B=C;blocked=CC",
        "turn=R;R=A;B=C;blocked=CA",
        "turn=X;R=A;B=C;blocked=",
    ]
    for idx, pos in enumerate(bad_positions):
        report = run_json("adjudicate", pos, output=tmp_path / f"bad-{idx}.json")
        assert report["valid"] is False
        assert isinstance(report.get("error"), str) and report["error"]
