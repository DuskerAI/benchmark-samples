const std = @import("std");
const Value = std.json.Value;

const Actor = struct {
    id: []const u8,
    x: i32,
    y: i32,
    glyph: []const u8,
    fg: i64,
    layer: i64,
    solid: bool,
    alive: bool,
    pushable: bool,
    trail: ?u8,
};

fn field(v: Value, key: []const u8) Value {
    return v.object.get(key).?;
}

fn maybe(v: Value, key: []const u8) ?Value {
    return switch (v) {
        .object => |obj| obj.get(key),
        else => null,
    };
}

fn intVal(v: Value) i64 {
    return switch (v) {
        .integer => |x| x,
        else => 0,
    };
}

fn boolVal(v: ?Value, default: bool) bool {
    if (v) |x| return switch (x) {
        .bool => |b| b,
        else => default,
    };
    return default;
}

fn intMaybe(v: ?Value) ?i64 {
    if (v) |x| return switch (x) {
        .integer => |n| n,
        else => null,
    };
    return null;
}

fn boolMaybe(v: ?Value) ?bool {
    if (v) |x| return switch (x) {
        .bool => |b| b,
        else => null,
    };
    return null;
}

fn strMaybe(v: ?Value) ?[]const u8 {
    if (v) |x| return switch (x) {
        .string => |s| s,
        else => null,
    };
    return null;
}

fn trailMaybe(v: ?Value) ?u8 {
    if (strMaybe(v)) |s| return if (s.len == 1) s[0] else null;
    return null;
}

