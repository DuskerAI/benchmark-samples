#!/bin/bash
set -euo pipefail
cat > /app/rvaudit/src/main.zig <<'ZIG'
const std = @import("std");

const Section = struct { name: []const u8, index: i64, addr: i64, size: i64 };
const Symbol = struct { name: []const u8, index: i64, section: i64, special: u8, value: i64, binding: []const u8, visibility: []const u8 };
const Reloc = struct { id: usize, section: i64, offset: i64, kind: []const u8, symbol: ?i64, addend: i64, pair: ?[]const u8, relaxed: bool };
const ErrorItem = struct { reloc: usize, code: []const u8 };
const ResultItem = struct { reloc: usize, value: i64, target: i64, paired: ?usize };

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    if (args.len != 3) return error.BadArgs;
    const data = try std.fs.cwd().readFileAlloc(allocator, args[1], 16 * 1024 * 1024);
    defer allocator.free(data);
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, data, .{});
    defer parsed.deinit();
    var sections = std.ArrayList(Section).init(allocator);
    defer sections.deinit();
    var symbols = std.ArrayList(Symbol).init(allocator);
    defer symbols.deinit();
    var relocs = std.ArrayList(Reloc).init(allocator);
    defer relocs.deinit();
    try parseInput(parsed.value, &sections, &symbols, &relocs);
    var errors = std.ArrayList(ErrorItem).init(allocator);
    defer errors.deinit();
    var results = std.ArrayList(ResultItem).init(allocator);
    defer results.deinit();
    try evaluate(sections.items, symbols.items, relocs.items, &errors, &results);
    var out = try std.fs.cwd().createFile(args[2], .{ .truncate = true });
    defer out.close();
    try writeReport(out.writer(), symbols.items, errors.items, results.items);
}

fn parseInput(root: std.json.Value, sections: *std.ArrayList(Section), symbols: *std.ArrayList(Symbol), relocs: *std.ArrayList(Reloc)) !void {
    const obj = switch (root) { .object => |o| o, else => return error.BadInput };
    const sec_arr = switch (try field(&obj, "sections")) { .array => |a| a, else => return error.BadInput };
    for (sec_arr.items) |item| {
        const o = switch (item) { .object => |v| v, else => return error.BadInput };
        try sections.append(.{ .name = try stringField(&o, "name"), .index = try intField(&o, "index"), .addr = try intField(&o, "addr"), .size = try intField(&o, "size") });
    }
    const sym_arr = switch (try field(&obj, "symbols")) { .array => |a| a, else => return error.BadInput };
    for (sym_arr.items) |item| {
        const o = switch (item) { .object => |v| v, else => return error.BadInput };
        const sv = try field(&o, "section");
        var section: i64 = 0;
        var special: u8 = 0;
        switch (sv) {
            .integer => |n| section = n,
            .string => |s| {
                if (std.mem.eql(u8, s, "ABS")) {
                    special = 1;
                } else if (std.mem.eql(u8, s, "UND")) {
                    special = 2;
                } else {
                    return error.BadInput;
                }
            },
            else => return error.BadInput,
        }
        try symbols.append(.{ .name = try stringField(&o, "name"), .index = try intField(&o, "index"), .section = section, .special = special, .value = try intField(&o, "value"), .binding = try stringField(&o, "binding"), .visibility = try stringField(&o, "visibility") });
    }
    const rel_arr = switch (try field(&obj, "relocations")) { .array => |a| a, else => return error.BadInput };
    for (rel_arr.items, 0..) |item, i| {
        const o = switch (item) { .object => |v| v, else => return error.BadInput };
        try relocs.append(.{ .id = i, .section = try intField(&o, "section"), .offset = try intField(&o, "offset"), .kind = try stringField(&o, "type"), .symbol = optIntField(&o, "symbol"), .addend = optIntField(&o, "addend") orelse 0, .pair = optStringField(&o, "pair"), .relaxed = optBoolField(&o, "relaxed") });
    }
}

