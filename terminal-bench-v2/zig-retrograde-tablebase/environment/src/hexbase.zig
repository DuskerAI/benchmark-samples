const std = @import("std");

const N: usize = 7;
const MASKS: usize = 128;
const MAX: usize = 2 * N * N * MASKS;
const UNKNOWN: i8 = -1;
const INVALID: i8 = -2;
const LOSS: i8 = 0;
const WIN: i8 = 1;
const DRAW: i8 = 2;
const cells = "ABCDEFG";
const mirror_map = [_]u8{ 2, 1, 0, 5, 4, 3, 6 };
const neigh = [_][6]u8{
    [_]u8{ 1, 3, 4, 255, 255, 255 },
    [_]u8{ 0, 2, 4, 255, 255, 255 },
    [_]u8{ 1, 4, 5, 255, 255, 255 },
    [_]u8{ 0, 4, 6, 255, 255, 255 },
    [_]u8{ 0, 1, 2, 3, 5, 6 },
    [_]u8{ 2, 4, 6, 255, 255, 255 },
    [_]u8{ 3, 4, 5, 255, 255, 255 },
};

const State = struct {
    turn: u8,
    r: u8,
    b: u8,
    blocked: u8,
};
const ParseResult = struct {
    ok: bool,
    state: State,
    err: []const u8,
};

fn bit(i: u8) u8 {
    return @as(u8, 1) << @as(u3, @intCast(i));
}

fn id(s: State) usize {
    return (((@as(usize, s.turn) * N + @as(usize, s.r)) * N + @as(usize, s.b)) * MASKS + @as(usize, s.blocked));
}

fn fromId(x: usize) State {
    const blocked: u8 = @intCast(x % MASKS);
    const q = x / MASKS;
    const b: u8 = @intCast(q % N);
    const q2 = q / N;
    const r: u8 = @intCast(q2 % N);
    const turn: u8 = @intCast(q2 / N);
    return .{ .turn = turn, .r = r, .b = b, .blocked = blocked };
}

fn validRaw(s: State) bool {
    if (s.turn > 1 or s.r >= N or s.b >= N or s.r == s.b) return false;
    return true;
}

fn parseCell(c: u8) ?u8 {
    if (c < 'A' or c > 'G') return null;
    return c - 'A';
}

fn parsePosition(text: []const u8) ParseResult {
    var s = State{ .turn = 255, .r = 255, .b = 255, .blocked = 0 };
    var seen_turn = false;
    var seen_r = false;
    var seen_b = false;
    var seen_blocked = false;
    var it = std.mem.splitScalar(u8, text, ';');
    while (it.next()) |part| {
        if (std.mem.startsWith(u8, part, "turn=")) {
            if (seen_turn or part.len != 6) return .{ .ok = false, .state = s, .err = "bad turn" };
            seen_turn = true;
            if (part[5] == 'R') s.turn = 0 else if (part[5] == 'B') s.turn = 1 else return .{ .ok = false, .state = s, .err = "bad turn" };
        } else if (std.mem.startsWith(u8, part, "R=")) {
            if (seen_r or part.len != 3) return .{ .ok = false, .state = s, .err = "bad runner" };
            seen_r = true;
            s.r = parseCell(part[2]) orelse return .{ .ok = false, .state = s, .err = "bad runner" };
        } else if (std.mem.startsWith(u8, part, "B=")) {
            if (seen_b or part.len != 3) return .{ .ok = false, .state = s, .err = "bad runner" };
            seen_b = true;
            s.b = parseCell(part[2]) orelse return .{ .ok = false, .state = s, .err = "bad runner" };
        } else if (std.mem.startsWith(u8, part, "blocked=")) {
            if (seen_blocked) return .{ .ok = false, .state = s, .err = "bad blocked" };
            seen_blocked = true;
            for (part[8..]) |ch| {
                const c = parseCell(ch) orelse return .{ .ok = false, .state = s, .err = "bad blocked" };
                s.blocked |= bit(c);
            }
        } else return .{ .ok = false, .state = s, .err = "bad field" };
    }
    if (!seen_turn or !seen_r or !seen_b or !seen_blocked) return .{ .ok = false, .state = s, .err = "missing field" };
    if (!validRaw(s)) return .{ .ok = false, .state = s, .err = "invalid position" };
    return .{ .ok = true, .state = s, .err = "" };
}

fn blockedString(allocator: std.mem.Allocator, mask: u8) ![]u8 {
    var out = std.ArrayList(u8).init(allocator);
    for (0..N) |i| {
        const ui: u8 = @intCast(i);
        if ((mask & bit(ui)) != 0) try out.append(cells[i]);
    }
    return out.toOwnedSlice();
}

fn rawString(allocator: std.mem.Allocator, s: State) ![]u8 {
    const bs = try blockedString(allocator, s.blocked);
    defer allocator.free(bs);
    return std.fmt.allocPrint(allocator, "turn={c};R={c};B={c};blocked={s}", .{ if (s.turn == 0) @as(u8, 'R') else @as(u8, 'B'), cells[s.r], cells[s.b], bs });
}

