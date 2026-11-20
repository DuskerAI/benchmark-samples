"""Behavioral verifier for the installed Shogi adjudication command."""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def installed_command():
    """Build the adjudication command that the task asks the agent to provide."""
    subprocess.run(
        ["make", "-C", "/opt/shogictl", "install"],
        check=True,
        capture_output=True,
        text=True,
    )


def run_judge(games):
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "games.json"
        dst = Path(tmp) / "out.json"
        src.write_text(json.dumps(games), encoding="utf-8")
        subprocess.run(
            ["/usr/local/bin/shogi-judge", str(src), str(dst)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(dst.read_text(encoding="utf-8"))


def test_adjudicates_mixed_games():
    """Verify pawn-drop mate, nifu, promotion, self-check, bishop, drop, and capture cases."""
    games = [
        {
            "id": "a1",
            "initial": "8k/9/9/9/9/9/4P4/9/4K4 b P",
            "moves": ["P*5e"],
        },
        {
            "id": "a2",
            "initial": "4k4/9/5p3/9/9/9/9/9/K8 w p",
            "moves": ["P*4e"],
        },
        {
            "id": "a3",
            "initial": "4k4/9/9/9/9/4s4/9/9/4K4 w -",
            "moves": ["5f5g+"],
        },
        {
            "id": "a4",
            "initial": "4r3k/9/9/9/9/9/9/4G4/4K4 b -",
            "moves": ["5h4h"],
        },
        {
            "id": "a5",
            "initial": "4k4/9/9/4B4/9/9/9/9/4K4 b -",
            "moves": ["5d4c"],
        },
        {
            "id": "a6",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b G",
            "moves": ["G*5e"],
        },
        {
            "id": "a7",
            "initial": "k8/9/9/4+p4/4R4/9/9/9/8K b -",
            "moves": ["5e5d", "9a8a", "P*5e"],
        },
    ]
    expected = {
        "a1": "illegal:1",
        "a2": "illegal:1",
        "a3": "ok",
        "a4": "illegal:1",
        "a5": "ok",
        "a6": "ok",
        "a7": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"mixed-game adjudication mismatch: {result}"


def test_adjudicates_late_game_results():
    """Verify quiet repetition and both colors' position-based perpetual-check rulings."""
    quiet = ["5h5g", "5b5c", "5g5h", "5c5b"] * 3
    checking = ["5a4a", "5b4b", "4a5a", "4b5b"] * 3
    checking_white = ["5i4i", "5h4h", "4i5i", "4h5h"] * 3
    games = [
        {
            "id": "b1",
            "initial": "8k/4g4/9/9/9/9/9/4G4/K8 b -",
            "moves": quiet,
        },
        {
            "id": "b2",
            "initial": "4k4/4R4/9/9/9/9/9/9/4K4 w -",
            "moves": checking,
        },
        {
            "id": "b3",
            "initial": "4k4/9/9/9/9/9/9/4r4/4K4 b -",
            "moves": checking_white,
        },
    ]
    expected = {
        "b1": "draw",
        "b2": "black_perpetual_check",
        "b3": "white_perpetual_check",
    }
    result = run_judge(games)
    assert result == expected, f"late-game adjudication mismatch: {result}"


def test_rejects_malformed_records_before_replay():
    """Malformed SFEN triples, king counts, and wrongly typed game records are illegal:0."""
    games = [
        {
            "id": "bt1",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 x -",
            "moves": [],
        },
        {
            "id": "bt2",
            "initial": "4k4/9/9/9/9/9/9/4K4 b -",
            "moves": [],
        },
        {
            "id": "bt3",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b 02P",
            "moves": [],
        },
        {
            "id": "bt4",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b K",
            "moves": [],
        },
        {
            "id": "bt5",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b -",
            "moves": ["5i5h", 17],
        },
        {
            "id": "bt6",
            "initial": "4k4/9/9/9/9/9/9/9/9 b -",
            "moves": [],
        },
        {
            "id": "bt7",
            "initial": "4k4/9/9/9/9/9/9/9/3KK4 b -",
            "moves": [],
        },
    ]
    expected = {
        "bt1": "illegal:0",
        "bt2": "illegal:0",
        "bt3": "illegal:0",
        "bt4": "illegal:0",
        "bt5": "illegal:0",
        "bt6": "illegal:0",
        "bt7": "illegal:0",
    }
    result = run_judge(games)
    assert result == expected, f"malformed-record handling mismatch: {result}"


def test_adjudicates_restricted_finish():
    """Verify forced terminal rulings for pawn-drop mate, nifu, and unresolved check."""
    games = [
        {
            "id": "c1",
            "initial": "3lkl3/3l1l3/4G4/9/9/9/9/9/K8 b P",
            "moves": ["P*5b"],
        },
        {
            "id": "c2",
            "initial": "4k4/9/9/9/9/9/4p4/9/4K4 w p",
            "moves": ["P*5e"],
        },
        {
            "id": "c3",
            "initial": "4r3k/9/9/9/9/9/9/4S4/4K4 b -",
            "moves": ["5h6g"],
        },
    ]
    expected = {
        "c1": "illegal:1",
        "c2": "illegal:1",
        "c3": "illegal:1",
    }
    result = run_judge(games)
    assert result == expected, f"restricted-finish mismatch: {result}"


def test_preserves_captured_piece_and_promotion_rules():
    """Captured promoted pieces become unpromoted hand pieces, and promotion is enforced."""
    games = [
        {
            "id": "d1",
            "initial": "4k4/9/9/9/4+p4/5S3/9/9/4K4 b -",
            "moves": ["4f5e", "5a6a", "P*5f"],
        },
        {
            "id": "d2",
            "initial": "k8/4P4/9/9/9/9/9/9/4K4 b -",
            "moves": ["5b5a"],
        },
        {
            "id": "d3",
            "initial": "k8/4P4/9/9/9/9/9/9/4K4 b -",
            "moves": ["5b5a+"],
        },
        {
            "id": "d4",
            "initial": "k8/9/9/9/5B3/9/9/9/4K4 b -",
            "moves": ["4e2c"],
        },
        {
            "id": "d5",
            "initial": "k8/9/9/9/5B3/9/9/9/4K4 b -",
            "moves": ["4e2c+"],
        },
    ]
    expected = {
        "d1": "ok",
        "d2": "illegal:1",
        "d3": "ok",
        "d4": "ok",
        "d5": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"capture/promotion mismatch: {result}"


def test_marks_drop_restrictions_at_the_drop_ply():
    """Verify drop-specific illegal moves and drop syntax are attributed to the drop ply."""
    games = [
        {
            "id": "e1",
            "initial": "3lkl3/3l1l3/4G4/9/9/9/9/9/K8 w P",
            "moves": ["6b6c", "P*5b"],
        },
        {
            "id": "e2",
            "initial": "k8/9/9/9/9/9/9/9/4K4 b N",
            "moves": ["N*5b"],
        },
        {
            "id": "e3",
            "initial": "4k4/9/9/9/9/9/9/9/K8 w n",
            "moves": ["N*5h"],
        },
        {
            "id": "e4",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b P",
            "moves": ["p*5e"],
        },
    ]
    expected = {
        "e1": "illegal:2",
        "e2": "illegal:1",
        "e3": "illegal:1",
        "e4": "illegal:1",
    }
    result = run_judge(games)
    assert result == expected, f"drop-restriction ply mismatch: {result}"


def test_validates_individual_piece_movement_edges():
    """Exercise lance, knight, silver, gold, rook, and promoted-rook movement boundaries."""
    games = [
        {
            "id": "f1",
            "initial": "4k4/9/9/9/4L4/9/9/9/4K4 b -",
            "moves": ["5e5b"],
        },
        {
            "id": "f2",
            "initial": "4k4/9/9/9/4L4/9/9/9/4K4 b -",
            "moves": ["5e4e"],
        },
        {
            "id": "f3",
            "initial": "4k4/9/9/9/4N4/9/9/9/4K4 b -",
            "moves": ["5e4c"],
        },
        {
            "id": "f4",
            "initial": "4k4/9/9/9/4N4/9/9/9/4K4 b -",
            "moves": ["5e5c"],
        },
        {
            "id": "f5",
            "initial": "4k4/9/9/9/4S4/9/9/9/4K4 b -",
            "moves": ["5e4d"],
        },
        {
            "id": "f6",
            "initial": "4k4/9/9/9/4G4/9/9/9/4K4 b -",
            "moves": ["5e4f"],
        },
        {
            "id": "f7",
            "initial": "4k4/9/9/9/4R4/9/9/9/4K4 b -",
            "moves": ["5e2e"],
        },
        {
            "id": "f8",
            "initial": "4k4/9/9/4P4/4R4/9/9/9/4K4 b -",
            "moves": ["5e5b"],
        },
        {
            "id": "f9",
            "initial": "4k4/9/9/9/4+R4/9/9/9/4K4 b -",
            "moves": ["5e4d"],
        },
        {
            "id": "f10",
            "initial": "4k4/9/9/9/4+R4/9/9/9/4K4 b -",
            "moves": ["5e3c"],
        },
    ]
    expected = {
        "f1": "ok",
        "f2": "illegal:1",
        "f3": "ok",
        "f4": "illegal:1",
        "f5": "ok",
        "f6": "illegal:1",
        "f7": "ok",
        "f8": "illegal:1",
        "f9": "ok",
        "f10": "illegal:1",
    }
    result = run_judge(games)
    assert result == expected, f"piece-movement edge mismatch: {result}"


def test_enforces_dead_lance_drops_and_king_safety():
    """Exercise lance dead-square drops and king moves into attacked squares."""
    games = [
        {
            "id": "g1",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b L",
            "moves": ["L*5a"],
        },
        {
            "id": "g2",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b L",
            "moves": ["L*5b"],
        },
        {
            "id": "g3",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 w l",
            "moves": ["L*5i"],
        },
        {
            "id": "g4",
            "initial": "4k4/9/9/9/9/9/9/4r4/4K4 b -",
            "moves": ["5i4i"],
        },
        {
            "id": "g5",
            "initial": "4k4/9/9/9/9/9/9/5r3/4K4 b -",
            "moves": ["5i4i"],
        },
    ]
    expected = {
        "g1": "illegal:1",
        "g2": "ok",
        "g3": "illegal:1",
        "g4": "ok",
        "g5": "illegal:1",
    }
    result = run_judge(games)
    assert result == expected, f"dead-drop/king-safety mismatch: {result}"


def test_repetition_can_begin_after_the_initial_position():
    """Verify perpetual check when the repeated checked position first appears after a move."""
    black_check_cycle = ["5a4a", "5b4b", "4a5a", "4b5b"] * 3
    white_check_cycle = ["5i4i", "5h4h", "4i5i", "4h5h"] * 3
    games = [
        {
            "id": "h1",
            "initial": "4k4/9/4R4/9/9/9/9/9/4K4 b -",
            "moves": ["5c5b", *black_check_cycle],
        },
        {
            "id": "h2",
            "initial": "4k4/9/9/9/9/9/4r4/9/4K4 w -",
            "moves": ["5g5h", *white_check_cycle],
        },
    ]
    expected = {
        "h1": "black_perpetual_check",
        "h2": "white_perpetual_check",
    }
    result = run_judge(games)
    assert result == expected, f"post-initial repetition mismatch: {result}"


def test_rejects_king_capture_and_requires_check_relief():
    """King capture is illegal, and checked sides must resolve the check before play continues."""
    games = [
        {
            "id": "i1",
            "initial": "4k4/4R4/9/9/9/9/9/9/4K4 b -",
            "moves": ["5b5a"],
        },
        {
            "id": "i2",
            "initial": "4k4/9/9/9/9/9/4r4/9/4K4 b G",
            "moves": ["G*1e"],
        },
        {
            "id": "i3",
            "initial": "4k4/9/9/9/9/9/4r4/9/4K4 b G",
            "moves": ["G*5h"],
        },
    ]
    expected = {
        "i1": "illegal:1",
        "i2": "illegal:1",
        "i3": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"king-safety adjudication mismatch: {result}"


def test_tracks_color_direction_promotions_and_hand_counts():
    """Exercise white movement, invalid promotions, multi-count hands, and depletion across drops."""
    games = [
        {
            "id": "j1",
            "initial": "4k4/9/9/9/9/9/4p4/9/4K4 w -",
            "moves": ["5g5h"],
        },
        {
            "id": "j2",
            "initial": "4k4/9/9/9/9/9/4p4/9/4K4 w -",
            "moves": ["5g5f"],
        },
        {
            "id": "j3",
            "initial": "k8/9/9/9/9/9/9/4G4/4K4 b -",
            "moves": ["5h5g+"],
        },
        {
            "id": "j4",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b 2P",
            "moves": ["P*4e", "5a4a", "P*5e"],
        },
        {
            "id": "j5",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b 2P",
            "moves": ["P*5e", "5a4a", "P*5d"],
        },
        {
            "id": "j6",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b P",
            "moves": ["P*4e", "5a4a", "P*5e"],
        },
    ]
    expected = {
        "j1": "ok",
        "j2": "illegal:1",
        "j3": "illegal:1",
        "j4": "ok",
        "j5": "illegal:3",
        "j6": "illegal:3",
    }
    result = run_judge(games)
    assert result == expected, f"direction/promotion/hand-count mismatch: {result}"


def test_combines_promoted_sliders_blockers_and_demoted_captures():
    """Promoted sliders retain range plus king steps, blockers stop sliders, and captures demote into hand."""
    games = [
        {
            "id": "k1",
            "initial": "4k4/9/9/9/4+B4/9/9/9/4K4 b -",
            "moves": ["5e5d"],
        },
        {
            "id": "k2",
            "initial": "4k4/9/9/9/4+B4/9/9/9/4K4 b -",
            "moves": ["5e5c"],
        },
        {
            "id": "k3",
            "initial": "4k4/9/4P4/9/4R4/9/9/9/4K4 b -",
            "moves": ["5e5b"],
        },
        {
            "id": "k4",
            "initial": "4k4/9/9/9/4+R4/9/9/9/4K4 b -",
            "moves": ["5e5b"],
        },
        {
            "id": "k5",
            "initial": "4k4/9/9/9/4+p4/5S3/9/9/4K4 b -",
            "moves": ["4f5e", "5a4a", "P*4f"],
        },
    ]
    expected = {
        "k1": "ok",
        "k2": "illegal:1",
        "k3": "illegal:1",
        "k4": "ok",
        "k5": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"promoted-slider/capture mismatch: {result}"


def test_adjudicates_cross_color_promotion_and_demoted_hand_reuse():
    """Exercise white-side captures, promoted major-piece demotion, and promoted slider movement."""
    games = [
        {
            "id": "l1",
            "initial": "4k4/9/9/5s3/4+P4/9/9/9/4K4 w -",
            "moves": ["4d5e", "5i4i", "P*4d"],
        },
        {
            "id": "l2",
            "initial": "4k4/9/9/9/4+b4/5G3/9/9/4K4 b -",
            "moves": ["4f5e", "5a4a", "B*4f"],
        },
        {
            "id": "l3",
            "initial": "4k4/9/9/9/4+r4/9/9/9/4K4 w -",
            "moves": ["5e4f"],
        },
        {
            "id": "l4",
            "initial": "4k4/9/9/9/4+r4/9/9/9/4K4 w -",
            "moves": ["5e3g"],
        },
    ]
    expected = {
        "l1": "ok",
        "l2": "ok",
        "l3": "ok",
        "l4": "illegal:1",
    }
    result = run_judge(games)
    assert result == expected, f"cross-color promotion/demotion mismatch: {result}"


def test_scores_entering_king_declarations_after_legal_replay():
    """Entering-king results require camped safe kings and side-specific material totals."""
    games = [
        {
            "id": "m1",
            "initial": "9/9/4K4/9/9/9/4k4/9/9 b 2R2B14P2r2b14p",
            "moves": [],
        },
        {
            "id": "m2",
            "initial": "9/9/4K4/9/9/9/4k4/9/9 b 2R2B14P",
            "moves": [],
        },
        {
            "id": "m3",
            "initial": "9/9/4K4/9/9/9/4k4/9/9 b 2r2b14p",
            "moves": [],
        },
        {
            "id": "m4",
            "initial": "9/9/4K4/9/4r4/9/4k4/9/9 b 2R2B14P2r2b14p",
            "moves": [],
        },
        {
            "id": "m5",
            "initial": "9/9/4K4/9/9/9/4k4/9/9 b 2R2B3P2r2b3p",
            "moves": [],
        },
    ]
    expected = {
        "m1": "jishogi",
        "m2": "black_entering_king",
        "m3": "white_entering_king",
        "m4": "white_entering_king",
        "m5": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"entering-king adjudication mismatch: {result}"


def test_entering_king_material_counts_board_hand_and_base_piece_values():
    """Material points include promoted board pieces by base kind and demoted captures in hand."""
    games = [
        {
            "id": "n1",
            "initial": "8+R/8+B/4K4/9/9/9/4k4/9/9 b 14P2r2b14p",
            "moves": [],
        },
        {
            "id": "n2",
            "initial": "9/9/4K3+r/8G/9/9/4k4/9/9 b 2B13P2r2b14p",
            "moves": ["1d1c"],
        },
    ]
    expected = {
        "n1": "jishogi",
        "n2": "jishogi",
    }
    result = run_judge(games)
    assert result == expected, f"entering-king material accounting mismatch: {result}"


def test_promoted_minors_king_adjacency_and_zone_leaving_promotions():
    """Promoted minors move as golds, kings attack adjacent squares, and source-zone promotion is legal."""
    games = [
        {
            "id": "o1",
            "initial": "4k4/9/9/9/4+P4/9/9/9/4K4 b -",
            "moves": ["5e4e"],
        },
        {
            "id": "o2",
            "initial": "4k4/9/9/9/4+P4/9/9/9/4K4 b -",
            "moves": ["5e4f"],
        },
        {
            "id": "o3",
            "initial": "4k4/9/9/9/4+s4/9/9/9/4K4 w -",
            "moves": ["5e6e"],
        },
        {
            "id": "o4",
            "initial": "4k4/9/9/9/4+s4/9/9/9/4K4 w -",
            "moves": ["5e4d"],
        },
        {
            "id": "o5",
            "initial": "9/9/9/9/4k4/9/9/9/4K4 b -",
            "moves": ["5i5h", "5e5f", "5h5g"],
        },
        {
            "id": "o6",
            "initial": "4k4/9/4R4/9/9/9/9/9/4K4 b -",
            "moves": ["5c5d+"],
        },
    ]
    expected = {
        "o1": "ok",
        "o2": "illegal:1",
        "o3": "ok",
        "o4": "illegal:1",
        "o5": "illegal:3",
        "o6": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"promoted-minor/king-adjacency mismatch: {result}"


def test_repetition_distinguishes_hands_after_piece_transfers():
    """Repetition keys include both hands after drops and non-promoting pawn advances."""
    quiet_after_hand_change = [
        "5h5g",
        "5b5c",
        "5g5h",
        "5c5b",
        "5h5g",
        "5b5c",
        "5g5h",
        "5c5b",
        "5h5g",
        "5b5c",
        "5g5h",
        "5c5b",
    ]
    games = [
        {
            "id": "p1",
            "initial": "8k/4g4/9/9/9/9/9/4G4/K8 b P",
            "moves": ["P*1e", "5b5c", "1e1d", "5c5b", *quiet_after_hand_change],
        }
    ]
    expected = {"p1": "draw"}
    result = run_judge(games)
    assert result == expected, f"repetition state/history mismatch: {result}"


def test_drop_and_capture_state_after_longer_replay():
    """Drops consume hand pieces, captures demote promoted pieces, and promoted pawns do not cause nifu."""
    games = [
        {
            "id": "q1",
            "initial": "4k4/9/9/9/4+P4/9/9/9/4K4 b P",
            "moves": ["P*5f"],
        },
        {
            "id": "q2",
            "initial": "4k4/9/9/9/4+p4/5S3/9/9/4K4 b -",
            "moves": ["4f5e", "5a6a", "P*5f", "6a5a", "P*5e"],
        },
        {
            "id": "q3",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b 2G",
            "moves": ["G*4e", "5a4a", "G*5e", "4a5a", "G*6e"],
        },
    ]
    expected = {
        "q1": "ok",
        "q2": "illegal:5",
        "q3": "illegal:5",
    }
    result = run_judge(games)
    assert result == expected, f"drop/capture state mismatch: {result}"


def test_handles_interlocking_check_relief_and_pawn_drop_mate_exceptions():
    """Check relief must account for captures, king escapes, drops, and pawn-drop mate exceptions."""
    games = [
        {
            "id": "r1",
            "initial": "4k4/9/9/9/9/9/4r4/9/4K4 b G",
            "moves": ["G*5h"],
        },
        {
            "id": "r2",
            "initial": "4k4/9/9/9/9/9/4r4/9/4K4 b B",
            "moves": ["B*5h"],
        },
        {
            "id": "r3",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b P",
            "moves": ["P*5b"],
        },
        {
            "id": "r4",
            "initial": "3lkl3/3l1l3/4G4/9/9/9/9/9/K8 b P",
            "moves": ["P*5b"],
        },
        {
            "id": "r5",
            "initial": "4k4/9/9/9/9/9/9/4R4/4K4 w g",
            "moves": ["G*5b"],
        },
    ]
    expected = {
        "r1": "ok",
        "r2": "ok",
        "r3": "ok",
        "r4": "illegal:1",
        "r5": "ok",
    }
    result = run_judge(games)
    assert result == expected, f"compound check-relief mismatch: {result}"


def test_replays_long_cycles_with_promotions_and_stateful_terminal_scoring():
    """Longer legal replays must preserve promotion state, hands, and final entering-king scoring."""
    black_check_cycle = ["5a4a", "5b4b", "4a5a", "4b5b"] * 3
    games = [
        {
            "id": "s1",
            "initial": "4k4/9/9/9/9/9/9/9/4K4 b 3G",
            "moves": ["G*4e", "5a4a", "G*5e", "4a5a", "G*6e"],
        },
        {
            "id": "s2",
            "initial": "4k4/9/4R4/9/9/9/9/9/4K4 b -",
            "moves": ["5c5b", *black_check_cycle],
        },
        {
            "id": "s3",
            "initial": "9/9/4K3+r/8G/9/9/4k4/9/9 b 2B13P2r2b14p",
            "moves": ["1d1c"],
        },
    ]
    expected = {
        "s1": "ok",
        "s2": "black_perpetual_check",
        "s3": "jishogi",
    }
    result = run_judge(games)
    assert result == expected, f"long-cycle/stateful scoring mismatch: {result}"
