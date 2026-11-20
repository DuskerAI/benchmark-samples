import json
import random
import subprocess
from collections import deque


def other(color):
    """Return the opponent color code."""
    return "W" if color == "B" else "B"


def key(x, y):
    return chr(97 + x) + chr(97 + y)


def coord_xy(size, coord):
    """Convert an input coordinate into zero-based board indexes, or None."""
    if len(coord) != 2:
        return None
    x = ord(coord[0]) - 97
    y = ord(coord[1]) - 97
    if 0 <= x < size and 0 <= y < size:
        return x, y
    return None


def neighbors(size, coord):
    """List orthogonal board neighbors for a valid coordinate."""
    xy = coord_xy(size, coord)
    if xy is None:
        return []
    x, y = xy
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < size and 0 <= ny < size:
            out.append(key(nx, ny))
    return out


def board_key(size, board):
    """Encode the whole-board position for positional superko checks."""
    return "".join(board.get(key(x, y), ".") for y in range(size) for x in range(size))


def stone_group(size, board, start):
    """Return all connected stones in a group and their liberties."""
    color = board[start]
    seen = set()
    libs = set()
    todo = [start]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        for n in neighbors(size, p):
            value = board.get(n)
            if value is None:
                libs.add(n)
            elif value == color and n not in seen:
                todo.append(n)
    return seen, libs


def play_stone(size, board, history, color, coord):
    """Apply one stone play, including capture, suicide, and superko rules."""
    if coord_xy(size, coord) is None or coord in board:
        return False, board, history, 0
    new_board = dict(board)
    captured = 0
    new_board[coord] = color
    for n in neighbors(size, coord):
        if new_board.get(n) == other(color):
            group, libs = stone_group(size, new_board, n)
            if not libs:
                captured += len(group)
                for p in group:
                    del new_board[p]
    _, own_libs = stone_group(size, new_board, coord)
    if not own_libs:
        return False, board, history, 0
    state = board_key(size, new_board)
    if state in history:
        return False, board, history, 0
    return True, new_board, history + [state], captured


def empty_region(size, board, start, seen):
    """Flood-fill an empty region and collect adjacent stone colors."""
    region = set()
    adjacent = set()
    todo = deque([start])
    seen.add(start)
    while todo:
        p = todo.popleft()
        region.add(p)
        for n in neighbors(size, p):
            value = board.get(n)
            if value is None:
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
            else:
                adjacent.add(value)
    return region, adjacent


def score(size, board, komi, scoring, captures):
    """Compute area or territory score for a finished game."""
    black = 0 if scoring == "area" else captures["B"]
    white = komi + (0 if scoring == "area" else captures["W"])
    seen_empty = set()
    for y in range(size):
        for x in range(size):
            p = key(x, y)
            value = board.get(p)
            if value == "B":
                if scoring == "area":
                    black += 1
            elif value == "W":
                if scoring == "area":
                    white += 1
            elif p not in seen_empty:
                region, adjacent = empty_region(size, board, p, seen_empty)
                if adjacent == {"B"}:
                    black += len(region)
                elif adjacent == {"W"}:
                    white += len(region)
    return black, white


