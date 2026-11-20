import hashlib
import json
import subprocess
from pathlib import Path


APP = "/app/arbiter.exs"


def invoke(tmp_path: Path, records: list[str], mode: str = "adjudicate", count: int = 0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    event_file = tmp_path / "match.events"
    report_file = tmp_path / "report.json"
    event_file.write_text("\n".join(records) + "\n")
    if mode == "adjudicate":
        command = ["elixir", APP, "adjudicate", str(event_file), str(report_file)]
    else:
        state_file = tmp_path / "match.state"
        command = ["elixir", APP, "checkpoint", str(event_file), str(count), str(state_file), str(report_file)]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    return json.loads(report_file.read_text()), event_file, report_file


def position(turn, white, black, wbar=0, bbar=0, woff=0, boff=0):
    return f"POSITION {turn} {white} {black} {wbar} {bbar} {woff} {boff}"


def test_adjudications_one(tmp_path):
    """The official accepts forced play and records the canonical event history."""
    rows = [
        "MATCH 0 1",
        "GAME m1",
        position("W", "1x14", "18x2,20x13", wbar=1),
        "ROLL 1 6",
        "PLAY W 25/19",
    ]
    report, _, _ = invoke(tmp_path, rows)
    assert report["status"] == "ok"
    assert report["accepted"] == len(rows)
    assert report["current"] == {"id": "m1", "turn": "B", "cube": 1, "owner": "center", "crawford": False}
    assert report["event_digest"] == hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_adjudications_two(tmp_path):
    """A completed money game has the required settlement and report shape."""
    rows = [
        "MATCH 0 1",
        "GAME cash",
        position("W", "1x1", "24x15", woff=14),
        "ROLL 1 1",
        "PLAY W 1/0",
    ]
    report, _, _ = invoke(tmp_path, rows)
    assert report == {
        "status": "ok",
        "accepted": 5,
        "event_digest": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
        "scores": {"W": 1, "B": 0},
        "games": [{"id": "cash", "winner": "W", "points": 1, "reason": "board", "cube": 1, "crawford": False}],
        "current": None,
    }


def test_adjudications_three(tmp_path):
    """Cube decisions remain part of the active game's official state."""
    rows = [
        "MATCH 5 0",
        "GAME cube",
        position("W", "6x15", "19x15"),
        "DOUBLE W",
        "TAKE B",
    ]
    report, _, _ = invoke(tmp_path, rows)
    assert report["status"] == "ok"
    assert report["current"] == {"id": "cube", "turn": "W", "cube": 2, "owner": "B", "crawford": False}


def test_adjudications_four(tmp_path):
    """A multi-game match applies its scoring and match-game restrictions consistently."""
    rows = ["MATCH 3 1"]
    for game in ("a", "b"):
        rows += ["GAME " + game, position("W", "6x15", "19x15"), "DOUBLE W", "DROP B"]
    rows += [
        "GAME c",
        position("B", "1x15", "24x1", boff=14),
        "ROLL 1 1",
        "PLAY B 24/25",
        "GAME d",
        position("W", "6x15", "19x15"),
        "DOUBLE W",
    ]
    report, _, _ = invoke(tmp_path, rows)
    assert report["status"] == "ok"
    assert report["scores"] == {"W": 2, "B": 2}
    assert [g["points"] for g in report["games"]] == [1, 1, 2]
    assert [g["crawford"] for g in report["games"]] == [False, False, True]
    assert report["current"]["crawford"] is False


def test_adjudications_five(tmp_path):
    """Saved prefixes produce identical continuation and reject different histories."""
    rows = [
        "MATCH 7 0",
        "GAME saved",
        position("W", "6x15", "19x15"),
        "DOUBLE W",
        "TAKE B",
    ]
    full, _, _ = invoke(tmp_path / "full", rows)
    partial, event_file, report_file = invoke(tmp_path / "resume", rows, "checkpoint", 3)
    assert partial["accepted"] == 3
    state_file = event_file.with_name("match.state")
    completed = subprocess.run(["elixir", APP, "resume", str(event_file), str(state_file), str(report_file)], text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0
    assert json.loads(report_file.read_text()) == full

    changed = [rows[1], rows[0], *rows[2:]]
    event_file.write_text("\n".join(changed) + "\n")
    subprocess.run(["elixir", APP, "resume", str(event_file), str(state_file), str(report_file)], check=True, timeout=30)
    rejected = json.loads(report_file.read_text())
    assert rejected["status"] == "invalid"
    assert isinstance(rejected["reason"], str) and rejected["reason"]
    assert rejected["line"] == 0


def test_adjudications_six(tmp_path):
    """Invalid records stop at the first unaccepted event with a stable ruling."""
    rows = [
        "MATCH 5 0",
        "GAME ruling",
        position("W", "8x1,6x14", "19x15"),
        "ROLL 2 3",
        "PLAY B 19/21",
        "DOUBLE W",
    ]
    report, _, _ = invoke(tmp_path, rows)
    assert report["status"] == "invalid"
    assert report["accepted"] == 4
    assert report["line"] == 5
    assert isinstance(report["reason"], str) and report["reason"]
    assert report["event_digest"] == hashlib.sha256("\n".join(rows[:4]).encode()).hexdigest()
