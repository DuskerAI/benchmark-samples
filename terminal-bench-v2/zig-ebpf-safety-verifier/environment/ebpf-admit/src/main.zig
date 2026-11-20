const std = @import("std");

const MAX_REGS = 11;
const MAX_STACK = 512;
const MAX_STEPS = 20000;

const Tag = enum { uninit, scalar, pkt };

const Reg = struct {
    tag: Tag = .uninit,
    umin: u64 = 0,
    umax: u64 = 0,
    smin: i64 = 0,
    smax: i64 = 0,
    off_min: i64 = 0,
    off_max: i64 = 0,

    fn scalarTop() Reg {
        return .{ .tag = .scalar, .umin = 0, .umax = std.math.maxInt(u64), .smin = std.math.minInt(i64), .smax = std.math.maxInt(i64) };
    }

    fn scalarImm(v: i64) Reg {
        return .{ .tag = .scalar, .umin = @as(u64, @bitCast(v)), .umax = @as(u64, @bitCast(v)), .smin = v, .smax = v };
    }

    fn scalarRange(lo: i64, hi: i64) Reg {
        if (lo == std.math.minInt(i64) or hi == std.math.maxInt(i64) or lo < 0) {
            return .{ .tag = .scalar, .umin = 0, .umax = std.math.maxInt(u64), .smin = lo, .smax = hi };
        }
        return .{ .tag = .scalar, .umin = @intCast(lo), .umax = @intCast(hi), .smin = lo, .smax = hi };
    }

    fn pktPtr() Reg {
        return .{ .tag = .pkt, .off_min = 0, .off_max = 0 };
    }
};

const State = struct {
    regs: [MAX_REGS]Reg,
    stack: [MAX_STACK]bool,

    fn init() State {
        var s = State{ .regs = [_]Reg{Reg{}} ** MAX_REGS, .stack = [_]bool{false} ** MAX_STACK };
        s.regs[1] = Reg.pktPtr();
        s.regs[10] = .{ .tag = .scalar, .umin = 0, .umax = 0, .smin = 0, .smax = 0 };
        return s;
    }
};

const Decision = struct {
    allow: bool,
    code: []const u8,

    fn ok() Decision {
        return .{ .allow = true, .code = "OK" };
    }

    fn deny(code: []const u8) Decision {
        return .{ .allow = false, .code = code };
    }
};

pub fn main() !void {
    var args = std.process.args();
    _ = args.next();
    const path = args.next() orelse {
        try std.io.getStdOut().writer().print("DENY INPUT\n", .{});
        return;
    };
    const allocator = std.heap.page_allocator;
    const data = std.fs.cwd().readFileAlloc(allocator, path, 1 << 20) catch {
        try std.io.getStdOut().writer().print("DENY INPUT\n", .{});
        return;
    };
    defer allocator.free(data);
    const d = verify(allocator, data) catch Decision.deny("INPUT");
    if (d.allow) {
        try std.io.getStdOut().writer().print("ALLOW {s}\n", .{d.code});
    } else {
        try std.io.getStdOut().writer().print("DENY {s}\n", .{d.code});
    }
}

