import json
import subprocess
from pathlib import Path

APP = Path("/app/bin/table-official")


def run_case(tmp_path, payload):
    src = tmp_path / "in.json"
    dst = tmp_path / "out.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run([str(APP), "adjudicate", str(src), str(dst)], check=True)
    return json.loads(dst.read_text(encoding="utf-8"))


def by_player(rows):
    return {row["player"]: row["points"] for row in rows}


def test_case_alpha(tmp_path):
    """A hanchan score sheet settles a closed single-ron hand and table counters correctly."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 2,
            "riichi_sticks": 1,
            "result": {
                "type": "win", "winner": "B", "from": "C", "riichi": False, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["chiitoitsu"], "dora": 0, "wait": "pair", "pair": "5m",
                    "groups": [
                        {"kind": "pair", "tiles": ["5m", "5m"]}, {"kind": "pair", "tiles": ["2p", "2p"]},
                        {"kind": "pair", "tiles": ["3s", "3s"]}, {"kind": "pair", "tiles": ["east", "east"]},
                        {"kind": "pair", "tiles": ["white", "white"]}, {"kind": "pair", "tiles": ["7m", "7m"]},
                        {"kind": "pair", "tiles": ["9s", "9s"]}
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "win", "winner": "B", "han": 2, "fu": 25, "limit": "normal"}
    assert by_player(out["scores"]) == {"A": 25000, "B": 28200, "C": 22800, "D": 25000}
    assert out["riichi_sticks"] == 0
    assert out["honba"] == 0


def test_case_alpha_honba_ron_is_isolated(tmp_path):
    """A non-dealer ron with honba applies the table counter payment directly."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 3,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "B", "from": "C", "riichi": False, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["pinfu"], "dora": 0, "wait": "open", "pair": "5p",
                    "groups": [
                        {"kind": "sequence", "tiles": ["2m", "3m", "4m"]},
                        {"kind": "sequence", "tiles": ["3p", "4p", "5p"]},
                        {"kind": "sequence", "tiles": ["6s", "7s", "8s"]},
                        {"kind": "sequence", "tiles": ["4m", "5m", "6m"]},
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "win", "winner": "B", "han": 1, "fu": 30, "limit": "normal"}
    assert by_player(out["scores"]) == {"A": 25000, "B": 26900, "C": 23100, "D": 25000}
    assert out["honba"] == 0


def test_case_beta(tmp_path):
    """A declaration with an unresolved table liability is scored as a round penalty."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 1,
            "honba": 3,
            "riichi_sticks": 2,
            "result": {
                "type": "win", "winner": "B", "from": "D", "riichi": True, "furiten": True,
                "hand": {
                    "closed": True, "yaku": ["riichi", "pinfu"], "dora": 1, "wait": "open", "pair": "5p",
                    "groups": [
                        {"kind": "sequence", "tiles": ["2m", "3m", "4m"]},
                        {"kind": "sequence", "tiles": ["3p", "4p", "5p"]},
                        {"kind": "sequence", "tiles": ["6s", "7s", "8s"]},
                        {"kind": "sequence", "tiles": ["4m", "5m", "6m"]},
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "chombo", "offender": "B"}
    assert by_player(out["scores"]) == {"A": 29000, "B": 13000, "C": 29000, "D": 29000}
    assert out["riichi_sticks"] == 2
    assert out["honba"] == 3


def test_case_gamma(tmp_path):
    """An exhaustive hand gives the same table result from independent seating and point checks."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 1,
            "riichi_sticks": 0,
            "result": {
                "type": "draw",
                "tenpai": ["A", "C"],
                "nagashi": ["D"],
                "discards": {
                    "A": ["2m", "3m"], "B": ["4p", "6p"], "C": ["5s", "8s"],
                    "D": ["1m", "9m", "east", "red", "N"]
                },
                "called_discards": []
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "nagashi", "winner": "D", "limit": "mangan"}
    assert by_player(out["scores"]) == {"A": 20900, "B": 22900, "C": 22900, "D": 33300}
    assert out["honba"] == 0
    assert [row["player"] for row in out["standings"]] == ["D", "B", "C", "A"]


