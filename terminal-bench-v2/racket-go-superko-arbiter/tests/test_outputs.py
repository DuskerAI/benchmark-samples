import hashlib
import json
import subprocess

LETTERS = "ABCDEFGHJKLMNOPQRST"
VALID_REASONS = {
    "setup",
    "record",
    "turn",
    "coordinate",
    "occupied",
    "suicide",
    "superko",
    "game-over",
    "prefix",
}


def clean(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def digest(lines):
    data = ("\n".join(lines) + "\n") if lines else ""
    return hashlib.sha256(data.encode()).hexdigest()


def xy(coord, size):
    coord = coord.upper()
    if len(coord) < 2 or coord[0] not in LETTERS:
        return None
    try:
        y = int(coord[1:]) - 1
    except ValueError:
        return None
    x = LETTERS.index(coord[0])
    if 0 <= x < size and 0 <= y < size:
        return x, y
    return None


def neighbours(size, x, y):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < size and 0 <= ny < size:
            yield nx, ny


def group_and_liberties(board, size, x, y):
    color = board[y][x]
    seen = {(x, y)}
    stack = [(x, y)]
    liberties = set()
    while stack:
        px, py = stack.pop()
        for nx, ny in neighbours(size, px, py):
            value = board[ny][nx]
            if value == ".":
                liberties.add((nx, ny))
            elif value == color and (nx, ny) not in seen:
                seen.add((nx, ny))
                stack.append((nx, ny))
    return seen, liberties


def board_key(board, turn, mode):
    flat = "".join("".join(row) for row in board)
    return f"{turn}:{flat}" if mode == "situational" else flat


def parse_setup(line):
    parts = line.split()
    if not parts or parts[0] != "setup":
        raise ValueError("setup")
    fields = {}
    for part in parts[1:]:
        if "=" not in part:
            raise ValueError("setup")
        key, value = part.split("=", 1)
        fields[key] = value
    size = int(fields["size"])
    komi = float(fields["komi"])
    mode = fields["superko"]
    if mode not in {"positional", "situational"} or not (2 <= size <= 19):
        raise ValueError("setup")
    board = [["." for _ in range(size)] for _ in range(size)]
    handicap = [] if fields.get("handicap", "none") == "none" else fields["handicap"].split(",")
    for coord in handicap:
        pos = xy(coord, size)
        if pos is None:
            raise ValueError("setup")
        x, y = pos
        if board[y][x] != ".":
            raise ValueError("setup")
        board[y][x] = "B"
    turn = "W" if handicap else "B"
    return {"size": size, "komi": komi, "mode": mode, "board": board, "turn": turn, "passes": 0, "history": {board_key(board, turn, mode)}}


def score(state):
    size = state["size"]
    board = state["board"]
    seen = set()
    totals = {"B": 0.0, "W": state["komi"]}
    for y in range(size):
        for x in range(size):
            value = board[y][x]
            if value in totals:
                totals[value] += 1
            elif (x, y) not in seen:
                pts = set()
                owners = set()
                stack = [(x, y)]
                seen.add((x, y))
                while stack:
                    px, py = stack.pop()
                    pts.add((px, py))
                    for nx, ny in neighbours(size, px, py):
                        v = board[ny][nx]
                        if v == "." and (nx, ny) not in seen:
                            seen.add((nx, ny))
                            stack.append((nx, ny))
                        elif v in totals:
                            owners.add(v)
                if len(owners) == 1:
                    totals[next(iter(owners))] += len(pts)
    return {"B": int(totals["B"]) if totals["B"].is_integer() else totals["B"], "W": int(totals["W"]) if totals["W"].is_integer() else totals["W"]}


def apply_game(lines, limit=None):
    state = parse_setup(lines[0])
    used = [lines[0]]
    stop = len(lines) if limit is None else min(limit, len(lines))
    for line_no, line in enumerate(lines[1:stop], start=2):
        if state["passes"] >= 2:
            return state, (line_no, "game-over"), used
        parts = line.split()
        if len(parts) != 2 or parts[0] not in {"B", "W"}:
            return state, (line_no, "record"), used
        color, raw = parts
        if color != state["turn"]:
            return state, (line_no, "turn"), used
        if raw.lower() == "pass":
            state["turn"] = "W" if color == "B" else "B"
            state["passes"] += 1
            used.append(line)
            continue
        pos = xy(raw, state["size"])
        if pos is None:
            return state, (line_no, "coordinate"), used
        x, y = pos
        board = [row[:] for row in state["board"]]
        if board[y][x] != ".":
            return state, (line_no, "occupied"), used
        board[y][x] = color
        enemy = "W" if color == "B" else "B"
        removed = set()
        for nx, ny in neighbours(state["size"], x, y):
            if board[ny][nx] == enemy:
                grp, libs = group_and_liberties(board, state["size"], nx, ny)
                if not libs:
                    removed.update(grp)
        for rx, ry in removed:
            board[ry][rx] = "."
        _, libs = group_and_liberties(board, state["size"], x, y)
        if not libs:
            return state, (line_no, "suicide"), used
        new_turn = enemy
        key = board_key(board, new_turn, state["mode"])
        if key in state["history"]:
            return state, (line_no, "superko"), used
        state["board"] = board
        state["turn"] = new_turn
        state["passes"] = 0
        state["history"].add(key)
        used.append(line)
    return state, None, used


def expected(text, limit=None):
    lines = clean(text)
    state, err, used = apply_game(lines, limit)
    scores = score(state)
    result = {
        "status": "invalid" if err else "ok",
        "accepted": len(used),
        "event_digest": digest(used),
        "scores": scores,
        "winner": "B" if scores["B"] > scores["W"] else "W" if scores["W"] > scores["B"] else "draw",
        "board": ["".join(state["board"][y]) for y in range(state["size"] - 1, -1, -1)],
    }
    if err:
        result["line"], result["reason"] = err
    return result


def run_cmd(args, cwd):
    subprocess.run(["racket", "/app/arbiter.rkt", *args], cwd=cwd, check=True)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def assert_report_schema(actual, *, invalid, size=None):
    base_keys = {"status", "accepted", "event_digest", "scores", "winner", "board"}
    assert set(actual) == (base_keys | {"line", "reason"} if invalid else base_keys)
    assert actual["status"] == ("invalid" if invalid else "ok")
    assert isinstance(actual["accepted"], int) and actual["accepted"] >= 0
    assert isinstance(actual["event_digest"], str)
    assert isinstance(actual["scores"], dict)
    assert set(actual["scores"]) == {"B", "W"}
    assert all(
        isinstance(actual["scores"][color], (int, float)) and not isinstance(actual["scores"][color], bool)
        for color in ("B", "W")
    )
    assert actual["winner"] in {"B", "W", "draw"}
    assert isinstance(actual["board"], list)
    if size is not None:
        assert len(actual["board"]) == size
        assert all(
            isinstance(row, str) and len(row) == size and set(row) <= {"B", "W", "."}
            for row in actual["board"]
        )
    if invalid:
        assert isinstance(actual["line"], int)
        assert actual["reason"] in VALID_REASONS


def assert_invalid_report(report_path, text, line, reason):
    actual = read_json(report_path)
    exp = expected(text)
    assert_report_schema(actual, invalid=True, size=len(exp["board"]))
    assert actual == exp
    assert actual["status"] == "invalid"
    assert actual["line"] == line
    assert actual["reason"] == reason
    assert actual["accepted"] == exp["accepted"]
    assert actual["event_digest"] == exp["event_digest"]
    assert actual["board"] == exp["board"]
    assert actual["scores"] == exp["scores"]


def assert_valid_report(report_path, text):
    actual = read_json(report_path)
    exp = expected(text)
    assert_report_schema(actual, invalid=False, size=len(exp["board"]))
    assert actual == exp


def assert_setup_invalid_report(report_path):
    actual = read_json(report_path)
    assert_report_schema(actual, invalid=True)
    assert actual == {
        "status": "invalid",
        "accepted": 0,
        "event_digest": digest([]),
        "scores": {"B": 0, "W": 0},
        "winner": "draw",
        "board": [],
        "line": 1,
        "reason": "setup",
    }


def assert_prefix_invalid_report(report_path):
    actual = read_json(report_path)
    assert_report_schema(actual, invalid=True)
    assert actual == {
        "status": "invalid",
        "accepted": 0,
        "event_digest": "",
        "scores": {"B": 0, "W": 0},
        "winner": "draw",
        "board": [],
        "line": 0,
        "reason": "prefix",
    }


def assert_report_matches(report_path, text):
    actual = read_json(report_path)
    exp = expected(text)
    assert_report_schema(actual, invalid=(exp["status"] == "invalid"), size=len(exp["board"]))
    assert actual == exp


def write_case(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def test_official_reports_cover_board_play(tmp_path):
    """The official report is checked against independent game adjudication."""
    text = """
setup size=5 komi=0.5 superko=positional handicap=none
B A2
W A1
B C2
W B2
B B3
W E5
B B1
W pass
B pass
"""
    events = write_case(tmp_path, "match-a.txt", text)
    report = tmp_path / "report-a.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_report_matches(report, text)


def test_single_move_captures_multiple_adjacent_groups(tmp_path):
    """A move can remove more than one neighboring opponent group at once."""
    text = """
setup size=5 komi=0.5 superko=positional handicap=none
B A2
W B2
B B1
W D2
B B3
W pass
B E2
W pass
B D1
W pass
B D3
W pass
B C2
"""
    events = write_case(tmp_path, "match-multi-capture.txt", text)
    report = tmp_path / "report-multi-capture.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_report_matches(report, text)
    actual = read_json(report)
    assert actual["status"] == "ok"
    assert all("W" not in row for row in actual["board"])


def test_neutral_territory_touching_both_colors_scores_for_neither(tmp_path):
    """An empty region bordered by both colors is dame, not owned territory."""
    text = """
setup size=5 komi=0 superko=positional handicap=none
B A1
W E1
B pass
W pass
"""
    events = write_case(tmp_path, "match-neutral-territory.txt", text)
    report = tmp_path / "report-neutral-territory.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_valid_report(report, text)
    actual = read_json(report)
    assert actual["scores"] == {"B": 1, "W": 1}
    assert actual["winner"] == "draw"


def test_official_reports_cover_rule_variants(tmp_path):
    """The official report remains stable across declared rule settings."""
    positional = """
setup size=3 komi=0 superko=positional handicap=none
B B1
W A2
B C2
W B2
B A1
W C1
B B1
"""
    situational = positional.replace("superko=positional", "superko=situational") + "W pass\nB pass\n"
    for index, text in enumerate((positional, situational), start=1):
        events = write_case(tmp_path, f"match-b{index}.txt", text)
        report = tmp_path / f"report-b{index}.json"
        run_cmd(["adjudicate", str(events), str(report)], tmp_path)
        assert_report_matches(report, text)


def test_invalid_turn_keeps_the_last_legal_position(tmp_path):
    """A move by the wrong player reports turn and preserves the prior board and score."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
W A1
"""
    events = write_case(tmp_path, "match-invalid-turn.txt", text)
    report = tmp_path / "report-invalid-turn.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 2, "turn")


def test_occupied_move_keeps_the_last_legal_position(tmp_path):
    """A play on an occupied point reports occupied and preserves accepted state."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
B B2
W B2
"""
    events = write_case(tmp_path, "match-invalid-occupied.txt", text)
    report = tmp_path / "report-invalid-occupied.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 3, "occupied")


def test_off_board_move_uses_coordinate_reason(tmp_path):
    """A coordinate outside the board is rejected with the public coordinate reason."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
B D1
"""
    events = write_case(tmp_path, "match-invalid-coordinate.txt", text)
    report = tmp_path / "report-invalid-coordinate.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 2, "coordinate")


def test_size_nine_coordinates_skip_i_but_keep_j(tmp_path):
    """On a 9x9 board the column scheme rejects I while keeping J as the ninth column."""
    invalid = """
setup size=9 komi=0.5 superko=positional handicap=none
B I1
"""
    valid = """
setup size=9 komi=0.5 superko=positional handicap=none
B J1
"""
    invalid_events = write_case(tmp_path, "match-invalid-i-column.txt", invalid)
    invalid_report = tmp_path / "report-invalid-i-column.json"
    valid_events = write_case(tmp_path, "match-valid-j-column.txt", valid)
    valid_report = tmp_path / "report-valid-j-column.json"
    run_cmd(["adjudicate", str(invalid_events), str(invalid_report)], tmp_path)
    run_cmd(["adjudicate", str(valid_events), str(valid_report)], tmp_path)
    assert_invalid_report(invalid_report, invalid, 2, "coordinate")
    assert_valid_report(valid_report, valid)


def test_malformed_setup_reports_setup_reason(tmp_path):
    """A setup line with a malformed rule declaration reports setup and accepts no records."""
    text = """
setup size=9 komi=0.5 superko=eternal handicap=none
B A1
"""
    events = write_case(tmp_path, "match-malformed-setup.txt", text)
    report = tmp_path / "report-malformed-setup.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_setup_invalid_report(report)


def test_malformed_record_reports_record_reason(tmp_path):
    """A later record with a malformed player field reports record and preserves accepted state."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
Q A1
"""
    events = write_case(tmp_path, "match-malformed-record.txt", text)
    report = tmp_path / "report-malformed-record.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 2, "record")


def test_suicide_move_keeps_the_last_legal_position(tmp_path):
    """A non-capturing play without liberties reports suicide and preserves accepted state."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
B B1
W C3
B A2
W A1
"""
    events = write_case(tmp_path, "match-invalid-suicide.txt", text)
    report = tmp_path / "report-invalid-suicide.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 5, "suicide")


def test_record_after_two_passes_is_invalid(tmp_path):
    """A later record after two consecutive passes reports game-over with final position data."""
    text = """
setup size=3 komi=0.5 superko=positional handicap=none
B pass
W pass
B A1
"""
    events = write_case(tmp_path, "match-invalid-game-over.txt", text)
    report = tmp_path / "report-invalid-game-over.json"
    run_cmd(["adjudicate", str(events), str(report)], tmp_path)
    assert_invalid_report(report, text, 4, "game-over")


def test_checkpoint_resume_matches_the_table_record(tmp_path):
    """Checkpointed play resumes to the same official result and protects its prefix."""
    text = """
setup size=5 komi=2.5 superko=situational handicap=B2,D2
W E5
B A1
W pass
B pass
"""
    events = write_case(tmp_path, "match-c.txt", text)
    full_report = tmp_path / "full.json"
    state = tmp_path / "state.json"
    prefix_report = tmp_path / "prefix.json"
    resumed_report = tmp_path / "resumed.json"
    run_cmd(["adjudicate", str(events), str(full_report)], tmp_path)
    run_cmd(["checkpoint", str(events), "3", str(state), str(prefix_report)], tmp_path)
    run_cmd(["resume", str(events), str(state), str(resumed_report)], tmp_path)
    assert read_json(prefix_report) == expected(text, limit=3)
    assert read_json(resumed_report) == read_json(full_report) == expected(text)
    altered = write_case(tmp_path, "match-c2.txt", text.replace("B A1", "B C3"))
    bad_report = tmp_path / "bad.json"
    run_cmd(["resume", str(altered), str(state), str(bad_report)], tmp_path)
    assert_prefix_invalid_report(bad_report)


def test_reordered_checkpoint_prefix_reports_exact_prefix_error(tmp_path):
    """Resume rejects a checkpoint prefix whose saved records are present in a different order."""
    original = """
setup size=5 komi=0.5 superko=positional handicap=none
B A1
W C3
B E5
W pass
B pass
"""
    reordered = """
setup size=5 komi=0.5 superko=positional handicap=none
B A1
B E5
W C3
W pass
B pass
"""
    events = write_case(tmp_path, "match-prefix-original.txt", original)
    state = tmp_path / "prefix-state.json"
    checkpoint_report = tmp_path / "prefix-checkpoint.json"
    run_cmd(["checkpoint", str(events), "4", str(state), str(checkpoint_report)], tmp_path)
    altered_events = write_case(tmp_path, "match-prefix-reordered.txt", reordered)
    bad_report = tmp_path / "prefix-reordered.json"
    run_cmd(["resume", str(altered_events), str(state), str(bad_report)], tmp_path)
    assert_prefix_invalid_report(bad_report)