fn evaluate(sections: []const Section, symbols: []const Symbol, relocs: []const Reloc, errors: *std.ArrayList(ErrorItem), results: *std.ArrayList(ResultItem)) !void {
    for (relocs) |r| {
        if (std.mem.eql(u8, r.kind, "R_RISCV_RELAX")) continue;
        const sec = findSection(sections, r.section) orelse { try errors.append(.{ .reloc = r.id, .code = "BAD_SECTION" }); continue; };
        if (r.relaxed) continue;
        const width: ?i64 = writeWidth(r.kind);
        if (width) |w| if (r.offset < 0 or sec.size < w or r.offset > sec.size - w) { try errors.append(.{ .reloc = r.id, .code = "WRITE_BOUNDS" }); continue; };
        const P = sec.addr + r.offset;
        if (isPcrel(r.kind)) {
            const mate_info = findPair(sections, relocs, r) orelse { try errors.append(.{ .reloc = r.id, .code = "PAIR" }); continue; };
            const mate_idx = mate_info.idx;
            const mate = mate_info.reloc;
            const hi = if (isHi(r.kind)) r else mate;
            const lo = if (isHi(r.kind)) mate else r;
            const hs = if (hi.symbol) |idx| findSymbol(symbols, idx) orelse { try errors.append(.{ .reloc = r.id, .code = "BAD_SYMBOL" }); continue; } else { try errors.append(.{ .reloc = r.id, .code = "BAD_SYMBOL" }); continue; };
            const base = symbolAddress(sections, hs) catch |err| switch (err) { error.Unresolved => { try errors.append(.{ .reloc = r.id, .code = "UNRESOLVED" }); continue; }, else => return err };
            const hsec = findSection(sections, hi.section) orelse { try errors.append(.{ .reloc = r.id, .code = "BAD_SECTION" }); continue; };
            const delta = base + hi.addend + lo.addend - (hsec.addr + hi.offset);
            const target = base + hi.addend + lo.addend;
            const value = if (isHi(r.kind)) @divFloor(delta + 0x800, 0x1000) else signExtend(delta, 12);
            try results.append(.{ .reloc = r.id, .value = value, .target = target, .paired = mate_idx });
            continue;
        }
        const S = if (r.symbol) |idx| blk: {
            const sym = findSymbol(symbols, idx) orelse { try errors.append(.{ .reloc = r.id, .code = "BAD_SYMBOL" }); continue; };
            break :blk symbolAddress(sections, sym) catch |err| switch (err) { error.Unresolved => { try errors.append(.{ .reloc = r.id, .code = "UNRESOLVED" }); continue; }, else => return err };
        } else 0;
        const target = S + r.addend;
        if (std.mem.eql(u8, r.kind, "R_RISCV_64")) {
            try results.append(.{ .reloc = r.id, .value = target, .target = target, .paired = null });
        } else if (isBranchKind(r.kind)) {
            const delta = target - P;
            if (@mod(delta, 2) != 0) { try errors.append(.{ .reloc = r.id, .code = "MISALIGNED" }); continue; }
            const limit: i64 = if (std.mem.eql(u8, r.kind, "R_RISCV_BRANCH")) 4096 else if (std.mem.eql(u8, r.kind, "R_RISCV_JAL")) 1048576 else 256;
            if (delta < -limit or delta > limit - 2) { try errors.append(.{ .reloc = r.id, .code = "RANGE" }); continue; }
            try results.append(.{ .reloc = r.id, .value = delta, .target = target, .paired = null });
        } else {
            try errors.append(.{ .reloc = r.id, .code = "UNSUPPORTED" });
        }
    }
}

