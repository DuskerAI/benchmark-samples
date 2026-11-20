#!/bin/bash
set -euo pipefail

cat > /opt/ebpf-admit/src/main.zig <<'ZIG'
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
        if (lo < 0 or lo > hi) return Reg.scalarTop();
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
        s.regs[10] = Reg.scalarImm(0);
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
    if (validateProgram(insns.items)) |schema_decision| return schema_decision;

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
            else => unreachable,
        };
        const op = try stringField(insn, "op");
        var st = states[pc];
        if (std.mem.eql(u8, op, "exit")) {
            exited = true;
            continue;
        } else if (std.mem.eql(u8, op, "ja")) {
            const target: usize = @intCast(try intField(insn, "target"));
            if (!try pushState(&queue, states, seen, target, st)) return Decision.deny("BAD_JUMP");
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
            st.regs[dst] = addScalar32(st.regs[dst], try intField(insn, "imm"));
        } else if (std.mem.eql(u8, op, "and_imm")) {
            const dst = try regField(insn, "dst");
            if (!andImm(&st.regs[dst], try intField(insn, "imm"))) return Decision.deny("BAD_REG");
        } else if (std.mem.eql(u8, op, "lsh_imm")) {
            const dst = try regField(insn, "dst");
            const shift: u6 = @intCast(try intField(insn, "imm"));
            if (!shiftImm(&st.regs[dst], shift, true)) return Decision.deny("BAD_REG");
        } else if (std.mem.eql(u8, op, "rsh_imm")) {
            const dst = try regField(insn, "dst");
            const shift: u6 = @intCast(try intField(insn, "imm"));
            if (!shiftImm(&st.regs[dst], shift, false)) return Decision.deny("BAD_REG");
        } else if (std.mem.eql(u8, op, "ld_pkt")) {
            const dst = try regField(insn, "dst");
            const base = try regField(insn, "base");
            const off = try intField(insn, "off");
            const size = try sizeField(insn);
            if (st.regs[base].tag != .pkt) return Decision.deny("BAD_REG");
            const lo = addI64(st.regs[base].off_min, off) orelse return Decision.deny("BAD_PACKET");
            const hi0 = addI64(st.regs[base].off_max, off) orelse return Decision.deny("BAD_PACKET");
            const hi = addI64(hi0, size) orelse return Decision.deny("BAD_PACKET");
            if (lo < 0 or hi > pkt_len) return Decision.deny("BAD_PACKET");
            st.regs[dst] = packetReadRange(size);
        } else if (std.mem.eql(u8, op, "st_stack")) {
            const off = try intField(insn, "off");
            const size = try sizeField(insn);
            if (insn.get("src")) |src_v| {
                const src = try regFromValue(src_v);
                if (st.regs[src].tag == .uninit) return Decision.deny("BAD_REG");
            }
            const r = stackRange(off, size) orelse return Decision.deny("BAD_STACK");
            var i = r.start;
            while (i < r.end) : (i += 1) st.stack[i] = true;
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
            st.regs[1] = Reg{};
            st.regs[2] = Reg{};
            st.regs[3] = Reg{};
            st.regs[4] = Reg{};
            st.regs[5] = Reg{};
            st.regs[0] = Reg.scalarTop();
        } else if (std.mem.eql(u8, op, "jge_imm") or std.mem.eql(u8, op, "jsge_imm") or std.mem.eql(u8, op, "jeq_imm")) {
            const dst = try regField(insn, "dst");
            const imm = try intField(insn, "imm");
            const target: usize = @intCast(try intField(insn, "target"));
            if (st.regs[dst].tag != .scalar) return Decision.deny("BAD_REG");
            var t = st;
            var f = st;
            var has_t: bool = false;
            var has_f: bool = false;
            if (std.mem.eql(u8, op, "jge_imm")) {
                has_t = refineUnsigned(&t.regs[dst], imm, true);
                has_f = refineUnsigned(&f.regs[dst], imm, false);
            } else if (std.mem.eql(u8, op, "jsge_imm")) {
                has_t = refineSigned(&t.regs[dst], imm, true);
                has_f = refineSigned(&f.regs[dst], imm, false);
            } else {
                has_t = refineEq(&t.regs[dst], imm, true);
                has_f = refineEq(&f.regs[dst], imm, false);
            }
            if (has_t and !try pushState(&queue, states, seen, target, t)) return Decision.deny("BAD_JUMP");
            if (has_f and !try pushState(&queue, states, seen, pc + 1, f)) return Decision.deny("BAD_JUMP");
            continue;
        } else {
            return Decision.deny("INPUT");
        }
        if (!try pushState(&queue, states, seen, pc + 1, st)) return Decision.deny("BAD_JUMP");
    }
    if (!exited) return Decision.deny("BAD_JUMP");
    return Decision.ok();
}

