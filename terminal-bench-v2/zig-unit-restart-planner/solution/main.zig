const std = @import("std");

const Allocator = std.mem.Allocator;
const StringList = std.ArrayList([]const u8);

const Unit = struct {
    name: []const u8,
    requires: StringList,
    after: StringList,
    before: StringList,
    part_of: StringList,
    refuse: bool,
    can_reload: bool,
    condition: ?[]const u8,
    active: bool,
    reasons: StringList,
};

const Edge = struct { from: usize, to: usize };

fn lessStr(_: void, a: []const u8, b: []const u8) bool {
    return std.mem.lessThan(u8, a, b);
}

fn lessUnit(_: void, a: *Unit, b: *Unit) bool {
    return std.mem.lessThan(u8, a.name, b.name);
}

fn newUnit(a: Allocator, name: []const u8) Unit {
    return .{
        .name = name,
        .requires = StringList.init(a),
        .after = StringList.init(a),
        .before = StringList.init(a),
        .part_of = StringList.init(a),
        .refuse = false,
        .can_reload = false,
        .condition = null,
        .active = false,
        .reasons = StringList.init(a),
    };
}

fn parseBool(v: []const u8) !bool {
    if (std.ascii.eqlIgnoreCase(v, "true") or std.ascii.eqlIgnoreCase(v, "yes")) return true;
    if (std.ascii.eqlIgnoreCase(v, "false") or std.ascii.eqlIgnoreCase(v, "no")) return false;
    return error.BadBoolean;
}

fn setList(a: Allocator, list: *StringList, raw: []const u8) !void {
    list.clearRetainingCapacity();
    var toks = std.mem.tokenizeAny(u8, raw, " \t\r\n\x0b\x0c");
    while (toks.next()) |tok| {
        try list.append(try a.dupe(u8, tok));
    }
}

fn sortUnique(list: *StringList) void {
    std.mem.sort([]const u8, list.items, {}, lessStr);
    var w: usize = 0;
    for (list.items) |item| {
        if (w == 0 or !std.mem.eql(u8, list.items[w - 1], item)) {
            list.items[w] = item;
            w += 1;
        }
    }
    list.shrinkRetainingCapacity(w);
}

fn contains(list: StringList, name: []const u8) bool {
    for (list.items) |item| if (std.mem.eql(u8, item, name)) return true;
    return false;
}

fn appendUnique(a: Allocator, list: *StringList, item: []const u8) !void {
    if (!contains(list.*, item)) try list.append(try a.dupe(u8, item));
}

fn applyLine(a: Allocator, u: *Unit, key: []const u8, value: []const u8) !void {
    if (std.mem.eql(u8, key, "Requires")) return setList(a, &u.requires, value);
    if (std.mem.eql(u8, key, "After")) return setList(a, &u.after, value);
    if (std.mem.eql(u8, key, "Before")) return setList(a, &u.before, value);
    if (std.mem.eql(u8, key, "PartOf")) return setList(a, &u.part_of, value);
    if (std.mem.eql(u8, key, "RefuseManualRestart")) {
        u.refuse = try parseBool(value);
        return;
    }
    if (std.mem.eql(u8, key, "CanReload")) {
        u.can_reload = try parseBool(value);
        return;
    }
    if (std.mem.eql(u8, key, "ConditionPathExists")) {
        u.condition = if (value.len == 0) null else try a.dupe(u8, value);
        return;
    }
    return;
}

fn parseMetadataFile(a: Allocator, path: []const u8, u: *Unit) !void {
    const data = try std.fs.cwd().readFileAlloc(a, path, 1024 * 1024);
    var it = std.mem.splitScalar(u8, data, '\n');
    while (it.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (line.len == 0 or line[0] == '#' or line[0] == ';') continue;
        if (line[0] == '[') continue;
        const eq = std.mem.indexOfScalar(u8, line, '=') orelse return error.BadMetadata;
        const key = std.mem.trim(u8, line[0..eq], " \t");
        const val = std.mem.trim(u8, line[eq + 1 ..], " \t");
        try applyLine(a, u, key, val);
    }
}