def test_case_gamma_rejects_ineligible_nagashi_and_keeps_riichi_deposit(tmp_path):
    """A rejected nagashi claim falls back to exhaustive-draw settlement while riichi sticks stay live."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 1,
            "riichi_sticks": 0,
            "result": {
                "type": "draw",
                "tenpai": ["A", "C"],
                "riichi": ["D"],
                "nagashi": ["D"],
                "discards": {"D": ["1m", "5m", "east"]},
                "called_discards": []
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "draw", "tenpai": ["A", "C"]}
    assert by_player(out["scores"]) == {"A": 26500, "B": 23500, "C": 26500, "D": 22500}
    assert out["riichi_sticks"] == 1
    assert out["honba"] == 2


def test_case_delta(tmp_path):
    """Equal scores keep the original seating order in the final table."""
    out = run_case(tmp_path, {"players": ["Zed", "Mika", "Orin", "Ava"], "start_points": 25000, "rounds": []})
    assert [row["player"] for row in out["standings"]] == ["Zed", "Mika", "Orin", "Ava"]
    assert [row["points"] for row in out["standings"]] == [25000, 25000, 25000, 25000]
    assert [row["rank"] for row in out["standings"]] == [1, 1, 1, 1]


def test_case_delta_explicit_chombo_and_tied_ranks(tmp_path):
    """An explicit penalty round uses the chombo payment schedule and shared ranks for equal scores."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 2,
            "riichi_sticks": 1,
            "result": {"type": "chombo", "offender": "B"},
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "chombo", "offender": "B"}
    assert by_player(out["scores"]) == {"A": 29000, "B": 17000, "C": 27000, "D": 27000}
    assert out["riichi_sticks"] == 1
    assert out["honba"] == 2
    assert out["standings"] == [
        {"player": "A", "points": 29000, "rank": 1},
        {"player": "C", "points": 27000, "rank": 2},
        {"player": "D", "points": 27000, "rank": 2},
        {"player": "B", "points": 17000, "rank": 4},
    ]


