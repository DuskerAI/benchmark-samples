import hashlib
import json
import subprocess
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

APP = Path("/app/arena")
BIN = APP / "bin" / "arena-official"


def run(cmd, *, expect=0):
    result = subprocess.run(
        cmd, cwd=APP, check=False, text=True, capture_output=True, timeout=30
    )
    assert result.returncode == expect, result.stderr + result.stdout
    return result


def build_tool():
    run([str(APP / "scripts" / "build.sh")])
    assert BIN.exists()


def write_match(tmp_path, name, match):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(match, indent=2) + "\n")
    return path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def cheb(a, b):
    return max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))


def reference_dossier(match, limit=None, saved=None):
    if saved is None:
        units = {u["id"]: {**deepcopy(u), "alive": u["hp"] > 0} for u in match["units"]}
        transcript = []
        visibility = []
        eliminated = {}
        rounds = {}
        start_index = 0
    else:
        units = deepcopy(saved["units"])
        transcript = deepcopy(saved["transcript"])
        visibility = deepcopy(saved["visibility"])
        eliminated = deepcopy(saved["eliminated"])
        rounds = deepcopy(saved["rounds"])
        start_index = saved["prefixLength"]

    def living():
        return [u for u in units.values() if u["alive"]]

    def log_visibility(round_no, band_no):
        for team in match["teams"]:
            viewers = [u for u in living() if u["team"] == team]
            visible = sorted(
                u["id"]
                for u in living()
                if any(cheb(v, u) <= v["vision"] for v in viewers)
            )
            visibility.append(
                {"round": round_no, "band": band_no, "team": team, "visible": visible}
            )

    if saved is None:
        log_visibility(0, 0)

    events = match["events"][start_index:limit]
    by_round = defaultdict(list)
    for ev in events:
        by_round[ev["round"]].append(ev)
    walls = {tuple(w) for w in match["arena"].get("walls", [])}

    for round_no in sorted(by_round):
        ordered = sorted(
            by_round[round_no],
            key=lambda e: (
                -(units.get(e["actor"], {"speed": -999})["speed"]),
                e["actor"],
            ),
        )
        rr = rounds.setdefault(
            str(round_no), {"round": round_no, "actions": 0, "eliminated": []}
        )
        band_no = 0
        i = 0
        while i < len(ordered):
            speed = units.get(ordered[i]["actor"], {"speed": -999})["speed"]
            band = []
            while (
                i < len(ordered)
                and units.get(ordered[i]["actor"], {"speed": -999})["speed"] == speed
            ):
                band.append(ordered[i])
                i += 1
            band_no += 1
            start_units = deepcopy(units)
            start_occ = {
                (u["x"], u["y"]): u["id"] for u in start_units.values() if u["alive"]
            }
            damage = defaultdict(int)
            pending = []
            moved_positions = {}
            for ev in band:
                actor = units.get(ev["actor"])
                start_actor = start_units.get(ev["actor"])
                valid = True
                note = "ok"
                targets = []
                if (
                    not actor
                    or not actor["alive"]
                    or not start_actor
                    or not start_actor["alive"]
                ):
                    valid = False
                    note = "actor-unavailable"
                elif ev["action"] == "move":
                    dx = ev.get("dx")
                    dy = ev.get("dy")
                    nx = start_actor["x"] + dx if isinstance(dx, int) else None
                    ny = start_actor["y"] + dy if isinstance(dy, int) else None
                    if (
                        dx not in (-1, 0, 1)
                        or dy not in (-1, 0, 1)
                        or nx is None
                        or ny is None
                        or nx < 0
                        or ny < 0
                        or nx >= match["arena"]["width"]
                        or ny >= match["arena"]["height"]
                        or (nx, ny) in walls
                        or (
                            (nx, ny) in start_occ and start_occ[(nx, ny)] != ev["actor"]
                        )
                    ):
                        valid = False
                        note = "illegal-move"
                    else:
                        moved_positions[ev["actor"]] = (nx, ny)
                elif ev["action"] == "strike":
                    target = start_units.get(ev.get("target"))
                    if (
                        not target
                        or not target["alive"]
                        or cheb(start_actor, target) > 1
                    ):
                        valid = False
                        note = "illegal-target"
                    else:
                        targets = [target["id"]]
                elif ev["action"] == "blast":
                    x = ev.get("x")
                    y = ev.get("y")
                    if (
                        not isinstance(x, int)
                        or not isinstance(y, int)
                        or cheb(start_actor, {"x": x, "y": y}) > 3
                    ):
                        valid = False
                        note = "illegal-target"
                    else:
                        targets = sorted(
                            u["id"]
                            for u in start_units.values()
                            if u["alive"]
                            and u["team"] != start_actor["team"]
                            and cheb(u, {"x": x, "y": y}) <= 1
                        )
                elif ev["action"] != "wait":
                    valid = False
                    note = "unknown-action"
                if valid and ev["action"] in ("strike", "blast"):
                    for target_id in targets:
                        damage[target_id] += start_actor["attack"]
                pending.append((ev, valid, note, targets))
            for actor_id, (x, y) in moved_positions.items():
                if units[actor_id]["alive"]:
                    units[actor_id]["x"] = x
                    units[actor_id]["y"] = y
            total_after = {
                uid: start_units[uid]["hp"] - amount for uid, amount in damage.items()
            }
            for ev, valid, note, targets in pending:
                effects = []
                if valid and ev["action"] in ("strike", "blast"):
                    for target_id in sorted(targets):
                        before = start_units[target_id]["hp"]
                        amount = start_units[ev["actor"]]["attack"]
                        effects.append(
                            {
                                "unit": target_id,
                                "amount": amount,
                                "hpBefore": before,
                                "hpAfter": total_after[target_id],
                            }
                        )
                transcript.append(
                    {
                        "round": round_no,
                        "band": band_no,
                        "actor": ev["actor"],
                        "action": ev["action"],
                        "valid": valid,
                        "note": note,
                        "effects": effects,
                    }
                )
                rr["actions"] += 1
            for target_id, amount in damage.items():
                if units[target_id]["alive"]:
                    units[target_id]["hp"] -= amount
            for unit_id in sorted(
                [u["id"] for u in units.values() if u["alive"] and u["hp"] <= 0]
            ):
                units[unit_id]["alive"] = False
                eliminated[unit_id] = {
                    "id": unit_id,
                    "round": round_no,
                    "band": band_no,
                }
                rr["eliminated"].append(unit_id)
            rr["eliminated"].sort()
            log_visibility(round_no, band_no)

    transcript_text = "".join(
        json.dumps(row, separators=(",", ":")) + "\n" for row in transcript
    )
    visibility_text = "".join(
        json.dumps(row, separators=(",", ":")) + "\n" for row in visibility
    )
    live_teams = sorted({u["team"] for u in units.values() if u["alive"]})
    report = {
        "matchId": match["matchId"],
        "status": "ok",
        "winner": live_teams[0] if len(live_teams) == 1 else "draw",
        "rounds": sorted(rounds.values(), key=lambda r: r["round"]),
        "survivors": [
            {"id": u["id"], "team": u["team"], "hp": u["hp"], "x": u["x"], "y": u["y"]}
            for u in sorted(units.values(), key=lambda u: u["id"])
            if u["alive"]
        ],
        "eliminated": sorted(eliminated.values(), key=lambda e: e["id"]),
        "checksum": hashlib.sha256(
            transcript_text.encode() + b"\n" + visibility_text.encode()
        ).hexdigest(),
    }
    state = {
        "matchId": match["matchId"],
        "prefixLength": limit if limit is not None else len(match["events"]),
        "prefixDigest": hashlib.sha256(
            canonical(
                {
                    "matchId": match["matchId"],
                    "arena": match["arena"],
                    "teams": match["teams"],
                    "units": match["units"],
                    "events": match["events"][
                        : limit if limit is not None else len(match["events"])
                    ],
                }
            ).encode()
        ).hexdigest(),
        "units": units,
        "transcript": transcript,
        "visibility": visibility,
        "eliminated": eliminated,
        "rounds": rounds,
    }
    return report, transcript_text, visibility_text, state


