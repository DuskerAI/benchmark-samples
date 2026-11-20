const std = @import("std");
const Value = std.json.Value;

const Reason = enum(u8) {
    malformed = 0,
    bad_release_signature = 1,
    bad_checkpoint_signature = 2,
    insufficient_witness_quorum = 3,
    history_mismatch = 4,
    split_view = 5,
    replay = 6,
};

const Release = struct {
    project: []const u8 = "",
    version: []const u8 = "",
    artifact_sha256: []const u8 = "",
    sequence: i64 = 0,
    signer: []const u8 = "",
    statement_sig: []const u8 = "",
};

const LogInfo = struct {
    log_id: []const u8 = "",
    prev_size: i64 = 0,
    prev_root_hash: []const u8 = "",
    tree_size: i64 = 0,
    root_hash: []const u8 = "",
    checkpoint_sig: []const u8 = "",
};

const Entry = struct {
    index: i64,
    leaf_hash: []const u8,
};

fn addReason(reasons: *[7]bool, reason: Reason) void {
    reasons[@intFromEnum(reason)] = true;
}

fn hasReasons(reasons: *const [7]bool) bool {
    for (reasons) |present| {
        if (present) return true;
    }
    return false;
}

fn isHexDigest(s: []const u8) bool {
    if (s.len != 64) return false;
    for (s) |c| {
        if (!((c >= '0' and c <= '9') or (c >= 'a' and c <= 'f'))) return false;
    }
    return true;
}

fn getObject(v: Value, key: []const u8, reasons: *[7]bool, critical: *bool) ?Value {
    if (v != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    }
    const child = v.object.get(key) orelse {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    };
    if (child != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    }
    return child;
}

fn getArray(v: Value, key: []const u8, reasons: *[7]bool, critical: *bool) ?Value {
    if (v != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    }
    const child = v.object.get(key) orelse {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    };
    if (child != .array) {
        addReason(reasons, .malformed);
        critical.* = true;
        return null;
    }
    return child;
}

fn getString(v: Value, key: []const u8, reasons: *[7]bool, critical: *bool, hex: bool) []const u8 {
    if (v != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return "";
    }
    const child = v.object.get(key) orelse {
        addReason(reasons, .malformed);
        critical.* = true;
        return "";
    };
    if (child != .string) {
        addReason(reasons, .malformed);
        critical.* = true;
        return "";
    }
    if (hex and !isHexDigest(child.string)) {
        addReason(reasons, .malformed);
        critical.* = true;
        return "";
    }
    return child.string;
}

fn getInt(v: Value, key: []const u8, reasons: *[7]bool, critical: *bool) i64 {
    if (v != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return 0;
    }
    const child = v.object.get(key) orelse {
        addReason(reasons, .malformed);
        critical.* = true;
        return 0;
    };
    if (child != .integer) {
        addReason(reasons, .malformed);
        critical.* = true;
        return 0;
    }
    if (child.integer < 0) {
        addReason(reasons, .malformed);
        critical.* = true;
        return 0;
    }
    return child.integer;
}

fn readJson(allocator: std.mem.Allocator, path: []const u8) !std.json.Parsed(Value) {
    const data = try std.fs.cwd().readFileAlloc(allocator, path, 4 * 1024 * 1024);
    defer allocator.free(data);
    return std.json.parseFromSlice(Value, allocator, data, .{});
}

fn putStringMap(map: *std.StringHashMap([]const u8), obj: Value, reasons: *[7]bool, critical: *bool) !void {
    if (obj != .object) {
        addReason(reasons, .malformed);
        critical.* = true;
        return;
    }
    var it = obj.object.iterator();
    while (it.next()) |entry| {
        if (entry.value_ptr.* != .string) {
            addReason(reasons, .malformed);
            critical.* = true;
            continue;
        }
        try map.put(entry.key_ptr.*, entry.value_ptr.string);
    }
}

fn sha256Hex(allocator: std.mem.Allocator, data: []const u8) ![]u8 {
    var digest: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(data, &digest, .{});
    return std.fmt.allocPrint(allocator, "{s}", .{std.fmt.fmtSliceHexLower(&digest)});
}

fn releaseTranscript(allocator: std.mem.Allocator, rel: Release) ![]const u8 {
    return std.fmt.allocPrint(
        allocator,
        "minisign-release-v1\nartifact_sha256={s}\nproject={s}\nsequence={d}\nversion={s}\n",
        .{ rel.artifact_sha256, rel.project, rel.sequence, rel.version },
    );
}

