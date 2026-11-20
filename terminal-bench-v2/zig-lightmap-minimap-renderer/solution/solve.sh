#!/bin/bash
set -euo pipefail

cd /app

python3 - <<'PY'
from pathlib import Path

p = Path('/app/minimap/src/main.zig')
s = p.read_text()


def patch(old: str, new: str, label: str) -> None:
    global s
    if old not in s:
        raise SystemExit(f'patch failed: {label}')
    s = s.replace(old, new, 1)


patch('''const MaxTiles = 1024;
const MaxLights = 16;
const MaxMoves = 512;
const Scale = 4;

const Color = struct { r: u8, g: u8, b: u8 };
const Light = struct { id: u8, x: usize, y: usize, radius: i32 };
const Move = struct { id: u8, x: usize, y: usize };''', '''const MaxTiles = 1024;
const MaxLights = 16;
const MaxMoves = 512;
const MaxTints = 512;
const MaxQueue = MaxTiles * 64;
const Scale = 4;

const Color = struct { r: u8, g: u8, b: u8 };
const Id = struct { bytes: [32]u8, len: usize };
const Light = struct { id: Id, x: usize, y: usize, radius: i32 };
const Move = struct { id: Id, x: usize, y: usize };
const Tint = struct { x: usize, y: usize, color: Color, alpha: u8 };''', 'types')

patch('''    tint_r: [MaxTiles]u8,
    tint_g: [MaxTiles]u8,
    tint_b: [MaxTiles]u8,
    tint_a: [MaxTiles]u8,
    lights: [MaxLights]Light,''', '''    tints: [MaxTints]Tint,
    tint_count: usize,
    lights: [MaxLights]Light,''', 'scene tint storage')

patch('''        applyMoves(&scene, scene.move_count);''', '''        applyMoves(&scene, scene.move_count, 0);''', 'render move range')
patch('''        applyMoves(&scene, n);''', '''        applyMoves(&scene, n, 0);''', 'checkpoint move range')
patch('''        if (turn > scene.move_count) return error.BadState;
        if (saved_hash != scene.hash) return error.BadState;
        applyMoves(&scene, scene.move_count);
        try writeOutputs(allocator, &scene, scene.move_count, args[4], null);''', '''        if (turn > scene.move_count) return error.BadState;
        if (saved_hash != scene.hash) return error.BadState;
        try loadSavedLights(allocator, &scene, state_bytes);
        applyMoves(&scene, scene.move_count, turn);
        try writeOutputs(allocator, &scene, scene.move_count, args[4], null);''', 'resume saved positions')

patch('''    sc.light_count = 0;
    sc.move_count = 0;
    sc.view_cx = 0;
    sc.view_cy = 0;
    sc.view_w = 1;
    sc.view_h = 1;
    sc.hash = sceneHash(data);
    @memset(sc.terrain[0..], '.');
    @memset(sc.edges[0..], 0);
    @memset(sc.tint_r[0..], 0);
    @memset(sc.tint_g[0..], 0);
    @memset(sc.tint_b[0..], 0);
    @memset(sc.tint_a[0..], 0);''', '''    sc.light_count = 0;
    sc.move_count = 0;
    sc.tint_count = 0;
    sc.view_cx = 0;
    sc.view_cy = 0;
    sc.view_w = 1;
    sc.view_h = 1;
    sc.hash = sceneHash(data);
    @memset(sc.terrain[0..], '.');
    @memset(sc.edges[0..], 0);''', 'scene init')

patch('''                .id = idtok[0],''', '''                .id = try parseId(idtok),''', 'light id parse')
patch('''                .id = idtok[0],''', '''                .id = try parseId(idtok),''', 'move id parse')

patch('''        } else if (std.mem.eql(u8, tag, "tint")) {
            const x = try parseNext(usize, &toks);
            const y = try parseNext(usize, &toks);
            const i = index(&sc, x, y);
            sc.tint_r[i] = try parseNext(u8, &toks);
            sc.tint_g[i] = try parseNext(u8, &toks);
            sc.tint_b[i] = try parseNext(u8, &toks);
            sc.tint_a[i] = try parseNext(u8, &toks);''', '''        } else if (std.mem.eql(u8, tag, "tint")) {
            if (sc.tint_count >= MaxTints) return error.BadScene;
            const x = try parseNext(usize, &toks);
            const y = try parseNext(usize, &toks);
            if (x >= sc.w or y >= sc.h) return error.BadScene;
            sc.tints[sc.tint_count] = .{
                .x = x,
                .y = y,
                .color = .{
                    .r = try parseNext(u8, &toks),
                    .g = try parseNext(u8, &toks),
                    .b = try parseNext(u8, &toks),
                },
                .alpha = try parseNext(u8, &toks),
            };
            sc.tint_count += 1;''', 'stacked tints')

