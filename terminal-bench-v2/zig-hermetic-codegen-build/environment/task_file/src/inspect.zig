const std = @import("std");
const meta = @import("build_meta");

fn printInfo() !void {
    try std.io.getStdOut().writer().print(
        "component=relay-inspect role={s} priority={d} revision={s} channel={s} schema={d} target={s} profile={s} epoch={d} link_mode={s} digest={s} route_count={d} route_weight_total={d} capability_count={d} manifest_digest={s}\n",
        .{ meta.inspect_role, meta.inspect_priority, meta.revision, meta.channel, meta.schema, meta.target, meta.profile, meta.epoch, meta.link_mode, meta.digest, meta.route_count, meta.route_weight_total, meta.capability_count, meta.manifest_digest },
    );
}

fn auditManifest(path: []const u8) !void {
    const allocator = std.heap.page_allocator;
    const data = try std.fs.cwd().readFileAlloc(allocator, path, 1024 * 1024);
    var hash: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data, &hash, .{});
    const actual = std.fmt.bytesToHex(hash, .lower);
    if (!std.mem.eql(u8, actual[0..], meta.manifest_digest)) {
        return error.ManifestMismatch;
    }
    try std.io.getStdOut().writer().print(
        "manifest=ok target={s} digest={s} route_count={d} route_weight_total={d} capability_count={d}\n",
        .{ meta.target, meta.digest, meta.route_count, meta.route_weight_total, meta.capability_count },
    );
}

pub fn main() !void {
    var args = std.process.args();
    _ = args.next();
    const command = args.next() orelse return error.MissingCommand;
    if (std.mem.eql(u8, command, "--build-info")) {
        if (args.next() != null) return error.InvalidCommand;
        return printInfo();
    }
    if (std.mem.eql(u8, command, "--audit-manifest")) {
        const path = args.next() orelse return error.MissingPath;
        if (args.next() != null) return error.InvalidCommand;
        return auditManifest(path);
    }
    return error.InvalidCommand;
}
