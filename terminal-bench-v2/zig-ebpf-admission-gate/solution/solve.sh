#!/bin/bash
set -euo pipefail

python - <<'PY'
from pathlib import Path

src = Path("/app/ebpfgate/src/main.zig")
text = src.read_text()

replacements = {
    'for (policy.helpers.items) |h| if (std.mem.startsWith(u8, name, h)) return true;':
        'for (policy.helpers.items) |h| if (std.mem.eql(u8, name, h)) return true;',
    '''fn token(line: []const u8, idx: usize) ?[]const u8 {
    var it = std.mem.tokenizeAny(u8, line, " \\t");
    var i: usize = 0;
    while (it.next()) |t| : (i += 1) if (i == idx) return t;
    return null;
}''':
        '''fn token(line: []const u8, idx: usize) ?[]const u8 {
    var it = std.mem.tokenizeAny(u8, line, " \\t");
    var i: usize = 0;
    while (it.next()) |t| : (i += 1) if (i == idx) return t;
    return null;
}

fn exactArity(line: []const u8, count: usize) bool { return token(line, count) == null; }''',
    'var pr = Program{ .id = "", .lines = std.ArrayList([]const u8).init(allocator), .labels = std.StringHashMap(usize).init(allocator) };':
        'var pr = Program{ .id = "", .lines = std.ArrayList([]const u8).init(allocator), .labels = std.StringHashMap(usize).init(allocator) };\n    var saw_header = false;',
    '''        if (std.mem.startsWith(u8, line, "id ")) { pr.id = try allocator.dupe(u8, trim(line[3..])); continue; }
        if (line[line.len - 1] == ':') { try pr.labels.put(try allocator.dupe(u8, line[0 .. line.len - 1]), pr.lines.items.len); continue; }
        try pr.lines.append(try allocator.dupe(u8, line));''':
        '''        if (std.mem.startsWith(u8, line, "id ")) {
            if (saw_header or !exactArity(line, 2)) {
                if (!saw_header) {
                    const maybe_id = token(line, 1) orelse return error.BadProgram;
                    pr.id = try allocator.dupe(u8, maybe_id);
                    saw_header = true;
                }
                try pr.lines.append(try allocator.dupe(u8, "bad-format"));
            } else {
                pr.id = try allocator.dupe(u8, trim(line[3..]));
                if (pr.id.len == 0) return error.BadProgram;
                saw_header = true;
            }
            continue;
        }
        if (!saw_header) { try pr.lines.append(try allocator.dupe(u8, "bad-format")); continue; }
        if (line[line.len - 1] == ':') { try pr.labels.put(try allocator.dupe(u8, line[0 .. line.len - 1]), pr.lines.items.len); continue; }
        try pr.lines.append(try allocator.dupe(u8, line));''',
    'if (std.mem.eql(u8, op, "mov")) {': 'if (std.mem.eql(u8, op, "mov")) {\n            if (!exactArity(line, 3)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "add")) {': '} else if (std.mem.eql(u8, op, "add")) {\n            if (!exactArity(line, 3)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "ptr")) {': '} else if (std.mem.eql(u8, op, "ptr")) {\n            if (!exactArity(line, 3)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "lookup")) {': '} else if (std.mem.eql(u8, op, "lookup")) {\n            if (!exactArity(line, 4)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "load")) {': '} else if (std.mem.eql(u8, op, "load")) {\n            if (!exactArity(line, 5)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "helper")) {': '} else if (std.mem.eql(u8, op, "helper")) {\n            if (!exactArity(line, 2)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "jeq")) {': '} else if (std.mem.eql(u8, op, "jeq")) {\n            if (!exactArity(line, 4)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "jgt")) {': '} else if (std.mem.eql(u8, op, "jgt")) {\n            if (!exactArity(line, 4)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "jnull")) {': '} else if (std.mem.eql(u8, op, "jnull")) {\n            if (!exactArity(line, 3)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "tail")) {': '} else if (std.mem.eql(u8, op, "tail")) {\n            if (!exactArity(line, 2)) return .{ .ok = false, .reason = "format" };',
    '} else if (std.mem.eql(u8, op, "exit")) {': '} else if (std.mem.eql(u8, op, "exit")) {\n            if (!exactArity(line, 1)) return .{ .ok = false, .reason = "format" };',
    'if (s.regs[key].kind != .scalar or s.regs[key].min < 0) return .{ .ok = false, .reason = "memory" };':
        'if (s.regs[key].kind != .scalar or s.regs[key].min < 0 or s.regs[key].max >= m.entries) return .{ .ok = false, .reason = "memory" };',
    's.regs[dst] = .{ .kind = .ptr, .map = m.name, .size = m.size, .nullable = false }; s.pc += 1; try addState(&work, s);':
        's.regs[dst] = .{ .kind = .ptr, .map = m.name, .size = m.size, .nullable = true }; s.pc += 1; try addState(&work, s);',
    'if (s.regs[ptr].kind != .ptr or off < 0 or sz <= 0 or off + sz > s.regs[ptr].size) return .{ .ok = false, .reason = "memory" };':
        'if (s.regs[ptr].kind != .ptr or s.regs[ptr].nullable or off < 0 or sz <= 0 or off + sz > s.regs[ptr].size) return .{ .ok = false, .reason = "memory" };',
    'if (v >= s.regs[r].min and v <= s.regs[r].max) { var t = s; t.regs[r].min = v; t.regs[r].max = v; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }\n            s.pc += 1; try addState(&work, s);':
        'if (v >= s.regs[r].min and v <= s.regs[r].max) { var t = s; t.regs[r].min = v; t.regs[r].max = v; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }\n            if (s.regs[r].min < v or s.regs[r].max > v) { var f = s; if (f.regs[r].min == v) f.regs[r].min = v + 1; if (f.regs[r].max == v) f.regs[r].max = v - 1; f.pc += 1; try addState(&work, f); }',
    'if (s.regs[r].max > v) { var t = s; if (t.regs[r].min <= v) t.regs[r].min = v + 1; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }':
        'if (s.regs[r].max > v) { var t = s; if (t.regs[r].min <= v) t.regs[r].min = v + 1; t.pc = p.labels.get(lab) orelse return .{ .ok = false, .reason = "format" }; try addState(&work, t); }\n            if (s.regs[r].min <= v) { var f = s; if (f.regs[r].max > v) f.regs[r].max = v; f.pc += 1; try addState(&work, f); }',
    's.prog = target; s.pc = 0; s.depth = 0; try addState(&work, s);':
        's.prog = target; s.pc = 0; s.depth += 1; try addState(&work, s);',
}

for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"expected source fragment not found: {old[:80]!r}")
    text = text.replace(old, new, 1)

src.write_text(text)
PY

bash /app/ebpfgate/scripts/build.sh