fn applyDropins(a: Allocator, root: []const u8, u: *Unit) !void {
    const dirname = try std.fmt.allocPrint(a, "{s}.d", .{u.name});
    const drop_dir_path = try std.fs.path.join(a, &.{ root, "dropins", dirname });
    var dir = std.fs.cwd().openDir(drop_dir_path, .{ .iterate = true }) catch |err| switch (err) {
        error.FileNotFound => return,
        else => return err,
    };
    defer dir.close();
    var names = StringList.init(a);
    var iter = dir.iterate();
    while (try iter.next()) |entry| {
        if (entry.kind == .file and std.mem.endsWith(u8, entry.name, ".conf")) try names.append(try a.dupe(u8, entry.name));
    }
    std.mem.sort([]const u8, names.items, {}, lessStr);
    for (names.items) |name| {
        const path = try std.fs.path.join(a, &.{ drop_dir_path, name });
        try parseMetadataFile(a, path, u);
    }
}

fn loadUnits(a: Allocator, root: []const u8) !std.StringHashMap(*Unit) {
    var units = std.StringHashMap(*Unit).init(a);
    const unit_dir_path = try std.fs.path.join(a, &.{ root, "units" });
    var dir = try std.fs.cwd().openDir(unit_dir_path, .{ .iterate = true });
    defer dir.close();
    var iter = dir.iterate();
    while (try iter.next()) |entry| {
        if (entry.kind != .file) continue;
        if (!(std.mem.endsWith(u8, entry.name, ".service") or std.mem.endsWith(u8, entry.name, ".target"))) continue;
        const name = try a.dupe(u8, entry.name);
        const ptr = try a.create(Unit);
        ptr.* = newUnit(a, name);
        const path = try std.fs.path.join(a, &.{ unit_dir_path, entry.name });
        try parseMetadataFile(a, path, ptr);
        try applyDropins(a, root, ptr);
        sortUnique(&ptr.requires);
        sortUnique(&ptr.after);
        sortUnique(&ptr.before);
        sortUnique(&ptr.part_of);
        try units.put(ptr.name, ptr);
    }
    return units;
}

fn loadState(a: Allocator, root: []const u8, units: *std.StringHashMap(*Unit)) !void {
    const path = try std.fs.path.join(a, &.{ root, "state.tsv" });
    const data = try std.fs.cwd().readFileAlloc(a, path, 1024 * 1024);
    var lines = std.mem.splitScalar(u8, data, '\n');
    const header = std.mem.trim(u8, lines.next() orelse return error.BadState, "\r");
    if (!std.mem.eql(u8, header, "unit\tstate")) return error.BadState;
    while (lines.next()) |raw| {
        const line = std.mem.trim(u8, raw, "\r");
        if (line.len == 0) continue;
        var cols = std.mem.splitScalar(u8, line, '\t');
        const name = cols.next() orelse return error.BadState;
        const state = cols.next() orelse return error.BadState;
        if (cols.next() != null) return error.BadState;
        const u = units.get(name) orelse return error.BadState;
        if (std.mem.eql(u8, state, "active")) u.active = true else if (std.mem.eql(u8, state, "inactive")) u.active = false else return error.BadState;
    }
}

fn conditionOK(a: Allocator, root: []const u8, u: *Unit) bool {
    const c = u.condition orelse return true;
    const rel = if (c.len > 0 and c[0] == '/') c[1..] else c;
    const path = std.fs.path.join(a, &.{ root, "fs", rel }) catch return false;
    std.fs.cwd().access(path, .{}) catch return false;
    return true;
}

fn safeUnit(a: Allocator, root: []const u8, u: *Unit) bool {
    return u.active and !u.refuse and conditionOK(a, root, u);
}

