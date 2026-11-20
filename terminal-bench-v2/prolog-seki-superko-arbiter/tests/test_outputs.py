import json
import subprocess
from pathlib import Path


APP = Path("/app")
BIN = APP / "bin" / "go_official"


def build_official():
    subprocess.run(["/app/scripts/build.sh"], check=True, cwd=APP)
    assert BIN.exists()


def write_record(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_official(args, *, check=True):
    result = subprocess.run([str(BIN), *map(str, args)], cwd=APP, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def game_by_id(report, game_id):
    return next(game for game in report["games"] if game["id"] == game_id)


def test_board_repetition_is_adjudicated(tmp_path):
    """A recorded fight that recreates an earlier position is not accepted as normal play."""
    build_official()
    record = write_record(
        tmp_path,
        "fight.rec",
        [
            "game(k1,5,0.5,ada,ben,area).",
            "move(b,ba).",
            "move(w,bb).",
            "move(b,ab).",
            "move(w,ac).",
            "move(b,cb).",
            "move(w,cc).",
            "move(b,ee).",
            "move(w,bd).",
            "move(b,bc).",
            "move(w,bb).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "k1")
    assert game["status"] == "illegal"
    assert game["winner"] == "ada"
    assert game["winner_color"] == "b"


def test_delayed_positional_superko_uses_full_board_history(tmp_path):
    """A legal-looking move that recreates a much older board position is rejected."""
    build_official()
    record = write_record(
        tmp_path,
        "long_cycle.rec",
        [
            "game(sk2,3,0.5,iris,jules,area).",
            "move(b,aa).",
            "move(w,ba).",
            "move(b,ca).",
            "move(w,bc).",
            "move(b,bb).",
            "move(w,ab).",
            "move(b,ba).",
            "move(w,cb).",
            "move(b,aa).",
            "move(w,ac).",
            "move(b,ca).",
            "move(w,cc).",
            "move(b,bb).",
            "move(w,bc).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "sk2")
    assert game["status"] == "illegal"
    assert game["winner"] == "iris"
    assert game["winner_color"] == "b"


def test_capture_changes_territory_score_with_prisoner_credit(tmp_path):
    """A captured stone is removed before scoring and credited as a prisoner."""
    build_official()
    record = write_record(
        tmp_path,
        "capture.rec",
        [
            "game(c1,3,0,alice,bob,territory).",
            "move(b,ba).",
            "move(w,aa).",
            "move(b,ab).",
            "pass(w).",
            "move(b,bb).",
            "pass(w).",
            "pass(b).",
            "score.",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "c1")
    assert game["status"] == "scored"
    assert game["black_score"] == 7
    assert game["white_score"] == 0
    assert game["winner"] == "alice"


def test_suicide_move_is_illegal_play(tmp_path):
    """A move with no liberties after capture resolution forfeits the game."""
    build_official()
    record = write_record(
        tmp_path,
        "suicide.rec",
        [
            "game(s1,5,0,alice,bob,area).",
            "move(b,cb).",
            "move(w,aa).",
            "move(b,bc).",
            "move(w,ee).",
            "move(b,dc).",
            "move(w,ae).",
            "move(b,cd).",
            "move(w,cc).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "s1")
    assert game["status"] == "illegal"
    assert game["winner"] == "alice"
    assert game["winner_color"] == "b"


def test_timeout_penalty_is_subtracted_from_score(tmp_path):
    """Timeout records reduce the timed-out color's final numeric score."""
    build_official()
    record = write_record(
        tmp_path,
        "timeout.rec",
        [
            "game(t1,3,0.5,alice,bob,area).",
            "timeout(b,2).",
            "pass(b).",
            "pass(w).",
            "score.",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "t1")
    assert game["status"] == "scored"
    assert game["black_score"] == -2
    assert game["white_score"] == 0.5
    assert game["winner"] == "bob"


def test_setup_stones_seed_scoring_turns_and_late_setup_is_illegal(tmp_path):
    """Initial setup stones affect play and scoring but cannot appear after play starts."""
    build_official()
    record = write_record(
        tmp_path,
        "setup.rec",
        [
            "game(handicap,5,0,ann,bea,area).",
            "setup(b,bb).",
            "setup(b,db).",
            "setup(w,cc).",
            "move(b,aa).",
            "move(w,ee).",
            "pass(b).",
            "pass(w).",
            "score.",
            "game(late_setup,3,0,ada,ben,area).",
            "move(b,aa).",
            "setup(w,bb).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    handicap = game_by_id(report, "handicap")
    assert handicap["status"] == "scored"
    assert handicap["black_score"] == 3
    assert handicap["white_score"] == 2
    assert handicap["winner"] == "ann"
    late_setup = game_by_id(report, "late_setup")
    assert late_setup["status"] == "illegal"
    assert late_setup["reason"] == "illegal_play"
    assert late_setup["winner"] == "ada"
    assert late_setup["winner_color"] == "b"


def test_scoring_handles_unowned_points_and_dead_chains(tmp_path):
    """Scoring distinguishes neutral intersections from owned points and applies adjudicated dead stones."""
    build_official()
    record = write_record(
        tmp_path,
        "score.rec",
        [
            "game(open,3,0.5,black_a,white_a,area).",
            "move(b,ba).",
            "move(w,cb).",
            "move(b,ab).",
            "move(w,bc).",
            "pass(b).",
            "pass(w).",
            "score.",
            "game(marked,3,0,black_b,white_b,territory).",
            "move(b,aa).",
            "move(w,bb).",
            "move(b,cc).",
            "pass(w).",
            "pass(b).",
            "status(bb,dead).",
            "score.",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    open_game = game_by_id(report, "open")
    assert open_game["status"] == "scored"
    assert open_game["black_score"] == 3
    assert open_game["white_score"] == 3.5
    assert open_game["winner"] == "white_a"
    marked = game_by_id(report, "marked")
    assert marked["status"] == "scored"
    assert marked["black_score"] == 8
    assert marked["white_score"] == 0
    assert marked["winner"] == "black_b"


def test_report_schema_order_and_draws_are_stable(tmp_path):
    """Reports keep adjudication order, expose the published fields, and preserve drawn games."""
    build_official()
    record = write_record(
        tmp_path,
        "schema.rec",
        [
            "game(first,1,0,kim,lee,area).",
            "pass(b).",
            "pass(w).",
            "score.",
            "game(second,3,0,lee,moon,area).",
            "resign(w).",
            "game(third,3,0,moon,kim,territory).",
            "resign(b).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    assert [game["id"] for game in report["games"]] == ["first", "second", "third"]
    game_keys = {"id", "status", "black", "white", "rule", "winner", "winner_color", "black_score", "white_score", "reason"}
    standings_keys = {"player", "wins", "score_diff"}
    assert all(set(game) == game_keys for game in report["games"])
    assert all(set(row) == standings_keys for row in report["standings"])
    first = report["games"][0]
    assert first["status"] == "scored"
    assert first["black_score"] == 0
    assert first["white_score"] == 0
    assert first["winner"] == "draw"
    assert first["winner_color"] == "draw"


def test_resignation_fields_and_non_scored_tiebreaks_are_explicit(tmp_path):
    """Resigned games keep null scores, a resignation reason, and decisive non-scored score_diff."""
    build_official()
    record = write_record(
        tmp_path,
        "resign_fields.rec",
        [
            "game(rb,5,6.5,ivy,noel,territory).",
            "resign(b).",
            "game(rw,5,0,noel,ivy,area).",
            "resign(w).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    rb = game_by_id(report, "rb")
    assert rb["status"] == "forfeit"
    assert rb["reason"] == "resignation"
    assert rb["winner"] == "noel"
    assert rb["winner_color"] == "w"
    assert rb["black_score"] is None
    assert rb["white_score"] is None
    rw = game_by_id(report, "rw")
    assert rw["status"] == "forfeit"
    assert rw["reason"] == "resignation"
    assert rw["winner"] == "noel"
    assert rw["winner_color"] == "b"
    assert [(row["player"], row["wins"], row["score_diff"]) for row in report["standings"]] == [
        ("noel", 2, 2),
        ("ivy", 0, -2),
    ]


def test_phase_invalid_records_are_illegal_without_scores(tmp_path):
    """Scoring-only records cannot be applied before both players have passed."""
    build_official()
    record = write_record(
        tmp_path,
        "phase.rec",
        [
            "game(early_status,3,0,alice,bob,area).",
            "move(b,aa).",
            "status(aa,dead).",
            "pass(w).",
            "pass(b).",
            "score.",
            "game(early_score,3,0,cid,dia,territory).",
            "score.",
            "game(late_pass,3,0,erin,fin,area).",
            "pass(b).",
            "pass(w).",
            "pass(b).",
            "game(empty_status,3,0,gail,hal,area).",
            "pass(b).",
            "pass(w).",
            "status(bb,dead).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    # early_status and early_score both violate the play/scoring phase boundary before scoring starts.
    for game_id in ("early_status", "early_score"):
        game = game_by_id(report, game_id)
        assert game["status"] == "illegal"
        assert game["reason"] == "illegal_play"
        assert game["black_score"] is None
        assert game["white_score"] is None
    # late_pass reaches scoring first, so another pass is illegal and awards the opponent of the actor.
    late = game_by_id(report, "late_pass")
    assert late["status"] == "illegal"
    assert late["reason"] == "illegal_play"
    assert late["winner"] == "fin"
    assert late["winner_color"] == "w"
    # empty_status has no live chain at the coordinate, so the game ends as a colorless illegal result.
    empty = game_by_id(report, "empty_status")
    assert empty["status"] == "illegal"
    assert empty["reason"] == "illegal_play"
    assert empty["winner"] == "draw"
    assert empty["winner_color"] == "draw"
    assert empty["black_score"] is None
    assert empty["white_score"] is None


def test_new_game_record_closes_active_game_as_colorless_illegal(tmp_path):
    """A tournament record can recover from an abandoned game before adjudicating the next one."""
    build_official()
    record = write_record(
        tmp_path,
        "interrupted.rec",
        [
            "game(abandoned,3,0,ann,bob,area).",
            "move(b,aa).",
            "game(next,1,0,bob,ann,area).",
            "pass(b).",
            "pass(w).",
            "score.",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    assert [game["id"] for game in report["games"]] == ["abandoned", "next"]
    abandoned = game_by_id(report, "abandoned")
    assert abandoned["status"] == "illegal"
    assert abandoned["reason"] == "illegal_play"
    assert abandoned["winner"] == "draw"
    assert abandoned["winner_color"] == "draw"
    assert abandoned["black_score"] is None
    assert abandoned["white_score"] is None
    assert [(row["player"], row["wins"], row["score_diff"]) for row in report["standings"]] == [
        ("ann", 0, 0),
        ("bob", 0, 0),
    ]


def test_color_tied_illegal_games_update_decisive_non_scored_standings(tmp_path):
    """Illegal records attributed to a color award the opponent and affect standings as decisive games."""
    build_official()
    record = write_record(
        tmp_path,
        "decisive_illegal.rec",
        [
            "game(wrong_white,5,0,mina,nori,area).",
            "move(w,aa).",
            "game(late_timeout,5,0,nori,mina,area).",
            "pass(b).",
            "pass(w).",
            "timeout(b,1).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    report = read_json(report_path)
    wrong_white = game_by_id(report, "wrong_white")
    assert wrong_white["status"] == "illegal"
    assert wrong_white["reason"] == "illegal_play"
    assert wrong_white["winner"] == "mina"
    assert wrong_white["winner_color"] == "b"
    assert wrong_white["black_score"] is None
    assert wrong_white["white_score"] is None
    late_timeout = game_by_id(report, "late_timeout")
    assert late_timeout["status"] == "illegal"
    assert late_timeout["reason"] == "illegal_play"
    assert late_timeout["winner"] == "mina"
    assert late_timeout["winner_color"] == "w"
    assert late_timeout["black_score"] is None
    assert late_timeout["white_score"] is None
    assert [(row["player"], row["wins"], row["score_diff"]) for row in report["standings"]] == [
        ("mina", 2, 2),
        ("nori", 0, -2),
    ]


def test_area_scoring_removes_marked_dead_without_prisoner_credit(tmp_path):
    """Area scoring removes marked dead stones but does not credit them as prisoners."""
    build_official()
    record = write_record(
        tmp_path,
        "area_dead.rec",
        [
            "game(area_dead,3,0,ann,bea,area).",
            "move(b,aa).",
            "move(w,bb).",
            "move(b,cc).",
            "pass(w).",
            "pass(b).",
            "status(bb,dead).",
            "score.",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    game = game_by_id(read_json(report_path), "area_dead")
    assert game["status"] == "scored"
    assert game["black_score"] == 9
    assert game["white_score"] == 0
    assert game["winner"] == "ann"


def test_checkpoint_resume_preserves_setup_timeout_and_dead_status_scoring(tmp_path):
    """Checkpoint/resume keeps setup, timeout penalties, dead stones, draws, and standings aligned."""
    build_official()
    lines = [
        "game(mixed,3,0,ann,bea,territory).",
        "setup(b,aa).",
        "setup(w,bb).",
        "timeout(w,1).",
        "pass(b).",
        "pass(w).",
        "status(bb,dead).",
        "score.",
        "game(drawn,1,0,bea,ann,area).",
        "pass(b).",
        "pass(w).",
        "score.",
    ]
    record = write_record(tmp_path, "mixed_resume.rec", lines)
    full_report = tmp_path / "full.json"
    run_official(["adjudicate", record, full_report])
    full = read_json(full_report)
    mixed = game_by_id(full, "mixed")
    assert mixed["status"] == "scored"
    assert mixed["black_score"] == 9
    assert mixed["white_score"] == -1
    assert mixed["winner"] == "ann"
    drawn = game_by_id(full, "drawn")
    assert drawn["winner"] == "draw"
    assert drawn["winner_color"] == "draw"
    assert [(row["player"], row["wins"], row["score_diff"]) for row in full["standings"]] == [
        ("ann", 1, 10),
        ("bea", 0, -10),
    ]

    state = tmp_path / "state.json"
    prefix_report = tmp_path / "prefix.json"
    run_official(["checkpoint", record, 4, state, prefix_report])
    resumed_report = tmp_path / "resumed.json"
    run_official(["resume", record, state, resumed_report])
    assert read_json(resumed_report) == full

    changed = write_record(tmp_path, "changed_mixed.rec", [*lines[:3], "timeout(w,2).", *lines[4:]])
    bad_report = tmp_path / "bad_mixed.json"
    bad_report.write_text('{"sentinel": true}\n', encoding="utf-8")
    result = run_official(["resume", changed, state, bad_report], check=False)
    assert result.returncode != 0
    assert bad_report.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_checkpoint_resume_is_prefix_safe_and_equivalent(tmp_path):
    """A resumed tournament matches a full run and refuses a record whose saved opening changed."""
    build_official()
    lines = [
        "game(a,3,0,zoe,amy,area).",
        "setup(b,bb).",
        "resign(b).",
        "game(b,3,0,bob,zoe,area).",
        "resign(b).",
        "game(c,3,0,amy,bob,area).",
        "resign(b).",
    ]
    record = write_record(tmp_path, "league.rec", lines)
    full_report = tmp_path / "full.json"
    run_official(["adjudicate", record, full_report])
    state = tmp_path / "state.json"
    prefix_report = tmp_path / "prefix.json"
    run_official(["checkpoint", record, 5, state, prefix_report])
    resumed_report = tmp_path / "resumed.json"
    run_official(["resume", record, state, resumed_report])
    assert read_json(resumed_report) == read_json(full_report)
    changed = write_record(tmp_path, "changed.rec", [lines[0], "setup(b,cc).", *lines[2:]])
    bad_report = tmp_path / "bad.json"
    bad_report.write_text('{"sentinel": true}\n', encoding="utf-8")
    result = run_official(["resume", changed, state, bad_report], check=False)
    assert result.returncode != 0
    assert bad_report.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_standings_have_stable_tiebreaks(tmp_path):
    """Equal tournament records are ordered by the published deterministic tiebreakers."""
    build_official()
    record = write_record(
        tmp_path,
        "standings.rec",
        [
            "game(g1,3,0,zoe,amy,area).",
            "resign(b).",
            "game(g2,3,0,bob,zoe,area).",
            "resign(b).",
            "game(g3,3,0,amy,bob,area).",
            "resign(b).",
        ],
    )
    report_path = tmp_path / "report.json"
    run_official(["adjudicate", record, report_path])
    standings = read_json(report_path)["standings"]
    assert [(row["player"], row["wins"], row["score_diff"]) for row in standings] == [
        ("amy", 1, 0),
        ("bob", 1, 0),
        ("zoe", 1, 0),
    ]