fn verify(allocator: std.mem.Allocator, data: []const u8) !Decision {
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, data, .{});
    defer parsed.deinit();
    const root = switch (parsed.value) {
        .object => |obj| obj,
        else => return Decision.deny("INPUT"),
    };
    const pkt_len_i = try intField(root, "pkt_len");
    if (pkt_len_i < 0) return Decision.deny("INPUT");
    const pkt_len: i64 = pkt_len_i;
    const insns_v = root.get("insns") orelse return Decision.deny("INPUT");
    const insns = switch (insns_v) {
        .array => |a| a,
        else => return Decision.deny("INPUT"),
    };
    if (insns.items.len == 0 or insns.items.len > 512) return Decision.deny("INPUT");

    const states = try allocator.alloc(State, insns.items.len);
    defer allocator.free(states);
    const seen = try allocator.alloc(bool, insns.items.len);
    defer allocator.free(seen);
    @memset(seen, false);
    var queue = std.ArrayList(usize).init(allocator);
    defer queue.deinit();
    states[0] = State.init();
    seen[0] = true;
    try queue.append(0);
    var steps: usize = 0;
    var exited = false;

    while (queue.items.len > 0) {
        if (steps > MAX_STEPS) return Decision.deny("LOOP");
        steps += 1;
        const pc = queue.pop();
        if (pc >= insns.items.len) return Decision.deny("BAD_JUMP");
        const insn = switch (insns.items[pc]) {
            .object => |obj| obj,
            else => return Decision.deny("INPUT"),
        };
        const op = try stringField(insn, "op");
        var st = states[pc];
        if (std.mem.eql(u8, op, "exit")) {
            exited = true;
            continue;
        } else if (std.mem.eql(u8, op, "ja")) {
            const target: usize = @intCast(try intField(insn, "target"));
            try pushState(&queue, states, seen, target, st);
            continue;
        } else if (std.mem.eql(u8, op, "mov_imm")) {
            const dst = try regField(insn, "dst");
            st.regs[dst] = Reg.scalarImm(try intField(insn, "imm"));
        } else if (std.mem.eql(u8, op, "mov_reg")) {
            const dst = try regField(insn, "dst");
            const src = try regField(insn, "src");
            if (st.regs[src].tag == .uninit) return Decision.deny("BAD_REG");
            st.regs[dst] = st.regs[src];
        } else if (std.mem.eql(u8, op, "add_imm")) {
            const dst = try regField(insn, "dst");
            const imm = try intField(insn, "imm");
            if (!addImm(&st.regs[dst], imm)) return Decision.deny("BAD_REG");
        } else if (std.mem.eql(u8, op, "add_reg")) {
            const dst = try regField(insn, "dst");
            const src = try regField(insn, "src");
            if (!addReg(&st.regs[dst], st.regs[src])) return Decision.deny("BAD_REG");
        } else if (std.mem.eql(u8, op, "alu32_add_imm")) {
            const dst = try regField(insn, "dst");
            if (st.regs[dst].tag != .scalar) return Decision.deny("BAD_REG");
            const imm = try intField(insn, "imm");
            st.regs[dst] = addScalar(st.regs[dst], imm);
        } else if (std.mem.eql(u8, op, "ld_pkt")) {
            const dst = try regField(insn, "dst");
            const base = try regField(insn, "base");
            const off = try intField(insn, "off");
            const size = try sizeField(insn);
            if (st.regs[base].tag != .pkt) return Decision.deny("BAD_REG");
            const lo = st.regs[base].off_min + off;
            const hi = st.regs[base].off_max + off + size;
            if (lo < 0 or hi > pkt_len) return Decision.deny("BAD_PACKET");
            st.regs[dst] = Reg.scalarTop();
        } else if (std.mem.eql(u8, op, "st_stack")) {
            const off = try intField(insn, "off");
            const size = try sizeField(insn);
            if (insn.get("src")) |src_v| {
                const src = try regFromValue(src_v);
                if (st.regs[src].tag == .uninit) return Decision.deny("BAD_REG");
            }
            const r = stackRange(off, size) orelse return Decision.deny("BAD_STACK");
            const base = r.start & ~@as(usize, 7);
            const end = @min(base + 8, MAX_STACK);
            var i = base;
            while (i < end) : (i += 1) st.stack[i] = true;
        } else if (std.mem.eql(u8, op, "ld_stack")) {
            const dst = try regField(insn, "dst");
            const off = try intField(insn, "off");
            const size = try sizeField(insn);
            const r = stackRange(off, size) orelse return Decision.deny("BAD_STACK");
            var i = r.start;
            while (i < r.end) : (i += 1) {
                if (!st.stack[i]) return Decision.deny("BAD_STACK");
            }
            st.regs[dst] = Reg.scalarTop();
        } else if (std.mem.eql(u8, op, "call")) {
            _ = try intField(insn, "helper");
            st.regs[0] = Reg.scalarTop();
        } else if (std.mem.eql(u8, op, "jge_imm") or std.mem.eql(u8, op, "jsge_imm")) {
            const dst = try regField(insn, "dst");
            const imm = try intField(insn, "imm");
            const target: usize = @intCast(try intField(insn, "target"));
            if (st.regs[dst].tag != .scalar) return Decision.deny("BAD_REG");
            var t = st;
            var f = st;
            const has_t = refineUnsigned(&t.regs[dst], imm, true);
            const has_f = refineUnsigned(&f.regs[dst], imm, false);
            if (has_t) try pushState(&queue, states, seen, target, t);
            if (has_f) try pushState(&queue, states, seen, pc + 1, f);
            continue;
        } else {
            return Decision.deny("INPUT");
        }
        try pushState(&queue, states, seen, pc + 1, st);
    }
    if (!exited) return Decision.deny("BAD_JUMP");
    return Decision.ok();
}

