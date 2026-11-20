#!/bin/bash
set -euo pipefail
cat > /app/src/main.zig <<'ZIG'
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

const World = struct {
    map: std.ArrayList([]u8),
    palette: [256]i64,
    solid_tiles: [256]bool,
    slope_dx: [256]i32,
    slope_dy: [256]i32,
    actors: std.ArrayList(Actor),
    next: usize,
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

fn strVal(v: ?Value) ?[]const u8 {
    if (v) |x| return switch (x) {
        .string => |s| s,
        else => null,
    };
    return null;
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

fn trailVal(v: ?Value) ?u8 {
    if (v) |x| {
        return switch (x) {
            .string => |s| if (s.len > 0) s[0] else null,
            else => null,
        };
    }
    return null;
}

fn clampI(v: i32, lo: i32, hi: i32) i32 {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

fn glyphWidth(g: []const u8) usize {
    if (g.len > 1) return 2;
    return 1;
}

fn actorWidth(a: Actor) i32 {
    return @as(i32, @intCast(glyphWidth(a.glyph)));
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

fn loadSolidTiles(root: Value) [256]bool {
    var solid: [256]bool = [_]bool{false} ** 256;
    solid['#'] = true;
    if (maybe(root, "solid_tiles")) |value| {
        switch (value) {
            .array => |items| {
                solid = [_]bool{false} ** 256;
                for (items.items) |item| {
                    switch (item) {
                        .string => |s| {
                            if (s.len == 1) solid[s[0]] = true;
                        },
                        else => {},
                    }
                }
            },
            else => {},
        }
    }
    return solid;
}

fn loadSlopes(root: Value) struct { dx: [256]i32, dy: [256]i32 } {
    var dxs: [256]i32 = [_]i32{0} ** 256;
    var dys: [256]i32 = [_]i32{0} ** 256;
    if (maybe(root, "slopes")) |value| {
        switch (value) {
            .object => |obj| {
                var it = obj.iterator();
                while (it.next()) |e| {
                    const key = e.key_ptr.*;
                    if (key.len != 1) continue;
                    const dx_raw = intMaybe(maybe(e.value_ptr.*, "dx")) orelse continue;
                    const dy_raw = intMaybe(maybe(e.value_ptr.*, "dy")) orelse continue;
                    if (dx_raw == 0 and dy_raw == 0) continue;
                    dxs[key[0]] = @as(i32, @intCast(dx_raw));
                    dys[key[0]] = @as(i32, @intCast(dy_raw));
                }
            },
            else => {},
        }
    }
    return .{ .dx = dxs, .dy = dys };
}

fn loadMap(allocator: std.mem.Allocator, root: Value) !std.ArrayList([]u8) {
    var rows = std.ArrayList([]u8).init(allocator);
    const input_rows = field(root, "map").array.items;
    const target_w = if (maybe(root, "width")) |v| @as(usize, @intCast(intVal(v))) else if (input_rows.len > 0) input_rows[0].string.len else 0;
    const target_h = if (maybe(root, "height")) |v| @as(usize, @intCast(intVal(v))) else input_rows.len;
    var y: usize = 0;
    while (y < target_h) : (y += 1) {
        const src = if (y < input_rows.len) input_rows[y].string else "";
        const out = try allocator.alloc(u8, target_w);
        var x: usize = 0;
        while (x < target_w) : (x += 1) {
            out[x] = if (x < src.len) src[x] else '.';
        }
        try rows.append(out);
    }
    return rows;
}

fn readActor(a: Value) Actor {
    return .{
        .id = field(a, "id").string,
        .x = @as(i32, @intCast(intVal(field(a, "x")))),
        .y = @as(i32, @intCast(intVal(field(a, "y")))),
        .glyph = field(a, "glyph").string,
        .fg = intVal(field(a, "fg")),
        .layer = intVal(field(a, "layer")),
        .solid = boolVal(maybe(a, "solid"), false),
        .alive = boolVal(maybe(a, "alive"), true),
        .pushable = boolVal(maybe(a, "pushable"), false),
        .trail = trailVal(maybe(a, "trail")),
    };
}

fn loadActors(allocator: std.mem.Allocator, root: Value) !std.ArrayList(Actor) {
    var actors = std.ArrayList(Actor).init(allocator);
    for (field(root, "actors").array.items) |a| {
        try actors.append(readActor(a));
    }
    return actors;
}

fn actorIndex(actors: []const Actor, id: []const u8) ?usize {
    for (actors, 0..) |a, i| {
        if (std.mem.eql(u8, a.id, id)) return i;
    }
    return null;
}

fn actorOccupies(a: Actor, x: i32, y: i32) bool {
    return a.alive and y == a.y and x >= a.x and x < a.x + actorWidth(a);
}

fn terrainBlocked(world: *const World, x: i32, y: i32) bool {
    if (y < 0 or y >= @as(i32, @intCast(world.map.items.len))) return true;
    const row = world.map.items[@as(usize, @intCast(y))];
    if (x < 0 or x >= @as(i32, @intCast(row.len))) return true;
    return world.solid_tiles[row[@as(usize, @intCast(x))]];
}

fn leaveTrail(world: *World, a: Actor, old_x: i32, old_y: i32) void {
    if (a.trail) |ch| {
        if (old_y < 0 or old_y >= @as(i32, @intCast(world.map.items.len))) return;
        const row = world.map.items[@as(usize, @intCast(old_y))];
        var x = old_x;
        const end = old_x + actorWidth(a);
        while (x < end) : (x += 1) {
            if (x >= 0 and x < @as(i32, @intCast(row.len))) row[@as(usize, @intCast(x))] = ch;
        }
    }
}

fn cloneMap(allocator: std.mem.Allocator, rows: std.ArrayList([]u8)) !std.ArrayList([]u8) {
    var out = std.ArrayList([]u8).init(allocator);
    for (rows.items) |row| try out.append(try allocator.dupe(u8, row));
    return out;
}

fn cloneActors(allocator: std.mem.Allocator, actors: []const Actor) ![]Actor {
    const out = try allocator.alloc(Actor, actors.len);
    std.mem.copyForwards(Actor, out, actors);
    return out;
}

fn restoreActors(actors: *std.ArrayList(Actor), backup: []const Actor) void {
    actors.clearRetainingCapacity();
    actors.appendSliceAssumeCapacity(backup);
}

fn tryMove(world: *World, idx: usize, dx: i32, dy: i32, moving: []bool) bool {
    if (!world.actors.items[idx].alive) return false;
    if (dx == 0 and dy == 0) return true;
    if (moving[idx]) return false;
    moving[idx] = true;
    defer moving[idx] = false;

    const old_x = world.actors.items[idx].x;
    const old_y = world.actors.items[idx].y;
    const nx = old_x + dx;
    const ny = old_y + dy;
    var x = nx;
    const end = nx + actorWidth(world.actors.items[idx]);
    while (x < end) : (x += 1) {
        if (terrainBlocked(world, x, ny)) return false;
        var j: usize = 0;
        while (j < world.actors.items.len) : (j += 1) {
            if (j == idx or moving[j] or !world.actors.items[j].alive or !world.actors.items[j].solid) continue;
            if (!actorOccupies(world.actors.items[j], x, ny)) continue;
            if (!world.actors.items[j].pushable) return false;
            if (!tryMove(world, j, dx, dy, moving)) return false;
        }
    }
    const moved = world.actors.items[idx];
    world.actors.items[idx].x = nx;
    world.actors.items[idx].y = ny;
    leaveTrail(world, moved, old_x, old_y);
    return true;
}

fn applyRecord(allocator: std.mem.Allocator, world: *World, rec: Value) !void {
    const op = strVal(maybe(rec, "op")) orelse return;
    if (std.mem.eql(u8, op, "move")) {
        const id = strVal(maybe(rec, "id")) orelse return;
        const dx_raw = intMaybe(maybe(rec, "dx")) orelse return;
        const dy_raw = intMaybe(maybe(rec, "dy")) orelse return;
        const repeat_raw = intMaybe(maybe(rec, "repeat")) orelse 1;
        if (repeat_raw < 0) return;
        if (actorIndex(world.actors.items, id)) |i| {
            const reps = @as(usize, @intCast(repeat_raw));
            const dx = @as(i32, @intCast(dx_raw));
            const dy = @as(i32, @intCast(dy_raw));
            var step: usize = 0;
            while (step < reps) : (step += 1) {
                const actor_backup = try cloneActors(allocator, world.actors.items);
                const map_backup = try cloneMap(allocator, world.map);
                const moving = try allocator.alloc(bool, world.actors.items.len);
                @memset(moving, false);
                if (!tryMove(world, i, dx, dy, moving)) {
                    restoreActors(&world.actors, actor_backup);
                    world.map = map_backup;
                }
            }
        }
    } else if (std.mem.eql(u8, op, "set")) {
        const id = strVal(maybe(rec, "id")) orelse return;
        if (actorIndex(world.actors.items, id)) |i| {
            if (intMaybe(maybe(rec, "x"))) |xv| world.actors.items[i].x = @as(i32, @intCast(xv));
            if (intMaybe(maybe(rec, "y"))) |yv| world.actors.items[i].y = @as(i32, @intCast(yv));
            if (boolMaybe(maybe(rec, "alive"))) |a| world.actors.items[i].alive = a;
            if (strVal(maybe(rec, "glyph"))) |g| world.actors.items[i].glyph = g;
            if (intMaybe(maybe(rec, "fg"))) |fg| world.actors.items[i].fg = fg;
            if (intMaybe(maybe(rec, "layer"))) |layer| world.actors.items[i].layer = layer;
            if (boolMaybe(maybe(rec, "solid"))) |solid| world.actors.items[i].solid = solid;
            if (boolMaybe(maybe(rec, "pushable"))) |pushable| world.actors.items[i].pushable = pushable;
            if (maybe(rec, "trail") != null) world.actors.items[i].trail = trailVal(maybe(rec, "trail"));
        } else if (intMaybe(maybe(rec, "x")) != null and intMaybe(maybe(rec, "y")) != null and strVal(maybe(rec, "glyph")) != null and intMaybe(maybe(rec, "fg")) != null and intMaybe(maybe(rec, "layer")) != null and boolMaybe(maybe(rec, "solid")) != null) {
            try world.actors.append(.{
                .id = id,
                .x = @as(i32, @intCast(intMaybe(maybe(rec, "x")).?)),
                .y = @as(i32, @intCast(intMaybe(maybe(rec, "y")).?)),
                .glyph = strVal(maybe(rec, "glyph")).?,
                .fg = intMaybe(maybe(rec, "fg")).?,
                .layer = intMaybe(maybe(rec, "layer")).?,
                .solid = boolMaybe(maybe(rec, "solid")).?,
                .alive = boolVal(maybe(rec, "alive"), true),
                .pushable = boolVal(maybe(rec, "pushable"), false),
                .trail = trailVal(maybe(rec, "trail")),
            });
        }
    } else if (std.mem.eql(u8, op, "glyph")) {
        const id = strVal(maybe(rec, "id")) orelse return;
        const glyph = strVal(maybe(rec, "glyph")) orelse return;
        const fg = intMaybe(maybe(rec, "fg")) orelse return;
        if (actorIndex(world.actors.items, id)) |i| {
            world.actors.items[i].glyph = glyph;
            world.actors.items[i].fg = fg;
        }
    } else if (std.mem.eql(u8, op, "tint")) {
        const ch = strVal(maybe(rec, "ch")) orelse return;
        const fg = intMaybe(maybe(rec, "fg")) orelse return;
        if (ch.len == 1) world.palette[ch[0]] = fg;
    } else if (std.mem.eql(u8, op, "tile")) {
        const x = intMaybe(maybe(rec, "x")) orelse return;
        const y = intMaybe(maybe(rec, "y")) orelse return;
        const ch = strVal(maybe(rec, "ch")) orelse return;
        if (ch.len != 1) return;
        if (y < 0 or y >= @as(i64, @intCast(world.map.items.len))) return;
        const row = world.map.items[@as(usize, @intCast(y))];
        if (x < 0 or x >= @as(i64, @intCast(row.len))) return;
        row[@as(usize, @intCast(x))] = ch[0];
    }
}

fn applyTurn(allocator: std.mem.Allocator, root: Value, world: *World, idx: usize) !void {
    const turns = field(root, "turns").array.items;
    for (turns[idx].array.items) |rec| try applyRecord(allocator, world, rec);
}

fn applySlopeSweep(allocator: std.mem.Allocator, world: *World) !void {
    var i: usize = 0;
    while (i < world.actors.items.len) : (i += 1) {
        const a = world.actors.items[i];
        if (!a.alive) continue;
        if (a.y < 0 or a.y >= @as(i32, @intCast(world.map.items.len))) continue;
        const row = world.map.items[@as(usize, @intCast(a.y))];
        if (a.x < 0 or a.x >= @as(i32, @intCast(row.len))) continue;
        const ch = row[@as(usize, @intCast(a.x))];
        const dx = world.slope_dx[ch];
        const dy = world.slope_dy[ch];
        if (dx == 0 and dy == 0) continue;
        const actor_backup = try cloneActors(allocator, world.actors.items);
        const map_backup = try cloneMap(allocator, world.map);
        const moving = try allocator.alloc(bool, world.actors.items.len);
        @memset(moving, false);
        if (!tryMove(world, i, dx, dy, moving)) {
            restoreActors(&world.actors, actor_backup);
            world.map = map_backup;
        }
    }
}

fn focus(actors: []const Actor) usize {
    if (actorIndex(actors, "hero")) |i| if (actors[i].alive) return i;
    for (actors, 0..) |a, i| if (a.alive) return i;
    return 0;
}

const Paint = struct { text: []const u8, fg: i64, advance: usize };

fn paintAt(world: *const World, wx: i32, wy: i32, vx: usize, vw: usize) Paint {
    var chosen: ?usize = null;
    for (world.actors.items, 0..) |a, i| {
        if (!actorOccupies(a, wx, wy)) continue;
        if (chosen) |j| {
            if (a.layer > world.actors.items[j].layer or (a.layer == world.actors.items[j].layer and i > j)) chosen = i;
        } else chosen = i;
    }
    if (chosen) |i| {
        const a = world.actors.items[i];
        const aw = glyphWidth(a.glyph);
        if (wx == a.x and vx + aw <= vw) return .{ .text = a.glyph, .fg = a.fg, .advance = aw };
        return .{ .text = " ", .fg = a.fg, .advance = 1 };
    }
    const row = world.map.items[@as(usize, @intCast(wy))];
    const ch = row[@as(usize, @intCast(wx))];
    return .{ .text = row[@as(usize, @intCast(wx)) .. @as(usize, @intCast(wx)) + 1], .fg = world.palette[ch], .advance = 1 };
}

fn emitFrame(writer: anytype, world: *const World) !void {
    const h = @as(i32, @intCast(world.map.items.len));
    const w = @as(i32, @intCast(world.map.items[0].len));
    const view_w = @as(usize, @intCast(intVal(field(globalRoot.?, "view_w"))));
    const view_h = @as(usize, @intCast(intVal(field(globalRoot.?, "view_h"))));
    const f = world.actors.items[focus(world.actors.items)];
    const left = clampI(f.x - @as(i32, @intCast(view_w / 2)), 0, w - @as(i32, @intCast(view_w)));
    const top = clampI(f.y - @as(i32, @intCast(view_h / 2)), 0, h - @as(i32, @intCast(view_h)));
    try writer.writeAll("\x1b[2J\x1b[H");
    var y: usize = 0;
    while (y < view_h) : (y += 1) {
        var last: i64 = -1;
        var x: usize = 0;
        while (x < view_w) {
            const p = paintAt(world, left + @as(i32, @intCast(x)), top + @as(i32, @intCast(y)), x, view_w);
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

var globalRoot: ?Value = null;

fn writeState(path: []const u8, world: *const World, next: usize) !void {
    const f = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer f.close();
    const w = f.writer();
    try w.print("{{\"next\":{},\"map\":[", .{next});
    for (world.map.items, 0..) |row, i| {
        if (i != 0) try w.writeAll(",");
        try w.print("\"{s}\"", .{row});
    }
    try w.writeAll("],\"palette\":{");
    var first = true;
    var c: usize = 32;
    while (c < 127) : (c += 1) {
        if (world.palette[c] == 37) continue;
        if (!first) try w.writeAll(",");
        first = false;
        try w.print("\"{c}\":{}", .{ @as(u8, @intCast(c)), world.palette[c] });
    }
    try w.writeAll("},\"actors\":[");
    for (world.actors.items, 0..) |a, i| {
        if (i != 0) try w.writeAll(",");
        try w.print("{{\"id\":\"{s}\",\"x\":{},\"y\":{},\"glyph\":\"{s}\",\"fg\":{},\"layer\":{},\"solid\":{},\"alive\":{},\"pushable\":{}", .{ a.id, a.x, a.y, a.glyph, a.fg, a.layer, a.solid, a.alive, a.pushable });
        if (a.trail) |tr| try w.print(",\"trail\":\"{c}\"", .{tr});
        try w.writeAll("}");
    }
    try w.writeAll("]}");
}

fn initWorld(allocator: std.mem.Allocator, root: Value) !World {
    const slopes = loadSlopes(root);
    return .{
        .map = try loadMap(allocator, root),
        .palette = loadPalette(root),
        .solid_tiles = loadSolidTiles(root),
        .slope_dx = slopes.dx,
        .slope_dy = slopes.dy,
        .actors = try loadActors(allocator, root),
        .next = 0,
    };
}

fn loadState(allocator: std.mem.Allocator, root: Value, path: []const u8) !struct { parsed: std.json.Parsed(Value), world: World } {
    const bytes = try std.fs.cwd().readFileAlloc(allocator, path, 1024 * 1024);
    const parsed = try std.json.parseFromSlice(Value, allocator, bytes, .{});
    const st = parsed.value;
    const slopes = loadSlopes(root);
    return .{
        .parsed = parsed,
        .world = .{
            .map = try loadMap(allocator, st),
            .palette = loadPalette(st),
            .solid_tiles = loadSolidTiles(root),
            .slope_dx = slopes.dx,
            .slope_dy = slopes.dy,
            .actors = try loadActors(allocator, st),
            .next = @as(usize, @intCast(intVal(field(st, "next")))),
        },
    };
}

fn runRender(allocator: std.mem.Allocator, root: Value, world: *World, out: []const u8) !void {
    const f = try std.fs.cwd().createFile(out, .{ .truncate = true });
    defer f.close();
    const w = f.writer();
    try emitFrame(w, world);
    const n = field(root, "turns").array.items.len;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        try applyTurn(allocator, root, world, i);
        try applySlopeSweep(allocator, world);
        try emitFrame(w, world);
    }
}

pub fn main() !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const allocator = arena.allocator();
    const args = try std.process.argsAlloc(allocator);
    if (args.len < 4) return error.BadArgs;
    const data = try std.fs.cwd().readFileAlloc(allocator, args[2], 10 * 1024 * 1024);
    const parsed = try std.json.parseFromSlice(Value, allocator, data, .{});
    const root = parsed.value;
    globalRoot = root;
    if (std.mem.eql(u8, args[1], "render")) {
        var world = try initWorld(allocator, root);
        try runRender(allocator, root, &world, args[3]);
    } else if (std.mem.eql(u8, args[1], "checkpoint")) {
        if (args.len < 6) return error.BadArgs;
        var world = try initWorld(allocator, root);
        const count = try std.fmt.parseInt(usize, args[3], 10);
        const f = try std.fs.cwd().createFile(args[5], .{ .truncate = true });
        defer f.close();
        const w = f.writer();
        try emitFrame(w, &world);
        var i: usize = 0;
        while (i < count) : (i += 1) {
            try applyTurn(allocator, root, &world, i);
            try applySlopeSweep(allocator, &world);
            try emitFrame(w, &world);
        }
        try writeState(args[4], &world, count);
    } else if (std.mem.eql(u8, args[1], "resume")) {
        if (args.len < 5) return error.BadArgs;
        var loaded = try loadState(allocator, root, args[3]);
        const f = try std.fs.cwd().createFile(args[4], .{ .truncate = true });
        defer f.close();
        const w = f.writer();
        const n = field(root, "turns").array.items.len;
        var i = loaded.world.next;
        while (i < n) : (i += 1) {
            try applyTurn(allocator, root, &loaded.world, i);
            try applySlopeSweep(allocator, &loaded.world);
            try emitFrame(w, &loaded.world);
        }
    } else return error.BadArgs;
}
ZIG
/app/scripts/build.sh