def expected_report(records):
    """Reference implementation for the tournament contract under test."""
    players = set()
    games = []
    current = None
    suspended = {}

    def finish(winner, reason, margin):
        if current is not None and not current["done"]:
            games.append(
                {
                    "id": current["id"],
                    "black": current["black"],
                    "white": current["white"],
                    "winner": winner,
                    "reason": reason,
                    "margin": margin,
                }
            )
            current["done"] = True

    def finish_by_score():
        black_score, white_score = score(
            current["size"],
            current["board"],
            current["komi"],
            current["scoring"],
            current["captures"],
        )
        diff = black_score - white_score
        if diff > 0:
            finish(current["black"], "score", diff)
        elif diff < 0:
            finish(current["white"], "score", -diff)
        else:
            finish("draw", "score", 0)

    def apply_ruling(game_id, side, margin):
        for game in games:
            if game["id"] != game_id:
                continue
            game["winner"] = "draw" if side == "draw" else game["black" if side == "B" else "white"]
            game["reason"] = "ruling"
            game["margin"] = 0 if side == "draw" else float(margin)
            break

    for line in records:
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "GAME":
            current = {
                "id": parts[1],
                "size": int(parts[2]),
                "komi": float(parts[3]),
                "black": parts[4],
                "white": parts[5],
                "scoring": parts[6] if len(parts) > 6 else "area",
                "board": {},
                "history": [],
                "stack": [],
                "next": None,
                "pass_count": 0,
                "captures": {"B": 0, "W": 0},
                "done": False,
            }
            current["history"].append(board_key(current["size"], current["board"]))
            players.add(current["black"])
            players.add(current["white"])
            continue
        if tag == "RULING":
            apply_ruling(parts[1], parts[2], parts[3])
            continue
        if tag == "RESUME":
            if len(parts) > 1 and parts[1] in suspended:
                current = suspended.pop(parts[1])
            continue
        if current is None or current["done"]:
            continue
        if tag == "SETUP":
            color = parts[1]
            for coord in parts[2:]:
                if coord_xy(current["size"], coord) is not None and coord not in current["board"]:
                    current["board"][coord] = color
            state = board_key(current["size"], current["board"])
            if state not in current["history"]:
                current["history"].append(state)
        elif tag == "PLAY":
            color, move = parts[1], parts[2]
            if current["next"] is not None and color != current["next"]:
                finish(current["black"] if other(color) == "B" else current["white"], "illegal", 0)
            elif move == "pass":
                current["stack"].append(
                    (
                        dict(current["board"]),
                        list(current["history"]),
                        current["next"],
                        current["pass_count"],
                        dict(current["captures"]),
                    )
                )
                current["next"] = other(color)
                current["pass_count"] += 1
                if current["pass_count"] >= 2:
                    finish_by_score()
            else:
                before = (
                    dict(current["board"]),
                    list(current["history"]),
                    current["next"],
                    current["pass_count"],
                    dict(current["captures"]),
                )
                ok, new_board, new_history, captured = play_stone(
                    current["size"], current["board"], current["history"], color, move
                )
                if not ok:
                    finish(current["black"] if other(color) == "B" else current["white"], "illegal", 0)
                else:
                    current["stack"].append(before)
                    current["board"] = new_board
                    current["history"] = new_history
                    current["captures"][color] += captured
                    current["next"] = other(color)
                    current["pass_count"] = 0
        elif tag == "UNDO":
            for _ in range(int(parts[1])):
                if not current["stack"]:
                    break
                board, history, next_color, pass_count, captures = current["stack"].pop()
                current["board"] = board
                current["history"] = history
                current["next"] = next_color
                current["pass_count"] = pass_count
                current["captures"] = captures
        elif tag == "PAUSE":
            if len(parts) > 1:
                suspended[parts[1]] = current
                current = None
        elif tag == "FORFEIT":
            finish(parts[1], "forfeit", 0)
        elif tag == "SCORE":
            finish_by_score()

    stats = {
        p: {
            "player": p,
            "points": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "sos": 0,
            "margin": 0,
            "opponents": [],
            "head_to_head": [],
        }
        for p in players
    }
    for game in games:
        b = game["black"]
        w = game["white"]
        stats[b]["opponents"].append(w)
        stats[w]["opponents"].append(b)
        if game["winner"] == "draw":
            for p in (b, w):
                stats[p]["points"] += 0.5
                stats[p]["draws"] += 1
            stats[b]["head_to_head"].append((w, 0.5))
            stats[w]["head_to_head"].append((b, 0.5))
        else:
            winner = game["winner"]
            loser = w if winner == b else b
            stats[winner]["points"] += 1
            stats[winner]["wins"] += 1
            stats[loser]["losses"] += 1
            stats[winner]["margin"] += game["margin"]
            stats[loser]["margin"] -= game["margin"]
            stats[winner]["head_to_head"].append((loser, 1))
            stats[loser]["head_to_head"].append((winner, 0))
    for row in stats.values():
        row["sos"] = sum(stats[opponent]["points"] for opponent in row.pop("opponents"))
    for row in stats.values():
        row["sort_h2h"] = sum(
            earned
            for opponent, earned in row.pop("head_to_head")
            if stats[opponent]["points"] == row["points"] and stats[opponent]["sos"] == row["sos"]
        )
    standings = sorted(
        stats.values(),
        key=lambda row: (-row["points"], -row["sos"], -row["sort_h2h"], -row["margin"], row["player"]),
    )
    for row in standings:
        del row["sort_h2h"]
    return {"games": games, "standings": standings}