fn pushState(queue: *std.ArrayList(usize), states: []State, seen: []bool, pc: usize, st: State) !void {
    if (pc >= states.len) return;
    if (!seen[pc]) {
        states[pc] = st;
        seen[pc] = true;
        try queue.append(pc);
        return;
    }
    var changed = false;
    states[pc] = joinState(states[pc], st, &changed);
    if (changed) try queue.append(pc);
}

fn joinState(a: State, b: State, changed: *bool) State {
    var out = a;
    var i: usize = 0;
    while (i < MAX_REGS) : (i += 1) out.regs[i] = joinReg(a.regs[i], b.regs[i], changed);
    i = 0;
    while (i < MAX_STACK) : (i += 1) {
        const v = a.stack[i] or b.stack[i];
        if (v != a.stack[i]) changed.* = true;
        out.stack[i] = v;
    }
    return out;
}

fn joinReg(a: Reg, b: Reg, changed: *bool) Reg {
    if (a.tag != b.tag) {
        if (a.tag != .uninit) changed.* = true;
        return Reg{};
    }
    var out = a;
    switch (a.tag) {
        .uninit => {},
        .scalar => {
            out.umin = @min(a.umin, b.umin);
            out.umax = @max(a.umax, b.umax);
            out.smin = @min(a.smin, b.smin);
            out.smax = @max(a.smax, b.smax);
        },
        .pkt => {
            out.off_min = @min(a.off_min, b.off_min);
            out.off_max = @max(a.off_max, b.off_max);
        },
    }
    if (!std.meta.eql(out, a)) changed.* = true;
    return out;
}

fn addImm(r: *Reg, imm: i64) bool {
    switch (r.tag) {
        .uninit => return false,
        .scalar => r.* = addScalar(r.*, imm),
        .pkt => {
            r.off_min += imm;
            r.off_max += imm;
        },
    }
    return true;
}

fn addReg(dst: *Reg, src: Reg) bool {
    if (src.tag == .uninit or dst.tag == .uninit) return false;
    if (dst.tag == .pkt and src.tag == .scalar) {
        if (src.smin == std.math.minInt(i64) or src.smax == std.math.maxInt(i64)) return false;
        dst.off_min += src.smin;
        dst.off_max += src.smax;
        return true;
    }
    if (dst.tag == .scalar and src.tag == .scalar) {
        dst.* = Reg.scalarTop();
        return true;
    }
    return false;
}

fn addScalar(r: Reg, imm: i64) Reg {
    if (r.smin == std.math.minInt(i64) or r.smax == std.math.maxInt(i64)) return Reg.scalarTop();
    return Reg.scalarRange(r.smin + imm, r.smax + imm);
}

fn refineUnsigned(r: *Reg, imm: i64, take: bool) bool {
    const u: u64 = @as(u64, @bitCast(imm));
    if (take) {
        r.umin = @max(r.umin, u);
    } else {
        if (u == 0) return false;
        r.umax = @min(r.umax, u - 1);
    }
    return r.umin <= r.umax;
}

const StackRange = struct { start: usize, end: usize };

fn stackRange(off: i64, size: i64) ?StackRange {
    if (size <= 0 or size > 8) return null;
    const start_i = @as(i64, MAX_STACK) + off;
    const end_i = start_i + size;
    if (start_i < 0 or end_i > MAX_STACK or end_i < start_i) return null;
    return .{ .start = @intCast(start_i), .end = @intCast(end_i) };
}

fn stringField(obj: std.json.ObjectMap, key: []const u8) ![]const u8 {
    const v = obj.get(key) orelse return error.BadInput;
    return switch (v) {
        .string => |s| s,
        else => error.BadInput,
    };
}

fn intField(obj: std.json.ObjectMap, key: []const u8) !i64 {
    const v = obj.get(key) orelse return error.BadInput;
    return switch (v) {
        .integer => |n| n,
        else => error.BadInput,
    };
}

fn regField(obj: std.json.ObjectMap, key: []const u8) !usize {
    const n = try intField(obj, key);
    if (n < 0 or n >= MAX_REGS) return error.BadInput;
    return @intCast(n);
}

fn regFromValue(v: std.json.Value) !usize {
    const n = switch (v) {
        .integer => |x| x,
        else => return error.BadInput,
    };
    if (n < 0 or n >= MAX_REGS) return error.BadInput;
    return @intCast(n);
}

fn sizeField(obj: std.json.ObjectMap) !i64 {
    const n = try intField(obj, "size");
    if (!(n == 1 or n == 2 or n == 4 or n == 8)) return error.BadInput;
    return n;
}