def test_case_epsilon(tmp_path):
    """A dealer mangan tsumo awards the riichi pool and keeps the dealer on seat."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 0,
            "riichi_sticks": 2,
            "result": {
                "type": "win", "winner": "A", "from": None, "riichi": False, "furiten": False,
                "hand": {
                    "closed": False, "yaku": ["honitsu", "toitoi"], "dora": 0, "wait": "open", "pair": "5m",
                    "groups": [
                        {"kind": "triplet", "open": True, "tiles": ["2m", "2m", "2m"]},
                        {"kind": "triplet", "open": True, "tiles": ["3m", "3m", "3m"]},
                        {"kind": "triplet", "open": True, "tiles": ["4m", "4m", "4m"]},
                        {"kind": "triplet", "open": True, "tiles": ["6m", "6m", "6m"]},
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "win", "winner": "A", "han": 5, "fu": 30, "limit": "mangan"}
    assert by_player(out["scores"]) == {"A": 39000, "B": 21000, "C": 21000, "D": 21000}
    assert out["riichi_sticks"] == 0
    assert out["honba"] == 1


def test_case_epsilon_honba_tsumo_counter_is_per_loser(tmp_path):
    """A non-zero honba on tsumo is charged once by each non-winner."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 2,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "A", "from": None, "riichi": False, "furiten": False,
                "hand": {
                    "closed": False, "yaku": ["honitsu", "toitoi"], "dora": 0, "wait": "open", "pair": "5m",
                    "groups": [
                        {"kind": "triplet", "open": True, "tiles": ["2m", "2m", "2m"]},
                        {"kind": "triplet", "open": True, "tiles": ["3m", "3m", "3m"]},
                        {"kind": "triplet", "open": True, "tiles": ["4m", "4m", "4m"]},
                        {"kind": "triplet", "open": True, "tiles": ["6m", "6m", "6m"]},
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "win", "winner": "A", "han": 5, "fu": 30, "limit": "mangan"}
    assert by_player(out["scores"]) == {"A": 37600, "B": 20800, "C": 20800, "D": 20800}
    assert out["honba"] == 3


def test_case_zeta(tmp_path):
    """Consecutive rounds carry dealer, honba, and point state through a short hanchan slice."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [
            {
                "dealer": 0,
                "honba": 0,
                "riichi_sticks": 0,
                "result": {
                    "type": "win", "winner": "B", "from": "C", "riichi": False, "furiten": False,
                    "hand": {
                        "closed": True, "yaku": ["riichi", "pinfu"], "dora": 0, "wait": "open", "pair": "5p",
                        "groups": [
                            {"kind": "sequence", "tiles": ["2m", "3m", "4m"]},
                            {"kind": "sequence", "tiles": ["3p", "4p", "5p"]},
                            {"kind": "sequence", "tiles": ["6s", "7s", "8s"]},
                            {"kind": "sequence", "tiles": ["4m", "5m", "6m"]},
                        ],
                    },
                },
            },
            {
                "result": {"type": "draw", "tenpai": ["A", "C"]},
            },
            {
                "result": {
                    "type": "win", "winner": "C", "from": None, "riichi": False, "furiten": False,
                    "hand": {
                        "closed": False, "yaku": ["toitoi"], "dora": 0, "wait": "open", "pair": "5m",
                        "groups": [
                            {"kind": "triplet", "open": True, "tiles": ["2m", "2m", "2m"]},
                            {"kind": "triplet", "open": True, "tiles": ["3p", "3p", "3p"]},
                            {"kind": "triplet", "open": True, "tiles": ["4s", "4s", "4s"]},
                            {"kind": "triplet", "open": True, "tiles": ["6m", "6m", "6m"]},
                        ],
                    },
                },
            },
        ],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"] == [
        {"kind": "win", "winner": "B", "han": 2, "fu": 30, "limit": "normal"},
        {"kind": "draw", "tenpai": ["A", "C"]},
        {"kind": "win", "winner": "C", "han": 2, "fu": 30, "limit": "normal"},
    ]
    assert by_player(out["scores"]) == {"A": 25400, "B": 24400, "C": 27800, "D": 22400}
    assert out["honba"] == 2
    assert out["riichi_sticks"] == 0
    assert [row["player"] for row in out["standings"]] == ["C", "A", "B", "D"]


def test_case_eta(tmp_path):
    """A closed color hand with quads reports the high-value yaku and doubled closed-group fu."""
    payload = {
        "players": ["A", "B", "C", "D"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 0,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "C", "from": "D", "riichi": False, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["chinitsu"], "dora": 0, "wait": "pair", "pair": "5m",
                    "groups": [
                        {"kind": "quad", "tiles": ["1m", "1m", "1m", "1m"]},
                        {"kind": "quad", "tiles": ["2m", "2m", "2m", "2m"]},
                        {"kind": "triplet", "tiles": ["9m", "9m", "9m"]},
                        {"kind": "sequence", "tiles": ["3m", "4m", "5m"]},
                    ],
                },
            },
        }],
    }
    out = run_case(tmp_path, payload)
    assert out["rounds"][0] == {"kind": "win", "winner": "C", "han": 6, "fu": 90, "limit": "haneman"}
    assert by_player(out["scores"]) == {"A": 25000, "B": 25000, "C": 37000, "D": 13000}
    assert out["honba"] == 0


def test_case_theta(tmp_path):
    """Named yaku totals reach the upper limit bands, not just the common hands."""
    baiman = run_case(tmp_path, {
        "players": ["East", "West", "North", "South"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 0,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "West", "from": None, "riichi": False, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["menzen_tsumo", "tanyao", "iipeikou"], "dora": 5,
                    "wait": "open", "pair": "5p",
                    "groups": [
                        {"kind": "sequence", "tiles": ["2m", "3m", "4m"]},
                        {"kind": "sequence", "tiles": ["3p", "4p", "5p"]},
                        {"kind": "sequence", "tiles": ["4s", "5s", "6s"]},
                        {"kind": "sequence", "tiles": ["6m", "7m", "8m"]},
                    ],
                },
            },
        }],
    })
    assert baiman["rounds"][0] == {"kind": "win", "winner": "West", "han": 8, "fu": 30, "limit": "baiman"}

    sanbaiman = run_case(tmp_path, {
        "players": ["East", "West", "North", "South"],
        "start_points": 25000,
        "rounds": [{
            "dealer": 0,
            "honba": 0,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "North", "from": "South", "riichi": False, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["yakuhai", "sanankou", "honroutou"], "dora": 6,
                    "wait": "pair", "pair": "red",
                    "groups": [
                        {"kind": "triplet", "tiles": ["1m", "1m", "1m"]},
                        {"kind": "triplet", "tiles": ["9m", "9m", "9m"]},
                        {"kind": "triplet", "tiles": ["east", "east", "east"]},
                        {"kind": "triplet", "tiles": ["white", "white", "white"]},
                    ],
                },
            },
        }],
    })
    assert sanbaiman["rounds"][0] == {"kind": "win", "winner": "North", "han": 11, "fu": 70, "limit": "sanbaiman"}

    yakuman = run_case(tmp_path, {
        "players": ["East", "West", "North", "South"],
        "start_points": 50000,
        "rounds": [{
            "dealer": 0,
            "honba": 0,
            "riichi_sticks": 0,
            "result": {
                "type": "win", "winner": "East", "from": "West", "riichi": True, "furiten": False,
                "hand": {
                    "closed": True, "yaku": ["riichi", "honitsu", "toitoi", "sanankou", "honroutou", "yakuhai"], "dora": 2,
                    "wait": "open", "pair": "red",
                    "groups": [
                        {"kind": "triplet", "tiles": ["1m", "1m", "1m"]},
                        {"kind": "triplet", "tiles": ["9m", "9m", "9m"]},
                        {"kind": "triplet", "tiles": ["1p", "1p", "1p"]},
                        {"kind": "triplet", "tiles": ["east", "east", "east"]},
                    ],
                },
            },
        }],
    })
    assert yakuman["rounds"][0] == {"kind": "win", "winner": "East", "han": 13, "fu": 70, "limit": "yakuman"}
