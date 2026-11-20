const std = @import("std");

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    if (args.len != 7 or !std.mem.eql(u8, args[1], "--root") or !std.mem.eql(u8, args[3], "--change") or !std.mem.eql(u8, args[5], "--out")) return error.BadArguments;

    const root = args[2];
    const change = args[4];
    const out = args[6];
    const plan_path = try std.fs.path.join(allocator, &.{ out, "plan.tsv" });
    defer allocator.free(plan_path);

    std.fs.cwd().makePath(out) catch {};
    var file = try std.fs.cwd().createFile(plan_path, .{});
    defer file.close();
    try file.writeAll("batch\torder\tunit\taction\treason\n");

    var change_file = try std.fs.cwd().openFile(change, .{});
    defer change_file.close();
    const data = try change_file.readToEndAlloc(allocator, 1024 * 1024);
    var it = std.mem.splitScalar(u8, data, '\n');
    var order: usize = 1;
    while (it.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (line.len == 0 or line[0] == '#') continue;
        var parts = std.mem.tokenizeAny(u8, line, " \t");
        const op = parts.next() orelse continue;
        const unit = parts.next() orelse continue;
        if (!std.mem.eql(u8, op, "restart")) return error.BadChange;
        try file.writer().print("1\t{d}\t{s}\trestart\trequested\n", .{ order, unit });
        order += 1;
    }

    const rb_path = try std.fs.path.join(allocator, &.{ out, "rollback.tsv" });
    defer allocator.free(rb_path);
    var rb = try std.fs.cwd().createFile(rb_path, .{});
    defer rb.close();
    try rb.writeAll("boundary\tunits\n");

    const eff_path = try std.fs.path.join(allocator, &.{ out, "effective.tsv" });
    defer allocator.free(eff_path);
    var eff = try std.fs.cwd().createFile(eff_path, .{});
    defer eff.close();
    _ = root;
    try eff.writeAll("unit\trequires\tafter\tbefore\tpart_of\trefuse_manual_restart\tcondition_path_exists\tcan_reload\tstate\n");
}
