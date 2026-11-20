const std = @import("std");

const MaxTiles = 1024;
const MaxLights = 16;
const MaxMoves = 512;
const Scale = 4;

const Color = struct { r: u8, g: u8, b: u8 };
const Light = struct { id: u8, x: usize, y: usize, radius: i32 };
const Move = struct { id: u8, x: usize, y: usize };
const Viewport = struct { x0: usize, y0: usize, x1: usize, y1: usize };

const Scene = struct {
    w: usize,
    h: usize,
    terrain: [MaxTiles]u8,
    edges: [MaxTiles]u8,
    tint_r: [MaxTiles]u8,
    tint_g: [MaxTiles]u8,
    tint_b: [MaxTiles]u8,
    tint_a: [MaxTiles]u8,
    lights: [MaxLights]Light,
    light_count: usize,
    moves: [MaxMoves]Move,
    move_count: usize,
    view_cx: i32,
    view_cy: i32,
    view_w: i32,
    view_h: i32,
    hash: u64,
};

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);

    if (args.len < 4) return error.InvalidArgs;
    const cmd = args[1];
    if (std.mem.eql(u8, cmd, "render")) {
        if (args.len != 4) return error.InvalidArgs;
        var scene = try parseScene(allocator, args[2]);
        applyMoves(&scene, scene.move_count);
        try writeOutputs(allocator, &scene, scene.move_count, args[3], null);
    } else if (std.mem.eql(u8, cmd, "checkpoint")) {
        if (args.len != 6) return error.InvalidArgs;
        var scene = try parseScene(allocator, args[2]);
        const n = try std.fmt.parseInt(usize, args[3], 10);
        if (n > scene.move_count) return error.InvalidArgs;
        applyMoves(&scene, n);
        try writeOutputs(allocator, &scene, n, args[5], args[4]);
    } else if (std.mem.eql(u8, cmd, "resume")) {
        if (args.len != 5) return error.InvalidArgs;
        var scene = try parseScene(allocator, args[2]);
        const state_bytes = try std.fs.cwd().readFileAlloc(allocator, args[3], 1 << 20);
        defer allocator.free(state_bytes);
        const turn = try parseTurn(state_bytes);
        const saved_hash = try parseHash(state_bytes);
        if (turn > scene.move_count) return error.BadState;
        if (saved_hash != scene.hash) return error.BadState;
        applyMoves(&scene, scene.move_count);
        try writeOutputs(allocator, &scene, scene.move_count, args[4], null);
    } else {
        return error.InvalidArgs;
    }
}