fn signature(allocator: std.mem.Allocator, key: []const u8, transcript: []const u8) ![]const u8 {
    const payload = try std.fmt.allocPrint(allocator, "local-sig-v1\n{s}\n{s}", .{ key, transcript });
    defer allocator.free(payload);
    return sha256Hex(allocator, payload);
}

fn releaseLeaf(allocator: std.mem.Allocator, rel: Release) ![]const u8 {
    const transcript = try releaseTranscript(allocator, rel);
    defer allocator.free(transcript);
    const payload = try std.fmt.allocPrint(allocator, "leaf-v1\n{s}", .{transcript});
    defer allocator.free(payload);
    return sha256Hex(allocator, payload);
}

fn checkpointTranscript(allocator: std.mem.Allocator, log: LogInfo) ![]const u8 {
    return std.fmt.allocPrint(
        allocator,
        "checkpoint-v1\nlog_id={s}\nprev_root_hash={s}\nprev_size={d}\nroot_hash={s}\ntree_size={d}\n",
        .{ log.log_id, log.prev_root_hash, log.prev_size, log.root_hash, log.tree_size },
    );
}

fn witnessTranscript(allocator: std.mem.Allocator, log: LogInfo, witness_id: []const u8) ![]const u8 {
    return std.fmt.allocPrint(
        allocator,
        "witness-v1\nlog_id={s}\nroot_hash={s}\ntree_size={d}\nwitness={s}\n",
        .{ log.log_id, log.root_hash, log.tree_size, witness_id },
    );
}

fn nodeFold(allocator: std.mem.Allocator, root: []const u8, leaf: []const u8) ![]u8 {
    const payload = try std.fmt.allocPrint(allocator, "node-v1\n{s}\n{s}\n", .{ root, leaf });
    defer allocator.free(payload);
    return sha256Hex(allocator, payload);
}

fn bundleId(allocator: std.mem.Allocator, rel_leaf: []const u8, log: LogInfo) ![]const u8 {
    const payload = try std.fmt.allocPrint(
        allocator,
        "bundle-v1\n{s}\n{s}\n{d}\n{s}\n",
        .{ rel_leaf, log.log_id, log.tree_size, log.root_hash },
    );
    defer allocator.free(payload);
    return sha256Hex(allocator, payload);
}

fn entryLessThan(_: void, a: Entry, b: Entry) bool {
    return a.index < b.index;
}

fn stringLessThan(_: void, a: []const u8, b: []const u8) bool {
    return std.mem.lessThan(u8, a, b);
}

fn writeJsonString(writer: anytype, s: []const u8) !void {
    try writer.writeByte('"');
    for (s) |c| {
        switch (c) {
            '"' => try writer.writeAll("\\\""),
            '\\' => try writer.writeAll("\\\\"),
            '\n' => try writer.writeAll("\\n"),
            '\r' => try writer.writeAll("\\r"),
            '\t' => try writer.writeAll("\\t"),
            else => try writer.writeByte(c),
        }
    }
    try writer.writeByte('"');
}

fn writeStringArray(writer: anytype, ids: [][]const u8) !void {
    try writer.writeByte('[');
    for (ids, 0..) |id, i| {
        if (i != 0) try writer.writeAll(", ");
        try writeJsonString(writer, id);
    }
    try writer.writeByte(']');
}

fn writeReasons(writer: anytype, reasons: *const [7]bool) !void {
    const names = [_][]const u8{ "malformed", "bad_release_signature", "bad_checkpoint_signature", "insufficient_witness_quorum", "history_mismatch", "split_view", "replay" };
    try writer.writeByte('[');
    var first = true;
    for (names, 0..) |name, i| {
        if (!reasons[i]) continue;
        if (!first) try writer.writeAll(", ");
        first = false;
        try writeJsonString(writer, name);
    }
    try writer.writeByte(']');
}