def assert_dossier(out_dir, expected):
    report, transcript_text, visibility_text, _ = expected
    assert json.loads((out_dir / "report.json").read_text()) == report
    assert (out_dir / "report.json").read_text() == json.dumps(report, indent=2) + "\n"
    assert (out_dir / "transcript.jsonl").read_text() == transcript_text
    assert (out_dir / "visibility.jsonl").read_text() == visibility_text
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "report.json",
        "transcript.jsonl",
        "visibility.jsonl",
    ]


def assert_checkpoint_state(state_path, match, count):
    _, _, _, expected = reference_dossier(match, limit=count)
    state = json.loads(state_path.read_text())
    assert set(state) == {
        "matchId",
        "prefixLength",
        "prefixDigest",
        "units",
        "transcript",
        "visibility",
        "eliminated",
        "rounds",
    }
    assert state["matchId"] == expected["matchId"]
    assert state["prefixLength"] == count
    assert state["prefixDigest"] == expected["prefixDigest"]
    assert state["units"] == expected["units"]
    assert state["transcript"] == expected["transcript"]
    assert state["visibility"] == expected["visibility"]
    assert state["eliminated"] == expected["eliminated"]
    assert state["rounds"] == expected["rounds"]


def hidden_match():
    return {
        "matchId": "hidden-cup",
        "arena": {"width": 7, "height": 6, "walls": [[3, 1], [3, 2], [1, 4]]},
        "teams": ["amber", "cobalt", "dusk"],
        "units": [
            {
                "id": "A1",
                "team": "amber",
                "hp": 8,
                "attack": 4,
                "speed": 5,
                "vision": 2,
                "x": 1,
                "y": 1,
            },
            {
                "id": "A2",
                "team": "amber",
                "hp": 10,
                "attack": 3,
                "speed": 3,
                "vision": 1,
                "x": 0,
                "y": 3,
            },
            {
                "id": "C1",
                "team": "cobalt",
                "hp": 7,
                "attack": 4,
                "speed": 5,
                "vision": 3,
                "x": 2,
                "y": 1,
            },
            {
                "id": "C2",
                "team": "cobalt",
                "hp": 9,
                "attack": 5,
                "speed": 2,
                "vision": 2,
                "x": 5,
                "y": 2,
            },
            {
                "id": "D1",
                "team": "dusk",
                "hp": 6,
                "attack": 6,
                "speed": 5,
                "vision": 2,
                "x": 2,
                "y": 2,
            },
            {
                "id": "D2",
                "team": "dusk",
                "hp": 8,
                "attack": 2,
                "speed": 1,
                "vision": 4,
                "x": 6,
                "y": 4,
            },
        ],
        "events": [
            {"round": 1, "actor": "C1", "action": "strike", "target": "A1"},
            {"round": 1, "actor": "A1", "action": "strike", "target": "C1"},
            {"round": 1, "actor": "D1", "action": "blast", "x": 1, "y": 1},
            {"round": 1, "actor": "A2", "action": "move", "dx": 1, "dy": 0},
            {"round": 1, "actor": "C2", "action": "blast", "x": 1, "y": 3},
            {"round": 1, "actor": "D2", "action": "strike", "target": "C2"},
            {"round": 2, "actor": "A1", "action": "strike", "target": "C1"},
            {"round": 2, "actor": "C1", "action": "strike", "target": "D1"},
            {"round": 2, "actor": "D1", "action": "strike", "target": "C1"},
            {"round": 2, "actor": "A2", "action": "blast", "x": 2, "y": 2},
            {"round": 2, "actor": "C2", "action": "move", "dx": -1, "dy": 0},
            {"round": 2, "actor": "D2", "action": "wait"},
            {"round": 3, "actor": "A2", "action": "strike", "target": "D1"},
            {"round": 3, "actor": "C2", "action": "blast", "x": 1, "y": 2},
            {"round": 3, "actor": "D2", "action": "move", "dx": -1, "dy": -1},
        ],
    }