fn addEdge(edges: *std.ArrayList(Edge), from: usize, to: usize) !void {
    for (edges.items) |e| if (e.from == from and e.to == to) return;
    try edges.append(.{ .from = from, .to = to });
}

fn indexOf(units: []*Unit, name: []const u8) ?usize {
    for (units, 0..) |u, i| if (std.mem.eql(u8, u.name, name)) return i;
    return null;
}

fn writeList(writer: anytype, list: StringList) !void {
    for (list.items, 0..) |item, i| {
        if (i != 0) try writer.writeAll(",");
        try writer.writeAll(item);
    }
}

fn writeOutputs(a: Allocator, out: []const u8, all_units: []*Unit, plan_units: []*Unit, batches: []StringList) !void {
    const tmp = try std.fmt.allocPrint(a, "{s}.tmp", .{out});
    std.fs.cwd().deleteTree(tmp) catch {};
    try std.fs.cwd().makePath(tmp);
    errdefer std.fs.cwd().deleteTree(tmp) catch {};

    {
        const path = try std.fs.path.join(a, &.{ tmp, "plan.tsv" });
        var f = try std.fs.cwd().createFile(path, .{});
        defer f.close();
        const w = f.writer();
        try w.writeAll("batch\torder\tunit\taction\treason\n");
        var order: usize = 1;
        for (batches, 0..) |batch, bidx| {
            for (batch.items) |name| {
                const u = plan_units[indexOf(plan_units, name).?];
                sortUnique(&u.reasons);
                try w.print("{d}\t{d}\t{s}\trestart\t", .{ bidx + 1, order, name });
                for (u.reasons.items, 0..) |r, i| {
                    if (i != 0) try w.writeAll(";");
                    try w.writeAll(r);
                }
                try w.writeAll("\n");
                order += 1;
            }
        }
    }
    {
        const path = try std.fs.path.join(a, &.{ tmp, "rollback.tsv" });
        var f = try std.fs.cwd().createFile(path, .{});
        defer f.close();
        const w = f.writer();
        try w.writeAll("boundary\tunits\n");
        for (batches, 0..) |batch, i| {
            try w.print("{d}\t", .{i + 1});
            for (batch.items, 0..) |name, j| {
                if (j != 0) try w.writeAll(",");
                try w.writeAll(name);
            }
            try w.writeAll("\n");
        }
    }
    {
        const path = try std.fs.path.join(a, &.{ tmp, "effective.tsv" });
        var f = try std.fs.cwd().createFile(path, .{});
        defer f.close();
        const w = f.writer();
        try w.writeAll("unit\trequires\tafter\tbefore\tpart_of\trefuse_manual_restart\tcondition_path_exists\tcan_reload\tstate\n");
        for (all_units) |u| {
            try w.print("{s}\t", .{u.name});
            try writeList(w, u.requires);
            try w.writeAll("\t");
            try writeList(w, u.after);
            try w.writeAll("\t");
            try writeList(w, u.before);
            try w.writeAll("\t");
            try writeList(w, u.part_of);
            try w.print("\t{}\t{s}\t{}\t{s}\n", .{ u.refuse, u.condition orelse "", u.can_reload, if (u.active) "active" else "inactive" });
        }
    }

    if (std.fs.cwd().access(out, .{})) |_| {
        std.fs.cwd().deleteTree(out) catch |err| switch (err) {
            error.NotDir => std.fs.cwd().deleteFile(out) catch {},
            else => return err,
        };
    } else |_| {}
    try std.fs.cwd().rename(tmp, out);
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    const a = gpa.allocator();
    const args = try std.process.argsAlloc(a);
    if (args.len != 7 or !std.mem.eql(u8, args[1], "--root") or !std.mem.eql(u8, args[3], "--change") or !std.mem.eql(u8, args[5], "--out")) return error.BadArguments;
    const root = args[2];
    const change = args[4];
    const out = args[6];

    var units = try loadUnits(a, root);
    try loadState(a, root, &units);

    var all_units = std.ArrayList(*Unit).init(a);
    var uit = units.iterator();
    while (uit.next()) |entry| try all_units.append(entry.value_ptr.*);
    std.mem.sort(*Unit, all_units.items, {}, lessUnit);

    var closure = std.StringHashMap(bool).init(a);
    const change_data = try std.fs.cwd().readFileAlloc(a, change, 1024 * 1024);
    var lines = std.mem.splitScalar(u8, change_data, '\n');
    while (lines.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (line.len == 0 or line[0] == '#') continue;
        var toks = std.mem.tokenizeAny(u8, line, " \t");
        const op = toks.next() orelse return error.BadChange;
        const name = toks.next() orelse return error.BadChange;
        if (toks.next() != null or !std.mem.eql(u8, op, "restart")) return error.BadChange;
        const u = units.get(name) orelse return error.UnknownUnit;
        try closure.put(u.name, true);
        try appendUnique(a, &u.reasons, "requested");
    }

    var changed = true;
    while (changed) {
        changed = false;
        for (all_units.items) |parent| {
            if (!closure.contains(parent.name)) continue;
            for (all_units.items) |u| {
                if (contains(u.part_of, parent.name)) {
                    const reason = try std.fmt.allocPrint(a, "propagated:{s}", .{parent.name});
                    try appendUnique(a, &u.reasons, reason);
                    if (!closure.contains(u.name)) {
                        try closure.put(u.name, true);
                        changed = true;
                    }
                }
            }
        }
    }

    var plan_units = std.ArrayList(*Unit).init(a);
    for (all_units.items) |u| if (closure.contains(u.name)) try plan_units.append(u);
    std.mem.sort(*Unit, plan_units.items, {}, lessUnit);

    for (plan_units.items) |u| {
        if (!safeUnit(a, root, u)) return error.UnsafeUnit;
        for (u.requires.items) |req| {
            const r = units.get(req) orelse return error.BadRequire;
            if (!safeUnit(a, root, r)) return error.UnsafeRequire;
        }
    }

    var edges = std.ArrayList(Edge).init(a);
    for (plan_units.items, 0..) |u, i| {
        for (u.requires.items) |name| if (indexOf(plan_units.items, name)) |j| try addEdge(&edges, j, i);
        for (u.after.items) |name| if (indexOf(plan_units.items, name)) |j| try addEdge(&edges, j, i);
        for (u.before.items) |name| if (indexOf(plan_units.items, name)) |j| try addEdge(&edges, i, j);
        for (u.reasons.items) |r| {
            const prefix = "propagated:";
            if (std.mem.startsWith(u8, r, prefix)) {
                const parent = r[prefix.len..];
                if (indexOf(plan_units.items, parent)) |j| try addEdge(&edges, j, i);
            }
        }
    }

    var indeg = try a.alloc(usize, plan_units.items.len);
    var remaining = try a.alloc(bool, plan_units.items.len);
    for (indeg, 0..) |_, i| {
        indeg[i] = 0;
        remaining[i] = true;
    }
    for (edges.items) |e| indeg[e.to] += 1;

    var done: usize = 0;
    var batches = std.ArrayList(StringList).init(a);
    while (done < plan_units.items.len) {
        var batch = StringList.init(a);
        for (plan_units.items, 0..) |u, i| {
            if (remaining[i] and indeg[i] == 0) try batch.append(u.name);
        }
        if (batch.items.len == 0) return error.OrderCycle;
        for (batch.items) |name| {
            const i = indexOf(plan_units.items, name).?;
            remaining[i] = false;
            done += 1;
            for (edges.items) |e| {
                if (e.from == i) indeg[e.to] -= 1;
            }
        }
        try batches.append(batch);
    }

    try writeOutputs(a, out, all_units.items, plan_units.items, batches.items);
}
