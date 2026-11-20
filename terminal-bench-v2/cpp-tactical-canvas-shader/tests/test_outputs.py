import json
import math
import subprocess
from pathlib import Path


ROOT = Path("/app/tacticref")


def _build():
    subprocess.run([str(ROOT / "scripts" / "build.sh")], check=True, cwd=ROOT)
    exe = ROOT / "bin" / "tactics_referee"
    assert exe.exists()
    return exe


def _inside(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def _bresenham(x0, y0, x1, y1):
    cells = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        cells.append((x, y))
        if x == x1 and y == y1:
            return cells
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _manhattan_offsets(radius):
    return [(dx, dy) for dx in range(-radius, radius + 1) for dy in range(-radius, radius + 1) if abs(dx) + abs(dy) <= radius]


def _flood_component(active, seed):
    if seed not in active:
        return set()
    seen = {seed}
    queue = [seed]
    for x, y in queue:
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            cell = (nx, ny)
            if cell in active and cell not in seen:
                seen.add(cell)
                queue.append(cell)
    return seen


def _grade_cells(active, minimum, maximum, width, height):
    out = set()
    for x, y in active:
        count = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and (nx, ny) in active:
                    count += 1
        if minimum <= count <= maximum:
            out.add((x, y))
    return out


def _route_cells(active, start, end, radius, width, height):
    def distances(seed):
        if seed not in active:
            return {}
        dist = {seed: 0}
        queue = [seed]
        for x, y in queue:
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                cell = (nx, ny)
                if cell in active and cell not in dist:
                    dist[cell] = dist[(x, y)] + 1
                    queue.append(cell)
        return dist

    from_start = distances(start)
    if end not in from_start:
        return set()
    from_end = distances(end)
    shortest = from_start[end]
    path_cells = {cell for cell in active if cell in from_start and cell in from_end and from_start[cell] + from_end[cell] == shortest}
    out = set()
    for px, py in path_cells:
        for ox in range(-radius, radius + 1):
            for oy in range(-radius, radius + 1):
                nx, ny = px + ox, py + oy
                if 0 <= nx < width and 0 <= ny < height and max(abs(ox), abs(oy)) <= radius and (nx, ny) in active:
                    out.add((nx, ny))
    return out


def _anchor_cells(active, source, radius):
    out = set()
    offsets = _manhattan_offsets(radius)
    expanded = set()
    for x, y in source:
        for dx, dy in offsets:
            expanded.add((x + dx, y + dy))
    for cell in active:
        if cell in expanded:
            out.add(cell)
    return out


def _model(turns):
    width = height = 0
    clips = [set()]
    saved = {}
    recorded = {}
    pieces = []
    t = 0.0
    select = (-1, -1)
    order = 0
    for raw in turns.strip().splitlines():
        parts = raw.split()
        if not parts:
            continue
        op = parts[0]
        if op == "board":
            width, height = map(int, parts[1:3])
            clips = [{(x, y) for x in range(width) for y in range(height)}]
            saved = {}
            recorded = {}
        elif op == "time":
            t = max(0.0, min(1.0, float(parts[1])))
        elif op == "select":
            select = (int(parts[1]), int(parts[2]))
        elif op == "window":
            rect = tuple(map(int, parts[1:5]))
            clips.append({cell for cell in clips[-1] if _inside(rect, *cell)})
        elif op == "block":
            rect = tuple(map(int, parts[1:5]))
            clips.append({cell for cell in clips[-1] if not _inside(rect, *cell)})
        elif op == "shift":
            dx, dy = map(int, parts[1:3])
            clips.append({(x + dx, y + dy) for x, y in clips[-1] if 0 <= x + dx < width and 0 <= y + dy < height})
        elif op == "wrap":
            dx, dy = map(int, parts[1:3])
            clips.append({((x + dx) % width, (y + dy) % height) for x, y in clips[-1]})
        elif op == "trace":
            x0, y0, x1, y1, radius = map(int, parts[1:6])
            mask = set()
            for bx, by in _bresenham(x0, y0, x1, y1):
                for ox in range(-radius, radius + 1):
                    for oy in range(-radius, radius + 1):
                        nx, ny = bx + ox, by + oy
                        if 0 <= nx < width and 0 <= ny < height:
                            mask.add((nx, ny))
            clips.append(clips[-1] & mask)
        elif op == "dilate":
            radius = int(parts[1])
            offsets = _manhattan_offsets(radius)
            out = set()
            for x, y in clips[-1]:
                for dx, dy in offsets:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        out.add((nx, ny))
            clips.append(out)
        elif op == "erode":
            radius = int(parts[1])
            offsets = _manhattan_offsets(radius)
            active = clips[-1]
            out = set()
            for x, y in active:
                if all(0 <= x + dx < width and 0 <= y + dy < height and (x + dx, y + dy) in active for dx, dy in offsets):
                    out.add((x, y))
            clips.append(out)
        elif op == "flood":
            x, y = map(int, parts[1:3])
            clips.append(_flood_component(clips[-1], (x, y)))
        elif op == "grade":
            minimum, maximum = map(int, parts[1:3])
            clips.append(_grade_cells(clips[-1], minimum, maximum, width, height))
        elif op == "route":
            x0, y0, x1, y1, radius = map(int, parts[1:6])
            clips.append(_route_cells(clips[-1], (x0, y0), (x1, y1), radius, width, height))
        elif op == "anchor":
            radius = int(parts[2])
            clips.append(_anchor_cells(clips[-1], recorded.get(parts[1], frozenset()), radius))
        elif op == "save":
            saved[parts[1]] = frozenset(clips[-1])
        elif op == "merge":
            clips.append(set(clips[-1]) | set(saved.get(parts[1], frozenset())))
        elif op == "cross":
            clips.append(set(clips[-1]) & set(saved.get(parts[1], frozenset())))
        elif op == "pop":
            if len(clips) > 1:
                clips.pop()
        elif op == "zone":
            ident = parts[1]
            rect = tuple(map(int, parts[2:6]))
            value = int(parts[6])
            layer = int(parts[7])
            visible = frozenset(cell for cell in clips[-1] if _inside(rect, *cell))
            pieces.append(
                {
                    "id": ident,
                    "kind": "zone",
                    "x": rect[0],
                    "y": rect[1],
                    "w": rect[2],
                    "h": rect[3],
                    "cells": len(visible),
                    "value": value,
                    "layer": layer,
                    "_visible": visible,
                    "_order": order,
                }
            )
            recorded[ident] = visible
            order += 1
        elif op == "unit":
            ident = parts[1]
            x0, y0, x1, y1, ww, hh = map(int, parts[2:8])
            value = int(parts[8])
            layer = int(parts[9])
            rect = (math.floor(x0 + (x1 - x0) * t), math.floor(y0 + (y1 - y0) * t), ww, hh)
            visible = frozenset(cell for cell in clips[-1] if _inside(rect, *cell))
            pieces.append(
                {
                    "id": ident,
                    "kind": "unit",
                    "x": rect[0],
                    "y": rect[1],
                    "w": ww,
                    "h": hh,
                    "cells": len(visible),
                    "value": value,
                    "layer": layer,
                    "_visible": visible,
                    "_order": order,
                }
            )
            recorded[ident] = visible
            order += 1
    selected = None
    sx, sy = select
    totals = {"zone": 0, "unit": 0}
    rows = []
    winner = None
    for piece in pieces:
        totals[piece["kind"]] += piece["cells"] * piece["value"]
        rect = (piece["x"], piece["y"], piece["w"], piece["h"])
        if _inside(rect, sx, sy) and (sx, sy) in piece["_visible"]:
            rank = (piece["layer"], piece["_order"])
            if winner is None or rank >= winner[0]:
                winner = (rank, piece["id"])
        rows.append({k: v for k, v in piece.items() if not k.startswith("_")})
    if winner is not None:
        selected = winner[1]
    return {"board": {"width": width, "height": height}, "selected": selected, "totals": totals, "pieces": rows}


def _run_turns(tmp_path, turns):
    exe = _build()
    turns_path = tmp_path / "turns.txt"
    report_path = tmp_path / "report.json"
    turns_path.write_text(turns)
    subprocess.run([str(exe), str(turns_path), str(report_path)], check=True, cwd=ROOT)
    return json.loads(report_path.read_text())


def test_timed_unit_selection_and_control_totals(tmp_path):
    """The referee applies turn timing before control and selection awards."""
    turns = """
board 10 8
time 0.5
select 3 2
zone base 0 0 6 4 2 1
unit scout 0 0 7 3 3 2 5 2
"""
    assert _run_turns(tmp_path, turns) == _model(turns)


def test_unit_positions_use_floor_not_nearest_integer(tmp_path):
    """Fractional unit positions use mathematical floor before scoring."""
    turns = """
board 9 7
time 0.5
select 3 3
unit lancer 0 0 7 7 2 2 4 2
zone pad 3 3 1 1 9 1
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["pieces"][0]["x"] == 3
    assert report["selected"] == "lancer"


def test_window_intersection_is_nested_for_later_records(tmp_path):
    """Each nested window is clipped by the active visible cells."""
    turns = """
board 10 10
select 6 6
window 2 2 6 6
window 5 0 4 9
zone gate 0 0 9 9 2 3
pop
zone hall 0 0 9 9 1 1
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["pieces"][0]["cells"] == 18
    assert report["pieces"][1]["cells"] == 36


def test_equal_layer_selection_uses_play_order(tmp_path):
    """Later records win selection ties on the same layer."""
    turns = """
board 6 6
select 2 2
zone first 1 1 3 3 1 4
unit second 0 0 2 2 2 2 1 4
zone third 2 2 1 1 1 4
"""
    report = _run_turns(tmp_path, turns)
    assert report == _model(turns)
    assert report["selected"] == "third"


def test_nested_windows_and_tie_breaking(tmp_path):
    """Nested board windows and equal-layer records follow match order."""
    turns = """
board 12 12
time 0.5
select 4 4
window 1 1 5 5
zone outer 0 0 8 8 1 1
window 4 4 5 5
unit ghost 6 6 2 2 4 4 7 5
pop
unit marker 1 1 7 7 3 3 3 5
pop
zone reserve 8 8 3 3 9 4
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "marker"
    assert report["pieces"][1]["cells"] == 4


def test_blocks_restore_with_stack_and_affect_selection(tmp_path):
    """Blocked visible cells subtract from scoring and unwind with pop."""
    turns = """
board 9 9
time 0.5
select 4 4
window 1 1 7 7
block 3 3 3 3
zone moat 1 1 7 7 2 5
unit shade 3 3 3 3 3 3 11 9
pop
unit captain 2 2 4 4 2 2 3 9
pop
zone field 0 0 9 9 1 1
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["pieces"][0]["cells"] == 40
    assert report["pieces"][1]["cells"] == 0
    assert report["selected"] == "captain"


def test_time_clamping_and_board_edges(tmp_path):
    """Out-of-range turn times and board edges keep scoring deterministic."""
    turns = """
board 8 6
time 1.5
select 7 5
unit runner 0 0 7 5 2 2 4 3
time -1
window 3 2 3 2
zone bunker 2 1 5 5 6 3
pop
zone flag 7 5 3 3 2 2
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "runner"
    assert report["totals"] == {"zone": 38, "unit": 4}


def test_shifted_visibility_is_stacked_and_cell_based(tmp_path):
    """Translated visibility cells combine with windows, blocks, and pop."""
    turns = """
board 11 9
time 0.625
select 7 4
window 1 1 7 6
block 3 2 2 3
shift 2 1
zone drift 0 0 11 9 2 4
unit echo 0 0 8 6 3 2 5 7
pop
zone before_shift 0 0 11 9 1 5
pop
unit restored 1 1 8 8 2 2 3 8
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "echo"
    assert report["pieces"][0]["cells"] == 36
    assert report["pieces"][2]["cells"] == 36


def test_long_replay_with_shifted_windows_and_repeated_pops(tmp_path):
    """Longer generated replays exercise stacked cell visibility semantics."""
    turns = ["board 13 10", "time 0.2", "select 8 6"]
    for i in range(8):
        turns.append(f"window {i % 4} {i % 3} {8 + (i % 3)} {7 - (i % 2)}")
        turns.append(f"block {2 + (i % 5)} {1 + (i % 4)} {1 + (i % 3)} 2")
        turns.append(f"shift {(i % 3) - 1} {1 if i % 2 == 0 else -1}")
        turns.append(f"zone z{i} {i % 6} {i % 5} {4 + (i % 4)} {3 + (i % 3)} {i + 2} {i % 6}")
        turns.append(f"time {0.08 * i - 0.15}")
        turns.append(f"unit u{i} {i % 4} {i % 3} {9 - (i % 4)} {7 - (i % 3)} {2 + (i % 2)} 2 {3 + i} {i % 6}")
        turns.append("pop")
        if i % 2:
            turns.append("pop")
    turns.append("shift 3 0")
    turns.append("zone final 6 4 4 4 11 9")
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert report["selected"] == expected["selected"]
    assert report["totals"]["zone"] > report["totals"]["unit"] // 3


def test_saved_visibility_masks_are_snapshots_and_stack_entries(tmp_path):
    """Saved visibility masks can be reused after later stack mutations."""
    turns = """
board 15 10
select 10 5
window 1 1 5 5
save pocket
shift 5 0
zone shifted 6 1 5 5 2 3
pop
window 9 4 4 3
merge pocket
zone saved_only 0 0 12 8 9 9
pop
pop
shift 5 0
zone winner 8 4 4 3 1 7
cross missing
zone absent 0 0 15 10 20 20
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "winner"
    assert report["pieces"][1]["cells"] == 25
    assert report["pieces"][3]["cells"] == 0


def test_saved_mask_intersections_survive_generated_replay(tmp_path):
    """Generated turns combine saved masks, shifts, intersections, and pops."""
    turns = ["board 17 13", "time 0.35", "select 8 6", "window 1 1 13 10", "save base"]
    for i in range(10):
        turns.append(f"window {i % 5} {1 + (i % 4)} {9 + (i % 3)} {8 - (i % 3)}")
        turns.append(f"block {3 + (i % 6)} {2 + (i % 5)} {1 + (i % 3)} {1 + ((i + 1) % 3)}")
        if i % 3 == 0:
            turns.append(f"save snap{i}")
        turns.append(f"shift {(i % 4) - 1} {1 - (i % 3)}")
        turns.append("merge base" if i % 2 == 0 else f"cross snap{3 * (i // 3)}")
        turns.append(f"zone z{i} {i % 7} {i % 6} {5 + (i % 4)} {4 + (i % 3)} {2 + i} {i % 8}")
        turns.append(f"time {0.11 * i - 0.2}")
        turns.append(f"unit u{i} {i % 5} {i % 4} {12 - (i % 5)} {9 - (i % 4)} {2 + (i % 2)} {2 + (i % 3)} {3 + i} {(i + 2) % 8}")
        turns.append("pop")
        turns.append("pop")
    turns.append("cross base")
    turns.append("zone final 7 5 3 3 13 12")
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert report["pieces"][-1]["cells"] == expected["pieces"][-1]["cells"]
    assert report["totals"]["zone"] > 0 and report["totals"]["unit"] > 0


def test_trace_dilate_and_erode_change_visibility_non_rectangularly(tmp_path):
    """Line masks and morphology produce non-rectangular scoring sets."""
    turns = """
board 14 11
time 0.375
select 5 6
window 1 1 12 9
trace 1 8 12 2 1
save lane
dilate 2
block 4 4 2 2
zone broad 0 0 14 11 3 4
erode 1
zone core 0 0 14 11 7 9
unit runner 1 8 11 2 3 3 5 9
pop
cross lane
zone lane_only 0 0 14 11 11 8
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "lane_only"
    assert report["pieces"][0]["cells"] > report["pieces"][1]["cells"] > 0
    assert report["pieces"][3]["cells"] == expected["pieces"][3]["cells"]


def test_wrapped_saved_masks_reset_and_equal_layer_selection(tmp_path):
    """Wrapped visibility snapshots survive stack changes but not board reset."""
    turns = """
board 9 7
select 0 6
window 6 4 3 3
wrap 3 2
save corner
zone wrapped 0 0 3 2 4 6
board 9 7
cross corner
zone cleared 0 0 9 7 99 99
pop
select 7 5
window 5 4 3 3
wrap 2 1
save second
pop
cross second
unit earlier 6 4 8 6 2 2 5 10
zone later 7 5 1 1 7 10
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["pieces"][1]["cells"] == 0
    assert report["selected"] == "later"


def test_generated_visibility_morphology_replay(tmp_path):
    """Generated replay mixes wrapping, traces, morphology, snapshots, and pops."""
    turns = ["board 23 19", "time 0.12", "select 17 11", "window 2 2 18 15", "save root"]
    for i in range(14):
        turns.append(f"trace {1 + (i * 3) % 20} {1 + (i * 5) % 17} {22 - (i * 4) % 21} {18 - (i * 2) % 17} {i % 3}")
        if i % 2 == 0:
            turns.append(f"dilate {1 + (i % 2)}")
        else:
            turns.append("erode 1")
        turns.append(f"wrap {(i * 5) % 9 - 4} {(i * 7) % 7 - 3}")
        if i % 4 == 1:
            turns.append(f"save mid{i}")
        turns.append("merge root" if i % 3 == 0 else f"cross mid{1 + 4 * ((i - 1) // 4)}")
        turns.append(f"zone gz{i} {i % 9} {(2 * i) % 8} {5 + (i % 5)} {4 + (i % 4)} {3 + i} {(i * 2) % 11}")
        turns.append(f"time {0.097 * i - 0.24}")
        turns.append(f"unit gu{i} {i % 6} {(i * 2) % 5} {21 - (i % 7)} {17 - (i % 6)} {2 + (i % 3)} {2 + (i % 2)} {5 + i} {(i * 3) % 11}")
        turns.append("pop")
        turns.append("pop")
        turns.append("pop")
    turns.append("cross root")
    turns.append("dilate 1")
    turns.append("zone finale 16 10 4 4 19 20")
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert report["selected"] == expected["selected"]
    assert len(report["pieces"]) == 29
    assert report["totals"]["zone"] > report["totals"]["unit"] // 4


def test_flood_keeps_only_seed_component_after_nonlocal_cuts(tmp_path):
    """Flood selection follows 4-neighbor connectivity through prior masks."""
    turns = """
board 13 11
select 10 4
window 1 1 11 9
block 5 1 2 9
trace 1 1 11 9 2
save split_lane
flood 2 4
zone left_island 0 0 13 11 3 6
pop
flood 10 4
unit right_runner 8 2 11 5 3 3 7 9
merge split_lane
grade 3 6
zone graded_bridge 0 0 13 11 5 10
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] is None
    assert report["pieces"][0]["cells"] == 23
    assert report["pieces"][1]["cells"] == 0
    assert report["pieces"][2]["cells"] == 33


