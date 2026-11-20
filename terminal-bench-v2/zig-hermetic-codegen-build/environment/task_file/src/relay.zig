const std = @import("std");
const meta = @import("build_meta");

pub fn main() !void {
    var args = std.process.args();
    _ = args.next();
    const command = args.next() orelse return error.MissingCommand;
    if (!std.mem.eql(u8, command, "--build-info") or args.next() != null) {
        return error.InvalidCommand;
    }
    try std.io.getStdOut().writer().print(
        "component=relay role={s} priority={d} revision={s} channel={s} schema={d} target={s} profile={s} epoch={d} link_mode={s} digest={s} route_count={d} route_weight_total={d} capability_count={d} manifest_digest={s}\n",
        .{ meta.relay_role, meta.relay_priority, meta.revision, meta.channel, meta.schema, meta.target, meta.profile, meta.epoch, meta.link_mode, meta.digest, meta.route_count, meta.route_weight_total, meta.capability_count, meta.manifest_digest },
    );
}