def run_arbiter(tmp_path, records):
    """Run the submitted Racket arbiter against temporary input and output files."""
    in_path = tmp_path / "rounds.txt"
    out_path = tmp_path / "report.json"
    in_path.write_text("\n".join(records) + "\n")
    result = subprocess.run(
        ["racket", "/app/arbiter.rkt", str(in_path), str(out_path)],
        cwd="/app",
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert out_path.exists()
    return json.loads(out_path.read_text())


def assert_report(actual, expected):
    """Require exact semantic output and the documented JSON field sets."""
    assert actual == expected
    for game in actual["games"]:
        assert set(game) == {"id", "black", "white", "winner", "reason", "margin"}
        assert isinstance(game["id"], str)
        assert isinstance(game["black"], str)
        assert isinstance(game["white"], str)
        assert isinstance(game["winner"], str)
        assert isinstance(game["reason"], str)
        assert isinstance(game["margin"], (int, float))
    for row in actual["standings"]:
        assert set(row) == {"player", "points", "wins", "losses", "draws", "sos", "margin"}
        assert isinstance(row["player"], str)
        assert isinstance(row["points"], (int, float))
        assert isinstance(row["wins"], int)
        assert isinstance(row["losses"], int)
        assert isinstance(row["draws"], int)
        assert isinstance(row["sos"], (int, float))
        assert isinstance(row["margin"], (int, float))


def test_tournament_contract(tmp_path):
    """Checks the adjudication report for a deterministic tournament."""
    records = [
        "GAME g1 5 0 Alpha Beta",
        "SETUP B ab ba bc",
        "SETUP W bb ca cc db",
        "PLAY B cb",
        "PLAY W bb",
        "SCORE",
        "GAME g2 5 5.5 Gamma Delta",
        "SETUP B aa ee",
        "PLAY W bb",
        "PLAY B pass",
        "UNDO 1",
        "PLAY B cc",
        "PLAY W dd",
        "SCORE",
        "GAME g3 5 0 Alpha Gamma",
        "SETUP B ab ba bc cb",
        "PLAY W bb",
        "GAME g4 5 0 Beta Delta",
        "PLAY B aa",
        "PLAY W ee",
        "UNDO 2",
        "PLAY W cc",
        "PLAY B dd",
        "SCORE",
        "GAME g5 5 0 Alpha Delta",
        "FORFEIT Delta",
        "GAME g6 5 0 Beta Gamma",
        "SCORE",
    ]
    assert_report(run_arbiter(tmp_path, records), expected_report(records))


def test_capture_changes_scoring(tmp_path):
    """Checks that single- and multi-stone captures affect final scoring."""
    records = [
        "GAME single 5 0 CaptureA CaptureB",
        "SETUP B ab ba bc",
        "SETUP W bb",
        "PLAY B cb",
        "PLAY W ee",
        "SCORE",
        "GAME group 5 0 CaptureC CaptureD",
        "SETUP B ba ca ab cb ac cc",
        "SETUP W bb bc",
        "PLAY B bd",
        "PLAY W ee",
        "SCORE",
    ]
    assert_report(run_arbiter(tmp_path, records), expected_report(records))


def test_simultaneous_capture_suicide_and_undo_layers(tmp_path):
    """Checks less common interactions among capture, suicide, passes, and undo."""
    records = [
        "GAME double 5 0 FusekiA FusekiB",
        "SETUP B ac bb ca",
        "SETUP W ab ba",
        "PLAY B aa",
        "PLAY W ee",
        "SCORE",
        "GAME suicide 5 0 LifeA LifeB",
        "SETUP B ab ba",
        "PLAY W aa",
        "SCORE",
        "GAME passundo 5 0 TempoA TempoB",
        "PLAY B aa",
        "PLAY W pass",
        "PLAY B bb",
        "UNDO 1",
        "PLAY B cc",
        "PLAY W dd",
        "SCORE",
        "GAME replay 5 0 ReplayA ReplayB",
        "PLAY B aa",
        "PLAY W bb",
        "PLAY B cc",
        "UNDO 2",
        "PLAY W bb",
        "PLAY B cc",
        "SCORE",
    ]
    assert_report(run_arbiter(tmp_path, records), expected_report(records))


def test_consecutive_passes_auto_score_and_undo_state(tmp_path):
    """Checks pass-pair scoring and undo restoration of consecutive-pass state."""
    records = [
        "GAME autopass 5 1 PassA PassB",
        "PLAY B aa",
        "PLAY W pass",
        "PLAY B pass",
        "PLAY W bb",
        "SCORE",
        "GAME passreset 5 2 ResetA ResetB",
        "PLAY B aa",
        "PLAY W pass",
        "UNDO 1",
        "PLAY W bb",
        "PLAY B pass",
        "PLAY W pass",
        "PLAY B cc",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["autopass", "passreset"]
    assert all(game["reason"] == "score" for game in actual["games"])


def test_stone_play_resets_prior_pass_before_later_scoring(tmp_path):
    """Checks that an accepted stone clears the consecutive-pass counter."""
    records = [
        "GAME resetchain 7 0 ChainB ChainW",
        "PLAY B pass",
        "PLAY W cc",
        "PLAY B pass",
        "PLAY W dd",
        "SCORE",
        "GAME table1 5 0 ChainB RivalA",
        "FORFEIT ChainB",
        "GAME table2 5 0 ChainW RivalB",
        "FORFEIT RivalB",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert actual["games"][0]["id"] == "resetchain"
    assert actual["games"][0]["reason"] == "score"
    assert actual["games"][0]["margin"] == expected["games"][0]["margin"]


def test_neutral_territory_draws_and_tiebreaks(tmp_path):
    """Checks neutral regions, fractional komi, draws, and standings ordering."""
    records = [
        "GAME neutral 5 0 WallA WallB",
        "SETUP B aa ab ac ad ae",
        "SETUP W ea eb ec ed ee",
        "SCORE",
        "GAME draw 5 1 TieA TieB",
        "PLAY B aa",
        "SCORE",
        "GAME margin1 5 0 TieA WallA",
        "PLAY B aa",
        "PLAY W ee",
        "SCORE",
        "GAME margin2 5 2.5 TieB WallB",
        "PLAY B aa",
        "PLAY W ee",
        "SCORE",
        "GAME sos 5 0 WallA TieB",
        "FORFEIT WallA",
    ]
    assert_report(run_arbiter(tmp_path, records), expected_report(records))


def test_head_to_head_tiebreak_precedes_name(tmp_path):
    """Checks the standing order when points, SOS, and margin are tied."""
    records = [
        "GAME h1 5 0 Zenith Atlas",
        "FORFEIT Zenith",
        "GAME h2 5 0 Atlas Delta",
        "FORFEIT Atlas",
        "GAME h3 5 0 Zenith Beta",
        "FORFEIT Beta",
        "GAME h4 5 0 Delta Epsilon",
        "FORFEIT Delta",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [row["player"] for row in actual["standings"][:2]] == ["Zenith", "Atlas"]


def test_head_to_head_bucket_excludes_cross_sos_results(tmp_path):
    """Checks head-to-head sorting only uses opponents in the same points/SOS bucket."""
    records = [
        "GAME ab 5 0 Atlas Beacon",
        "FORFEIT Atlas",
        "GAME ad 5 0 Atlas Draco",
        "FORFEIT Draco",
        "GAME bc 5 0 Beacon Cygnus",
        "FORFEIT Beacon",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [row["player"] for row in actual["standings"]] == [
        "Atlas",
        "Beacon",
        "Draco",
        "Cygnus",
    ]


def test_multi_bucket_head_to_head_precedes_name_without_margin(tmp_path):
    """Checks same-bucket head-to-head in two independent standings buckets."""
    records = [
        "GAME ab 5 0 A B",
        "FORFEIT A",
        "GAME ac 5 0 A C",
        "FORFEIT A",
        "GAME ad 5 0 A D",
        "FORFEIT D",
        "GAME bc 5 0 B C",
        "FORFEIT B",
        "GAME bd 5 0 B D",
        "FORFEIT D",
        "GAME cd 5 0 C D",
        "FORFEIT C",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [row["player"] for row in actual["standings"]] == ["D", "A", "B", "C"]


def test_setup_position_superko(tmp_path):
    """Checks that setup-created positions are included in superko history."""
    records = [
        "GAME ko 5 0 KoBlack KoWhite",
        "SETUP B ab ba bc",
        "SETUP W bb ca cc db",
        "PLAY B cb",
        "PLAY W bb",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert actual["games"][0]["reason"] == "illegal"


def test_setup_ignores_occupied_and_invalid_points(tmp_path):
    """Checks that setup records ignore occupied and off-board intersections."""
    records = [
        "GAME setupignore 5 0 StableB StableW",
        "SETUP B aa bb",
        "SETUP W aa cc zz a",
        "PLAY W dd",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert actual["games"][0]["winner"] == "draw"


def test_turn_gate_survives_passes_undo_and_abandoned_games(tmp_path):
    """Checks next-color enforcement after passes, undo, and game boundaries."""
    records = [
        "GAME passgate 5 0 GateBlack GateWhite",
        "PLAY W pass",
        "PLAY W aa",
        "SCORE",
        "GAME undogate 5 0 UndoBlack UndoWhite",
        "PLAY B aa",
        "PLAY W bb",
        "UNDO 1",
        "PLAY B cc",
        "SCORE",
        "GAME abandonedgate 5 0 FreshBlack FreshWhite",
        "PLAY W aa",
        "GAME freshgate 5 0 FreshBlack FreshWhite",
        "PLAY W bb",
        "PLAY B pass",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == [
        "passgate",
        "undogate",
        "freshgate",
    ]
    assert actual["games"][0]["winner"] == "GateBlack"
    assert actual["games"][1]["winner"] == "UndoWhite"


def test_undo_restores_position_history_for_replayed_capture(tmp_path):
    """Checks that undo restores superko history as well as board and turn."""
    records = [
        "GAME replayko 5 0 RecoverB RecoverW",
        "SETUP B ab ba bc",
        "SETUP W bb ca cc db",
        "PLAY B cb",
        "UNDO 1",
        "PLAY B cb",
        "PLAY W ee",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert actual["games"][0]["reason"] == "score"


def test_pause_resume_preserves_superko_and_replaces_active_game(tmp_path):
    """Checks suspended games keep whole-position history across another active game."""
    records = [
        "# comments and surrounding whitespace are ignored",
        "  GAME pausedko 5 0 PauseB PauseW  ",
        "SETUP B ab ba bc",
        "SETUP W bb ca cc db",
        "PLAY B cb",
        "UNDO 1",
        "PLAY B cb",
        "PAUSE ko-slot",
        "GAME replacement 5 0 ReplaceB ReplaceW",
        "PLAY B aa",
        "RESUME ko-slot",
        "PLAY W bb",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["pausedko"]
    assert actual["games"][0]["reason"] == "illegal"


def test_pause_resume_keeps_pass_state_and_excess_undo_contract(tmp_path):
    """Checks suspension of pass state plus undo requests larger than history."""
    records = [
        "GAME passhold 5 0 HoldB HoldW",
        "PLAY B aa",
        "PLAY W pass",
        "PAUSE p-slot",
        "GAME side 5 0 SideB SideW",
        "FORFEIT SideW",
        "RESUME p-slot",
        "PLAY B pass",
        "PLAY W cc",
        "GAME excess 5 0 ExcessB ExcessW territory",
        "PLAY B aa",
        "PLAY W bb",
        "PAUSE e-slot",
        "RESUME missing-slot",
        "RESUME e-slot",
        "UNDO 99",
        "PLAY W cc",
        "PLAY B dd",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["side", "passhold", "excess"]
    assert actual["games"][1]["reason"] == "score"


def test_reused_pause_key_replaces_prior_suspended_game(tmp_path):
    """Checks that a later PAUSE under the same key replaces the old suspension."""
    records = [
        "GAME old 5 0 OldB OldW",
        "PLAY B aa",
        "PAUSE shared",
        "GAME new 5 0 NewB NewW",
        "PLAY W bb",
        "PAUSE shared",
        "GAME active 5 0 ActiveB ActiveW",
        "PLAY B cc",
        "RESUME shared",
        "PLAY B dd",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["new"]
    rows = {row["player"]: row for row in actual["standings"]}
    for player in ["OldB", "OldW", "ActiveB", "ActiveW"]:
        assert rows[player]["points"] == 0
        assert rows[player]["wins"] == rows[player]["losses"] == rows[player]["draws"] == 0


def test_unknown_resume_preserves_active_and_valid_resume_replaces_it(tmp_path):
    """Checks missing resume keys and replacement of an active unfinished game."""
    records = [
        "GAME keep 5 0 KeepB KeepW",
        "PLAY W aa",
        "RESUME nowhere",
        "PLAY B bb",
        "SCORE",
        "GAME suspended 5 0 StashB StashW",
        "PLAY B aa",
        "PAUSE slot",
        "GAME active 5 0 ActiveB ActiveW",
        "PLAY B cc",
        "RESUME missing",
        "PLAY W dd",
        "RESUME slot",
        "PLAY W bb",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["keep", "suspended"]


def test_resumed_completion_order_with_compound_scoring_state(tmp_path):
    """Checks completion order when a resumed game carries undo and prisoners."""
    records = [
        "GAME main 5 0 MainB MainW territory",
        "SETUP W aa ac",
        "SETUP B ba bc",
        "PLAY B ab",
        "PAUSE carry",
        "GAME between 5 0 BetweenB BetweenW",
        "PLAY B aa",
        "PLAY W bb",
        "PLAY B pass",
        "PLAY W pass",
        "GAME replaced 5 0 ReplacedB ReplacedW",
        "PLAY B ee",
        "RESUME carry",
        "UNDO 1",
        "PLAY B ab",
        "PLAY W ee",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["between", "main"]
    rows = {row["player"]: row for row in actual["standings"]}
    assert rows["ReplacedB"]["points"] == rows["ReplacedW"]["points"] == 0
    assert actual["games"][1]["margin"] == expected["games"][1]["margin"]


def test_territory_scoring_tracks_prisoners_through_undo(tmp_path):
    """Checks scoring-mode branches and capture totals across rollback and replay."""
    records = [
        "GAME blackterritory 5 0 PrisonB PrisonW territory",
        "SETUP W aa dd",
        "SETUP B ba ee",
        "PLAY B ab",
        "UNDO 1",
        "PLAY B ab",
        "SCORE",
        "GAME blackarea 5 0 AreaB AreaW area",
        "SETUP W aa dd",
        "SETUP B ba ee",
        "PLAY B ab",
        "SCORE",
        "GAME whiteterritory 5 0.5 ReverseB ReverseW territory",
        "SETUP B aa dd",
        "SETUP W ba ee",
        "PLAY W ab",
        "PLAY B pass",
        "UNDO 1",
        "PLAY B pass",
        "PLAY W pass",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    margins = {game["id"]: game["margin"] for game in actual["games"]}
    assert margins == {"blackterritory": 2, "blackarea": 3, "whiteterritory": 2.5}


def test_large_board_prisoner_margin_and_bucket_interactions(tmp_path):
    """Checks prisoner accounting inside a multi-player standings bucket."""
    records = [
        "GAME tcap1 7 0 Aki Bo territory",
        "SETUP W cc ff",
        "SETUP B bc cb gf",
        "PLAY B cd",
        "PLAY W aa",
        "PLAY B dc",
        "UNDO 2",
        "PLAY W aa",
        "PLAY B dc",
        "SCORE",
        "GAME tcap2 7 0.5 Ciro Dia territory",
        "SETUP B cc ff",
        "SETUP W bc cb gf",
        "PLAY W cd",
        "PLAY B aa",
        "PLAY W dc",
        "PLAY B pass",
        "PLAY W pass",
        "GAME hbucket1 7 0 Aki Ciro",
        "FORFEIT Ciro",
        "GAME hbucket2 7 0 Bo Dia",
        "FORFEIT Bo",
        "GAME hbucket3 7 0 Aki Dia",
        "FORFEIT Dia",
        "GAME hbucket4 7 0 Bo Ciro",
        "FORFEIT Ciro",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    rows = {row["player"]: row for row in actual["standings"]}
    margins = {game["id"]: game["margin"] for game in actual["games"]}
    assert margins["tcap1"] == 2
    assert margins["tcap2"] == 2.5
    assert rows["Aki"]["points"] == rows["Bo"]["points"]
    assert rows["Ciro"]["points"] == rows["Dia"]["points"]
    assert [row["player"] for row in actual["standings"]] == ["Dia", "Ciro", "Aki", "Bo"]


def test_paused_ko_reentry_remains_superko_after_side_game(tmp_path):
    """Checks positional superko after suspension, side games, and active replacement."""
    records = [
        "GAME suspendedko 5 0 KoB KoW",
        "SETUP B ba ab cb bc",
        "SETUP W ca cc db",
        "PLAY W bb",
        "PAUSE live-ko",
        "GAME sidewin 5 0 SideB SideW",
        "PLAY B aa",
        "PLAY W pass",
        "PLAY B pass",
        "GAME replaced 5 0 ReplacedB ReplacedW",
        "PLAY W ee",
        "RESUME live-ko",
        "PLAY B cb",
        "SCORE",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["sidewin", "suspendedko"]
    assert actual["games"][1]["reason"] == "illegal"
    assert actual["games"][1]["winner"] == "KoW"
    rows = {row["player"]: row for row in actual["standings"]}
    assert rows["ReplacedB"]["points"] == rows["ReplacedW"]["points"] == 0


def test_large_board_coordinate_boundary_and_undo_layers(tmp_path):
    """Checks size-26 coordinates with rollback, invalid setup skips, and standings."""
    records = [
        "GAME edge26 26 7.5 EdgeB EdgeW territory",
        "SETUP B aa az za zz z{ {z",
        "SETUP W mm mz zm aa",
        "PLAY W zy",
        "PLAY B yz",
        "PAUSE edge-slot",
        "GAME small 5 0 SmallB SmallW",
        "FORFEIT SmallB",
        "RESUME edge-slot",
        "UNDO 2",
        "PLAY W xy",
        "PLAY B yx",
        "SCORE",
        "GAME edge-rematch 26 0 EdgeB SmallW",
        "PLAY W zz",
        "PLAY B pass",
        "PLAY W pass",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["small", "edge26", "edge-rematch"]
    assert actual["games"][1]["winner"] == expected["games"][1]["winner"]
    rows = {row["player"]: row for row in actual["standings"]}
    expected_rows = {row["player"]: row for row in expected["standings"]}
    assert rows["EdgeB"]["sos"] == expected_rows["EdgeB"]["sos"]


def test_generated_pause_resume_ladders_with_large_boards(tmp_path):
    """Checks generated schedules that combine slot replacement, rollback, and boundaries."""
    records = []
    for seed in (41, 43, 47):
        names = [f"H{seed}{suffix}" for suffix in "ABCDEF"]
        for game_no, size in enumerate((9, 13, 19, 26)):
            black = names[(game_no + seed) % len(names)]
            white = names[(game_no * 2 + seed) % len(names)]
            if black == white:
                white = names[(game_no * 2 + seed + 1) % len(names)]
            mode = "territory" if game_no % 2 else "area"
            records.append(
                f"GAME hard{seed}_{game_no} {size} "
                f"{0.5 * (game_no + 1)} {black} {white} {mode}"
            )
            b_setup = " ".join(
                key((seed + i * 3) % size, (game_no + i * 5) % size) for i in range(5)
            )
            w_setup = " ".join(
                key((seed + i * 7 + 1) % size, (game_no + i * 2 + 3) % size)
                for i in range(5)
            )
            records.append("SETUP B " + b_setup)
            records.append("SETUP W " + w_setup)
            records.append(
                f"PLAY {['B', 'W'][(seed + game_no) % 2]} {key(size - 1, size - 1)}"
            )
            records.append(f"PAUSE slot-{seed % 5}")
            records.append(f"GAME filler{seed}_{game_no} 5 0 {names[0]} {names[-1]}")
            records.append(f"FORFEIT {names[0] if game_no % 2 else names[-1]}")
            records.append(f"RESUME slot-{seed % 5}")
            records.append("UNDO 9")
            records.append(f"PLAY W {key(size - 2, size - 1)}")
            records.append(f"PLAY B {key(size - 1, size - 2)}")
            records.append("PLAY W pass")
            records.append("PLAY B pass")
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert len(actual["games"]) >= 18


def test_repeated_pair_sos_counts_each_completed_game(tmp_path):
    """Checks repeated opponents are counted once per completed game for SOS."""
    records = [
        "GAME rr1 5 0 Alpha Beta",
        "FORFEIT Alpha",
        "GAME rr2 5 0 Alpha Beta",
        "FORFEIT Beta",
        "GAME rr3 5 0 Alpha Gamma",
        "FORFEIT Gamma",
        "GAME rr4 5 0 Beta Gamma",
        "FORFEIT Beta",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    rows = {row["player"]: row for row in actual["standings"]}
    assert rows["Alpha"]["sos"] == 5
    assert rows["Beta"]["sos"] == 3


def test_repeated_opponents_after_rulings_still_count_per_game(tmp_path):
    """Checks SOS after duplicate pairings and retroactive rewritten outcomes."""
    records = [
        "GAME pair1 5 0 North South",
        "FORFEIT North",
        "GAME pair2 5 0 North South",
        "FORFEIT South",
        "GAME pair3 5 0 North South",
        "FORFEIT North",
        "GAME cross1 5 0 North East",
        "FORFEIT East",
        "GAME cross2 5 0 South East",
        "FORFEIT South",
        "RULING pair2 W 4",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    rows = {row["player"]: row for row in actual["standings"]}
    expected_rows = {row["player"]: row for row in expected["standings"]}
    assert rows["North"]["sos"] == expected_rows["North"]["sos"]
    assert rows["South"]["sos"] == expected_rows["South"]["sos"]


def test_rulings_rewrite_completed_games_and_standings(tmp_path):
    """Checks retroactive game rulings flow into records, SOS, and tiebreaks."""
    records = [
        "GAME appeal1 5 0 Alpha Beta",
        "FORFEIT Alpha",
        "GAME appeal2 5 0 Gamma Delta",
        "FORFEIT Gamma",
        "GAME table 5 0 Alpha Gamma",
        "FORFEIT Gamma",
        "RULING appeal1 W 3",
        "RULING appeal2 draw 9",
        "RULING missing B 2",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    games = {game["id"]: game for game in actual["games"]}
    assert games["appeal1"] == {
        "id": "appeal1",
        "black": "Alpha",
        "white": "Beta",
        "winner": "Beta",
        "reason": "ruling",
        "margin": 3,
    }
    assert games["appeal2"]["winner"] == "draw"
    assert games["appeal2"]["margin"] == 0


def test_rulings_use_earliest_completed_duplicate_id(tmp_path):
    """Checks duplicate ids, suspension boundaries, and repeated rulings."""
    records = [
        "GAME dup 5 0 EarlyB EarlyW",
        "PLAY B aa",
        "PAUSE hold",
        "GAME dup 5 0 LaterB LaterW",
        "FORFEIT LaterB",
        "RULING dup W 5",
        "RESUME hold",
        "PLAY W bb",
        "SCORE",
        "RULING dup draw 4",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["dup", "dup"]
    assert actual["games"][0]["winner"] == "draw"
    assert actual["games"][0]["reason"] == "ruling"
    assert actual["games"][1]["winner"] == expected["games"][1]["winner"]


def test_unfinished_games_are_abandoned_and_finished_games_ignore_late_records(tmp_path):
    """Checks non-local record boundaries after unfinished and already scored games."""
    records = [
        "GAME abandoned 5 0 DropA DropB",
        "PLAY B aa",
        "PLAY W bb",
        "GAME scored 5 0 KeepA KeepB",
        "PLAY B aa",
        "PLAY W pass",
        "PLAY B pass",
        "UNDO 2",
        "PLAY W cc",
        "SCORE",
        "GAME illegaldone 5 0 DoneA DoneB",
        "PLAY B aa",
        "PLAY W aa",
        "SCORE",
        "FORFEIT DoneB",
        "GAME final 5 0 LastA LastB",
        "FORFEIT LastB",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["scored", "illegaldone", "final"]
    assert actual["games"][1]["reason"] == "illegal"


def test_completed_games_ignore_pause_resume_undo_and_late_play(tmp_path):
    """Checks that completed games ignore every later record until a boundary."""
    records = [
        "GAME done 5 0 DoneB DoneW",
        "PLAY B aa",
        "SCORE",
        "PAUSE after-done",
        "UNDO 1",
        "RESUME after-done",
        "PLAY W bb",
        "FORFEIT DoneW",
        "GAME next 5 0 NextB NextW",
        "FORFEIT NextW",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert [game["id"] for game in actual["games"]] == ["done", "next"]


def test_multiway_standings_bucket_and_repeated_opponents(tmp_path):
    """Checks standings across uneven schedules with repeated pairings."""
    records = [
        "GAME a1 5 0 Orion Lyra",
        "FORFEIT Orion",
        "GAME a2 5 0 Lyra Vega",
        "FORFEIT Lyra",
        "GAME a3 5 0 Vega Orion",
        "FORFEIT Orion",
        "GAME b1 5 0 Orion Nova",
        "FORFEIT Nova",
        "GAME b2 5 0 Lyra Nova",
        "FORFEIT Nova",
        "GAME b3 5 0 Vega Nova",
        "FORFEIT Nova",
        "GAME c1 5 0 Orion Pulsar",
        "FORFEIT Orion",
        "GAME c2 5 0 Lyra Pulsar",
        "FORFEIT Pulsar",
        "GAME c3 5 0 Vega Pulsar",
        "FORFEIT Pulsar",
        "GAME repeat1 5 0 Nova Pulsar",
        "FORFEIT Nova",
        "GAME repeat2 5 0 Nova Pulsar",
        "FORFEIT Pulsar",
    ]
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    rows = {row["player"]: row for row in actual["standings"]}
    assert rows["Nova"]["sos"] == 10
    assert rows["Pulsar"]["sos"] == 12
    assert [row["player"] for row in actual["standings"][:3]] == ["Nova", "Pulsar", "Orion"]


def stress_records(seed):
    """Build a deterministic stress schedule with generated board interactions."""
    rng = random.Random(seed)
    names = [f"S{seed}{suffix}" for suffix in "ABCDEFGH"]
    coords = [key(x, y) for y in range(7) for x in range(7)]
    records = []
    for game_no in range(14):
        size = 5 + game_no % 3
        black = names[(game_no * 3) % len(names)]
        white = names[(game_no * 3 + 2) % len(names)]
        if black == white:
            white = names[(game_no * 3 + 3) % len(names)]
        komi = [0, 0.5, 2.5, 5.5][(seed + game_no) % 4]
        scoring = "territory" if game_no % 3 == 1 else "area"
        records.append(
            f"GAME stress{seed}_{game_no} {size} {komi} {black} {white} {scoring}"
        )
        sample = rng.sample(coords[: size * size], min(8, size * size))
        records.append("SETUP B " + " ".join(sample[:3] + [sample[0], "zz"]))
        records.append("SETUP W " + " ".join(sample[2:6] + ["a", sample[3]]))
        turn = rng.choice(["B", "W"])
        used = set(sample[:6])
        for ply in range(10):
            roll = rng.random()
            if roll < 0.12:
                records.append(f"UNDO {rng.randint(1, 3)}")
            elif roll < 0.30:
                records.append(f"PLAY {turn} pass")
                turn = other(turn)
            else:
                if rng.random() < 0.16 and used:
                    move = rng.choice(sorted(used))
                else:
                    move = rng.choice(coords[: size * size])
                    used.add(move)
                records.append(f"PLAY {turn} {move}")
                turn = other(turn)
            if ply == 4 and game_no % 4 == 2:
                records.append(f"PAUSE stress-slot-{seed}-{game_no}")
                records.append(f"RESUME stress-slot-{seed}-{game_no}")
        if game_no % 5 == 0:
            records.append(f"FORFEIT {rng.choice([black, white])}")
        else:
            records.append("SCORE")
    return records


def test_generated_stress_records_cover_record_boundary_and_scoring_layers(tmp_path):
    """Checks deterministic generated schedules with abandons, illegal plays, and ties."""
    records = []
    for seed in (3, 19, 23, 29, 31):
        records.extend(stress_records(seed))
    actual = run_arbiter(tmp_path, records)
    expected = expected_report(records)
    assert_report(actual, expected)
    assert len(actual["games"]) >= 18


def generated_records(seed):
    a, b, c, d = [f"P{seed}{suffix}" for suffix in "ABCD"]
    return [
        f"GAME k{seed} 6 0 {a} {b}",
        "SETUP B ab ba bc",
        "SETUP W bb ca cc db",
        "PLAY B cb",
        "PLAY W bb",
        f"GAME s{seed} 6 0 {c} {d}",
        "SETUP B bc cb cd dc",
        "PLAY W cc",
        f"GAME u{seed} 6 2.5 {a} {c}",
        "SETUP W aa af",
        "PLAY W bb",
        "PLAY B pass",
        "PLAY W cc",
        "UNDO 2",
        "PLAY B dd",
        "PLAY W ee",
        "SCORE",
        f"GAME f{seed} 6 0 {b} {d}",
        f"FORFEIT {d}",
        f"GAME n{seed} 6 0 {a} {d}",
        "SETUP B aa ab ac",
        "SETUP W fa fb fc",
        "SCORE",
    ]


def test_generated_schedules(tmp_path):
    """Checks additional deterministic schedules with the same public contract."""
    records = []
    for seed in (7, 9, 11, 13, 17):
        records.extend(generated_records(seed))
    assert_report(run_arbiter(tmp_path, records), expected_report(records))
