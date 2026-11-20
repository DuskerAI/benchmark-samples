import json
import subprocess
from pathlib import Path

APP = Path("/app/isorender")
BIN = APP / "bin" / "isoboard"


def run_renderer(tmp_path: Path, scene_text: str, label: str):
    scene_path = tmp_path / f"{label}.scene"
    out_dir = tmp_path / f"{label}_out"
    scene_path.write_text(scene_text, encoding="utf-8")
    subprocess.run([str(APP / "scripts" / "build.sh")], cwd=str(APP), check=True)
    subprocess.run([str(BIN), "render", str(scene_path), str(out_dir)], cwd=str(APP), check=True)
    return out_dir


def parse_ppm(path: Path):
    parts = path.read_text(encoding="ascii").split()
    assert parts[0] == "P3"
    width = int(parts[1])
    height = int(parts[2])
    assert int(parts[3]) == 255
    values = list(map(int, parts[4:]))
    assert len(values) == width * height * 3
    rows = []
    pos = 0
    for _ in range(height):
        row = []
        for _ in range(width):
            row.append(tuple(values[pos:pos + 3]))
            pos += 3
        rows.append(row)
    return width, height, rows


def fnv_hex(rows, tick: int) -> str:
    h = 14695981039346656037
    prime = 1099511628211
    for row in rows:
        for pixel in row:
            for channel in pixel:
                h ^= channel & 0xFF
                h = (h * prime) & 0xFFFFFFFFFFFFFFFF
    for b in str(tick).encode("ascii"):
        h ^= b
        h = (h * prime) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def reference(scene_text: str):
    width = 64
    height = 48
    ox = oy = cxam = cyam = tick = 0
    items = []
    for raw in scene_text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        if parts[0] == "size":
            width, height = map(int, parts[1:3])
        elif parts[0] == "origin":
            ox, oy = map(int, parts[1:3])
        elif parts[0] == "camera":
            cxam, cyam = map(int, parts[1:3])
        elif parts[0] == "tick":
            tick = int(parts[1])
        elif parts[0] == "tile":
            x, y, r, g, b = map(int, parts[1:6])
            items.append(("tile", x, y, 0, (r, g, b), None, 0, len(items) + 1))
        elif parts[0] == "sprite":
            x, y, z, r1, g1, b1, r2, g2, b2, seed = map(int, parts[1:11])
            items.append(("sprite", x, y, z, (r1, g1, b1), (r2, g2, b2), seed, len(items) + 1))
        elif parts[0] == "overlay":
            x, y, w, h, r, g, b, alpha = map(int, parts[1:9])
            items.append(("overlay", x, y, w, (r, g, b), None, alpha, len(items) + 1, h))
    rows = [[(0, 0, 0) for _ in range(width)] for _ in range(height)]
    depths = [[None for _ in range(width)] for _ in range(height)]

    def plot(px, py, rgb, depth):
        if 0 <= px < width and 0 <= py < height:
            if depths[py][px] is None or depth > depths[py][px]:
                rows[py][px] = rgb
                depths[py][px] = depth

    overlays = []
    for kind, x, y, z, rgb1, rgb2, seed, index, *rest in items:
        if kind == "overlay":
            overlays.append((x, y, z, rest[0], rgb1, seed))
            continue
        center_x = ox - cxam + (x - y) * 8
        center_y = oy - cyam + (x + y) * 4
        if kind == "tile":
            depth = (x + y, 0, x, y, index)
            for py in range(center_y - 4, center_y + 5):
                for px in range(center_x - 8, center_x + 9):
                    if abs(px - center_x) * 4 + abs(py - center_y) * 8 <= 32:
                        plot(px, py, rgb1, depth)
        else:
            rgb = rgb1 if (tick + seed) % 2 == 0 else rgb2
            depth = (x + y, z, x, y, index)
            anchor_y = center_y - z
            for dy in range(-3, 4):
                for dx in range(-2, 3):
                    if abs(dx) <= 2 and abs(dy) <= 3 and abs(dx) + abs(dy) <= 4:
                        plot(center_x + dx, anchor_y + dy, rgb, depth)
    for x, y, w, h, rgb, alpha in overlays:
        for py in range(y, y + h):
            for px in range(x, x + w):
                if 0 <= px < width and 0 <= py < height:
                    old = rows[py][px]
                    rows[py][px] = tuple((old[i] * (255 - alpha) + rgb[i] * alpha) // 255 for i in range(3))
    return width, height, rows, {"width": width, "height": height, "tick": tick, "hash": fnv_hex(rows, tick)}


def assert_artifacts(out_dir: Path, scene_text: str):
    exp_w, exp_h, exp_rows, exp_state = reference(scene_text)
    got_w, got_h, got_rows = parse_ppm(out_dir / "board.ppm")
    assert (got_w, got_h) == (exp_w, exp_h)
    assert got_rows == exp_rows
    assert json.loads((out_dir / "state.json").read_text(encoding="utf-8")) == exp_state


def generated_scene() -> str:
    lines = ["size 73 52", "origin 29 11", "camera -5 6", "tick 14"]
    palette = [(31, 77, 113), (168, 54, 42), (72, 150, 82), (204, 190, 88), (111, 72, 172)]
    n = 0
    for x in range(-2, 4):
        for y in range(-1, 4):
            if (x * 7 + y * 5) % 3 != 1:
                r, g, b = palette[n % len(palette)]
                lines.append(f"tile {x} {y} {r} {g} {b}")
                n += 1
    sprites = [
        (1, 1, 0, 250, 40, 40, 40, 230, 250, 2),
        (1, 1, 4, 12, 240, 90, 240, 210, 35, 5),
        (2, 0, 3, 220, 220, 250, 25, 55, 210, 4),
        (-1, 3, 2, 180, 30, 210, 40, 210, 160, 1),
    ]
    for s in sprites:
        lines.append("sprite " + " ".join(map(str, s)))
    lines.extend([
        "overlay 0 0 73 8 18 30 90 64",
        "overlay 24 12 23 19 210 160 40 87",
        "overlay 39 4 18 42 20 170 190 33",
    ])
    return "\n".join(lines) + "\n"


def test_official_artifacts_for_layered_scene(tmp_path):
    """The renderer produces the official artifacts for a layered board scene."""
    scene = """\
size 55 43
origin 24 12
camera -4 3
tick 7
tile -1 0 35 50 95
tile 0 0 80 120 70
tile 1 0 140 90 45
tile 0 1 90 55 135
tile 1 1 170 160 70
tile 2 0 60 150 170
sprite 1 0 2 240 45 45 35 210 245 3
sprite 0 1 5 30 240 100 220 210 20 6
overlay 6 3 26 18 15 20 120 48
sprite 1 1 1 250 250 250 80 80 210 2
overlay 16 12 20 21 210 60 35 96
"""
    assert_artifacts(run_renderer(tmp_path, scene, "layered"), scene)


def test_tabs_and_late_overlays_match_reference(tmp_path):
    """Whitespace and post-draw tinting follow the scene contract."""
    scene = """\
size\t61\t39
origin 28\t9
camera\t-6\t4
tick 22
tile\t-1\t1\t30\t90\t150
tile 0 0 180 110 45
overlay 0 0 61 39 3 4 5 0
sprite\t0\t0\t2\t240\t80\t30\t40\t190\t250\t1
tile 1 0 45 190 80
sprite 1 0 5 250 250 250 20 20 220 4
overlay 9 4 30 24 200 50 80 115
overlay 22 0 31 19 10 180 220 71
"""
    assert_artifacts(run_renderer(tmp_path, scene, "tabs_overlay"), scene)


def test_official_artifacts_for_generated_scene(tmp_path):
    """A generated board with many overlaps matches the official artifact rules."""
    scene = generated_scene()
    assert_artifacts(run_renderer(tmp_path, scene, "generated"), scene)


def test_repeated_runs_are_identical(tmp_path):
    """Rendering the same recorded scene twice gives byte-stable artifacts."""
    scene = generated_scene().replace("tick 14", "tick 15")
    first = run_renderer(tmp_path, scene, "repeat_a")
    second = run_renderer(tmp_path, scene, "repeat_b")
    assert (first / "board.ppm").read_bytes() == (second / "board.ppm").read_bytes()
    assert json.loads((first / "state.json").read_text(encoding="utf-8")) == json.loads((second / "state.json").read_text(encoding="utf-8"))
    assert_artifacts(first, scene)
