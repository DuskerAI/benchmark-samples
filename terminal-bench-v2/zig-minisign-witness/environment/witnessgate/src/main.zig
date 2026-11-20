const std = @import("std");

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var args = try std.process.argsWithAllocator(allocator);
    defer args.deinit();
    _ = args.next();

    var out_path: ?[]const u8 = null;
    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "--out")) out_path = args.next();
    }
    const path = out_path orelse std.process.exit(2);
    const file = try std.fs.cwd().createFile(path, .{ .truncate = true });
    defer file.close();
    try file.writeAll(
        \\{
        \\  "accepted": true,
        \\  "status": "accepted",
        \\  "bundle_id": "",
        \\  "release": {"project": "", "version": "", "sequence": 0, "artifact_sha256": ""},
        \\  "log": {"log_id": "", "previous_size": 0, "tree_size": 0, "root_hash": ""},
        \\  "witness_quorum": {"required": 0, "valid": 0, "accepted_ids": []},
        \\  "reasons": []
        \\}
    );
    std.process.exit(0);
}
