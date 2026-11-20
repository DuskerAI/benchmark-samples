const std = @import("std");
const config = @import("config.zig");

extern fn relkit_abi() u32;
extern fn relkit_packet_limit() u32;
extern fn relkit_feature_mask() u32;
extern fn relkit_codec_salt() u32;
extern fn relkit_dependency_hash() u64;
extern fn relkit_release_hash() u64;
extern fn relkit_policy_fingerprint() u64;

pub fn main() !void {
    var stdout = std.io.getStdOut().writer();
    try stdout.print("target={s}\n", .{config.target_name});
    try stdout.print("profile={s}\n", .{config.profile});
    try stdout.print("abi=0x{x:0>8}\n", .{relkit_abi()});
    try stdout.print("packet_limit={d}\n", .{relkit_packet_limit()});
    try stdout.print("feature_mask=0x{x:0>8}\n", .{relkit_feature_mask()});
    try stdout.print("codec_salt={d}\n", .{relkit_codec_salt()});
    try stdout.print("channel={s}\n", .{config.channel});
    try stdout.print("tier={s}\n", .{config.tier});
    try stdout.print("audit_salt={d}\n", .{config.audit_salt});
    try stdout.print("dependency_hash=0x{x:0>16}\n", .{relkit_dependency_hash()});
    try stdout.print("release_hash=0x{x:0>16}\n", .{relkit_release_hash()});
    try stdout.print("policy_fingerprint=0x{x:0>16}\n", .{relkit_policy_fingerprint()});
}