fn validateProgram(insns: []const std.json.Value) ?Decision {
    for (insns) |value| {
        const obj = switch (value) {
            .object => |o| o,
            else => return Decision.deny("INPUT"),
        };
        const op = stringField(obj, "op") catch return Decision.deny("INPUT");
        if (std.mem.eql(u8, op, "exit")) {
            continue;
        } else if (std.mem.eql(u8, op, "ja")) {
            if (checkTarget(obj, "target", insns.len)) |d| return d;
        } else if (std.mem.eql(u8, op, "mov_imm")) {
            if (!validRegField(obj, "dst") or !validIntField(obj, "imm")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "mov_reg")) {
            if (!validRegField(obj, "dst") or !validRegField(obj, "src")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "add_imm") or std.mem.eql(u8, op, "alu32_add_imm") or std.mem.eql(u8, op, "and_imm")) {
            if (!validRegField(obj, "dst") or !validIntField(obj, "imm")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "lsh_imm") or std.mem.eql(u8, op, "rsh_imm")) {
            if (!validRegField(obj, "dst") or !validShiftField(obj, "imm")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "add_reg")) {
            if (!validRegField(obj, "dst") or !validRegField(obj, "src")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "ld_pkt")) {
            if (!validRegField(obj, "dst") or !validRegField(obj, "base") or !validIntField(obj, "off") or !validSizeField(obj)) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "st_stack")) {
            if (!validIntField(obj, "off") or !validSizeField(obj)) return Decision.deny("INPUT");
            if (obj.get("src")) |src_v| _ = regFromValue(src_v) catch return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "ld_stack")) {
            if (!validRegField(obj, "dst") or !validIntField(obj, "off") or !validSizeField(obj)) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "call")) {
            if (!validIntField(obj, "helper")) return Decision.deny("INPUT");
        } else if (std.mem.eql(u8, op, "jge_imm") or std.mem.eql(u8, op, "jsge_imm") or std.mem.eql(u8, op, "jeq_imm")) {
            if (!validRegField(obj, "dst") or !validIntField(obj, "imm")) return Decision.deny("INPUT");
            if (checkTarget(obj, "target", insns.len)) |d| return d;
        } else {
            return Decision.deny("INPUT");
        }
    }
    return null;
}

fn pushState(queue: *std.ArrayList(usize), states: []State, seen: []bool, pc: usize, st: State) !bool {
    if (pc >= states.len) return false;
    if (!seen[pc]) {
        states[pc] = st;
        seen[pc] = true;
        try queue.append(pc);
        return true;
    }
    var changed = false;
    states[pc] = joinState(states[pc], st, &changed);
    if (changed) try queue.append(pc);
    return true;
}

fn joinState(a: State, b: State, changed: *bool) State {
    var out = a;
    var i: usize = 0;
    while (i < MAX_REGS) : (i += 1) out.regs[i] = joinReg(a.regs[i], b.regs[i], changed);
    i = 0;
    while (i < MAX_STACK) : (i += 1) {
        const v = a.stack[i] and b.stack[i];
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
            r.off_min = addI64(r.off_min, imm) orelse return false;
            r.off_max = addI64(r.off_max, imm) orelse return false;
        },
    }
    return true;
}