fn parseScene(allocator: std.mem.Allocator, path: []const u8) !Scene {
    const data = try std.fs.cwd().readFileAlloc(allocator, path, 1 << 20);
    defer allocator.free(data);

    var sc: Scene = undefined;
    sc.w = 0;
    sc.h = 0;
    sc.light_count = 0;
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
    @memset(sc.tint_a[0..], 0);

    var lines = std.mem.splitScalar(u8, data, '\n');
    var tile_row: usize = 0;
    var reading_tiles = false;
    while (lines.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (line.len == 0) continue;
        if (reading_tiles) {
            if (tile_row >= sc.h or line.len < sc.w) return error.BadScene;
            for (0..sc.w) |x| sc.terrain[index(&sc, x, tile_row)] = line[x];
            tile_row += 1;
            if (tile_row == sc.h) reading_tiles = false;
            continue;
        }
        var toks = std.mem.tokenizeScalar(u8, line, ' ');
        const tag = toks.next() orelse continue;
        if (std.mem.eql(u8, tag, "size")) {
            sc.w = try parseNext(usize, &toks);
            sc.h = try parseNext(usize, &toks);
            if (sc.w == 0 or sc.h == 0 or sc.w * sc.h > MaxTiles) return error.BadScene;
        } else if (std.mem.eql(u8, tag, "tiles")) {
            if (sc.w == 0 or sc.h == 0) return error.BadScene;
            reading_tiles = true;
            tile_row = 0;
        } else if (std.mem.eql(u8, tag, "edge")) {
            const x = try parseNext(usize, &toks);
            const y = try parseNext(usize, &toks);
            const dirs = toks.next() orelse return error.BadScene;
            const i = index(&sc, x, y);
            for (dirs) |d| sc.edges[i] |= dirBit(d);
        } else if (std.mem.eql(u8, tag, "light")) {
            if (sc.light_count >= MaxLights) return error.BadScene;
            const idtok = toks.next() orelse return error.BadScene;
            sc.lights[sc.light_count] = .{
                .id = idtok[0],
                .x = try parseNext(usize, &toks),
                .y = try parseNext(usize, &toks),
                .radius = try parseNext(i32, &toks),
            };
            sc.light_count += 1;
        } else if (std.mem.eql(u8, tag, "view")) {
            sc.view_cx = try parseNext(i32, &toks);
            sc.view_cy = try parseNext(i32, &toks);
            sc.view_w = try parseNext(i32, &toks);
            sc.view_h = try parseNext(i32, &toks);
        } else if (std.mem.eql(u8, tag, "tint")) {
            const x = try parseNext(usize, &toks);
            const y = try parseNext(usize, &toks);
            const i = index(&sc, x, y);
            sc.tint_r[i] = try parseNext(u8, &toks);
            sc.tint_g[i] = try parseNext(u8, &toks);
            sc.tint_b[i] = try parseNext(u8, &toks);
            sc.tint_a[i] = try parseNext(u8, &toks);
        } else if (std.mem.eql(u8, tag, "move")) {
            if (sc.move_count >= MaxMoves) return error.BadScene;
            const idtok = toks.next() orelse return error.BadScene;
            sc.moves[sc.move_count] = .{
                .id = idtok[0],
                .x = try parseNext(usize, &toks),
                .y = try parseNext(usize, &toks),
            };
            sc.move_count += 1;
        } else {
            return error.BadScene;
        }
    }
    if (reading_tiles or tile_row != sc.h) return error.BadScene;
    return sc;
}

fn parseNext(comptime T: type, toks: anytype) !T {
    const tok = toks.next() orelse return error.BadScene;
    return std.fmt.parseInt(T, tok, 10);
}

fn index(sc: *const Scene, x: usize, y: usize) usize {
    return y * sc.w + x;
}

fn applyMoves(sc: *Scene, count: usize) void {
    for (sc.moves[0..count]) |mv| {
        for (sc.lights[0..sc.light_count]) |*light| {
            if (light.id == mv.id) {
                light.x = mv.x;
                light.y = mv.y;
                break;
            }
        }
    }
}

fn writeOutputs(allocator: std.mem.Allocator, sc: *const Scene, turn: usize, outdir: []const u8, checkpoint_state: ?[]const u8) !void {
    try std.fs.cwd().makePath(outdir);
    const image_path = try std.fmt.allocPrint(allocator, "{s}/image.ppm", .{outdir});
    defer allocator.free(image_path);
    const state_path = try std.fmt.allocPrint(allocator, "{s}/state.json", .{outdir});
    defer allocator.free(state_path);
    try writeImage(image_path, sc);
    try writeState(state_path, sc, turn);
    if (checkpoint_state) |p| try writeState(p, sc, turn);
}

