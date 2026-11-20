const std = @import("std");

const Kind = enum { scalar, ptr };
const MapInfo = struct { name: []const u8, size: i64, entries: i64 };
const Policy = struct { max_tail: u32, helpers: std.ArrayList([]const u8), maps: std.ArrayList(MapInfo) };
const Reg = struct { kind: Kind = .scalar, min: i64 = 0, max: i64 = 255, map: []const u8 = "", size: i64 = 0, nullable: bool = false };
const State = struct { prog: usize, pc: usize, depth: u32, regs: [10]Reg };
const Program = struct { id: []const u8, lines: std.ArrayList([]const u8), labels: std.StringHashMap(usize) };
const Verdict = struct { ok: bool, reason: []const u8 };

fn trim(s: []const u8) []const u8 { return std.mem.trim(u8, s, " \t\r\n"); }

fn regIndex(s: []const u8) !usize {
    if (s.len != 2 or s[0] != 'r' or s[1] < '0' or s[1] > '9') return error.BadReg;
    return s[1] - '0';
}

fn parseNum(s: []const u8) !i64 { return std.fmt.parseInt(i64, s, 10); }

fn findMap(policy: *const Policy, name: []const u8) ?MapInfo {
    for (policy.maps.items) |m| if (std.mem.eql(u8, m.name, name)) return m;
    return null;
}

fn helperAllowed(policy: *const Policy, name: []const u8) bool {
    for (policy.helpers.items) |h| if (std.mem.startsWith(u8, name, h)) return true;
    return false;
}

fn parsePolicy(allocator: std.mem.Allocator, path: []const u8) !Policy {
    var p = Policy{ .max_tail = 0, .helpers = std.ArrayList([]const u8).init(allocator), .maps = std.ArrayList(MapInfo).init(allocator) };
    const data = try std.fs.cwd().readFileAlloc(allocator, path, 1 << 20);
    var it = std.mem.splitScalar(u8, data, '\n');
    while (it.next()) |raw| {
        const line = trim(raw);
        if (line.len == 0) continue;
        if (std.mem.startsWith(u8, line, "max_tail=")) {
            p.max_tail = try std.fmt.parseInt(u32, line[9..], 10);
        } else if (std.mem.startsWith(u8, line, "helper=")) {
            try p.helpers.append(try allocator.dupe(u8, line[7..]));
        } else if (std.mem.startsWith(u8, line, "map=")) {
            var parts = std.mem.splitScalar(u8, line[4..], ':');
            const n = parts.next() orelse return error.BadPolicy;
            const sz = parts.next() orelse return error.BadPolicy;
            const en = parts.next() orelse return error.BadPolicy;
            try p.maps.append(.{ .name = try allocator.dupe(u8, n), .size = try parseNum(sz), .entries = try parseNum(en) });
        } else return error.BadPolicy;
    }
    return p;
}

fn parseProgram(allocator: std.mem.Allocator, path: []const u8) !Program {
    var pr = Program{ .id = "", .lines = std.ArrayList([]const u8).init(allocator), .labels = std.StringHashMap(usize).init(allocator) };
    const data = try std.fs.cwd().readFileAlloc(allocator, path, 1 << 20);
    var it = std.mem.splitScalar(u8, data, '\n');
    while (it.next()) |raw| {
        const line = trim(raw);
        if (line.len == 0) continue;
        if (std.mem.startsWith(u8, line, "id ")) { pr.id = try allocator.dupe(u8, trim(line[3..])); continue; }
        if (line[line.len - 1] == ':') { try pr.labels.put(try allocator.dupe(u8, line[0 .. line.len - 1]), pr.lines.items.len); continue; }
        try pr.lines.append(try allocator.dupe(u8, line));
    }
    if (pr.id.len == 0) return error.BadProgram;
    return pr;
}