def test_grade_uses_on_board_neighborhood_counts_and_snapshots(tmp_path):
    """Grade counts only on-board Chebyshev neighbors from the current snapshot."""
    turns = """
board 10 8
select 1 1
window 0 0 9 7
block 2 2 2 2
block 6 0 1 5
save carved
grade 4 7
zone shell 0 0 10 8 2 4
pop
dilate 1
cross carved
grade 6 9
unit dense 0 0 4 4 3 3 11 8
flood 7 6
zone flooded_component 0 0 10 8 13 20
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "flooded_component"
    assert report["pieces"][0]["cells"] == 43
    assert report["pieces"][1]["cells"] == 7
    assert report["pieces"][2]["cells"] == 48


def test_route_keeps_shortest_active_paths_around_blockers(tmp_path):
    """Route isolates shortest 4-neighbor paths through the current mask."""
    turns = """
board 12 9
select 6 6
window 1 1 10 7
block 5 1 2 5
save maze
route 2 3 9 3 0
zone path 0 0 12 9 4 8
pop
cross maze
route 2 3 9 3 1
unit widened 4 4 5 6 4 3 9 10
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "widened"
    assert report["pieces"][0]["cells"] > report["pieces"][1]["cells"] > 0


def test_anchor_uses_recorded_visible_cells_not_piece_rectangle(tmp_path):
    """Anchor filters from a prior piece's recorded visible cells after later masks."""
    turns = """
board 14 10
select 7 5
window 2 2 4 4
block 3 3 1 2
zone beacon 2 2 4 4 2 3
pop
pop
block 4 4 2 2
anchor beacon 2
zone halo 0 0 14 10 5 9
pop
anchor missing 3
zone absent 0 0 14 10 99 20
"""
    report = _run_turns(tmp_path, turns)
    expected = _model(turns)
    assert report == expected
    assert report["selected"] == "halo"
    assert report["pieces"][2]["cells"] == 0