patch('''fn parseNext(comptime T: type, toks: anytype) !T {
    const tok = toks.next() orelse return error.BadScene;
    return std.fmt.parseInt(T, tok, 10);
}''', '''fn parseNext(comptime T: type, toks: anytype) !T {
    const tok = toks.next() orelse return error.BadScene;
    return std.fmt.parseInt(T, tok, 10);
}

fn parseId(tok: []const u8) !Id {
    if (tok.len == 0 or tok.len > 32) return error.BadScene;
    var id: Id = .{ .bytes = undefined, .len = tok.len };
    @memset(id.bytes[0..], 0);
    @memcpy(id.bytes[0..tok.len], tok);
    return id;
}

fn idEqual(a: Id, b: Id) bool {
    return a.len == b.len and std.mem.eql(u8, a.bytes[0..a.len], b.bytes[0..b.len]);
}

fn loadSavedLights(allocator: std.mem.Allocator, sc: *Scene, data: []const u8) !void {
    for (sc.lights[0..sc.light_count]) |*light| {
        const needle = try std.fmt.allocPrint(allocator, "\\\"id\\\":\\\"{s}\\\"", .{light.id.bytes[0..light.id.len]});
        defer allocator.free(needle);
        const at = std.mem.indexOf(u8, data, needle) orelse return error.BadState;
        const rest = data[at + needle.len ..];
        light.x = try parseJsonNumberAfter(rest, "\\\"x\\\":");
        light.y = try parseJsonNumberAfter(rest, "\\\"y\\\":");
    }
}

fn parseJsonNumberAfter(data: []const u8, key: []const u8) !usize {
    const at = std.mem.indexOf(u8, data, key) orelse return error.BadState;
    var i = at + key.len;
    while (i < data.len and (data[i] == ' ' or data[i] == '\\t' or data[i] == '\\r' or data[i] == '\\n')) : (i += 1) {}
    var j = i;
    while (j < data.len and data[j] >= '0' and data[j] <= '9') : (j += 1) {}
    if (j == i) return error.BadState;
    return std.fmt.parseInt(usize, data[i..j], 10);
}''', 'id and saved-state helpers')

patch('''fn applyMoves(sc: *Scene, count: usize) void {
    for (sc.moves[0..count]) |mv| {
        for (sc.lights[0..sc.light_count]) |*light| {
            if (light.id == mv.id) {
                light.x = mv.x;
                light.y = mv.y;
                break;
            }
        }
    }
}''', '''fn applyMoves(sc: *Scene, count: usize, start: usize) void {
    for (sc.moves[start..count]) |mv| {
        for (sc.lights[0..sc.light_count]) |*light| {
            if (idEqual(light.id, mv.id)) {
                light.x = mv.x;
                light.y = mv.y;
                break;
            }
        }
    }
}''', 'apply move IDs and range')

patch('''    var best_seen: [MaxTiles]i32 = undefined;
    @memset(best_seen[0..], -1);

    for (sc.lights[0..sc.light_count]) |light| {
        var qx: [MaxTiles * 4]usize = undefined;
        var qy: [MaxTiles * 4]usize = undefined;
        var qr: [MaxTiles * 4]i32 = undefined;''', '''    for (sc.lights[0..sc.light_count]) |light| {
        var best_seen: [MaxTiles]i32 = undefined;
        @memset(best_seen[0..], -1);
        var qx: [MaxQueue]usize = undefined;
        var qy: [MaxQueue]usize = undefined;
        var qr: [MaxQueue]i32 = undefined;''', 'per-light search state')

patch('''            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'N', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'E', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'S', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'W', rem - 1);''', '''            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'N', rem);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'E', rem);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'S', rem);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'W', rem);''', 'terrain cost caller')