fn collectPrograms(allocator: std.mem.Allocator, dir_path: []const u8) !std.ArrayList(Program) {
    var out = std.ArrayList(Program).init(allocator);
    var dir = try std.fs.cwd().openDir(dir_path, .{ .iterate = true });
    defer dir.close();
    var it = dir.iterate();
    while (try it.next()) |ent| {
        if (ent.kind != .file or !std.mem.endsWith(u8, ent.name, ".ebpf")) continue;
        const full = try std.fs.path.join(allocator, &[_][]const u8{ dir_path, ent.name });
        try out.append(try parseProgram(allocator, full));
    }
    std.mem.sort(Program, out.items, {}, struct { fn less(_: void, a: Program, b: Program) bool { return std.mem.lessThan(u8, a.id, b.id); } }.less);
    return out;
}

fn initRegs() [10]Reg { var regs: [10]Reg = undefined; for (&regs) |*r| r.* = Reg{}; return regs; }

fn token(line: []const u8, idx: usize) ?[]const u8 {
    var it = std.mem.tokenizeAny(u8, line, " \t");
    var i: usize = 0;
    while (it.next()) |t| : (i += 1) if (i == idx) return t;
    return null;
}

fn programIndex(programs: []Program, id: []const u8) ?usize { for (programs, 0..) |p, i| if (std.mem.eql(u8, p.id, id)) return i; return null; }

fn addState(work: *std.ArrayList(State), s: State) !void { try work.append(s); }

fn checkProgram(policy: *const Policy, programs: []Program, start: usize, allocator: std.mem.Allocator) !Verdict {
    var work = std.ArrayList(State).init(allocator);
    try work.append(.{ .prog = start, .pc = 0, .depth = 0, .regs = initRegs() });
    var steps: usize = 0;
    while (work.popOrNull()) |s0| {
        var s = s0;
        steps += 1;
        if (steps > 20000) return .{ .ok = false, .reason = "control-flow" };
        const p = programs[s.prog];
        if (s.pc >= p.lines.items.len) return .{ .ok = false, .reason = "control-flow" };
        const line = p.lines.items[s.pc];
        const op = token(line, 0) orelse return .{ .ok = false, .reason = "format" };
        if (std.mem.eql(u8, op, "mov")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const v = try parseNum(token(line, 2) orelse return .{ .ok = false, .reason = "format" });
            s.regs[r] = .{ .min = v, .max = v }; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "add")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const v = try parseNum(token(line, 2) orelse return .{ .ok = false, .reason = "format" });
            if (s.regs[r].kind != .scalar) return .{ .ok = false, .reason = "memory" };
            s.regs[r].min += v; s.regs[r].max += v; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "ptr")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const mn = token(line, 2) orelse return .{ .ok = false, .reason = "format" };
            const m = findMap(policy, mn) orelse return .{ .ok = false, .reason = "memory" };
            s.regs[r] = .{ .kind = .ptr, .map = m.name, .size = m.size, .nullable = false }; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "lookup")) {
            const dst = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const mn = token(line, 2) orelse return .{ .ok = false, .reason = "format" };
            const key = try regIndex(token(line, 3) orelse return .{ .ok = false, .reason = "format" });
            const m = findMap(policy, mn) orelse return .{ .ok = false, .reason = "memory" };
            if (s.regs[key].kind != .scalar or s.regs[key].min < 0) return .{ .ok = false, .reason = "memory" };
            s.regs[dst] = .{ .kind = .ptr, .map = m.name, .size = m.size, .nullable = false }; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "load")) {
            const dst = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const ptr = try regIndex(token(line, 2) orelse return .{ .ok = false, .reason = "format" });
            const off = try parseNum(token(line, 3) orelse return .{ .ok = false, .reason = "format" });
            const sz = try parseNum(token(line, 4) orelse return .{ .ok = false, .reason = "format" });
            if (s.regs[ptr].kind != .ptr or off < 0 or sz <= 0 or off + sz > s.regs[ptr].size) return .{ .ok = false, .reason = "memory" };
            s.regs[dst] = .{ .min = 0, .max = 255 }; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "helper")) {
            if (!helperAllowed(policy, token(line, 1) orelse return .{ .ok = false, .reason = "format" })) return .{ .ok = false, .reason = "helper" };
            s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "jeq")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const v = try parseNum(token(line, 2) orelse return .{ .ok = false, .reason = "format" });
            const lab = token(line, 3) orelse return .{ .ok = false, .reason = "format" };
            if (s.regs[r].kind != .scalar) return .{ .ok = false, .reason = "memory" };
            if (v >= s.regs[r].min and v <= s.regs[r].max) { var t = s; t.regs[r].min = v; t.regs[r].max = v; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }
            s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "jgt")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const v = try parseNum(token(line, 2) orelse return .{ .ok = false, .reason = "format" });
            const lab = token(line, 3) orelse return .{ .ok = false, .reason = "format" };
            if (s.regs[r].kind != .scalar) return .{ .ok = false, .reason = "memory" };
            if (s.regs[r].max > v) { var t = s; if (t.regs[r].min <= v) t.regs[r].min = v + 1; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }
        } else if (std.mem.eql(u8, op, "jnull")) {
            const r = try regIndex(token(line, 1) orelse return .{ .ok = false, .reason = "format" });
            const lab = token(line, 2) orelse return .{ .ok = false, .reason = "format" };
            if (s.regs[r].kind == .ptr and s.regs[r].nullable) { var t = s; t.regs[r] = .{ .min = 0, .max = 0 }; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }
            s.regs[r].nullable = false; s.pc += 1; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "tail")) {
            if (s.depth >= policy.max_tail) return .{ .ok = false, .reason = "tail-call" };
            const target = programIndex(programs, token(line, 1) orelse return .{ .ok = false, .reason = "format" }) orelse return .{ .ok = false, .reason = "tail-call" };
            s.prog = target; s.pc = 0; s.depth = 0; try addState(&work, s);
        } else if (std.mem.eql(u8, op, "exit")) {
            continue;
        } else return .{ .ok = false, .reason = "format" };
    }
    return .{ .ok = true, .reason = "" };
}

