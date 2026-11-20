import json
import subprocess

ESC = "\x1b"


def width(glyph):
    return 2 if len(glyph.encode("utf-8")) > 1 else 1


def normalize_rows(run):
    rows = []
    for y in range(run["height"]):
        row = run["map"][y] if y < len(run["map"]) else ""
        rows.append((row + "." * run["width"])[:run["width"]])
    return rows


def write_run(tmp_path, data, name="run.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def build_binary():
    subprocess.run(["/app/scripts/build.sh"], check=True, cwd="/app")


def run_cmd(args):
    subprocess.run(["/app/bin/dungeon_render", *map(str, args)], check=True, cwd="/app")


def parse_frames(data, view_w, view_h, expected_count):
    text = data.decode("utf-8")
    start = f"{ESC}[2J{ESC}[H"
    row_reset = f"{ESC}[0m\n"
    frame_reset = f"{ESC}[0m\n\f\n"
    color_prefix = f"{ESC}[38;5;"
    frames = []
    pos = 0
    for frame_no in range(expected_count):
        assert text.startswith(start, pos), f"frame {frame_no} missing reset"
        pos += len(start)
        rows = []
        for row_no in range(view_h):
            fg = None
            row = []
            col = 0
            while True:
                if text.startswith(row_reset, pos):
                    pos += len(row_reset)
                    break
                if text.startswith(color_prefix, pos):
                    end = text.index("m", pos)
                    next_fg = int(text[pos + len(color_prefix):end])
                    assert next_fg != fg, "foreground escape repeated without a color change"
                    fg = next_fg
                    pos = end + 1
                    continue
                assert fg is not None, "visible cell emitted before foreground escape"
                glyph = text[pos]
                pos += 1
                advance = width(glyph)
                assert col + advance <= view_w, "wide glyph crosses the viewport edge"
                row.append((glyph, fg))
                if advance == 2:
                    row.append(("", fg))
                col += advance
            assert col == view_w, f"frame {frame_no} row {row_no} width mismatch"
            rows.append(row)
        assert text.startswith(frame_reset, pos), f"frame {frame_no} missing frame terminator"
        pos += len(frame_reset)
        frames.append(rows)
    assert pos == len(text), "extra bytes after final frame"
    return frames


def assert_cell(frames, frame, row, col, glyph, fg):
    got = frames[frame][row][col]
    assert got == (glyph, fg), f"frame {frame} row {row} col {col}: expected {(glyph, fg)}, got {got}"


def actor_width(actor):
    return width(actor["glyph"])


def actor_occupies(actor, x, y):
    return actor.get("alive", True) and actor["y"] == y and actor["x"] <= x < actor["x"] + actor_width(actor)


def leave_trail(map_rows, actor, old_x, old_y):
    trail = actor.get("trail")
    if not trail:
        return
    row = list(map_rows[old_y])
    for x in range(old_x, old_x + actor_width(actor)):
        if 0 <= x < len(row):
            row[x] = trail
    map_rows[old_y] = "".join(row)


def focus_actor(actors):
    for i, actor in enumerate(actors):
        if actor["id"] == "hero" and actor.get("alive", True):
            return i
    for i, actor in enumerate(actors):
        if actor.get("alive", True):
            return i
    return 0


def terrain_blocked(run, map_rows, x, y):
    solid_tiles = set(run.get("solid_tiles", ["#"]))
    return x < 0 or y < 0 or x >= run["width"] or y >= run["height"] or map_rows[y][x] in solid_tiles


def try_push_move(run, map_rows, actors, idx, dx, dy, moving):
    if not actors[idx].get("alive", True):
        return False
    if idx in moving:
        return False
    if dx == 0 and dy == 0:
        return True
    moving.add(idx)
    nx = actors[idx]["x"] + dx
    ny = actors[idx]["y"] + dy
    old_x = actors[idx]["x"]
    old_y = actors[idx]["y"]
    for x in range(nx, nx + actor_width(actors[idx])):
        if terrain_blocked(run, map_rows, x, ny):
            moving.remove(idx)
            return False
        for j, other in enumerate(actors):
            if j == idx or j in moving or not other.get("alive", True) or not other.get("solid", False):
                continue
            if not actor_occupies(other, x, ny):
                continue
            if not other.get("pushable", False) or not try_push_move(run, map_rows, actors, j, dx, dy, moving):
                moving.remove(idx)
                return False
    actors[idx]["x"] = nx
    actors[idx]["y"] = ny
    leave_trail(map_rows, actors[idx], old_x, old_y)
    moving.remove(idx)
    return True


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def apply_reference_record(run, map_rows, actors, palette, rec):
    if not isinstance(rec, dict) or not isinstance(rec.get("op"), str):
        return
    op = rec["op"]
    index = next((i for i, actor in enumerate(actors) if actor["id"] == rec.get("id")), None)
    if op == "move" and index is not None:
        if not is_int(rec.get("dx")) or not is_int(rec.get("dy")):
            return
        repeat = rec.get("repeat", 1)
        if not is_int(repeat) or repeat < 0:
            return
        for _ in range(repeat):
            before = [dict(actor) for actor in actors]
            before_map = list(map_rows)
            if not try_push_move(run, map_rows, actors, index, rec["dx"], rec["dy"], set()):
                actors[:] = before
                map_rows[:] = before_map
    elif op == "set":
        if not isinstance(rec.get("id"), str):
            return
        if index is None:
            required = {"x", "y", "glyph", "fg", "layer", "solid"}
            if required.issubset(rec):
                actor = {"id": rec["id"], "alive": True, "pushable": False}
                actor.update({key: rec[key] for key in required})
                if "alive" in rec:
                    actor["alive"] = rec["alive"]
                for key in ("alive", "pushable", "trail"):
                    if key in rec:
                        actor[key] = rec[key]
                actors.append(actor)
        else:
            for key in ("x", "y", "alive", "glyph", "fg", "layer", "solid", "pushable", "trail"):
                if key in rec:
                    actors[index][key] = rec[key]
    elif op == "glyph" and index is not None:
        if not isinstance(rec.get("glyph"), str) or not is_int(rec.get("fg")):
            return
        actors[index]["glyph"] = rec["glyph"]
        actors[index]["fg"] = rec["fg"]
    elif op == "tint":
        if not isinstance(rec.get("ch"), str) or len(rec["ch"]) != 1 or not is_int(rec.get("fg")):
            return
        palette[rec["ch"]] = rec["fg"]
    elif op == "tile":
        if not is_int(rec.get("x")) or not is_int(rec.get("y")) or not isinstance(rec.get("ch"), str) or len(rec["ch"]) != 1:
            return
        x = rec["x"]
        y = rec["y"]
        if 0 <= y < run["height"] and 0 <= x < run["width"]:
            row = list(map_rows[y])
            row[x] = rec["ch"]
            map_rows[y] = "".join(row)


def slope_vector(run, map_rows, actor):
    if not actor.get("alive", True):
        return None
    if actor["y"] < 0 or actor["y"] >= run["height"] or actor["x"] < 0 or actor["x"] >= run["width"]:
        return None
    rule = run.get("slopes", {}).get(map_rows[actor["y"]][actor["x"]])
    if not isinstance(rule, dict):
        return None
    dx = rule.get("dx")
    dy = rule.get("dy")
    if not is_int(dx) or not is_int(dy) or (dx == 0 and dy == 0):
        return None
    return dx, dy


def apply_slope_sweep(run, map_rows, actors):
    for idx in range(len(actors)):
        vector = slope_vector(run, map_rows, actors[idx])
        if vector is None:
            continue
        before = [dict(actor) for actor in actors]
        before_map = list(map_rows)
        if not try_push_move(run, map_rows, actors, idx, vector[0], vector[1], set()):
            actors[:] = before
            map_rows[:] = before_map


def reference_paint(run, map_rows, actors, palette, wx, wy, vx):
    chosen = None
    for i, actor in enumerate(actors):
        if not actor_occupies(actor, wx, wy):
            continue
        if chosen is None or (actor["layer"], i) > (actors[chosen]["layer"], chosen):
            chosen = i
    if chosen is not None:
        actor = actors[chosen]
        advance = actor_width(actor)
        if wx == actor["x"] and vx + advance <= run["view_w"]:
            return actor["glyph"], actor["fg"], advance
        return " ", actor["fg"], 1
    ch = map_rows[wy][wx]
    return ch, palette[ch], 1


def reference_frame(run, map_rows, actors, palette):
    focus = actors[focus_actor(actors)]
    left = max(0, min(focus["x"] - run["view_w"] // 2, run["width"] - run["view_w"]))
    top = max(0, min(focus["y"] - run["view_h"] // 2, run["height"] - run["view_h"]))
    chunks = [f"{ESC}[2J{ESC}[H"]
    for vy in range(run["view_h"]):
        last_fg = None
        vx = 0
        while vx < run["view_w"]:
            text, fg, advance = reference_paint(run, map_rows, actors, palette, left + vx, top + vy, vx)
            if fg != last_fg:
                chunks.append(f"{ESC}[38;5;{fg}m")
                last_fg = fg
            chunks.append(text)
            vx += advance
        chunks.append(f"{ESC}[0m\n")
    chunks.append(f"{ESC}[0m\n\f\n")
    return "".join(chunks).encode("utf-8")


def simulate_reference(run, turns=None):
    actors = [dict(actor) for actor in run["actors"]]
    for actor in actors:
        actor.setdefault("alive", True)
        actor.setdefault("pushable", False)
    palette = dict(run["palette"])
    map_rows = normalize_rows(run)
    chunks = [reference_frame(run, map_rows, actors, palette)]
    selected_turns = run["turns"] if turns is None else run["turns"][:turns]
    for turn in selected_turns:
        for rec in turn:
            apply_reference_record(run, map_rows, actors, palette, rec)
        apply_slope_sweep(run, map_rows, actors)
        chunks.append(reference_frame(run, map_rows, actors, palette))
    return b"".join(chunks), actors, palette, map_rows


def reference_bytes(run, turns=None):
    return simulate_reference(run, turns)[0]


def reference_state(run, turns):
    _, actors, palette, map_rows = simulate_reference(run, turns)
    return {"next": turns, "palette": palette, "actors": actors, "map": map_rows}


def render_and_parse(tmp_path, run, name="run"):
    source = write_run(tmp_path, run, f"{name}.json")
    out = tmp_path / f"{name}.ans"
    run_cmd(["render", source, out])
    data = out.read_bytes()
    frames = parse_frames(data, run["view_w"], run["view_h"], len(run["turns"]) + 1)
    return source, out, data, frames


def base_run():
    return {
        "width": 13,
        "height": 8,
        "view_w": 7,
        "view_h": 4,
        "map": [
            "#############",
            "#...~...+..#",
            "#..##......#",
            "#..........#",
            "#..+..~....#",
            "#......##..#",
            "#..........#",
            "#############",
        ],
        "palette": {"#": 244, ".": 238, "~": 33, "+": 178},
        "actors": [
            {"id": "hero", "x": 4, "y": 3, "glyph": "@", "fg": 46, "layer": 5, "solid": True},
            {"id": "gate", "x": 6, "y": 3, "glyph": "門", "fg": 160, "layer": 8, "solid": True},
            {"id": "shade", "x": 7, "y": 3, "glyph": "s", "fg": 93, "layer": 4, "solid": False},
            {"id": "orb", "x": 1, "y": 1, "glyph": "*", "fg": 226, "layer": 7, "solid": False},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tint", "ch": ".", "fg": 240}, {"op": "glyph", "id": "gate", "glyph": "界", "fg": 201}],
            [{"op": "set", "id": "orb", "x": 5, "y": 3, "alive": True}],
            [{"op": "move", "id": "hero", "dx": 0, "dy": -1}],
        ],
    }


def generated_run(seed):
    run = base_run()
    run["actors"] = [dict(a) for a in run["actors"]]
    run["turns"] = []
    dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    for i in range(10):
        dx, dy = dirs[(seed + i * 3) % len(dirs)]
        turn = [{"op": "move", "id": "hero", "dx": dx, "dy": dy}]
        if i % 3 == 1:
            turn.append({"op": "tint", "ch": "+" if i % 2 else "~", "fg": 100 + seed + i})
        if i % 4 == 2:
            turn.append({"op": "glyph", "id": "shade", "glyph": "火", "fg": 124 + i})
        if i == 6:
            turn.append({"op": "set", "id": "orb", "x": 8, "y": 4, "alive": True})
        run["turns"].append(turn)
    return run


def stateful_run():
    return {
        "width": 16,
        "height": 9,
        "view_w": 6,
        "view_h": 5,
        "map": [
            "################",
            "#....+....~...#",
            "#..##.........#",
            "#.............#",
            "#.............#",
            "#....##.......#",
            "#........+....#",
            "#.............#",
            "################",
        ],
        "palette": {"#": 244, ".": 238, "+": 178, "~": 33},
        "actors": [
            {"id": "scout", "x": 2, "y": 4, "glyph": "界", "fg": 45, "layer": 5, "solid": True},
            {"id": "door", "x": 5, "y": 4, "glyph": "D", "fg": 160, "layer": 7, "solid": True},
            {"id": "wisp", "x": 9, "y": 4, "glyph": "*", "fg": 226, "layer": 9, "solid": False},
            {"id": "echo", "x": 2, "y": 4, "glyph": "!", "fg": 39, "layer": 5, "solid": False},
        ],
        "turns": [
            [{"op": "glyph", "id": "scout", "glyph": "S", "fg": 45}],
            [{"op": "move", "id": "scout", "dx": 1, "dy": 0}],
            [{"op": "tint", "ch": ".", "fg": 242}, {"op": "glyph", "id": "door", "glyph": "門", "fg": 196}],
            [{"op": "move", "id": "scout", "dx": 1, "dy": 0}],
            [{"op": "move", "id": "scout", "dx": 2, "dy": 0}],
            [{"op": "set", "id": "door", "x": 8, "y": 4, "alive": True}],
            [{"op": "move", "id": "scout", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "wisp", "x": 5, "y": 4, "alive": True}, {"op": "glyph", "id": "wisp", "glyph": "龍", "fg": 201}],
            [{"op": "move", "id": "scout", "dx": 1, "dy": 0}],
        ],
    }


def gauntlet_run():
    return {
        "width": 18,
        "height": 10,
        "view_w": 8,
        "view_h": 5,
        "map": [
            "##################",
            "#....+.....~.....#",
            "#..##............#",
            "#................#",
            "#.......#........#",
            "#................#",
            "#............+...#",
            "#................#",
            "#................#",
            "##################",
        ],
        "palette": {"#": 244, ".": 238, "+": 178, "~": 33},
        "actors": [
            {"id": "hero", "x": 2, "y": 3, "glyph": "@", "fg": 46, "layer": 5, "solid": True},
            {"id": "block", "x": 5, "y": 3, "glyph": "箱", "fg": 202, "layer": 4, "solid": True},
            {"id": "cloak", "x": 6, "y": 3, "glyph": "c", "fg": 99, "layer": 6, "solid": False},
            {"id": "spark", "x": 1, "y": 1, "glyph": "*", "fg": 226, "layer": 9, "solid": False, "alive": False},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 4}],
            [{"op": "set", "id": "block", "x": 7, "y": 3, "glyph": "▒", "fg": 208, "layer": 9, "solid": False}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 3}, {"op": "tint", "ch": "+", "fg": 171}],
            [{"op": "set", "id": "spark", "x": 7, "y": 3, "alive": True, "glyph": "龍", "fg": 201, "layer": 11, "solid": False}],
            [{"op": "set", "id": "block", "x": 8, "y": 3, "layer": 3, "solid": True}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
            [{"op": "glyph", "id": "hero", "glyph": "界", "fg": 50}, {"op": "move", "id": "hero", "dx": 0, "dy": 1, "repeat": 2}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 6}],
        ],
    }


def boundary_run():
    return {
        "width": 6,
        "height": 4,
        "view_w": 6,
        "view_h": 4,
        "map": ["......", "......", "......", "......"],
        "palette": {".": 238},
        "actors": [
            {"id": "hero", "x": 0, "y": 1, "glyph": "@", "fg": 46, "layer": 5, "solid": True},
            {"id": "guard", "x": 2, "y": 1, "glyph": "G", "fg": 160, "layer": 6, "solid": True},
            {"id": "orb", "x": 4, "y": 1, "glyph": "o", "fg": 226, "layer": 4, "solid": True},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": -1, "dy": 0, "repeat": 3}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 3}],
            [{"op": "set", "id": "guard", "x": 2, "y": 1, "alive": False, "solid": True}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
        ],
    }


def push_chain_run():
    return {
        "width": 15,
        "height": 7,
        "view_w": 9,
        "view_h": 5,
        "map": [
            "###############",
            "#.............#",
            "#....#........#",
            "#.............#",
            "#........#....#",
            "#.............#",
            "###############",
        ],
        "palette": {"#": 244, ".": 238},
        "actors": [
            {"id": "hero", "x": 2, "y": 3, "glyph": "@", "fg": 46, "layer": 5, "solid": True},
            {"id": "crate-a", "x": 4, "y": 3, "glyph": "箱", "fg": 202, "layer": 4, "solid": True, "pushable": True},
            {"id": "crate-b", "x": 6, "y": 3, "glyph": "b", "fg": 203, "layer": 4, "solid": True, "pushable": True},
            {"id": "pillar", "x": 9, "y": 3, "glyph": "P", "fg": 250, "layer": 7, "solid": True},
            {"id": "ghost", "x": 2, "y": 3, "glyph": "g", "fg": 87, "layer": 9, "solid": False, "alive": False},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "crate-b", "pushable": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "crate-b", "pushable": True}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "pillar", "alive": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
            [{"op": "set", "id": "banner", "x": 8, "y": 3, "glyph": "⚑", "fg": 220, "layer": 12, "solid": False}],
            [{"op": "glyph", "id": "hero", "glyph": "界", "fg": 51}, {"op": "tint", "ch": ".", "fg": 241}],
            [{"op": "set", "id": "hero", "alive": False}, {"op": "set", "id": "ghost", "alive": True, "x": 6, "y": 1}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}, {"op": "move", "id": "ghost", "dx": 0, "dy": 1, "repeat": 2}],
        ],
    }


def trail_run():
    return {
        "width": 16,
        "height": 8,
        "view_w": 9,
        "view_h": 5,
        "map": [
            "################",
            "#..............#",
            "#..............#",
            "#..............#",
            "#......#.......#",
            "#..............#",
            "#..............#",
            "################",
        ],
        "palette": {"#": 244, ".": 238, ":": 52, "~": 130, "+": 178},
        "actors": [
            {"id": "hero", "x": 2, "y": 3, "glyph": "@", "fg": 46, "layer": 8, "solid": True, "trail": ":"},
            {"id": "crate-a", "x": 4, "y": 3, "glyph": "箱", "fg": 202, "layer": 5, "solid": True, "pushable": True, "trail": "~"},
            {"id": "crate-b", "x": 7, "y": 3, "glyph": "b", "fg": 203, "layer": 5, "solid": True, "pushable": True, "trail": "+"},
            {"id": "lamp", "x": 9, "y": 2, "glyph": "l", "fg": 226, "layer": 4, "solid": False},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tint", "ch": ":", "fg": 57}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "crate-b", "pushable": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "crate-b", "pushable": True}, {"op": "glyph", "id": "hero", "glyph": "界", "fg": 51}],
            [{"op": "move", "id": "hero", "dx": 0, "dy": -1, "repeat": 2}],
            [{"op": "set", "id": "scrawl", "x": 5, "y": 5, "glyph": "s", "fg": 99, "layer": 6, "solid": False, "trail": ":"}],
            [{"op": "move", "id": "scrawl", "dx": 1, "dy": 0, "repeat": 3}],
        ],
    }


def test_render_matches_official_cases(tmp_path):
    """A complete recorded run follows row normalization, ANSI, viewport, tint, and layering rules."""
    build_binary()
    run = base_run()
    _, _, data, frames = render_and_parse(tmp_path, run, "base")
    assert data.endswith(b"\x1b[0m\n\x0c\n")
    assert_cell(frames, 0, 2, 3, "@", 46)
    assert_cell(frames, 0, 2, 5, "門", 160)
    assert_cell(frames, 2, 2, 3, "@", 46)
    assert_cell(frames, 2, 2, 4, "門", 160)
    assert_cell(frames, 3, 0, 1, ".", 240)
    assert_cell(frames, 3, 2, 4, "界", 201)
    assert_cell(frames, 4, 2, 3, "*", 226)
    assert_cell(frames, 5, 2, 3, "@", 46)


def test_map_rows_are_padded_and_truncated_before_replay(tmp_path):
    """Map row normalization is applied before movement, painting, and checkpoint state capture."""
    build_binary()
    run = {
        "width": 8,
        "height": 5,
        "view_w": 8,
        "view_h": 5,
        "map": [
            "#########extra",
            "#..",
            "#......#tail",
            "#....",
            "########",
        ],
        "palette": {"#": 244, ".": 238, "+": 178},
        "actors": [
            {"id": "hero", "x": 2, "y": 1, "glyph": "@", "fg": 46, "layer": 5, "solid": True, "trail": "+"},
            {"id": "crate", "x": 5, "y": 1, "glyph": "箱", "fg": 202, "layer": 4, "solid": True, "pushable": True},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 7, "y": 3, "ch": "+"}],
        ],
    }
    source, full, full_data, frames = render_and_parse(tmp_path, run, "normalized")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 0, 1, 3, ".", 238)
    assert_cell(frames, 1, 1, 2, "+", 178)
    assert_cell(frames, 1, 1, 5, "箱", 202)
    part = tmp_path / "normalized-part.ans"
    state = tmp_path / "normalized-state.json"
    rest = tmp_path / "normalized-rest.ans"
    run_cmd(["checkpoint", source, 2, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert all(len(row) == run["width"] for row in saved["map"])
    assert saved["map"] == reference_state(run, 2)["map"]
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def test_segmented_run_bytes(tmp_path):
    """A saved game segment and its continuation reproduce the uninterrupted byte stream."""
    build_binary()
    run = generated_run(5)
    source, full, full_data, _ = render_and_parse(tmp_path, run, "generated-5")
    part = tmp_path / "part.ans"
    state = tmp_path / "state.json"
    rest = tmp_path / "rest.ans"
    run_cmd(["checkpoint", source, 6, state, part])
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data == full.read_bytes()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["next"] == 6
    assert saved["palette"].get("+", 178) != 178 or saved["palette"].get("~", 33) != 33


def test_generated_runs(tmp_path):
    """Several deterministic replays preserve frame counts, row formats, and actor cells."""
    build_binary()
    for seed in (2, 7, 11):
        run = generated_run(seed)
        _, _, data, frames = render_and_parse(tmp_path, run, f"seed-{seed}")
        assert data.count(b"\x0c\n") == len(run["turns"]) + 1
        assert_cell(frames, 0, 2, 3, "@", 46)
        assert_cell(frames, 0, 2, 5, "門", 160)


def test_checkpoint_stateful_nonhero_run(tmp_path):
    """A replay without a hero preserves saved state, layering, and resumed bytes."""
    build_binary()
    run = stateful_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "stateful")
    assert_cell(frames, 0, 2, 2, "!", 39)
    assert_cell(frames, 3, 2, 5, " ", 196)
    part = tmp_path / "part-stateful.ans"
    state = tmp_path / "stateful-state.json"
    rest = tmp_path / "rest-stateful.ans"
    run_cmd(["checkpoint", source, 5, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["next"] == 5
    assert saved["palette"]["."] == 242
    assert next(a for a in saved["actors"] if a["id"] == "scout")["x"] == 4
    assert next(a for a in saved["actors"] if a["id"] == "door")["glyph"] == "門"
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data == full.read_bytes()


def test_repeated_moves_and_mutating_set_records(tmp_path):
    """Repeated movement and stateful actor mutations survive checkpoint replay."""
    build_binary()
    run = gauntlet_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "gauntlet")
    assert_cell(frames, 1, 2, 4, "@", 46)
    assert_cell(frames, 4, 2, 4, "龍", 201)
    assert_cell(frames, 5, 2, 4, "龍", 201)
    part = tmp_path / "part-gauntlet.ans"
    state = tmp_path / "gauntlet-state.json"
    rest = tmp_path / "rest-gauntlet.ans"
    run_cmd(["checkpoint", source, 4, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["next"] == 4
    assert saved["palette"]["+"] == 171
    assert isinstance(saved["palette"], dict)
    block = next(a for a in saved["actors"] if a["id"] == "block")
    spark = next(a for a in saved["actors"] if a["id"] == "spark")
    assert (block["x"], block["glyph"], block["fg"], block["layer"], block["solid"]) == (7, "▒", 208, 9, False)
    assert (spark["alive"], spark["glyph"], spark["layer"]) == (True, "龍", 11)
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data == full.read_bytes()


def test_outside_map_rejection_and_dead_actor_state(tmp_path):
    """Outside-map moves are rejected, and alive=false removes actor paint and blocking."""
    build_binary()
    run = boundary_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "boundary")
    assert_cell(frames, 0, 1, 0, "@", 46)
    assert_cell(frames, 1, 1, 0, "@", 46)
    assert_cell(frames, 2, 1, 1, "@", 46)
    assert_cell(frames, 3, 1, 2, ".", 238)
    assert_cell(frames, 3, 1, 3, "@", 46)
    assert_cell(frames, 4, 1, 3, "@", 46)
    part = tmp_path / "part-boundary.ans"
    state = tmp_path / "boundary-state.json"
    rest = tmp_path / "rest-boundary.ans"
    run_cmd(["checkpoint", source, 3, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    hero = next(a for a in saved["actors"] if a["id"] == "hero")
    guard = next(a for a in saved["actors"] if a["id"] == "guard")
    assert hero["x"] == 3
    assert guard["alive"] is False
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data == full.read_bytes()


def test_push_chains_partial_set_and_created_actor(tmp_path):
    """Pushable chains, partial set updates, created actors, and dead focus match the public replay model."""
    build_binary()
    run = push_chain_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "push-chain")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 2, 2, 4, "@", 46)
    assert_cell(frames, 2, 2, 5, "箱", 202)
    assert_cell(frames, 3, 2, 4, "@", 46)
    assert_cell(frames, 6, 2, 5, "⚑", 220)
    part = tmp_path / "part-push.ans"
    state = tmp_path / "push-state.json"
    rest = tmp_path / "rest-push.ans"
    run_cmd(["checkpoint", source, 6, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert part.read_bytes() == reference_bytes(run, turns=6)
    assert saved["next"] == 6
    assert saved["palette"]["."] == 238
    crate_b = next(a for a in saved["actors"] if a["id"] == "crate-b")
    banner = next(a for a in saved["actors"] if a["id"] == "banner")
    assert (crate_b["x"], crate_b["pushable"]) == (10, True)
    assert (banner["glyph"], banner["pushable"], banner["alive"]) == ("⚑", False, True)
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def test_reference_model_generated_push_variants(tmp_path):
    """Generated same-schema push variants reject partial hardcoding and preserve checkpoint bytes."""
    build_binary()
    for offset in (0, 1, 2):
        run = push_chain_run()
        run["actors"] = [dict(actor) for actor in run["actors"]]
        run["actors"][1]["fg"] += offset
        run["actors"][2]["x"] += offset
        run["turns"] = [list(turn) for turn in run["turns"]]
        if offset == 1:
            run["turns"].insert(4, [{"op": "set", "id": "crate-a", "glyph": "A", "fg": 210}])
        elif offset == 2:
            run["turns"].insert(5, [{"op": "set", "id": "crate-b", "solid": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}])
        source = write_run(tmp_path, run, f"push-generated-{offset}.json")
        full = tmp_path / f"push-generated-{offset}.ans"
        run_cmd(["render", source, full])
        full_data = full.read_bytes()
        assert full_data == reference_bytes(run)
        part = tmp_path / f"push-generated-{offset}-part.ans"
        state = tmp_path / f"push-generated-{offset}-state.json"
        rest = tmp_path / f"push-generated-{offset}-rest.ans"
        cut = min(7, len(run["turns"]))
        run_cmd(["checkpoint", source, cut, state, part])
        run_cmd(["resume", source, state, rest])
        assert part.read_bytes() + rest.read_bytes() == full_data


def test_trails_mutate_map_and_checkpoint_state(tmp_path):
    """Actor trails mutate terrain, survive push ordering, and checkpoint into resumable map state."""
    build_binary()
    run = trail_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "trail")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 1, 2, 2, ":", 52)
    assert_cell(frames, 3, 2, 1, ":", 57)
    assert_cell(frames, 3, 2, 3, ":", 57)
    assert_cell(frames, 6, 2, 5, ":", 57)
    part = tmp_path / "trail-part.ans"
    state = tmp_path / "trail-state.json"
    rest = tmp_path / "trail-rest.ans"
    run_cmd(["checkpoint", source, 6, state, part])
    expected = reference_state(run, 6)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert part.read_bytes() == reference_bytes(run, turns=6)
    assert saved["next"] == 6
    assert saved["map"] == expected["map"]
    assert saved["palette"][":"] == 57
    hero = next(actor for actor in saved["actors"] if actor["id"] == "hero")
    crate_b = next(actor for actor in saved["actors"] if actor["id"] == "crate-b")
    assert (hero["glyph"], hero["trail"], hero["x"], hero["y"]) == ("界", ":", 5, 1)
    assert (crate_b["x"], crate_b["pushable"], crate_b["trail"]) == (8, True, "+")
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def test_generated_trail_variants_with_reference_model(tmp_path):
    """Generated trail variants preserve mutable terrain, actor creation, and resume boundaries."""
    build_binary()
    for offset, trail_char in enumerate((":", "~", "+")):
        run = trail_run()
        run["actors"] = [dict(actor) for actor in run["actors"]]
        run["actors"][0]["fg"] += offset
        run["actors"][0]["trail"] = trail_char
        run["actors"][1]["trail"] = ["~", "+", ":"][offset]
        run["turns"] = [list(turn) for turn in run["turns"]]
        if offset == 1:
            run["turns"].insert(3, [{"op": "set", "id": "lamp", "x": 6, "y": 2, "trail": "+"}, {"op": "move", "id": "lamp", "dx": -1, "dy": 0}])
        elif offset == 2:
            run["turns"].append([{"op": "set", "id": "crate-a", "solid": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}])
        source = write_run(tmp_path, run, f"trail-generated-{offset}.json")
        full = tmp_path / f"trail-generated-{offset}.ans"
        run_cmd(["render", source, full])
        full_data = full.read_bytes()
        assert full_data == reference_bytes(run)
        cut = min(6 + offset, len(run["turns"]))
        part = tmp_path / f"trail-generated-{offset}-part.ans"
        state = tmp_path / f"trail-generated-{offset}-state.json"
        rest = tmp_path / f"trail-generated-{offset}-rest.ans"
        run_cmd(["checkpoint", source, cut, state, part])
        saved = json.loads(state.read_text(encoding="utf-8"))
        expected = reference_state(run, cut)
        assert saved["map"] == expected["map"]
        run_cmd(["resume", source, state, rest])
        assert part.read_bytes() + rest.read_bytes() == full_data


def test_inapplicable_records_and_tile_mutations_are_replay_safe(tmp_path):
    """Unknown, malformed, and typed-invalid records are ignored while valid tile changes persist."""
    build_binary()
    run = trail_run()
    run["turns"] = [
        [
            {"op": "tile", "x": 3, "y": 3, "ch": "+"},
            {"op": "move", "id": "hero", "dy": 0},
            {"op": "move", "id": "hero", "dx": "1", "dy": 0},
            {"op": "glyph", "id": "hero", "glyph": "龍"},
            {"op": "tint", "ch": ":", "fg": "57"},
            {"op": "mystery", "id": "hero", "x": 12},
            "not-a-record",
            {"id": "hero", "dx": 5, "dy": 0},
        ],
        [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
        [{"op": "tile", "x": 5, "y": 3, "ch": "~"}, {"op": "tint", "ch": "+", "fg": 181}],
        [{"op": "set", "id": "hero", "trail": "+"}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
        [{"op": "tile", "x": 99, "y": 3, "ch": ":"}, {"op": "set", "id": "newcomer", "x": 6, "y": 2, "glyph": "n", "fg": 77, "layer": 4}],
    ]
    source, full, full_data, frames = render_and_parse(tmp_path, run, "invalid-tile")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 1, 2, 2, "@", 46)
    assert_cell(frames, 1, 2, 3, "+", 178)
    assert_cell(frames, 2, 2, 2, ":", 52)
    assert_cell(frames, 4, 2, 3, "+", 181)
    part = tmp_path / "invalid-tile-part.ans"
    state = tmp_path / "invalid-tile-state.json"
    rest = tmp_path / "invalid-tile-rest.ans"
    run_cmd(["checkpoint", source, 4, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    expected = reference_state(run, 4)
    assert saved["map"] == expected["map"]
    assert saved["palette"]["+"] == 181
    hero = next(actor for actor in saved["actors"] if actor["id"] == "hero")
    assert (hero["x"], hero["glyph"], hero["trail"]) == (4, "@", "+")
    assert "newcomer" not in {actor["id"] for actor in saved["actors"]}
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def solid_terrain_run():
    return {
        "width": 12,
        "height": 6,
        "view_w": 8,
        "view_h": 4,
        "map": [
            "############",
            "#..~...+..#",
            "#..........#",
            "#..........#",
            "#..........#",
            "############",
        ],
        "palette": {"#": 244, ".": 238, "~": 39, "+": 178, ":": 52},
        "solid_tiles": ["#", "~"],
        "actors": [
            {"id": "hero", "x": 2, "y": 1, "glyph": "@", "fg": 46, "layer": 7, "solid": True, "trail": ":"},
            {"id": "crate", "x": 5, "y": 1, "glyph": "箱", "fg": 202, "layer": 5, "solid": True, "pushable": True, "trail": "+"},
            {"id": "warden", "x": 8, "y": 1, "glyph": "W", "fg": 160, "layer": 8, "solid": True},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 3, "y": 1, "ch": "."}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 7, "y": 1, "ch": "~"}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 3}],
            [{"op": "tile", "x": 7, "y": 1, "ch": "."}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
            [{"op": "move", "id": "hero", "dx": 1.0, "dy": 0}, {"op": "tile", "x": 4.0, "y": 1, "ch": "~"}],
            [{"op": "set", "id": "warden", "solid": False}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2}],
        ],
    }


def slope_run():
    return {
        "width": 17,
        "height": 8,
        "view_w": 9,
        "view_h": 5,
        "map": [
            "#################",
            "#..>...^....+..#",
            "#.......#.......#",
            "#...............#",
            "#......~........#",
            "#...............#",
            "#...............#",
            "#################",
        ],
        "palette": {"#": 244, ".": 238, ">": 34, "^": 39, "~": 57, "+": 178, ":": 52},
        "solid_tiles": ["#", "~"],
        "slopes": {">": {"dx": 1, "dy": 0}, "^": {"dx": 0, "dy": -1}},
        "actors": [
            {"id": "hero", "x": 2, "y": 1, "glyph": "@", "fg": 46, "layer": 8, "solid": True, "trail": ":"},
            {"id": "crate", "x": 4, "y": 1, "glyph": "箱", "fg": 202, "layer": 6, "solid": True, "pushable": True, "trail": "+"},
            {"id": "sentinel", "x": 7, "y": 2, "glyph": "S", "fg": 160, "layer": 7, "solid": True},
            {"id": "wisp", "x": 8, "y": 5, "glyph": "w", "fg": 81, "layer": 4, "solid": False},
        ],
        "turns": [
            [{"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 5, "y": 1, "ch": ">"}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 6, "y": 1, "ch": "~"}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 6, "y": 1, "ch": ">"}, {"op": "set", "id": "sentinel", "x": 7, "y": 1, "pushable": True}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}],
            [{"op": "tile", "x": 8, "y": 1, "ch": "^"}, {"op": "move", "id": "sentinel", "dx": 1, "dy": 0}],
            [{"op": "set", "id": "wisp", "x": 8, "y": 1, "solid": False}, {"op": "glyph", "id": "wisp", "glyph": "龍", "fg": 201}],
        ],
    }


def test_solid_tile_mutations_and_strict_integer_records(tmp_path):
    """Solid terrain characters can change during replay, and float-valued records are ignored."""
    build_binary()
    run = solid_terrain_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "solid-terrain")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 1, 1, 2, "@", 46)
    assert_cell(frames, 2, 1, 2, ":", 52)
    assert_cell(frames, 2, 1, 3, "@", 46)
    assert_cell(frames, 3, 1, 5, "箱", 202)
    assert_cell(frames, 4, 1, 5, "箱", 202)
    assert_cell(frames, 5, 1, 5, "箱", 202)
    assert_cell(frames, 5, 1, 7, "W", 160)
    part = tmp_path / "solid-part.ans"
    state = tmp_path / "solid-state.json"
    rest = tmp_path / "solid-rest.ans"
    run_cmd(["checkpoint", source, 5, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    expected = reference_state(run, 5)
    assert saved["map"] == expected["map"]
    hero = next(actor for actor in saved["actors"] if actor["id"] == "hero")
    crate = next(actor for actor in saved["actors"] if actor["id"] == "crate")
    assert (hero["x"], crate["x"], saved["map"][1][7]) == (5, 6, ".")
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def test_generated_solid_tile_variants(tmp_path):
    """Same-schema solid-terrain variants preserve push chains and checkpoint boundaries."""
    build_binary()
    for offset, blocker in enumerate(("~", "+", ":")):
        run = solid_terrain_run()
        run["solid_tiles"] = ["#", blocker]
        run["map"] = list(run["map"])
        row = list(run["map"][1])
        row[3] = blocker
        run["map"][1] = "".join(row)
        run["actors"] = [dict(actor) for actor in run["actors"]]
        run["actors"][0]["fg"] += offset
        run["turns"] = [list(turn) for turn in run["turns"]]
        run["turns"][1] = [{"op": "tile", "x": 3, "y": 1, "ch": "."}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}]
        run["turns"][2] = [{"op": "tile", "x": 7, "y": 1, "ch": blocker}, {"op": "move", "id": "hero", "dx": 1, "dy": 0, "repeat": 2 + offset}]
        source = write_run(tmp_path, run, f"solid-generated-{offset}.json")
        full = tmp_path / f"solid-generated-{offset}.ans"
        run_cmd(["render", source, full])
        full_data = full.read_bytes()
        assert full_data == reference_bytes(run)
        part = tmp_path / f"solid-generated-{offset}-part.ans"
        state = tmp_path / f"solid-generated-{offset}-state.json"
        rest = tmp_path / f"solid-generated-{offset}-rest.ans"
        cut = min(5, len(run["turns"]))
        run_cmd(["checkpoint", source, cut, state, part])
        saved = json.loads(state.read_text(encoding="utf-8"))
        assert saved["map"] == reference_state(run, cut)["map"]
        run_cmd(["resume", source, state, rest])
        assert part.read_bytes() + rest.read_bytes() == full_data


def test_slope_sweeps_push_and_checkpoint_mutable_terrain(tmp_path):
    """Post-turn slope sweeps use current terrain, normal collisions, push chains, and checkpoint state."""
    build_binary()
    run = slope_run()
    source, full, full_data, frames = render_and_parse(tmp_path, run, "slopes")
    assert full_data == reference_bytes(run) == full.read_bytes()
    assert_cell(frames, 1, 1, 3, ":", 52)
    assert_cell(frames, 1, 1, 4, "@", 46)
    assert_cell(frames, 2, 1, 2, ":", 52)
    assert_cell(frames, 3, 1, 5, "箱", 202)
    assert_cell(frames, 4, 1, 7, "S", 160)
    part = tmp_path / "slope-part.ans"
    state = tmp_path / "slope-state.json"
    rest = tmp_path / "slope-rest.ans"
    run_cmd(["checkpoint", source, 4, state, part])
    saved = json.loads(state.read_text(encoding="utf-8"))
    expected = reference_state(run, 4)
    assert part.read_bytes() == reference_bytes(run, turns=4)
    assert saved["map"] == expected["map"]
    assert saved["actors"] == expected["actors"]
    run_cmd(["resume", source, state, rest])
    assert part.read_bytes() + rest.read_bytes() == full_data


def test_generated_slope_variants_with_blocking_and_layering(tmp_path):
    """Generated slope variants reject one-shot movement and visible-fixture hardcoding."""
    build_binary()
    for offset, blocker in enumerate(("~", "+", ":")):
        run = slope_run()
        run["solid_tiles"] = ["#", blocker]
        run["map"] = list(run["map"])
        row = list(run["map"][1])
        row[6] = blocker
        run["map"][1] = "".join(row)
        run["actors"] = [dict(actor) for actor in run["actors"]]
        run["actors"][0]["fg"] += offset
        run["actors"][1]["trail"] = ["+", ":", ">"][offset]
        run["turns"] = [list(turn) for turn in run["turns"]]
        run["turns"][2] = [{"op": "tile", "x": 6, "y": 1, "ch": ">"}, {"op": "move", "id": "hero", "dx": 1, "dy": 0}]
        if offset == 2:
            run["turns"].append([{"op": "tile", "x": 8, "y": 1, "ch": "^"}, {"op": "set", "id": "wisp", "x": 8, "y": 1, "layer": 12}])
        source = write_run(tmp_path, run, f"slope-generated-{offset}.json")
        full = tmp_path / f"slope-generated-{offset}.ans"
        run_cmd(["render", source, full])
        full_data = full.read_bytes()
        assert full_data == reference_bytes(run)
        part = tmp_path / f"slope-generated-{offset}-part.ans"
        state = tmp_path / f"slope-generated-{offset}-state.json"
        rest = tmp_path / f"slope-generated-{offset}-rest.ans"
        cut = min(5, len(run["turns"]))
        run_cmd(["checkpoint", source, cut, state, part])
        saved = json.loads(state.read_text(encoding="utf-8"))
        assert saved["actors"] == reference_state(run, cut)["actors"]
        run_cmd(["resume", source, state, rest])
        assert part.read_bytes() + rest.read_bytes() == full_data