fn addReg(dst: *Reg, src: Reg) bool {
    if (src.tag == .uninit or dst.tag == .uninit) return false;
    if (dst.tag == .pkt and src.tag == .scalar) {
        if (src.smin == std.math.minInt(i64) or src.smax == std.math.maxInt(i64)) return false;
        dst.off_min = addI64(dst.off_min, src.smin) orelse return false;
        dst.off_max = addI64(dst.off_max, src.smax) orelse return false;
        return true;
    }
    if (dst.tag == .scalar and src.tag == .scalar) {
        if (dst.smin != std.math.minInt(i64) and dst.smax != std.math.maxInt(i64) and src.smin != std.math.minInt(i64) and src.smax != std.math.maxInt(i64)) {
            if (addI64(dst.smin, src.smin)) |lo_sum| {
                if (addI64(dst.smax, src.smax)) |hi_sum| {
                    dst.* = Reg.scalarRange(lo_sum, hi_sum);
                    return true;
                }
            }
        }
        dst.* = Reg.scalarTop();
        return true;
    }
    return false;
}

fn addScalar(r: Reg, imm: i64) Reg {
    if (r.smin == std.math.minInt(i64) or r.smax == std.math.maxInt(i64)) return Reg.scalarTop();
    const lo = addI64(r.smin, imm) orelse return Reg.scalarTop();
    const hi = addI64(r.smax, imm) orelse return Reg.scalarTop();
    return Reg.scalarRange(lo, hi);
}

fn addScalar32(r: Reg, imm: i64) Reg {
    if (r.tag != .scalar) return Reg{};
    if (r.smin == r.smax) {
        const low: u32 = @truncate(@as(u64, @bitCast(r.smin)));
        const add: u32 = @truncate(@as(u64, @bitCast(imm)));
        const out = low +% add;
        return Reg.scalarImm(@intCast(out));
    }
    if (r.umin <= r.umax and r.umax <= std.math.maxInt(u32)) {
        const lo0: u32 = @truncate(r.umin);
        const hi0: u32 = @truncate(r.umax);
        const add: u32 = @truncate(@as(u64, @bitCast(imm)));
        const lo = lo0 +% add;
        const hi = hi0 +% add;
        if (lo <= hi) return Reg.scalarRange(@intCast(lo), @intCast(hi));
    }
    return Reg.scalarRange(0, std.math.maxInt(u32));
}

fn andImm(r: *Reg, imm: i64) bool {
    if (r.tag != .scalar) return false;
    if (r.smin == r.smax) {
        const out = @as(u64, @bitCast(r.smin)) & @as(u64, @bitCast(imm));
        if (out <= @as(u64, @intCast(std.math.maxInt(i64)))) {
            r.* = Reg.scalarImm(@intCast(out));
        } else {
            r.* = Reg.scalarTop();
        }
        return true;
    }
    if (imm >= 0) {
        r.* = Reg.scalarRange(0, imm);
    } else {
        r.* = Reg.scalarTop();
    }
    return true;
}

fn shiftImm(r: *Reg, shift: u6, left: bool) bool {
    if (r.tag != .scalar) return false;
    if (r.smin == r.smax and r.smin >= 0) {
        if (left) {
            const max_before = @as(i64, std.math.maxInt(i64)) >> shift;
            if (r.smin <= max_before) {
                r.* = Reg.scalarImm(r.smin << shift);
            } else {
                r.* = Reg.scalarTop();
            }
        } else {
            r.* = Reg.scalarImm(r.smin >> shift);
        }
        return true;
    }
    if (r.smin >= 0 and r.smax != std.math.maxInt(i64)) {
        if (left) {
            const max_before = @as(i64, std.math.maxInt(i64)) >> shift;
            if (r.smax <= max_before) {
                r.* = Reg.scalarRange(r.smin << shift, r.smax << shift);
            } else {
                r.* = Reg.scalarTop();
            }
        } else {
            r.* = Reg.scalarRange(r.smin >> shift, r.smax >> shift);
        }
    } else {
        r.* = Reg.scalarTop();
    }
    return true;
}