def test_generated_route_anchor_replay_with_saved_masks(tmp_path):
    """Generated replay mixes shortest routes, anchors, snapshots, and pops."""
    turns = ["board 21 17", "time 0.28", "select 13 9", "window 1 1 19 15", "save arena"]
    for i in range(9):
        turns.append(f"block {3 + (i * 4) % 13} {2 + (i * 5) % 11} {1 + (i % 3)} {2 + (i % 2)}")
        turns.append(f"route {1 + (i * 2) % 17} {1 + (i * 3) % 13} {19 - (i * 5) % 18} {15 - (i * 4) % 14} {i % 2}")
        turns.append("merge arena" if i % 2 == 0 else "cross arena")
        turns.append(f"zone rz{i} {i % 8} {(2 * i) % 7} {5 + (i % 4)} {4 + (i % 3)} {5 + i} {(i * 3) % 14}")
        turns.append(f"anchor rz{i} {1 + (i % 3)}")
        turns.append(f"time {0.12 * i - 0.18}")
        turns.append(f"unit ru{i} {i % 6} {(i * 2) % 5} {19 - (i % 7)} {15 - (i % 6)} {2 + (i % 2)} {2 + (i % 3)} {7 + i} {(i * 5) % 14}")
        turns.extend(["pop", "pop", "pop", "pop"])
    turns.extend(["cross arena", "anchor rz8 2", "zone final_anchor 10 7 5 5 23 30"])
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert len(report["pieces"]) == 19
    assert report["totals"]["zone"] > report["totals"]["unit"] // 5


