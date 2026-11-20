import json
import subprocess
from collections import deque
from pathlib import Path

import pytest

BIN = Path("/app/minimap/zig-out/bin/minimap")

SCENE_ALPHA = """size 7 6
tiles
..g..s.
.gwww..
..g.s..
s..g..s
..wwg..
g...s..
edge 2 1 W
edge 3 1 E
edge 1 2 N
edge 5 3 S
light A 1 1 4
light B 6 4 7
view 4 3 4 3
tint 4 2 200 40 160 96
tint 0 5 20 150 220 128
move A 3 2
move B 5 1
"""

SCENE_BETA = """size 5 5
tiles
.g..s
..w..
g...g
..w..
s..g.
edge 2 1 S
edge 3 3 W
light A 0 0 5
view 2 2 3 3
tint 2 2 255 120 20 112
move A 4 0
move A 4 4
move A 0 4
"""

SCENE_GAMMA = """size 6 4
tiles
..g.s.
w...g.
.s..w.
g...s.
edge 2 0 S
edge 2 1 N
edge 4 2 W
light scout-1 0 0 4
light scout-10 5 3 5
view 3 2 4 3
tint 3 1 90 210 250 104
move scout-10 4 0
move scout-1 1 3
move scout-10 2 2
"""

SCENE_DELTA = """size 8 5
tiles
..w..g..
.ww.s..g
..g.w..s
g...ww..
..s...g.
edge 2 0 S
edge 2 1 N
edge 4 2 E
edge 5 2 W
light alpha 0 0 7
light beta 7 4 6
view 5 2 5 3
tint 2 1 120 30 220 80
tint 2 1 20 200 60 96
tint 5 3 240 180 30 112
move alpha 6 0
move beta 1 4
move alpha 3 3
"""

SCENE_EPSILON = """size 6 5
tiles
..g..s
w.w..g
..s.w.
g...w.
..g..s
edge 1 1 E
edge 2 1 W
light alpha 0 0 5
light beta 5 4 5
view 3 2 4 3
tint 4 3 200 60 30 88
move alpha 4 0
move alpha 2 2
"""

BASE = {
    ".": (46, 48, 54),
    "g": (36, 82, 48),
    "s": (70, 58, 42),
    "w": (28, 31, 36),
}
WARM = (255, 214, 126)
BORDER = (230, 238, 255)
BITS = {"N": 1, "E": 2, "S": 4, "W": 8}
OPP = {"N": "S", "E": "W", "S": "N", "W": "E"}
STEP = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}


@pytest.fixture(scope="session", autouse=True)
def build_renderer():
    """Build the renderer once for the verifier run."""
    subprocess.run(["/app/minimap/scripts/build.sh"], check=True, cwd="/app")


