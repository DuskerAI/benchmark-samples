const std = @import("std");

fn fnv64WithSalt(value: []const u8, salt: u32) u64 {
    var h: u64 = 1469598103934665603 ^ @as(u64, salt);
    for (value, 0..) |byte, index| {
        h ^= byte;
        h *%= 1099511628211;
        h +%= @as(u64, salt) << @intCast(index % 13);
    }
    return h;
}

pub fn main() !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const args = try std.process.argsAlloc(arena.allocator());
    if (args.len != 12) return error.InvalidArgument;

    const target = args[1];
    const name = args[2];
    const profile = args[3];
    const channel = args[4];
    const tier = args[5];
    const abi = try std.fmt.parseInt(u32, args[6][2..], 16);
    const packet_limit = try std.fmt.parseInt(u32, args[7], 10);
    const feature_mask = try std.fmt.parseInt(u32, args[8][2..], 16);
    const codec_salt = try std.fmt.parseInt(u32, args[9], 10);
    const audit_salt = try std.fmt.parseInt(u32, args[10], 10);
    const dependency_hash = try std.fmt.parseInt(u64, args[11][2..], 16);

    const layout = fnv64WithSalt(target, codec_salt) ^
        fnv64WithSalt(name, codec_salt) ^
        fnv64WithSalt(profile, codec_salt) ^
        fnv64WithSalt(channel, codec_salt) ^
        fnv64WithSalt(tier, codec_salt) ^
        dependency_hash ^
        @as(u64, abi) ^
        (@as(u64, packet_limit) << 8) ^
        (@as(u64, feature_mask) << 32) ^
        (@as(u64, audit_salt) << 17);
    const release_hash = layout ^ 0x9e3779b97f4a7c15;
    try std.io.getStdOut().writer().print("0x{x:0>16}\n", .{release_hash});
}