def invalid_match():
    match = hidden_match()
    match["matchId"] = "invalid-cup"
    match["events"] = [
        {"round": 1, "actor": "A2", "action": "move", "dx": 1, "dy": 0},
        {"round": 1, "actor": "C2", "action": "blast", "x": 0, "y": 5},
        {"round": 1, "actor": "D2", "action": "strike", "target": "missing"},
        {"round": 2, "actor": "A2", "action": "move", "dx": 0, "dy": 1},
        {"round": 2, "actor": "C2", "action": "strike", "target": "D2"},
        {"round": 2, "actor": "D2", "action": "strike", "target": "C2"},
        {"round": 3, "actor": "C2", "action": "wait"},
        {"round": 3, "actor": "D2", "action": "move", "dx": -1, "dy": 0},
    ]
    return match


def unavailable_actor_match():
    return {
        "matchId": "unavailable-cup",
        "arena": {"width": 4, "height": 4, "walls": []},
        "teams": ["red", "blue"],
        "units": [
            {
                "id": "A",
                "team": "red",
                "hp": 9,
                "attack": 5,
                "speed": 4,
                "vision": 3,
                "x": 1,
                "y": 1,
            },
            {
                "id": "B",
                "team": "blue",
                "hp": 5,
                "attack": 1,
                "speed": 2,
                "vision": 3,
                "x": 2,
                "y": 1,
            },
        ],
        "events": [
            {"round": 1, "actor": "A", "action": "strike", "target": "B"},
            {"round": 2, "actor": "B", "action": "wait"},
            {"round": 2, "actor": "ghost", "action": "strike", "target": "A"},
        ],
    }