fn field(obj: *const std.json.ObjectMap, key: []const u8) !std.json.Value { return obj.get(key) orelse error.BadInput; }
fn stringField(obj: *const std.json.ObjectMap, key: []const u8) ![]const u8 { return switch (try field(obj, key)) { .string => |s| s, else => error.BadInput }; }
fn intField(obj: *const std.json.ObjectMap, key: []const u8) !i64 { return switch (try field(obj, key)) { .integer => |n| n, else => error.BadInput }; }
fn optIntField(obj: *const std.json.ObjectMap, key: []const u8) ?i64 { const v = obj.get(key) orelse return null; return switch (v) { .integer => |n| n, else => null }; }
fn optStringField(obj: *const std.json.ObjectMap, key: []const u8) ?[]const u8 { const v = obj.get(key) orelse return null; return switch (v) { .string => |s| s, else => null }; }
fn optBoolField(obj: *const std.json.ObjectMap, key: []const u8) bool { const v = obj.get(key) orelse return false; return switch (v) { .bool => |b| b, else => false }; }
fn findSection(sections: []const Section, idx: i64) ?Section { for (sections) |s| if (s.index == idx) return s; return null; }
fn findSymbol(symbols: []const Symbol, idx: i64) ?Symbol { for (symbols) |s| if (s.index == idx) return s; return null; }
fn symbolAddress(sections: []const Section, sym: Symbol) !i64 { if (sym.special == 1) return sym.value; if (sym.special == 2) { if (std.mem.eql(u8, sym.binding, "WEAK")) return 0; return error.Unresolved; } const s = findSection(sections, sym.section) orelse return error.BadInput; return s.addr + sym.value; }
fn isHi(kind: []const u8) bool { return std.mem.eql(u8, kind, "R_RISCV_PCREL_HI20"); }
fn isLo(kind: []const u8) bool { return std.mem.eql(u8, kind, "R_RISCV_PCREL_LO12_I") or std.mem.eql(u8, kind, "R_RISCV_PCREL_LO12_S"); }
fn isPcrel(kind: []const u8) bool { return isHi(kind) or isLo(kind); }
fn isBranchKind(kind: []const u8) bool { return std.mem.eql(u8, kind, "R_RISCV_BRANCH") or std.mem.eql(u8, kind, "R_RISCV_JAL") or std.mem.eql(u8, kind, "R_RISCV_RVC_BRANCH"); }
fn writeWidth(kind: []const u8) ?i64 { if (std.mem.eql(u8, kind, "R_RISCV_64")) return 8; if (isPcrel(kind) or std.mem.eql(u8, kind, "R_RISCV_BRANCH") or std.mem.eql(u8, kind, "R_RISCV_JAL")) return 4; if (std.mem.eql(u8, kind, "R_RISCV_RVC_BRANCH")) return 2; return null; }

const PairHit = struct { idx: usize, reloc: Reloc };
fn admittedPcrel(sections: []const Section, r: Reloc) bool {
    if (!isPcrel(r.kind)) return false;
    if (r.pair == null or r.pair.?.len == 0) return false;
    const sec = findSection(sections, r.section) orelse return false;
    if (r.relaxed) return false;
    const w = writeWidth(r.kind) orelse return false;
    return !(r.offset < 0 or sec.size < w or r.offset > sec.size - w);
}

fn findPair(sections: []const Section, relocs: []const Reloc, r: Reloc) ?PairHit {
    const key = r.pair orelse return null;
    if (key.len == 0 or !admittedPcrel(sections, r)) return null;
    var count: usize = 0;
    var hi_count: usize = 0;
    var lo_count: usize = 0;
    var mate: ?Reloc = null;
    for (relocs) |other| {
        if (other.pair == null or !std.mem.eql(u8, other.pair.?, key) or !admittedPcrel(sections, other)) continue;
        count += 1;
        if (isHi(other.kind)) hi_count += 1;
        if (isLo(other.kind)) lo_count += 1;
        if (other.id != r.id) mate = other;
    }
    if (count != 2 or hi_count != 1 or lo_count != 1) return null;
    const m = mate orelse return null;
    return .{ .idx = m.id, .reloc = m };
}

fn signExtend(value: i64, bits: u6) i64 { const mask = (@as(i64, 1) << bits) - 1; var v = value & mask; const sign = @as(i64, 1) << (bits - 1); if ((v & sign) != 0) v -= (@as(i64, 1) << bits); return v; }

fn writeReport(w: anytype, symbols: []const Symbol, errors: []const ErrorItem, results: []const ResultItem) !void {
    try w.print("{{\"ok\":{},\"errors\":[", .{errors.len == 0});
    for (errors, 0..) |e, i| { if (i != 0) try w.writeAll(","); try w.print("{{\"reloc\":{},\"code\":\"{s}\"}}", .{ e.reloc, e.code }); }
    try w.writeAll("],\"relocations\":[");
    for (results, 0..) |r, i| { if (i != 0) try w.writeAll(","); try w.print("{{\"reloc\":{},\"value\":{},\"target\":{},\"paired\":", .{ r.reloc, r.value, r.target }); if (r.paired) |p| try w.print("{}", .{p}) else try w.writeAll("null"); try w.writeAll("}"); }
    try w.writeAll("],\"exports\":[");
    var first = true;
    for (symbols) |s| if (s.special != 2 and (std.mem.eql(u8, s.binding, "GLOBAL") or std.mem.eql(u8, s.binding, "WEAK")) and (std.mem.eql(u8, s.visibility, "DEFAULT") or std.mem.eql(u8, s.visibility, "PROTECTED"))) { if (!first) try w.writeAll(","); first = false; try w.print("\"{s}\"", .{s.name}); };
    try w.writeAll("]}\n");
}
ZIG
cd /app/rvaudit
make clean all