fn writeJson(path: []const u8, ids: [][]const u8, verdicts: []Verdict) !bool {
    var f = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer f.close();
    var w = f.writer();
    var accepted: usize = 0; var rejected: usize = 0;
    for (verdicts) |v| {
        if (v.ok) accepted += 1 else rejected += 1;
    }
    try w.print("{{\"accepted\":[", .{});
    var first = true;
    for (ids, verdicts) |id, v| {
        if (v.ok) {
            if (!first) try w.print(",", .{});
            first = false;
            try w.print("\"{s}\"", .{id});
        }
    }
    try w.print("],\"rejected\":[", .{});
    first = true;
    for (ids, verdicts) |id, v| {
        if (!v.ok) {
            if (!first) try w.print(",", .{});
            first = false;
            try w.print("{{\"id\":\"{s}\",\"reason\":\"{s}\"}}", .{ id, v.reason });
        }
    }
    try w.print("],\"summary\":{{\"accepted\":{},\"rejected\":{}}}}}\n", .{ accepted, rejected });
    return rejected == 0;
}

pub fn main() !u8 {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){}; const allocator = gpa.allocator(); const args = try std.process.argsAlloc(allocator);
    var policy_path: []const u8 = ""; var bundle_path: []const u8 = ""; var out_path: []const u8 = ""; var i: usize = 1;
    while (i + 1 < args.len) : (i += 2) {
        if (std.mem.eql(u8, args[i], "--policy")) policy_path = args[i + 1] else if (std.mem.eql(u8, args[i], "--bundle")) bundle_path = args[i + 1] else if (std.mem.eql(u8, args[i], "--out")) out_path = args[i + 1] else return 2;
    }
    if (policy_path.len == 0 or bundle_path.len == 0 or out_path.len == 0) return 2;
    const policy = try parsePolicy(allocator, policy_path); const programs = try collectPrograms(allocator, bundle_path);
    const ids = try allocator.alloc([]const u8, programs.items.len); const verdicts = try allocator.alloc(Verdict, programs.items.len);
    for (programs.items, 0..) |p, n| { ids[n] = p.id; verdicts[n] = checkProgram(&policy, programs.items, n, allocator) catch .{ .ok = false, .reason = "format" }; }
    return if (try writeJson(out_path, ids, verdicts)) 0 else 1;
}