fn writeImage(path: []const u8, sc: *const Scene) !void {
    const colors = computeColors(sc);
    const vp = viewport(sc);
    var file = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer file.close();
    var bw = std.io.bufferedWriter(file.writer());
    const w = bw.writer();
    const img_w = sc.w * Scale;
    const img_h = sc.h * Scale;
    try w.print("P3\n{d} {d}\n255\n", .{ img_w, img_h });
    for (0..img_h) |py| {
        for (0..img_w) |px| {
            const tx = px / Scale;
            const ty = py / Scale;
            var c = colors[index(sc, tx, ty)];
            const bx0 = vp.x0 * Scale;
            const bx1 = (vp.x1 + 1) * Scale - 1;
            const by0 = vp.y0 * Scale;
            const by1 = (vp.y1 + 1) * Scale - 1;
            const horizontal = (py == by0 or py == by1) and px >= bx0 and px <= bx1;
            const vertical = (px == bx0 or px == bx1) and py >= by0 and py <= by1;
            if (horizontal or vertical) c = .{ .r = 230, .g = 238, .b = 255 };
            try w.print("{d} {d} {d}\n", .{ c.r, c.g, c.b });
        }
    }
    try bw.flush();
}

fn computeColors(sc: *const Scene) [MaxTiles]Color {
    var light_map: [MaxTiles]i32 = undefined;
    @memset(light_map[0..], 0);
    var best_seen: [MaxTiles]i32 = undefined;
    @memset(best_seen[0..], -1);

    for (sc.lights[0..sc.light_count]) |light| {
        var qx: [MaxTiles * 4]usize = undefined;
        var qy: [MaxTiles * 4]usize = undefined;
        var qr: [MaxTiles * 4]i32 = undefined;
        var head: usize = 0;
        var tail: usize = 0;
        qx[tail] = light.x;
        qy[tail] = light.y;
        qr[tail] = light.radius;
        tail += 1;
        while (head < tail) : (head += 1) {
            const x = qx[head];
            const y = qy[head];
            const rem = qr[head];
            if (x >= sc.w or y >= sc.h or rem <= 0) continue;
            const i = index(sc, x, y);
            if (rem <= best_seen[i]) continue;
            best_seen[i] = rem;
            const b = @divTrunc(rem * 255 + @divTrunc(light.radius, 2), light.radius);
            if (b > light_map[i]) light_map[i] = b;
            if (rem <= 1) continue;
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'N', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'E', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'S', rem - 1);
            tryPush(sc, &qx, &qy, &qr, &tail, x, y, 'W', rem - 1);
        }
    }

    var out: [MaxTiles]Color = undefined;
    for (0..sc.w * sc.h) |i| {
        var c = baseColor(sc.terrain[i]);
        const b = light_map[i];
        c = blendToward(c, .{ .r = 255, .g = 214, .b = 126 }, b, true);
        if (sc.tint_a[i] != 0) {
            c = blendToward(c, .{ .r = sc.tint_r[i], .g = sc.tint_g[i], .b = sc.tint_b[i] }, sc.tint_a[i], false);
        }
        out[i] = c;
    }
    return out;
}

fn tryPush(sc: *const Scene, qx: *[MaxTiles * 4]usize, qy: *[MaxTiles * 4]usize, qr: *[MaxTiles * 4]i32, tail: *usize, x: usize, y: usize, dir: u8, rem: i32) void {
    var nx_i = @as(i32, @intCast(x));
    var ny_i = @as(i32, @intCast(y));
    switch (dir) {
        'N' => ny_i -= 1,
        'E' => nx_i += 1,
        'S' => ny_i += 1,
        'W' => nx_i -= 1,
        else => {},
    }
    if (nx_i < 0 or ny_i < 0) return;
    const nx: usize = @intCast(nx_i);
    const ny: usize = @intCast(ny_i);
    if (nx >= sc.w or ny >= sc.h) return;
    if (blocked(sc, x, y, dir)) return;
    if (tail.* >= qx.len) return;
    qx[tail.*] = nx;
    qy[tail.*] = ny;
    qr[tail.*] = rem;
    tail.* += 1;
}

fn blocked(sc: *const Scene, x: usize, y: usize, dir: u8) bool {
    const from_bits = sc.edges[index(sc, x, y)];
    return (from_bits & dirBit(dir)) != 0;
}

fn dirBit(c: u8) u8 {
    return switch (c) {
        'N' => 1,
        'E' => 2,
        'S' => 4,
        'W' => 8,
        else => 0,
    };
}