fn writeReport(
    allocator: std.mem.Allocator,
    out_path: []const u8,
    rel: Release,
    log: LogInfo,
    quorum: i64,
    valid_ids: [][]const u8,
    reasons: *const [7]bool,
) !void {
    const accepted = !hasReasons(reasons);
    var bid: []const u8 = "";
    var allocated_bid = false;
    if (isHexDigest(rel.artifact_sha256) and log.log_id.len != 0 and isHexDigest(log.root_hash)) {
        const rel_leaf = try releaseLeaf(allocator, rel);
        defer allocator.free(rel_leaf);
        bid = try bundleId(allocator, rel_leaf, log);
        allocated_bid = true;
    }
    defer if (allocated_bid) allocator.free(bid);
    const file = try std.fs.cwd().createFile(out_path, .{ .truncate = true });
    defer file.close();
    var writer = file.writer();

    try writer.writeAll("{\n  \"accepted\": ");
    try writer.writeAll(if (accepted) "true" else "false");
    try writer.writeAll(",\n  \"status\": ");
    try writeJsonString(writer, if (accepted) "accepted" else "rejected");
    try writer.writeAll(",\n  \"bundle_id\": ");
    try writeJsonString(writer, bid);
    try writer.writeAll(",\n  \"release\": {\n    \"project\": ");
    try writeJsonString(writer, rel.project);
    try writer.writeAll(",\n    \"version\": ");
    try writeJsonString(writer, rel.version);
    try writer.print(",\n    \"sequence\": {d},\n    \"artifact_sha256\": ", .{rel.sequence});
    try writeJsonString(writer, rel.artifact_sha256);
    try writer.writeAll("\n  },\n  \"log\": {\n    \"log_id\": ");
    try writeJsonString(writer, log.log_id);
    try writer.print(",\n    \"previous_size\": {d},\n    \"tree_size\": {d},\n    \"root_hash\": ", .{ log.prev_size, log.tree_size });
    try writeJsonString(writer, log.root_hash);
    try writer.writeAll("\n  },\n  \"witness_quorum\": {\n");
    try writer.print("    \"required\": {d},\n    \"valid\": {d},\n    \"accepted_ids\": ", .{ quorum, valid_ids.len });
    try writeStringArray(writer, valid_ids);
    try writer.writeAll("\n  },\n  \"reasons\": ");
    try writeReasons(writer, reasons);
    try writer.writeAll("\n}\n");
}

fn parseRelease(v: Value, reasons: *[7]bool, critical: *bool) Release {
    return .{
        .project = getString(v, "project", reasons, critical, false),
        .version = getString(v, "version", reasons, critical, false),
        .artifact_sha256 = getString(v, "artifact_sha256", reasons, critical, true),
        .sequence = getInt(v, "sequence", reasons, critical),
        .signer = getString(v, "signer", reasons, critical, false),
        .statement_sig = getString(v, "statement_sig", reasons, critical, true),
    };
}

fn parseLog(v: Value, reasons: *[7]bool, critical: *bool) LogInfo {
    return .{
        .log_id = getString(v, "log_id", reasons, critical, false),
        .prev_size = getInt(v, "prev_size", reasons, critical),
        .prev_root_hash = getString(v, "prev_root_hash", reasons, critical, true),
        .tree_size = getInt(v, "tree_size", reasons, critical),
        .root_hash = getString(v, "root_hash", reasons, critical, true),
        .checkpoint_sig = getString(v, "checkpoint_sig", reasons, critical, true),
    };
}

