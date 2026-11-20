const std = @import("std");

const Values = struct {
    revision: []const u8,
    channel: []const u8,
    schema: u32,
};

const Component = struct {
    name: []const u8,
    role: []const u8,
    priority: u32,
};

const Components = struct {
    relay: Component,
    inspect: Component,
    first: Component,
    second: Component,
};

const TargetProfile = struct {
    target: []const u8,
    profile: []const u8,
    epoch: u32,
    link_mode: []const u8,
};

fn parseConfig(data: []const u8) !Values {
    var revision: ?[]const u8 = null;
    var channel: ?[]const u8 = null;
    var schema: ?u32 = null;
    var lines = std.mem.splitScalar(u8, data, '\n');
    while (lines.next()) |raw_line| {
        const line = std.mem.trim(u8, raw_line, " \t\r");
        if (line.len == 0) continue;
        const split = std.mem.indexOfScalar(u8, line, '=') orelse return error.BadConfig;
        const key = std.mem.trim(u8, line[0..split], " \t");
        const value = std.mem.trim(u8, line[split + 1 ..], " \t");
        if (std.mem.eql(u8, key, "revision")) {
            revision = value;
        } else if (std.mem.eql(u8, key, "channel")) {
            channel = value;
        } else if (std.mem.eql(u8, key, "schema")) {
            schema = try std.fmt.parseInt(u32, value, 10);
        } else return error.BadConfig;
    }
    return .{
        .revision = revision orelse return error.BadConfig,
        .channel = channel orelse return error.BadConfig,
        .schema = schema orelse return error.BadConfig,
    };
}

fn parseComponent(line: []const u8) !Component {
    var name: ?[]const u8 = null;
    var role: ?[]const u8 = null;
    var priority: ?u32 = null;
    var fields = std.mem.splitScalar(u8, line, ' ');
    while (fields.next()) |field| {
        if (field.len == 0) continue;
        const split = std.mem.indexOfScalar(u8, field, '=') orelse return error.BadConfig;
        const key = field[0..split];
        const value = field[split + 1 ..];
        if (std.mem.eql(u8, key, "component")) {
            name = value;
        } else if (std.mem.eql(u8, key, "role")) {
            role = value;
        } else if (std.mem.eql(u8, key, "priority")) {
            priority = try std.fmt.parseInt(u32, value, 10);
        } else return error.BadConfig;
    }
    return .{
        .name = name orelse return error.BadConfig,
        .role = role orelse return error.BadConfig,
        .priority = priority orelse return error.BadConfig,
    };
}

fn parseComponents(data: []const u8) !Components {
    var relay: ?Component = null;
    var inspect: ?Component = null;
    var first: ?Component = null;
    var second: ?Component = null;
    var lines = std.mem.splitScalar(u8, data, '\n');
    while (lines.next()) |raw_line| {
        const line = std.mem.trim(u8, raw_line, " \t\r");
        if (line.len == 0) continue;
        const component = try parseComponent(line);
        if (first == null) {
            first = component;
        } else if (second == null) {
            second = component;
        } else return error.BadConfig;
        if (std.mem.eql(u8, component.name, "relay")) {
            relay = component;
        } else if (std.mem.eql(u8, component.name, "relay-inspect")) {
            inspect = component;
        } else return error.BadConfig;
    }
    return .{
        .relay = relay orelse return error.BadConfig,
        .inspect = inspect orelse return error.BadConfig,
        .first = first orelse return error.BadConfig,
        .second = second orelse return error.BadConfig,
    };
}

fn parseTarget(line: []const u8) !TargetProfile {
    var target: ?[]const u8 = null;
    var profile: ?[]const u8 = null;
    var epoch: ?u32 = null;
    var link_mode: ?[]const u8 = null;
    var fields = std.mem.splitScalar(u8, line, ' ');
    while (fields.next()) |field| {
        if (field.len == 0) continue;
        const split = std.mem.indexOfScalar(u8, field, '=') orelse return error.BadConfig;
        const key = field[0..split];
        const value = field[split + 1 ..];
        if (std.mem.eql(u8, key, "target")) {
            target = value;
        } else if (std.mem.eql(u8, key, "profile")) {
            profile = value;
        } else if (std.mem.eql(u8, key, "epoch")) {
            epoch = try std.fmt.parseInt(u32, value, 10);
        } else if (std.mem.eql(u8, key, "link_mode")) {
            link_mode = value;
        } else return error.BadConfig;
    }
    return .{
        .target = target orelse return error.BadConfig,
        .profile = profile orelse return error.BadConfig,
        .epoch = epoch orelse return error.BadConfig,
        .link_mode = link_mode orelse return error.BadConfig,
    };
}