def test_generated_flood_grade_replay_with_reused_masks(tmp_path):
    """Generated replay combines component masks, neighborhood grading, and stack unwinds."""
    turns = ["board 19 15", "time 0.41", "select 13 9", "window 1 1 17 13", "save whole"]
    for i in range(11):
        turns.append(f"block {2 + (i * 3) % 12} {1 + (i * 5) % 10} {1 + (i % 3)} {2 + ((i + 1) % 3)}")
        turns.append(f"trace {1 + (i * 4) % 16} {1 + (i * 7) % 12} {18 - (i * 5) % 17} {14 - (i * 2) % 13} {i % 2}")
        turns.append(f"flood {1 + (i * 5) % 17} {1 + (i * 4) % 13}")
        turns.append(f"grade {1 + (i % 4)} {5 + (i % 4)}")
        if i % 3 == 1:
            turns.append(f"save island{i}")
        turns.append("merge whole" if i % 2 == 0 else f"cross island{1 + 3 * ((i - 1) // 3)}")
        turns.append(f"zone fgz{i} {i % 8} {(2 * i) % 7} {5 + (i % 5)} {4 + (i % 4)} {4 + i} {(i * 3) % 13}")
        turns.append(f"time {0.13 * i - 0.31}")
        turns.append(f"unit fgu{i} {i % 7} {(i * 3) % 6} {17 - (i % 6)} {13 - (i % 5)} {2 + (i % 2)} {2 + (i % 3)} {6 + i} {(i * 5) % 13}")
        turns.extend(["pop", "pop", "pop", "pop"])
    turns.extend(["cross whole", "grade 5 9", "zone final_grade 12 8 4 4 17 30"])
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert report["selected"] == expected["selected"]
    assert len(report["pieces"]) == 23