patch('''        if (sc.tint_a[i] != 0) {
            c = blendToward(c, .{ .r = sc.tint_r[i], .g = sc.tint_g[i], .b = sc.tint_b[i] }, sc.tint_a[i], false);
        }
        out[i] = c;''', '''        const tx = i % sc.w;
        const ty = i / sc.w;
        for (sc.tints[0..sc.tint_count]) |tint| {
            if (tint.x == tx and tint.y == ty) {
                c = blendToward(c, tint.color, tint.alpha, false);
            }
        }
        out[i] = c;''', 'apply stacked tints')

patch('''fn tryPush(sc: *const Scene, qx: *[MaxTiles * 4]usize, qy: *[MaxTiles * 4]usize, qr: *[MaxTiles * 4]i32, tail: *usize, x: usize, y: usize, dir: u8, rem: i32) void {''', '''fn tryPush(sc: *const Scene, qx: *[MaxQueue]usize, qy: *[MaxQueue]usize, qr: *[MaxQueue]i32, tail: *usize, x: usize, y: usize, dir: u8, rem: i32) void {''', 'queue size')
patch('''    if (nx >= sc.w or ny >= sc.h) return;
    if (blocked(sc, x, y, dir)) return;
    if (tail.* >= qx.len) return;
    qx[tail.*] = nx;
    qy[tail.*] = ny;
    qr[tail.*] = rem;''', '''    if (nx >= sc.w or ny >= sc.h) return;
    if (blocked(sc, x, y, nx, ny, dir)) return;
    const cost = stepCost(sc, nx, ny);
    if (rem <= cost) return;
    if (tail.* >= qx.len) return;
    qx[tail.*] = nx;
    qy[tail.*] = ny;
    qr[tail.*] = rem - cost;''', 'terrain cost push')

patch('''fn blocked(sc: *const Scene, x: usize, y: usize, dir: u8) bool {
    const from_bits = sc.edges[index(sc, x, y)];
    return (from_bits & dirBit(dir)) != 0;
}''', '''fn blocked(sc: *const Scene, x: usize, y: usize, nx: usize, ny: usize, dir: u8) bool {
    const from_bits = sc.edges[index(sc, x, y)];
    const to_bits = sc.edges[index(sc, nx, ny)];
    return (from_bits & dirBit(dir)) != 0 or (to_bits & dirBit(opposite(dir))) != 0;
}

fn opposite(c: u8) u8 {
    return switch (c) {
        'N' => 'S',
        'E' => 'W',
        'S' => 'N',
        'W' => 'E',
        else => c,
    };
}

fn stepCost(sc: *const Scene, x: usize, y: usize) i32 {
    return if (sc.terrain[index(sc, x, y)] == 'w') 2 else 1;
}''', 'bidirectional edges and water cost')

patch('''fn blendToward(a: Color, b: Color, alpha: i32, rounded: bool) Color {
    const bias: i32 = if (rounded) 127 else 0;''', '''fn blendToward(a: Color, b: Color, alpha: i32, rounded: bool) Color {
    _ = rounded;
    const bias: i32 = 127;''', 'blend rounding')

patch('''sc.view_cx - @divTrunc(sc.view_w - 1, 2)''', '''sc.view_cx - @divTrunc(sc.view_w, 2)''', 'viewport x')
patch('''sc.view_cy - @divTrunc(sc.view_h - 1, 2)''', '''sc.view_cy - @divTrunc(sc.view_h, 2)''', 'viewport y')

patch(r'''        try w.print("{{\"id\":\"{c}\",\"x\":{d},\"y\":{d}}}", .{ light.id, light.x, light.y });''', r'''        try w.print("{{\"id\":\"{s}\",\"x\":{d},\"y\":{d}}}", .{ light.id.bytes[0..light.id.len], light.x, light.y });''', 'state id strings')

patch(r'''fn sceneHash(data: []const u8) u64 {
    var h: u64 = 14695981039346656037;
    var lines = std.mem.splitScalar(u8, data, '\n');
    while (lines.next()) |line| {
        const t = std.mem.trim(u8, line, " \t\r");
        if (std.mem.startsWith(u8, t, "edge ") or std.mem.startsWith(u8, t, "tint ")) continue;
        for (t) |b| {
            h ^= b;
            h *%= 1099511628211;
        }
        h ^= '\n';
        h *%= 1099511628211;
    }
    return h;
}''', r'''fn sceneHash(data: []const u8) u64 {
    var h: u64 = 14695981039346656037;
    for (data) |b| {
        h ^= b;
        h *%= 1099511628211;
    }
    return h;
}''', 'raw FNV scene hash')

p.write_text(s)
PY

/app/minimap/scripts/build.sh