def unsupported_action_match():
    return {
        "matchId": "unsupported-cup",
        "arena": {"width": 5, "height": 5, "walls": []},
        "teams": ["red", "blue"],
        "units": [
            {
                "id": "A",
                "team": "red",
                "hp": 10,
                "attack": 3,
                "speed": 4,
                "vision": 3,
                "x": 1,
                "y": 1,
            },
            {
                "id": "B",
                "team": "blue",
                "hp": 5,
                "attack": 1,
                "speed": 2,
                "vision": 3,
                "x": 2,
                "y": 1,
            },
        ],
        "events": [
            {"round": 1, "actor": "A", "action": "teleport", "x": 4, "y": 4},
            {"round": 2, "actor": "A", "action": "strike", "target": "B"},
            {"round": 2, "actor": "B", "action": "wait"},
        ],
    }


def test_visible_league_case_matches_the_public_arena_contract(tmp_path):
    """The bundled league case must produce the contract dossier byte for byte."""
    build_tool()
    match_path = APP / "cases" / "league-opener.json"
    match = json.loads(match_path.read_text())
    out_dir = tmp_path / "visible-out"
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))


def test_generated_arena_covers_ties_simultaneous_damage_and_visibility(tmp_path):
    """A generated same-schema match is checked by an independent replay engine."""
    build_tool()
    match = hidden_match()
    match_path = write_match(tmp_path, "hidden", match)
    out_dir = tmp_path / "hidden-out"
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))


def test_invalid_commands_are_recorded_without_changing_later_adjudication(tmp_path):
    """Invalid arena commands must be non-mutating transcript entries."""
    build_tool()
    match = invalid_match()
    match_path = write_match(tmp_path, "invalid", match)
    out_dir = tmp_path / "invalid-out"
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))
    transcript = [
        json.loads(line)
        for line in (out_dir / "transcript.jsonl").read_text().splitlines()
    ]
    assert [row["valid"] for row in transcript[:3]] == [True, False, False]
    assert all(row["effects"] == [] for row in transcript[1:3])


def test_unavailable_actors_are_logged_without_mutating_the_arena(tmp_path):
    """Unknown and eliminated actors must be invalid transcript rows with no effects."""
    build_tool()
    match = unavailable_actor_match()
    match_path = write_match(tmp_path, "unavailable", match)
    out_dir = tmp_path / "unavailable-out"
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))
    transcript = [
        json.loads(line)
        for line in (out_dir / "transcript.jsonl").read_text().splitlines()
    ]
    assert [row["actor"] for row in transcript] == ["A", "B", "ghost"]
    assert [row["valid"] for row in transcript] == [True, False, False]
    assert [row["note"] for row in transcript[1:]] == [
        "actor-unavailable",
        "actor-unavailable",
    ]
    assert all(row["effects"] == [] for row in transcript[1:])
    report = json.loads((out_dir / "report.json").read_text())
    assert report["survivors"] == [{"id": "A", "team": "red", "hp": 9, "x": 1, "y": 1}]
    assert report["eliminated"] == [{"id": "B", "round": 1, "band": 1}]


def test_unsupported_actions_are_logged_without_mutating_later_state(tmp_path):
    """Unsupported actions must be invalid transcript rows and leave later replay intact."""
    build_tool()
    match = unsupported_action_match()
    match_path = write_match(tmp_path, "unsupported", match)
    out_dir = tmp_path / "unsupported-out"
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))
    transcript = [
        json.loads(line)
        for line in (out_dir / "transcript.jsonl").read_text().splitlines()
    ]
    assert transcript[0]["actor"] == "A"
    assert transcript[0]["action"] == "teleport"
    assert transcript[0]["valid"] is False
    assert transcript[0]["note"] == "unknown-action"
    assert transcript[0]["effects"] == []
    assert transcript[1]["valid"] is True
    report = json.loads((out_dir / "report.json").read_text())
    assert report["survivors"] == [
        {"id": "A", "team": "red", "hp": 10, "x": 1, "y": 1},
        {"id": "B", "team": "blue", "hp": 2, "x": 2, "y": 1},
    ]