fn clampI(v: i32, lo: i32, hi: i32) i32 {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

fn glyphWidth(g: []const u8) usize {
    return if (g.len > 1) 2 else 1;
}

fn loadPalette(root: Value) [256]i64 {
    var p: [256]i64 = [_]i64{37} ** 256;
    var obj = field(root, "palette").object;
    var it = obj.iterator();
    while (it.next()) |e| {
        const k = e.key_ptr.*;
        if (k.len > 0) p[k[0]] = intVal(e.value_ptr.*);
    }
    return p;
}

fn loadActors(allocator: std.mem.Allocator, root: Value) !std.ArrayList(Actor) {
    var actors = std.ArrayList(Actor).init(allocator);
    for (field(root, "actors").array.items) |a| {
        try actors.append(.{
            .id = field(a, "id").string,
            .x = @as(i32, @intCast(intVal(field(a, "x")))),
            .y = @as(i32, @intCast(intVal(field(a, "y")))),
            .glyph = field(a, "glyph").string,
            .fg = intVal(field(a, "fg")),
            .layer = intVal(field(a, "layer")),
            .solid = boolVal(maybe(a, "solid"), false),
            .alive = boolVal(maybe(a, "alive"), true),
            .pushable = boolVal(maybe(a, "pushable"), false),
            .trail = trailMaybe(maybe(a, "trail")),
        });
    }
    return actors;
}

fn actorIndex(actors: []Actor, id: []const u8) ?usize {
    for (actors, 0..) |a, i| {
        if (std.mem.eql(u8, a.id, id)) return i;
    }
    return null;
}

fn blocked(root: Value, actors: []Actor, self: usize, x: i32, y: i32) bool {
    const w = @as(i32, @intCast(intVal(field(root, "width"))));
    const h = @as(i32, @intCast(intVal(field(root, "height"))));
    if (x < 0 or y < 0 or x >= w or y >= h) return true;
    const row = field(root, "map").array.items[@as(usize, @intCast(y))].string;
    if (row[@as(usize, @intCast(x))] == '#') return true;
    for (actors, 0..) |a, i| {
        if (i == self or !a.alive or !a.solid or a.y != y) continue;
        const end = a.x + @as(i32, @intCast(glyphWidth(a.glyph)));
        if (x >= a.x and x < end) return true;
    }
    return false;
}

fn applyRecord(root: Value, actors: []Actor, palette: *[256]i64, rec: Value) void {
    const op = field(rec, "op").string;
    if (std.mem.eql(u8, op, "move")) {
        if (actorIndex(actors, field(rec, "id").string)) |i| {
            const dx = @as(i32, @intCast(intMaybe(maybe(rec, "dx")) orelse return));
            const dy = @as(i32, @intCast(intMaybe(maybe(rec, "dy")) orelse return));
            const repeat = @as(usize, @intCast(intMaybe(maybe(rec, "repeat")) orelse 1));
            var step: usize = 0;
            while (step < repeat) : (step += 1) {
                const nx = actors[i].x + dx;
                const ny = actors[i].y + dy;
                if (!blocked(root, actors, i, nx, ny)) {
                    actors[i].x = nx;
                    actors[i].y = ny;
                }
            }
        }
    } else if (std.mem.eql(u8, op, "set")) {
        if (actorIndex(actors, field(rec, "id").string)) |i| {
            if (intMaybe(maybe(rec, "x"))) |xv| actors[i].x = @as(i32, @intCast(xv));
            if (intMaybe(maybe(rec, "y"))) |yv| actors[i].y = @as(i32, @intCast(yv));
            if (boolMaybe(maybe(rec, "alive"))) |a| actors[i].alive = a;
            if (strMaybe(maybe(rec, "glyph"))) |g| actors[i].glyph = g;
            if (intMaybe(maybe(rec, "fg"))) |fg| actors[i].fg = fg;
            if (intMaybe(maybe(rec, "layer"))) |layer| actors[i].layer = layer;
            if (boolMaybe(maybe(rec, "solid"))) |solid| actors[i].solid = solid;
            if (boolMaybe(maybe(rec, "pushable"))) |pushable| actors[i].pushable = pushable;
            if (maybe(rec, "trail") != null) actors[i].trail = trailMaybe(maybe(rec, "trail"));
        }
    } else if (std.mem.eql(u8, op, "glyph")) {
        if (actorIndex(actors, field(rec, "id").string)) |i| {
            actors[i].glyph = strMaybe(maybe(rec, "glyph")) orelse return;
            actors[i].fg = intMaybe(maybe(rec, "fg")) orelse return;
        }
    } else if (std.mem.eql(u8, op, "tint")) {
        const ch = strMaybe(maybe(rec, "ch")) orelse return;
        if (ch.len == 1) palette[ch[0]] = intMaybe(maybe(rec, "fg")) orelse return;
    }
}

fn applyTurn(root: Value, actors: []Actor, palette: *[256]i64, idx: usize) void {
    const turns = field(root, "turns").array.items;
    for (turns[idx].array.items) |rec| applyRecord(root, actors, palette, rec);
}

fn slopeVector(root: Value, x: i32, y: i32) ?struct { dx: i32, dy: i32 } {
    if (maybe(root, "slopes") == null) return null;
    if (y < 0 or x < 0) return null;
    const rows = field(root, "map").array.items;
    if (y >= @as(i32, @intCast(rows.len))) return null;
    const row = rows[@as(usize, @intCast(y))].string;
    if (x >= @as(i32, @intCast(row.len))) return null;
    const ch = row[@as(usize, @intCast(x)) .. @as(usize, @intCast(x)) + 1];
    const slopes = maybe(root, "slopes").?.object;
    if (slopes.get(ch)) |rule| {
        const dx = @as(i32, @intCast(intMaybe(maybe(rule, "dx")) orelse return null));
        const dy = @as(i32, @intCast(intMaybe(maybe(rule, "dy")) orelse return null));
        if (dx == 0 and dy == 0) return null;
        return .{ .dx = dx, .dy = dy };
    }
    return null;
}

fn applySlopeSweep(root: Value, actors: []Actor) void {
    for (actors, 0..) |a, i| {
        if (!a.alive) continue;
        if (slopeVector(root, a.x, a.y)) |v| {
            const nx = actors[i].x + v.dx;
            const ny = actors[i].y + v.dy;
            if (!blocked(root, actors, i, nx, ny)) {
                actors[i].x = nx;
                actors[i].y = ny;
            }
        }
    }
}

fn focus(actors: []Actor) usize {
    if (actorIndex(actors, "hero")) |i| {
        if (actors[i].alive) return i;
    }
    for (actors, 0..) |a, i| {
        if (a.alive) return i;
    }
    return 0;
}

const Paint = struct { text: []const u8, fg: i64, advance: usize };

fn paintAt(root: Value, actors: []Actor, palette: *const [256]i64, wx: i32, wy: i32, vx: usize, vw: usize) Paint {
    _ = vx;
    _ = vw;
    for (actors) |a| {
        if (!a.alive or a.y != wy) continue;
        const aw = @as(i32, @intCast(glyphWidth(a.glyph)));
        if (wx >= a.x and wx < a.x + aw) {
            if (wx == a.x) return .{ .text = a.glyph, .fg = a.fg, .advance = glyphWidth(a.glyph) };
            return .{ .text = " ", .fg = a.fg, .advance = 1 };
        }
    }
    const row = field(root, "map").array.items[@as(usize, @intCast(wy))].string;
    const ch = row[@as(usize, @intCast(wx))];
    return .{ .text = row[@as(usize, @intCast(wx)) .. @as(usize, @intCast(wx)) + 1], .fg = palette[ch], .advance = 1 };
}

fn emitFrame(writer: anytype, root: Value, actors: []Actor, palette: *const [256]i64) !void {
    const w = @as(i32, @intCast(intVal(field(root, "width"))));
    const h = @as(i32, @intCast(intVal(field(root, "height"))));
    const vw = @as(usize, @intCast(intVal(field(root, "view_w"))));
    const vh = @as(usize, @intCast(intVal(field(root, "view_h"))));
    const f = actors[focus(actors)];
    const left = clampI(f.x - @as(i32, @intCast(vw / 2)), 0, w - @as(i32, @intCast(vw)));
    const top = clampI(f.y - @as(i32, @intCast(vh / 2)) + 1, 0, h - @as(i32, @intCast(vh)));
    try writer.writeAll("\x1b[2J\x1b[H");
    var y: usize = 0;
    while (y < vh) : (y += 1) {
        var last: i64 = -1;
        var x: usize = 0;
        while (x < vw) {
            const p = paintAt(root, actors, palette, left + @as(i32, @intCast(x)), top + @as(i32, @intCast(y)), x, vw);
            if (p.fg != last) {
                try writer.print("\x1b[38;5;{}m", .{p.fg});
                last = p.fg;
            }
            try writer.writeAll(p.text);
            x += p.advance;
        }
        try writer.writeAll("\x1b[0m\n");
    }
    try writer.writeAll("\x1b[0m\n\x0c\n");
}

fn writeState(path: []const u8, actors: []Actor, next: usize) !void {
    var f = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer f.close();
    var w = f.writer();
    try w.print("{{\"next\":{},\"actors\":[", .{next});
    for (actors, 0..) |a, i| {
        if (i != 0) try w.writeAll(",");
        try w.print("{{\"id\":\"{s}\",\"x\":{},\"y\":{},\"glyph\":\"{s}\",\"fg\":{},\"layer\":{},\"solid\":{},\"alive\":{},\"pushable\":{}", .{ a.id, a.x, a.y, a.glyph, a.fg, a.layer, a.solid, a.alive, a.pushable });
        if (a.trail) |tr| try w.print(",\"trail\":\"{c}\"", .{tr});
        try w.writeAll("}");
    }
    try w.writeAll("]}");
}

fn loadState(allocator: std.mem.Allocator, path: []const u8, root: Value, palette: *[256]i64) !struct { parsed: std.json.Parsed(Value), actors: std.ArrayList(Actor), next: usize } {
    const bytes = try std.fs.cwd().readFileAlloc(allocator, path, 1024 * 1024);
    defer allocator.free(bytes);
    var parsed = try std.json.parseFromSlice(Value, allocator, bytes, .{});
    const st = parsed.value;
    var actors = try loadActors(allocator, st);
    palette.* = loadPalette(root);
    return .{ .parsed = parsed, .actors = actors, .next = @as(usize, @intCast(intVal(field(st, "next")))) + 1 };
}

fn runRender(root: Value, actors: []Actor, palette: *[256]i64, out: []const u8) !void {
    var f = try std.fs.cwd().createFile(out, .{ .truncate = true });
    defer f.close();
    var w = f.writer();
    try emitFrame(w, root, actors, palette);
    const n = field(root, "turns").array.items.len;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        applyTurn(root, actors, palette, i);
        applySlopeSweep(root, actors);
        try emitFrame(w, root, actors, palette);
    }
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    if (args.len < 4) return error.BadArgs;
    const data = try std.fs.cwd().readFileAlloc(allocator, args[2], 10 * 1024 * 1024);
    defer allocator.free(data);
    var parsed = try std.json.parseFromSlice(Value, allocator, data, .{});
    defer parsed.deinit();
    const root = parsed.value;
    var palette = loadPalette(root);
    if (std.mem.eql(u8, args[1], "render")) {
        var actors = try loadActors(allocator, root);
        defer actors.deinit();
        try runRender(root, actors.items, &palette, args[3]);
    } else if (std.mem.eql(u8, args[1], "checkpoint")) {
        if (args.len < 6) return error.BadArgs;
        var actors = try loadActors(allocator, root);
        defer actors.deinit();
        const count = try std.fmt.parseInt(usize, args[3], 10);
        var f = try std.fs.cwd().createFile(args[5], .{ .truncate = true });
        defer f.close();
        var w = f.writer();
        try emitFrame(w, root, actors.items, &palette);
        var i: usize = 0;
        while (i < count) : (i += 1) {
            applyTurn(root, actors.items, &palette, i);
            applySlopeSweep(root, actors.items);
            try emitFrame(w, root, actors.items, &palette);
        }
        try writeState(args[4], actors.items, count);
    } else if (std.mem.eql(u8, args[1], "resume")) {
        if (args.len < 5) return error.BadArgs;
        var loaded = try loadState(allocator, args[3], root, &palette);
        defer loaded.parsed.deinit();
        defer loaded.actors.deinit();
        var f = try std.fs.cwd().createFile(args[4], .{ .truncate = true });
        defer f.close();
        var w = f.writer();
        const n = field(root, "turns").array.items.len;
        var i = loaded.next;
        while (i < n) : (i += 1) {
            applyTurn(root, loaded.actors.items, &palette, i);
            applySlopeSweep(root, loaded.actors.items);
            try emitFrame(w, root, loaded.actors.items, &palette);
        }
    } else return error.BadArgs;
}