def test_generated_snapshot_stack_component_replay(tmp_path):
    """Generated replay mixes recurring snapshots, components, grading, and stack restores."""
    turns = ["board 24 18", "time 0.52", "select 15 10", "window 1 1 22 16", "save root", "save phase"]
    for i in range(9):
        turns.append(f"block {2 + (i * 5) % 16} {1 + (i * 7) % 12} {1 + (i % 4)} {2 + ((i + 2) % 4)}")
        turns.append(f"trace {1 + (i * 3) % 21} {1 + (i * 5) % 15} {23 - (i * 4) % 22} {17 - (i * 6) % 16} {1 + (i % 2)}")
        if i % 2 == 0:
            turns.append(f"grade {2 + (i % 3)} {6 + (i % 4)}")
        else:
            turns.append(f"flood {1 + (i * 4) % 22} {1 + (i * 3) % 16}")
        turns.append("save phase")
        turns.append("merge root" if i % 3 == 0 else "cross phase")
        turns.append(f"zone psz{i} {i % 9} {(2 * i) % 8} {5 + (i % 5)} {4 + (i % 4)} {5 + i} {(i * 4) % 15}")
        turns.append(f"time {0.105 * i - 0.23}")
        turns.append(f"unit psu{i} {i % 8} {(i * 2) % 7} {22 - (i % 7)} {16 - (i % 6)} {2 + (i % 3)} {2 + (i % 2)} {7 + i} {(i * 5) % 15}")
        turns.extend(["pop", "pop", "pop", "pop"])
    turns.extend(
        [
            "cross root",
            "block 10 6 3 8",
            "save phase",
            "pop",
            "merge phase",
            "grade 4 9",
            "zone phase_final 13 8 5 5 19 40",
        ]
    )
    text = "\n".join(turns) + "\n"
    report = _run_turns(tmp_path, text)
    expected = _model(text)
    assert report == expected
    assert report["selected"] == "phase_final"
    assert len(report["pieces"]) == 19
    assert report["totals"]["zone"] > report["totals"]["unit"] // 4