def test_checkpoint_resume_matches_full_adjudication_and_rejects_changed_prefix(
    tmp_path,
):
    """Checkpoint, resume, and prefix rejection must follow the saved-prefix contract."""
    build_tool()
    match = hidden_match()
    match_path = write_match(tmp_path, "resume", match)
    full_dir = tmp_path / "full"
    checkpoint_dir = tmp_path / "checkpoint"
    resume_dir = tmp_path / "resume"
    state_path = tmp_path / "state.json"
    run([str(BIN), "adjudicate", str(match_path), str(full_dir)])
    run(
        [
            str(BIN),
            "checkpoint",
            str(match_path),
            "6",
            str(state_path),
            str(checkpoint_dir),
        ]
    )
    run([str(BIN), "resume", str(match_path), str(state_path), str(resume_dir)])
    assert_dossier(full_dir, reference_dossier(match))
    assert_dossier(checkpoint_dir, reference_dossier(match, limit=6))
    assert_checkpoint_state(state_path, match, 6)
    assert (resume_dir / "report.json").read_text() == (
        full_dir / "report.json"
    ).read_text()
    assert (resume_dir / "transcript.jsonl").read_text() == (
        full_dir / "transcript.jsonl"
    ).read_text()
    assert (resume_dir / "visibility.jsonl").read_text() == (
        full_dir / "visibility.jsonl"
    ).read_text()

    zero_state = tmp_path / "state-zero.json"
    zero_dir = tmp_path / "checkpoint-zero"
    zero_resume_dir = tmp_path / "resume-zero"
    run([str(BIN), "checkpoint", str(match_path), "0", str(zero_state), str(zero_dir)])
    run([str(BIN), "resume", str(match_path), str(zero_state), str(zero_resume_dir)])
    assert_dossier(zero_dir, reference_dossier(match, limit=0))
    assert_checkpoint_state(zero_state, match, 0)
    assert (zero_resume_dir / "report.json").read_text() == (
        full_dir / "report.json"
    ).read_text()
    assert (zero_resume_dir / "transcript.jsonl").read_text() == (
        full_dir / "transcript.jsonl"
    ).read_text()
    assert (zero_resume_dir / "visibility.jsonl").read_text() == (
        full_dir / "visibility.jsonl"
    ).read_text()

    final_count = len(match["events"])
    final_state = tmp_path / "state-final.json"
    final_checkpoint_dir = tmp_path / "checkpoint-final"
    final_resume_dir = tmp_path / "resume-final"
    run(
        [
            str(BIN),
            "checkpoint",
            str(match_path),
            str(final_count),
            str(final_state),
            str(final_checkpoint_dir),
        ]
    )
    run([str(BIN), "resume", str(match_path), str(final_state), str(final_resume_dir)])
    assert_dossier(final_checkpoint_dir, reference_dossier(match, limit=final_count))
    assert_checkpoint_state(final_state, match, final_count)
    assert (final_resume_dir / "report.json").read_text() == (
        full_dir / "report.json"
    ).read_text()
    assert (final_resume_dir / "transcript.jsonl").read_text() == (
        full_dir / "transcript.jsonl"
    ).read_text()
    assert (final_resume_dir / "visibility.jsonl").read_text() == (
        full_dir / "visibility.jsonl"
    ).read_text()

    tampered = deepcopy(match)
    tampered["events"][0], tampered["events"][1] = (
        tampered["events"][1],
        tampered["events"][0],
    )
    tampered_path = write_match(tmp_path, "tampered", tampered)
    rejected_dir = tmp_path / "rejected"
    run(
        [str(BIN), "resume", str(tampered_path), str(state_path), str(rejected_dir)],
        expect=2,
    )
    assert json.loads((rejected_dir / "report.json").read_text()) == {
        "matchId": "hidden-cup",
        "status": "rejected",
        "reason": "prefix-mismatch",
    }
    assert (rejected_dir / "transcript.jsonl").read_text() == ""
    assert (rejected_dir / "visibility.jsonl").read_text() == ""
    assert sorted(p.name for p in rejected_dir.iterdir()) == [
        "report.json",
        "transcript.jsonl",
        "visibility.jsonl",
    ]


def test_output_directory_is_recreated_for_each_dossier(tmp_path):
    """The official must replace stale dossier contents in the requested output directory."""
    build_tool()
    match = hidden_match()
    match_path = write_match(tmp_path, "clean", match)
    out_dir = tmp_path / "clean-out"
    out_dir.mkdir()
    (out_dir / "old.txt").write_text("stale")
    (out_dir / "nested").mkdir()
    (out_dir / "nested" / "old.txt").write_text("stale")
    run([str(BIN), "adjudicate", str(match_path), str(out_dir)])
    assert_dossier(out_dir, reference_dossier(match))
    assert not (out_dir / "old.txt").exists()
    assert not (out_dir / "nested").exists()