def fnv64(data: bytes) -> int:
    h = 14695981039346656037
    for b in data:
        h ^= b
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def parse_scene(text: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out = {"edges": {}, "lights": [], "moves": [], "tints": []}
    i = 0
    while i < len(lines):
        parts = lines[i].split()
        tag = parts[0]
        if tag == "size":
            out["w"], out["h"] = int(parts[1]), int(parts[2])
        elif tag == "tiles":
            rows = lines[i + 1 : i + 1 + out["h"]]
            out["tiles"] = rows
            i += out["h"]
        elif tag == "edge":
            x, y = int(parts[1]), int(parts[2])
            mask = out["edges"].setdefault((x, y), 0)
            for c in parts[3]:
                mask |= BITS[c]
            out["edges"][(x, y)] = mask
        elif tag == "light":
            out["lights"].append({"id": parts[1], "x": int(parts[2]), "y": int(parts[3]), "r": int(parts[4])})
        elif tag == "view":
            out["view"] = tuple(map(int, parts[1:5]))
        elif tag == "tint":
            out["tints"].append((int(parts[1]), int(parts[2]), *tuple(map(int, parts[3:7]))))
        elif tag == "move":
            out["moves"].append((parts[1], int(parts[2]), int(parts[3])))
        else:
            raise AssertionError(tag)
        i += 1
    return out


def apply_moves(scene, count, *, lights=None, start=0):
    lights = [dict(light) for light in (scene["lights"] if lights is None else lights)]
    for ident, x, y in scene["moves"][start:count]:
        for light in lights:
            if light["id"] == ident:
                light["x"], light["y"] = x, y
                break
    return lights


def blocked(scene, x, y, nx, ny, d):
    return bool(scene["edges"].get((x, y), 0) & BITS[d] or scene["edges"].get((nx, ny), 0) & BITS[OPP[d]])


def blend(a, b, alpha):
    return tuple((a[i] * (255 - alpha) + b[i] * alpha + 127) // 255 for i in range(3))


def viewport(scene):
    cx, cy, vw, vh = scene["view"]
    x0 = cx - vw // 2
    y0 = cy - vh // 2
    x0 = min(max(x0, 0), scene["w"] - vw)
    y0 = min(max(y0, 0), scene["h"] - vh)
    return [x0, y0, x0 + vw - 1, y0 + vh - 1]


def expected_image(text, turn, *, lights=None, move_start=0):
    scene = parse_scene(text)
    lights = apply_moves(scene, turn, lights=lights, start=move_start)
    bright = [[0 for _ in range(scene["w"])] for _ in range(scene["h"])]
    for light in lights:
        seen = [[-1 for _ in range(scene["w"])] for _ in range(scene["h"])]
        q = deque([(light["x"], light["y"], light["r"])])
        while q:
            x, y, rem = q.popleft()
            if not (0 <= x < scene["w"] and 0 <= y < scene["h"]) or rem <= 0:
                continue
            if rem <= seen[y][x]:
                continue
            seen[y][x] = rem
            bright[y][x] = max(bright[y][x], (rem * 255 + light["r"] // 2) // light["r"])
            if rem <= 1:
                continue
            for d, (dx, dy) in STEP.items():
                nx, ny = x + dx, y + dy
                if 0 <= nx < scene["w"] and 0 <= ny < scene["h"] and not blocked(scene, x, y, nx, ny, d):
                    cost = 2 if scene["tiles"][ny][nx] == "w" else 1
                    if rem > cost:
                        q.append((nx, ny, rem - cost))
    tile_colors = []
    for y in range(scene["h"]):
        row = []
        for x in range(scene["w"]):
            c = blend(BASE[scene["tiles"][y][x]], WARM, bright[y][x])
            for tx, ty, tr, tg, tb, ta in scene["tints"]:
                if (tx, ty) == (x, y):
                    c = blend(c, (tr, tg, tb), ta)
            row.append(c)
        tile_colors.append(row)
    vp = viewport(scene)
    width, height = scene["w"] * 4, scene["h"] * 4
    lines = ["P3", f"{width} {height}", "255"]
    bx0, by0, bx1, by1 = vp[0] * 4, vp[1] * 4, (vp[2] + 1) * 4 - 1, (vp[3] + 1) * 4 - 1
    for py in range(height):
        for px in range(width):
            c = tile_colors[py // 4][px // 4]
            if ((py == by0 or py == by1) and bx0 <= px <= bx1) or ((px == bx0 or px == bx1) and by0 <= py <= by1):
                c = BORDER
            lines.append(f"{c[0]} {c[1]} {c[2]}")
    return "\n".join(lines) + "\n"


def ppm_data(text):
    tokens = text.split()
    assert len(tokens) >= 4
    assert tokens[0] == "P3"
    width, height = int(tokens[1]), int(tokens[2])
    assert tokens[3] == "255"
    values = [int(token) for token in tokens[4:]]
    assert len(values) == width * height * 3
    assert all(0 <= value <= 255 for value in values)
    return width, height, values


def assert_ppm_matches(actual_path, expected_text):
    assert ppm_data(actual_path.read_text()) == ppm_data(expected_text)


def expected_state(text, turn, *, lights=None, move_start=0):
    scene = parse_scene(text)
    lights = apply_moves(scene, turn, lights=lights, start=move_start)
    return {
        "turn": turn,
        "scene_hash": f"{fnv64(text.encode()):016x}",
        "lights": [{"id": light["id"], "x": light["x"], "y": light["y"]} for light in lights],
        "viewport": viewport(scene),
    }


def write_scene(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def run_render(scene_path, out_dir):
    subprocess.run([str(BIN), "render", str(scene_path), str(out_dir)], check=True, text=True, capture_output=True)


def test_official_frame_for_recorded_scene(tmp_path):
    """A full recorded scene renders the exact official minimap frame and state."""
    scene = write_scene(tmp_path, "alpha.scene", SCENE_ALPHA)
    out = tmp_path / "out"
    run_render(scene, out)
    assert_ppm_matches(out / "image.ppm", expected_image(SCENE_ALPHA, 2))
    assert json.loads((out / "state.json").read_text()) == expected_state(SCENE_ALPHA, 2)


def test_timeline_commands_preserve_frame_equivalence(tmp_path):
    """Checkpoint and resume produce the same final game UI as direct rendering."""
    scene = write_scene(tmp_path, "beta.scene", SCENE_BETA)
    direct = tmp_path / "direct"
    resumed = tmp_path / "resumed"
    state = tmp_path / "state.json"
    run_render(scene, direct)
    subprocess.run([str(BIN), "checkpoint", str(scene), "1", str(state), str(tmp_path / "checkpoint")], check=True, text=True, capture_output=True)
    assert json.loads(state.read_text()) == expected_state(SCENE_BETA, 1)
    subprocess.run([str(BIN), "resume", str(scene), str(state), str(resumed)], check=True, text=True, capture_output=True)
    assert (resumed / "image.ppm").read_text() == (direct / "image.ppm").read_text()
    assert json.loads((resumed / "state.json").read_text()) == expected_state(SCENE_BETA, 3)


def test_saved_state_belongs_to_the_same_scene(tmp_path):
    """A saved state is not accepted for a different recorded scene."""
    scene = write_scene(tmp_path, "base.scene", SCENE_BETA)
    state = tmp_path / "state.json"
    subprocess.run([str(BIN), "checkpoint", str(scene), "2", str(state), str(tmp_path / "checkpoint")], check=True, text=True, capture_output=True)
    changed = write_scene(tmp_path, "changed.scene", SCENE_BETA + "tint 1 1 9 9 9 9\n")
    result = subprocess.run([str(BIN), "resume", str(changed), str(state), str(tmp_path / "changed")], text=True, capture_output=True)
    assert result.returncode != 0


def test_second_scene_matches_independent_reference(tmp_path):
    """Another scene has deterministic pixels and metadata under direct rendering."""
    scene = write_scene(tmp_path, "beta.scene", SCENE_BETA)
    out = tmp_path / "out"
    run_render(scene, out)
    assert_ppm_matches(out / "image.ppm", expected_image(SCENE_BETA, 3))
    assert json.loads((out / "state.json").read_text()) == expected_state(SCENE_BETA, 3)


def test_textual_light_identifiers_remain_distinct(tmp_path):
    """Light identifiers are matched and serialized as complete strings."""
    scene = write_scene(tmp_path, "gamma.scene", SCENE_GAMMA)
    out = tmp_path / "out"
    run_render(scene, out)
    assert_ppm_matches(out / "image.ppm", expected_image(SCENE_GAMMA, 3))
    assert json.loads((out / "state.json").read_text()) == expected_state(SCENE_GAMMA, 3)


def test_slow_tiles_and_stacked_tints_shape_the_final_frame(tmp_path):
    """Water travel cost and repeated tints are both reflected in the rendered frame."""
    scene = write_scene(tmp_path, "delta.scene", SCENE_DELTA)
    out = tmp_path / "out"
    run_render(scene, out)
    assert_ppm_matches(out / "image.ppm", expected_image(SCENE_DELTA, 3))
    assert json.loads((out / "state.json").read_text()) == expected_state(SCENE_DELTA, 3)


def test_resume_continues_from_saved_light_positions(tmp_path):
    """Resume uses saved light coordinates before applying the remaining moves."""
    scene = write_scene(tmp_path, "epsilon.scene", SCENE_EPSILON)
    out = tmp_path / "resumed"
    saved_lights = [
        {"id": "alpha", "x": 4, "y": 0, "r": 5},
        {"id": "beta", "x": 0, "y": 4, "r": 5},
    ]
    state_doc = expected_state(SCENE_EPSILON, 1, lights=saved_lights, move_start=1)
    state = tmp_path / "saved.json"
    state.write_text(json.dumps(state_doc, separators=(",", ":")) + "\n")

    subprocess.run([str(BIN), "resume", str(scene), str(state), str(out)], check=True, text=True, capture_output=True)

    assert_ppm_matches(out / "image.ppm", expected_image(SCENE_EPSILON, 2, lights=saved_lights, move_start=1))
    assert json.loads((out / "state.json").read_text()) == expected_state(SCENE_EPSILON, 2, lights=saved_lights, move_start=1)


def test_binary_rejects_unknown_subcommand(tmp_path):
    """The renderer rejects invocations outside the published command set."""
    result = subprocess.run([str(BIN), "preview", str(tmp_path / "x.scene"), str(tmp_path / "out")], text=True, capture_output=True)
    assert result.returncode != 0