fn mirrorState(s: State) State {
    var mask: u8 = 0;
    for (0..N) |i| {
        const ui: u8 = @intCast(i);
        if ((s.blocked & bit(ui)) != 0) mask |= bit(mirror_map[i]);
    }
    return .{ .turn = s.turn, .r = mirror_map[s.r], .b = mirror_map[s.b], .blocked = mask };
}

fn canonicalString(allocator: std.mem.Allocator, s: State) ![]u8 {
    return rawString(allocator, s);
}

fn successors(s: State, out: *[6]State) usize {
    var count: usize = 0;
    const at = if (s.turn == 0) s.r else s.b;
    const other = if (s.turn == 0) s.b else s.r;
    for (neigh[at]) |to| {
        if (to == 255) continue;
        if (to == other) continue;
        if ((s.blocked & bit(to)) != 0) continue;
        var n = s;
        n.turn = 1 - s.turn;
        if (s.turn == 0) n.r = to else n.b = to;
        out[count] = n;
        count += 1;
    }
    return count;
}

fn classify(status: *[MAX]i8) void {
    @memset(status[0..], INVALID);
    for (0..MAX) |i| {
        const s = fromId(i);
        if (validRaw(s)) status[i] = UNKNOWN;
    }
    var changed = true;
    while (changed) {
        changed = false;
        for (0..MAX) |i| {
            if (status[i] != UNKNOWN) continue;
            const s = fromId(i);
            var succ: [6]State = undefined;
            const n = successors(s, &succ);
            if (n == 0) {
                status[i] = LOSS;
                changed = true;
                continue;
            }
            var any_loss = false;
            var all_win = true;
            for (succ[0..n]) |c| {
                const st = status[id(c)];
                if (st == LOSS) any_loss = true;
                if (st != WIN) all_win = false;
            }
            if (any_loss) {
                status[i] = WIN;
                changed = true;
            } else if (all_win) {
                status[i] = LOSS;
                changed = true;
            }
        }
    }
    for (0..MAX) |i| {
        if (status[i] == UNKNOWN) status[i] = LOSS;
    }
}

fn statusName(st: i8) []const u8 {
    return switch (st) { LOSS => "loss", WIN => "win", DRAW => "draw", else => "invalid" };
}

fn writeCertificate(allocator: std.mem.Allocator, writer: anytype, s: State, status: *[MAX]i8) !void {
    const st = status[id(s)];
    if (st == LOSS) {
        try writer.writeAll("[]");
        return;
    }
    var succ: [6]State = undefined;
    const n = successors(s, &succ);
    if (st == WIN) {
        for (succ[0..n]) |c| {
            if (status[id(c)] == LOSS) {
                const cs = try canonicalString(allocator, c);
                defer allocator.free(cs);
                try writer.print("[\"{s}\"]", .{cs});
                return;
            }
        }
    }
    try writer.writeAll("[]");
}

fn writePosition(allocator: std.mem.Allocator, writer: anytype, s: State, status: *[MAX]i8) !void {
    const cs = try canonicalString(allocator, s);
    defer allocator.free(cs);
    try writer.print("{{\"valid\":true,\"canonical\":\"{s}\",\"status\":\"{s}\",\"certificate\":", .{ cs, statusName(status[id(s)]) });
    try writeCertificate(allocator, writer, s, status);
    try writer.writeAll("}");
}

fn adjudicate(allocator: std.mem.Allocator, pos: []const u8, path: []const u8) !void {
    var file = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer file.close();
    var writer = file.writer();
    const pr = parsePosition(pos);
    if (!pr.ok) {
        try writer.print("{{\"valid\":false,\"error\":\"{s}\"}}\n", .{pr.err});
        return;
    }
    var status: [MAX]i8 = undefined;
    classify(&status);
    try writePosition(allocator, writer, pr.state, &status);
    try writer.writeAll("\n");
}

fn lessThan(_: void, a: []const u8, b: []const u8) bool {
    return std.mem.lessThan(u8, a, b);
}

fn table(allocator: std.mem.Allocator, path: []const u8) !void {
    var status: [MAX]i8 = undefined;
    classify(&status);
    var seen = std.StringHashMap(void).init(allocator);
    defer seen.deinit();
    var keys = std.ArrayList([]const u8).init(allocator);
    defer keys.deinit();
    for (0..MAX) |i| {
        const s = fromId(i);
        if (!validRaw(s)) continue;
        const cs = try canonicalString(allocator, s);
        if (!seen.contains(cs)) {
            try seen.put(cs, {});
            try keys.append(cs);
        } else allocator.free(cs);
    }
    std.mem.sort([]const u8, keys.items, {}, lessThan);
    var file = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer file.close();
    var writer = file.writer();
    try writer.writeAll("{\"positions\":[");
    for (keys.items, 0..) |k, idx| {
        if (idx != 0) try writer.writeAll(",");
        const pr = parsePosition(k);
        try writePosition(allocator, writer, pr.state, &status);
    }
    try writer.writeAll("]}\n");
    for (keys.items) |k| allocator.free(k);
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    if (args.len == 4 and std.mem.eql(u8, args[1], "adjudicate")) {
        try adjudicate(allocator, args[2], args[3]);
    } else if (args.len == 3 and std.mem.eql(u8, args[1], "table")) {
        try table(allocator, args[2]);
    } else {
        std.debug.print("usage\n", .{});
        std.process.exit(2);
    }
}