fn run() !u8 {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var args = try std.process.argsWithAllocator(allocator);
    defer args.deinit();
    _ = args.next();
    var trust_path: ?[]const u8 = null;
    var bundle_path: ?[]const u8 = null;
    var state_path: ?[]const u8 = null;
    var out_path: ?[]const u8 = null;
    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "--trust")) trust_path = args.next() else if (std.mem.eql(u8, arg, "--bundle")) bundle_path = args.next() else if (std.mem.eql(u8, arg, "--state")) state_path = args.next() else if (std.mem.eql(u8, arg, "--out")) out_path = args.next();
    }
    const out = out_path orelse return 2;
    const trust_p = trust_path orelse return 2;
    const bundle_p = bundle_path orelse return 2;
    const state_p = state_path orelse return 2;

    var reasons = [_]bool{false} ** 7;
    var critical_malformed = false;
    var release = Release{};
    var log = LogInfo{};
    var quorum: i64 = 0;
    var valid_ids = std.ArrayList([]const u8).init(allocator);
    defer valid_ids.deinit();

    var trust_parsed = readJson(allocator, trust_p) catch {
        addReason(&reasons, .malformed);
        try writeReport(allocator, out, release, log, quorum, valid_ids.items, &reasons);
        return 1;
    };
    defer trust_parsed.deinit();
    var bundle_parsed = readJson(allocator, bundle_p) catch {
        addReason(&reasons, .malformed);
        try writeReport(allocator, out, release, log, quorum, valid_ids.items, &reasons);
        return 1;
    };
    defer bundle_parsed.deinit();
    var state_parsed = readJson(allocator, state_p) catch {
        addReason(&reasons, .malformed);
        try writeReport(allocator, out, release, log, quorum, valid_ids.items, &reasons);
        return 1;
    };
    defer state_parsed.deinit();

    var release_keys = std.StringHashMap([]const u8).init(allocator);
    defer release_keys.deinit();
    var log_keys = std.StringHashMap([]const u8).init(allocator);
    defer log_keys.deinit();
    var witness_keys = std.StringHashMap([]const u8).init(allocator);
    defer witness_keys.deinit();

    const trust_root = trust_parsed.value;
    const bundle_root = bundle_parsed.value;
    const state_root = state_parsed.value;

    if (getObject(trust_root, "release_keys", &reasons, &critical_malformed)) |obj| try putStringMap(&release_keys, obj, &reasons, &critical_malformed);
    if (getObject(trust_root, "log_keys", &reasons, &critical_malformed)) |obj| try putStringMap(&log_keys, obj, &reasons, &critical_malformed);
    if (getObject(trust_root, "witness_keys", &reasons, &critical_malformed)) |obj| try putStringMap(&witness_keys, obj, &reasons, &critical_malformed);
    quorum = getInt(trust_root, "quorum", &reasons, &critical_malformed);

    const release_v = getObject(bundle_root, "release", &reasons, &critical_malformed) orelse Value{ .object = std.json.ObjectMap.init(allocator) };
    const log_v = getObject(bundle_root, "log", &reasons, &critical_malformed) orelse Value{ .object = std.json.ObjectMap.init(allocator) };
    release = parseRelease(release_v, &reasons, &critical_malformed);
    log = parseLog(log_v, &reasons, &critical_malformed);

    var entries = std.ArrayList(Entry).init(allocator);
    defer entries.deinit();
    var saw_release_leaf = false;
    const rel_leaf = try releaseLeaf(allocator, release);
    defer allocator.free(rel_leaf);

    if (getArray(bundle_root, "entries", &reasons, &critical_malformed)) |entries_v| {
        for (entries_v.array.items) |item| {
            var entry_critical = false;
            const idx = getInt(item, "index", &reasons, &entry_critical);
            const leaf = getString(item, "leaf_hash", &reasons, &entry_critical, true);
            if (entry_critical) {
                critical_malformed = true;
            } else {
                if (idx < log.prev_size or idx >= log.tree_size) addReason(&reasons, .malformed);
                if (std.mem.eql(u8, leaf, rel_leaf)) saw_release_leaf = true;
                try entries.append(.{ .index = idx, .leaf_hash = leaf });
            }
        }
    }

    var state_tree_size: i64 = 0;
    var state_root_hash: []const u8 = "";
    if (getObject(state_root, "logs", &reasons, &critical_malformed)) |logs_obj| {
        if (logs_obj.object.get(log.log_id)) |state_log| {
            state_tree_size = getInt(state_log, "tree_size", &reasons, &critical_malformed);
            state_root_hash = getString(state_log, "root_hash", &reasons, &critical_malformed, true);
        } else {
            addReason(&reasons, .malformed);
            critical_malformed = true;
        }
    }

    if (!critical_malformed) {
        const rel_key = release_keys.get(release.signer) orelse blk: {
            addReason(&reasons, .malformed);
            addReason(&reasons, .bad_release_signature);
            break :blk "";
        };
        const rel_tx = try releaseTranscript(allocator, release);
        defer allocator.free(rel_tx);
        const rel_sig = try signature(allocator, rel_key, rel_tx);
        defer allocator.free(rel_sig);
        if (!std.mem.eql(u8, rel_sig, release.statement_sig)) addReason(&reasons, .bad_release_signature);

        const log_key = log_keys.get(log.log_id) orelse blk: {
            addReason(&reasons, .malformed);
            addReason(&reasons, .bad_checkpoint_signature);
            break :blk "";
        };
        const cp_tx = try checkpointTranscript(allocator, log);
        defer allocator.free(cp_tx);
        const cp_sig = try signature(allocator, log_key, cp_tx);
        defer allocator.free(cp_sig);
        if (!std.mem.eql(u8, cp_sig, log.checkpoint_sig)) addReason(&reasons, .bad_checkpoint_signature);

        var seen_witnesses = std.StringHashMap(void).init(allocator);
        defer seen_witnesses.deinit();
        if (getArray(bundle_root, "witnesses", &reasons, &critical_malformed)) |wits| {
            for (wits.array.items) |wit| {
                var wit_critical = false;
                const wid = getString(wit, "id", &reasons, &wit_critical, false);
                const w_tree = getInt(wit, "tree_size", &reasons, &wit_critical);
                const w_root = getString(wit, "root_hash", &reasons, &wit_critical, true);
                const w_sig = getString(wit, "sig", &reasons, &wit_critical, true);
                if (wit_critical) continue;
                if (seen_witnesses.contains(wid)) {
                    addReason(&reasons, .malformed);
                    continue;
                }
                try seen_witnesses.put(wid, {});
                const w_key = witness_keys.get(wid) orelse {
                    addReason(&reasons, .malformed);
                    continue;
                };
                if (w_tree == log.tree_size and std.mem.eql(u8, w_root, log.root_hash)) {
                    const w_tx = try witnessTranscript(allocator, log, wid);
                    defer allocator.free(w_tx);
                    const expected = try signature(allocator, w_key, w_tx);
                    defer allocator.free(expected);
                    if (std.mem.eql(u8, expected, w_sig)) try valid_ids.append(wid);
                }
            }
        }
        std.mem.sort([]const u8, valid_ids.items, {}, stringLessThan);
        if (valid_ids.items.len < @as(usize, @intCast(quorum))) addReason(&reasons, .insufficient_witness_quorum);

        if (log.tree_size < state_tree_size) addReason(&reasons, .history_mismatch);
        if (log.tree_size == state_tree_size and !std.mem.eql(u8, log.root_hash, state_root_hash)) addReason(&reasons, .split_view);
        if (log.prev_size != state_tree_size or !std.mem.eql(u8, log.prev_root_hash, state_root_hash)) addReason(&reasons, .history_mismatch);

        std.mem.sort(Entry, entries.items, {}, entryLessThan);
        var exact_cover = false;
        const extension_count = log.tree_size - log.prev_size;
        if (extension_count < 0) {
            addReason(&reasons, .history_mismatch);
        } else {
            exact_cover = entries.items.len == @as(usize, @intCast(extension_count));
            for (entries.items, 0..) |entry, i| {
                const expected_index = log.prev_size + @as(i64, @intCast(i));
                if (entry.index != expected_index) exact_cover = false;
                if (i > 0 and entry.index == entries.items[i - 1].index) addReason(&reasons, .malformed);
            }
        }
        if (!exact_cover) addReason(&reasons, .history_mismatch);
        var folded = try allocator.dupe(u8, log.prev_root_hash);
        defer allocator.free(folded);
        if (extension_count >= 0) {
            for (entries.items) |entry| {
                const next = try nodeFold(allocator, folded, entry.leaf_hash);
                allocator.free(folded);
                folded = next;
            }
        }
        if (!std.mem.eql(u8, folded, log.root_hash) or !saw_release_leaf) addReason(&reasons, .history_mismatch);

        if (getArray(state_root, "seen_releases", &reasons, &critical_malformed)) |seen_rels| {
            for (seen_rels.array.items) |seen| {
                var seen_critical = false;
                const sp = getString(seen, "project", &reasons, &seen_critical, false);
                const sv = getString(seen, "version", &reasons, &seen_critical, false);
                const ss = getInt(seen, "sequence", &reasons, &seen_critical);
                const sa = getString(seen, "artifact_sha256", &reasons, &seen_critical, true);
                if (seen_critical) continue;
                if (std.mem.eql(u8, sp, release.project) and (std.mem.eql(u8, sv, release.version) or std.mem.eql(u8, sa, release.artifact_sha256) or ss >= release.sequence)) {
                    addReason(&reasons, .replay);
                }
            }
        }
    }

    try writeReport(allocator, out, release, log, quorum, valid_ids.items, &reasons);
    return if (hasReasons(&reasons)) 1 else 0;
}

pub fn main() !void {
    const code = try run();
    std.process.exit(code);
}