fn packetReadRange(size: i64) Reg {
    return switch (size) {
        1 => Reg.scalarRange(0, 255),
        2 => Reg.scalarRange(0, 65535),
        4 => Reg.scalarRange(0, std.math.maxInt(u32)),
        else => Reg.scalarTop(),
    };
}

fn refineSigned(r: *Reg, imm: i64, take: bool) bool {
    if (take) {
        r.smin = @max(r.smin, imm);
    } else {
        if (imm == std.math.minInt(i64)) return false;
        r.smax = @min(r.smax, imm - 1);
    }
    if (r.smin > r.smax) return false;
    if (r.smin >= 0) {
        r.umin = @max(r.umin, @as(u64, @intCast(r.smin)));
        r.umax = @min(r.umax, @as(u64, @intCast(r.smax)));
        if (r.umin > r.umax) return false;
    }
    return true;
}

fn refineUnsigned(r: *Reg, imm: i64, take: bool) bool {
    const u: u64 = @as(u64, @bitCast(imm));
    if (take) {
        r.umin = @max(r.umin, u);
    } else {
        if (u == 0) return false;
        r.umax = @min(r.umax, u - 1);
    }
    if (r.umin > r.umax) return false;
    if (r.umax <= @as(u64, @intCast(std.math.maxInt(i64)))) {
        r.smin = @max(r.smin, @as(i64, @intCast(r.umin)));
        r.smax = @min(r.smax, @as(i64, @intCast(r.umax)));
        if (r.smin > r.smax) return false;
    }
    return true;
}

fn refineEq(r: *Reg, imm: i64, take: bool) bool {
    const exact = r.smin == r.smax;
    if (take) {
        if (exact and r.smin != imm) return false;
        if (imm >= 0) {
            const u: u64 = @intCast(imm);
            if (u < r.umin or u > r.umax) return false;
        } else if (imm < r.smin or imm > r.smax) return false;
        r.* = Reg.scalarImm(imm);
        return true;
    }
    if (exact and r.smin == imm) return false;
    return true;
}

fn addI64(a: i64, b: i64) ?i64 {
    const sum = @as(i128, a) + @as(i128, b);
    if (sum < std.math.minInt(i64) or sum > std.math.maxInt(i64)) return null;
    return @intCast(sum);
}

const StackRange = struct { start: usize, end: usize };

fn stackRange(off: i64, size: i64) ?StackRange {
    if (size <= 0 or size > 8) return null;
    const start_i = @as(i64, MAX_STACK) + off;
    const end_i = start_i + size;
    if (start_i < 0 or end_i > MAX_STACK or end_i < start_i) return null;
    return .{ .start = @intCast(start_i), .end = @intCast(end_i) };
}

fn checkTarget(obj: std.json.ObjectMap, key: []const u8, len: usize) ?Decision {
    const n = intField(obj, key) catch return Decision.deny("INPUT");
    if (n < 0) return Decision.deny("BAD_JUMP");
    const u: usize = @intCast(n);
    if (u >= len) return Decision.deny("BAD_JUMP");
    return null;
}

fn validIntField(obj: std.json.ObjectMap, key: []const u8) bool {
    _ = intField(obj, key) catch return false;
    return true;
}

fn validRegField(obj: std.json.ObjectMap, key: []const u8) bool {
    _ = regField(obj, key) catch return false;
    return true;
}

fn validSizeField(obj: std.json.ObjectMap) bool {
    _ = sizeField(obj) catch return false;
    return true;
}

fn validShiftField(obj: std.json.ObjectMap, key: []const u8) bool {
    const n = intField(obj, key) catch return false;
    return n >= 0 and n < 64;
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
ZIG

/opt/ebpf-admit/scripts/build.sh