fn baseColor(t: u8) Color {
    return switch (t) {
        'g' => .{ .r = 36, .g = 82, .b = 48 },
        's' => .{ .r = 70, .g = 58, .b = 42 },
        'w' => .{ .r = 28, .g = 31, .b = 36 },
        else => .{ .r = 46, .g = 48, .b = 54 },
    };
}

fn blendToward(a: Color, b: Color, alpha: i32, rounded: bool) Color {
    const bias: i32 = if (rounded) 127 else 0;
    return .{
        .r = @intCast(@divTrunc(@as(i32, a.r) * (255 - alpha) + @as(i32, b.r) * alpha + bias, 255)),
        .g = @intCast(@divTrunc(@as(i32, a.g) * (255 - alpha) + @as(i32, b.g) * alpha + bias, 255)),
        .b = @intCast(@divTrunc(@as(i32, a.b) * (255 - alpha) + @as(i32, b.b) * alpha + bias, 255)),
    };
}

fn viewport(sc: *const Scene) Viewport {
    const max_x0 = @as(i32, @intCast(sc.w)) - sc.view_w;
    const max_y0 = @as(i32, @intCast(sc.h)) - sc.view_h;
    var x0 = sc.view_cx - @divTrunc(sc.view_w - 1, 2);
    var y0 = sc.view_cy - @divTrunc(sc.view_h - 1, 2);
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x0 > max_x0) x0 = max_x0;
    if (y0 > max_y0) y0 = max_y0;
    return .{
        .x0 = @intCast(x0),
        .y0 = @intCast(y0),
        .x1 = @intCast(x0 + sc.view_w - 1),
        .y1 = @intCast(y0 + sc.view_h - 1),
    };
}

fn writeState(path: []const u8, sc: *const Scene, turn: usize) !void {
    var file = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer file.close();
    var bw = std.io.bufferedWriter(file.writer());
    const w = bw.writer();
    const vp = viewport(sc);
    try w.print("{{\"turn\":{d},\"scene_hash\":\"", .{turn});
    try writeHex64(w, sc.hash);
    try w.writeAll("\",\"lights\":[");
    for (sc.lights[0..sc.light_count], 0..) |light, i| {
        if (i != 0) try w.writeAll(",");
        try w.print("{{\"id\":\"{c}\",\"x\":{d},\"y\":{d}}}", .{ light.id, light.x, light.y });
    }
    try w.print("],\"viewport\":[{d},{d},{d},{d}]}}\n", .{ vp.x0, vp.y0, vp.x1, vp.y1 });
    try bw.flush();
}

fn writeHex64(w: anytype, value: u64) !void {
    for (0..16) |i| {
        const shift: u6 = @intCast((15 - i) * 4);
        const n: u8 = @intCast((value >> shift) & 0xf);
        const c: u8 = if (n < 10) '0' + n else 'a' + (n - 10);
        try w.writeByte(c);
    }
}

fn parseTurn(data: []const u8) !usize {
    const key = "\"turn\":";
    const at = std.mem.indexOf(u8, data, key) orelse return error.BadState;
    const i = at + key.len;
    var j = i;
    while (j < data.len and data[j] >= '0' and data[j] <= '9') : (j += 1) {}
    return std.fmt.parseInt(usize, data[i..j], 10);
}

fn parseHash(data: []const u8) !u64 {
    const key = "\"scene_hash\":\"";
    const at = std.mem.indexOf(u8, data, key) orelse return error.BadState;
    const i = at + key.len;
    if (i + 16 > data.len) return error.BadState;
    var h: u64 = 0;
    for (data[i .. i + 16]) |c| {
        const v: u64 = switch (c) {
            '0'...'9' => c - '0',
            'a'...'f' => c - 'a' + 10,
            'A'...'F' => c - 'A' + 10,
            else => return error.BadState,
        };
        h = (h << 4) | v;
    }
    return h;
}

fn sceneHash(data: []const u8) u64 {
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
}