fn parseTargetProfile(data: []const u8, requested: []const u8) !TargetProfile {
    _ = requested;
    var first: ?TargetProfile = null;
    var lines = std.mem.splitScalar(u8, data, '\n');
    while (lines.next()) |raw_line| {
        const line = std.mem.trim(u8, raw_line, " \t\r");
        if (line.len == 0) continue;
        const profile = try parseTarget(line);
        if (first == null) first = profile;
    }
    return first orelse return error.BadConfig;
}

pub fn main() !void {
    const allocator = std.heap.page_allocator;
    const args = try std.process.argsAlloc(allocator);
    if (args.len != 10) return error.BadArguments;

    const data = try std.fs.cwd().readFileAlloc(allocator, args[1], 1024 * 1024);
    const components_data = try std.fs.cwd().readFileAlloc(allocator, args[2], 1024 * 1024);
    const targets_data = try std.fs.cwd().readFileAlloc(allocator, args[3], 1024 * 1024);
    _ = try std.fs.cwd().readFileAlloc(allocator, args[4], 1024 * 1024);
    _ = try std.fs.cwd().readFileAlloc(allocator, args[5], 1024 * 1024);
    _ = try std.fs.cwd().readFileAlloc(allocator, args[6], 1024 * 1024);
    const values = try parseConfig(data);
    const components = try parseComponents(components_data);
    const target_profile = try parseTargetProfile(targets_data, args[7]);
    const normalized = try std.fmt.allocPrint(
        allocator,
        "revision={s}\nchannel={s}\nschema={d}\n",
        .{ values.revision, values.channel, values.schema },
    );
    var hash: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(normalized, &hash, .{});
    const digest = std.fmt.bytesToHex(hash, .lower);

    const manifest = try std.fmt.allocPrint(
        allocator,
        "revision={s}\nchannel={s}\nschema={d}\ntarget={s}\nprofile={s}\nepoch={d}\nlink_mode={s}\ndigest={s}\nroute_count=0\nroute_weight_total=0\ncapability_count=0\ncomponent={s} role={s} priority={d}\ncomponent={s} role={s} priority={d}\n",
        .{ values.revision, values.channel, values.schema, args[7], target_profile.profile, target_profile.epoch, target_profile.link_mode, digest, components.first.name, components.first.role, components.first.priority, components.second.name, components.second.role, components.second.priority },
    );
    var manifest_hash: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(manifest, &manifest_hash, .{});
    const manifest_digest = std.fmt.bytesToHex(manifest_hash, .lower);

    const source = try std.fmt.allocPrint(
        allocator,
        "pub const revision = \"{s}\";\npub const channel = \"{s}\";\npub const schema: u32 = {d};\npub const target = \"{s}\";\npub const profile = \"{s}\";\npub const epoch: u32 = {d};\npub const link_mode = \"{s}\";\npub const digest = \"{s}\";\npub const relay_role = \"{s}\";\npub const relay_priority: u32 = {d};\npub const inspect_role = \"{s}\";\npub const inspect_priority: u32 = {d};\npub const route_count: u32 = 0;\npub const route_weight_total: u32 = 0;\npub const capability_count: u32 = 0;\npub const manifest_digest = \"{s}\";\n",
        .{ values.revision, values.channel, values.schema, args[7], target_profile.profile, target_profile.epoch, target_profile.link_mode, digest, components.relay.role, components.relay.priority, components.inspect.role, components.inspect.priority, manifest_digest },
    );
    try std.fs.cwd().writeFile(.{ .sub_path = args[8], .data = source });
    try std.fs.cwd().writeFile(.{ .sub_path = args[9], .data = manifest });
}
