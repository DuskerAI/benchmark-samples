#!/bin/bash
set -euo pipefail

cat > /app/relay/build.zig <<'EOF'
const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const triple = target.query.zigTriple(b.allocator) catch @panic("out of memory");

    const run_generator = b.addSystemCommand(&.{ "python3", "tools/meta_gen.py" });
    run_generator.addFileArg(b.path("config/release.cfg"));
    run_generator.addFileArg(b.path("config/components.cfg"));
    run_generator.addFileArg(b.path("config/targets.cfg"));
    run_generator.addFileArg(b.path("config/routes.cfg"));
    run_generator.addFileArg(b.path("config/segments.cfg"));
    run_generator.addFileArg(b.path("config/capabilities.cfg"));
    run_generator.addFileArg(b.path("config/policies.cfg"));
    run_generator.addFileArg(b.path("config/exclusions.cfg"));
    run_generator.addFileArg(b.path("config/handoffs.cfg"));
    run_generator.addFileArg(b.path("config/anchors.cfg"));
    run_generator.addFileArg(b.path("config/guards.cfg"));
    run_generator.addFileArg(b.path("config/attestations.cfg"));
    run_generator.addArg(triple);
    const generated_source = run_generator.addOutputFileArg("build_meta.zig");
    const generated_manifest = run_generator.addOutputFileArg("build-meta.txt");

    const relay = b.addExecutable(.{ .name = "relay", .root_source_file = b.path("src/relay.zig"), .target = target, .optimize = optimize, .strip = true });
    relay.root_module.addImport("build_meta", b.createModule(.{ .root_source_file = generated_source }));
    const inspect = b.addExecutable(.{ .name = "relay-inspect", .root_source_file = b.path("src/inspect.zig"), .target = target, .optimize = optimize, .strip = true });
    inspect.root_module.addImport("build_meta", b.createModule(.{ .root_source_file = generated_source }));

    const install_relay = b.addInstallFileWithDir(relay.getEmittedBin(), .prefix, b.fmt("{s}/bin/relay", .{triple}));
    const install_inspect = b.addInstallFileWithDir(inspect.getEmittedBin(), .prefix, b.fmt("{s}/bin/relay-inspect", .{triple}));
    const install_manifest = b.addInstallFileWithDir(generated_manifest, .prefix, b.fmt("{s}/share/relay/build-meta.txt", .{triple}));
    const bundle = b.step("bundle", "Build and install the release bundle");
    bundle.dependOn(&install_relay.step);
    bundle.dependOn(&install_inspect.step);
    bundle.dependOn(&install_manifest.step);
}
EOF

cat > /app/relay/src/relay.zig <<'EOF'
const std = @import("std");
const meta = @import("build_meta");

pub fn main() !void {
    var args = std.process.args();
    _ = args.next();
    const command = args.next() orelse return error.MissingCommand;
    if (!std.mem.eql(u8, command, "--build-info") or args.next() != null) return error.InvalidCommand;
    try std.io.getStdOut().writer().print(
        "component=relay role={s} priority={d} revision={s} channel={s} schema={d} target={s} profile={s} epoch={d} link_mode={s} digest={s} route_count={d} route_weight_total={d} segment_count={d} capacity_total={d} capability_count={d} policy_count={d} handoff_count={d} handoff_weight_total={d} anchor_count={d} anchor_weight_total={d} guard_count={d} attestation_count={d} exclusion_count={d} policy_score={d} guard_strength_total={d} risk_total={d} manifest_digest={s}\n",
        .{ meta.relay_role, meta.relay_priority, meta.revision, meta.channel, meta.schema, meta.target, meta.profile, meta.epoch, meta.link_mode, meta.digest, meta.route_count, meta.route_weight_total, meta.segment_count, meta.capacity_total, meta.capability_count, meta.policy_count, meta.handoff_count, meta.handoff_weight_total, meta.anchor_count, meta.anchor_weight_total, meta.guard_count, meta.attestation_count, meta.exclusion_count, meta.policy_score, meta.guard_strength_total, meta.risk_total, meta.manifest_digest },
    );
}
EOF

cat > /app/relay/src/inspect.zig <<'EOF'
const std = @import("std");
const meta = @import("build_meta");

fn readFile(path: []const u8) ![]u8 {
    const allocator = std.heap.page_allocator;
    if (std.fs.path.isAbsolute(path)) {
        var file = try std.fs.openFileAbsolute(path, .{});
        defer file.close();
        return try file.readToEndAlloc(allocator, 1024 * 1024);
    }
    return try std.fs.cwd().readFileAlloc(allocator, path, 1024 * 1024);
}

pub fn main() !void {
    var args = std.process.args();
    _ = args.next();
    const command = args.next() orelse return error.MissingCommand;
    if (std.mem.eql(u8, command, "--build-info")) {
        if (args.next() != null) return error.InvalidCommand;
        return std.io.getStdOut().writer().print(
            "component=relay-inspect role={s} priority={d} revision={s} channel={s} schema={d} target={s} profile={s} epoch={d} link_mode={s} digest={s} route_count={d} route_weight_total={d} segment_count={d} capacity_total={d} capability_count={d} policy_count={d} handoff_count={d} handoff_weight_total={d} anchor_count={d} anchor_weight_total={d} guard_count={d} attestation_count={d} exclusion_count={d} policy_score={d} guard_strength_total={d} risk_total={d} manifest_digest={s}\n",
            .{ meta.inspect_role, meta.inspect_priority, meta.revision, meta.channel, meta.schema, meta.target, meta.profile, meta.epoch, meta.link_mode, meta.digest, meta.route_count, meta.route_weight_total, meta.segment_count, meta.capacity_total, meta.capability_count, meta.policy_count, meta.handoff_count, meta.handoff_weight_total, meta.anchor_count, meta.anchor_weight_total, meta.guard_count, meta.attestation_count, meta.exclusion_count, meta.policy_score, meta.guard_strength_total, meta.risk_total, meta.manifest_digest },
        );
    }
    if (std.mem.eql(u8, command, "--audit-manifest")) {
        const path = args.next() orelse return error.MissingPath;
        if (args.next() != null) return error.InvalidCommand;
        const data = try readFile(path);
        var hash: [32]u8 = undefined;
        std.crypto.hash.sha2.Sha256.hash(data, &hash, .{});
        const actual = std.fmt.bytesToHex(hash, .lower);
        if (!std.mem.eql(u8, actual[0..], meta.manifest_digest)) return error.ManifestMismatch;
        return std.io.getStdOut().writer().print(
            "manifest=ok target={s} digest={s} route_count={d} route_weight_total={d} segment_count={d} capacity_total={d} capability_count={d} policy_count={d} handoff_count={d} handoff_weight_total={d} anchor_count={d} anchor_weight_total={d} guard_count={d} attestation_count={d} exclusion_count={d} policy_score={d} guard_strength_total={d} risk_total={d}\n",
            .{ meta.target, meta.digest, meta.route_count, meta.route_weight_total, meta.segment_count, meta.capacity_total, meta.capability_count, meta.policy_count, meta.handoff_count, meta.handoff_weight_total, meta.anchor_count, meta.anchor_weight_total, meta.guard_count, meta.attestation_count, meta.exclusion_count, meta.policy_score, meta.guard_strength_total, meta.risk_total },
        );
    }
    return error.InvalidCommand;
}
EOF

cat > /app/relay/tools/meta_gen.py <<'EOF'
import hashlib
import sys
from pathlib import Path

SUPPORTED = {"x86_64-linux-musl", "aarch64-linux-musl"}

def parse_record(line):
    fields = {}
    for part in line.split():
        key, sep, value = part.partition("=")
        if not sep or not key or not value or key in fields:
            raise ValueError("bad config")
        fields[key] = value
    return fields

def records(raw):
    return [parse_record(line.strip()) for line in raw.decode().splitlines() if line.strip()]

def one(raw):
    out = {}
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in out or not value:
            raise ValueError("bad release")
        out[key] = value
    if set(out) != {"revision", "channel", "schema"}:
        raise ValueError("bad release")
    int(out["schema"])
    return out

def esc(value):
    if any(ch in value for ch in ['\\', '"', '\n', '\r']):
        raise ValueError("unsupported token")
    return value

def main():
    release_p, components_p, targets_p, routes_p, segments_p, caps_p, policies_p, exclusions_p, handoffs_p, anchors_p, guards_p, attestations_p, target, source_out, manifest_out = map(Path, sys.argv[1:])
    target = str(target)
    raw = [p.read_bytes() for p in (release_p, components_p, targets_p, routes_p, segments_p, caps_p, policies_p, exclusions_p, handoffs_p, anchors_p, guards_p, attestations_p)]
    rel = one(raw[0])
    comps = records(raw[1])
    if [c.get("component") for c in comps].count("relay") != 1 or [c.get("component") for c in comps].count("relay-inspect") != 1 or len(comps) != 2:
        raise ValueError("bad components")
    comp_names = {c["component"] for c in comps}
    comp_order = {c["component"]: i for i, c in enumerate(comps)}
    relay = next(c for c in comps if c["component"] == "relay")
    inspect = next(c for c in comps if c["component"] == "relay-inspect")
    for c in comps:
        int(c["priority"])
    targets = records(raw[2])
    if {t.get("target") for t in targets} != SUPPORTED or target not in SUPPORTED:
        raise ValueError("bad target")
    profile = next(t for t in targets if t["target"] == target)
    epoch = int(profile["epoch"])
    all_routes = records(raw[3])
    for r in all_routes:
        if r["target"] not in SUPPORTED or r["component"] not in comp_names:
            raise ValueError("bad route")
        int(r["weight"])
    routes = [r for r in all_routes if r["target"] == target]
    all_segments = records(raw[4])
    segment_by_route = {}
    for s in all_segments:
        if s["target"] not in SUPPORTED or s["component"] not in comp_names:
            raise ValueError("bad segment")
        key = (s["target"], s["component"], s["lane"])
        if key in segment_by_route:
            raise ValueError("duplicate segment")
        int(s["capacity"])
        segment_by_route[key] = s
    for r in routes:
        s = segment_by_route[(r["target"], r["component"], r["lane"])]
        r["segment"] = s["segment"]
        r["capacity"] = s["capacity"]
    all_caps = records(raw[5])
    for i, c in enumerate(all_caps):
        if c["target"] not in SUPPORTED or c["component"] not in comp_names:
            raise ValueError("bad capability")
        c["_index"] = i
        int(c["ordinal"])
    caps = [c for c in all_caps if c["target"] == target]
    caps.sort(key=lambda c: (comp_order[c["component"]], int(c["ordinal"]), c["_index"]))
    route_rank = {(r["component"], r["lane"]): i for i, r in enumerate(routes)}
    cap_rank = {(c["component"], c["name"]): i for i, c in enumerate(caps)}
    route_weight = {(r["component"], r["lane"]): int(r["weight"]) for r in routes}
    cap_ordinal = {(c["component"], c["name"]): int(c["ordinal"]) for c in caps}
    policies = []
    for i, p in enumerate(records(raw[6])):
        if p["target"] not in SUPPORTED or p["component"] not in comp_names:
            raise ValueError("bad policy")
        rkey, ckey = (p["component"], p["lane"]), (p["component"], p["capability"])
        if p["target"] == target and epoch >= int(p["min_epoch"]) and rkey in route_rank and ckey in cap_rank:
            route = routes[route_rank[rkey]]
            if p["segment"] != route["segment"]:
                continue
            policies.append(p | {"_index": i})
    policies.sort(key=lambda p: (route_rank[(p["component"], p["lane"])], cap_rank[(p["component"], p["capability"])], p["_index"]))
    exclusions = []
    policy_keys = {(p["component"], p["lane"], p["capability"], p["segment"]) for p in policies}
    for i, e in enumerate(records(raw[7])):
        if e["target"] not in SUPPORTED or e["component"] not in comp_names:
            raise ValueError("bad exclusion")
        key = (e["component"], e["lane"], e["capability"], e["segment"])
        if e["target"] == target and key in policy_keys and int(e["not_before"]) <= epoch <= int(e["not_after"]):
            exclusions.append(e | {"_index": i})
    excluded = {(e["component"], e["lane"], e["capability"], e["segment"]) for e in exclusions}
    policies = [p for p in policies if (p["component"], p["lane"], p["capability"], p["segment"]) not in excluded]
    policy_rank = {(p["component"], p["lane"], p["capability"], p["segment"]): i for i, p in enumerate(policies)}
    handoffs = []
    for i, h in enumerate(records(raw[8])):
        if h["target"] not in SUPPORTED or h["component"] not in comp_names or h["peer_component"] not in comp_names:
            raise ValueError("bad handoff")
        key = (h["component"], h["lane"], h["capability"], h["segment"])
        peer_key = (h["peer_component"], h["peer_lane"], h["peer_capability"], h["peer_segment"])
        if h["target"] == target and key in policy_rank and peer_key in policy_rank and key != peer_key:
            handoffs.append(h | {"_index": i})
    handoffs.sort(key=lambda h: (policy_rank[(h["component"], h["lane"], h["capability"], h["segment"])], policy_rank[(h["peer_component"], h["peer_lane"], h["peer_capability"], h["peer_segment"])], -int(h["weight"]), h["_index"]))
    handoff_count_by_policy = {}
    handoff_weight_by_policy = {}
    for h in handoffs:
        key = (h["component"], h["lane"], h["capability"], h["segment"])
        handoff_count_by_policy[key] = handoff_count_by_policy.get(key, 0) + 1
        handoff_weight_by_policy[key] = handoff_weight_by_policy.get(key, 0) + int(h["weight"])
    anchors = []
    for i, a in enumerate(records(raw[9])):
        if a["target"] not in SUPPORTED or a["component"] not in comp_names or a["peer_component"] not in comp_names:
            raise ValueError("bad anchor")
        key = (a["component"], a["lane"], a["capability"], a["segment"])
        peer_key = (a["peer_component"], a["peer_lane"], a["peer_capability"], a["peer_segment"])
        if a["target"] == target and key in policy_rank and peer_key in policy_rank and key != peer_key:
            anchors.append(a | {"_index": i})
    anchors.sort(key=lambda a: (policy_rank[(a["component"], a["lane"], a["capability"], a["segment"])], int(a["phase"]), policy_rank[(a["peer_component"], a["peer_lane"], a["peer_capability"], a["peer_segment"])], -int(a["weight"]), a["_index"]))
    anchor_count_by_policy = {}
    anchor_weight_by_policy = {}
    for a in anchors:
        for key in ((a["component"], a["lane"], a["capability"], a["segment"]), (a["peer_component"], a["peer_lane"], a["peer_capability"], a["peer_segment"])):
            anchor_count_by_policy[key] = anchor_count_by_policy.get(key, 0) + 1
            anchor_weight_by_policy[key] = anchor_weight_by_policy.get(key, 0) + int(a["weight"])
    guards = []
    for i, g in enumerate(records(raw[10])):
        if g["target"] not in SUPPORTED or g["component"] not in comp_names:
            raise ValueError("bad guard")
        divisor = int(g["divisor"])
        if divisor <= 0:
            raise ValueError("bad guard")
        key = (g["component"], g["lane"], g["capability"], g["segment"])
        if g["target"] == target and key in policy_rank and epoch % divisor == int(g["remainder"]):
            guards.append(g | {"_index": i})
    guards.sort(key=lambda g: (policy_rank[(g["component"], g["lane"], g["capability"], g["segment"])], int(g["strength"]), g["_index"]))
    guard_count_by_policy = {}
    guard_strength_by_policy = {}
    for g in guards:
        key = (g["component"], g["lane"], g["capability"], g["segment"])
        guard_count_by_policy[key] = guard_count_by_policy.get(key, 0) + 1
        guard_strength_by_policy[key] = guard_strength_by_policy.get(key, 0) + int(g["strength"])
    scored_policies = []
    for p in policies:
        rkey, ckey = (p["component"], p["lane"]), (p["component"], p["capability"])
        route = routes[route_rank[rkey]]
        key = (p["component"], p["lane"], p["capability"], p["segment"])
        score = int(p["severity"]) * (route_weight[rkey] + int(route["capacity"]) + cap_ordinal[ckey] + guard_strength_by_policy.get(key, 0) + handoff_weight_by_policy.get(key, 0) + anchor_weight_by_policy.get(key, 0))
        scored_policies.append(p | {"score": score})
    policies = scored_policies
    attestations = []
    for i, a in enumerate(records(raw[11])):
        if a["target"] not in SUPPORTED or a["component"] not in comp_names:
            raise ValueError("bad attestation")
        key = (a["component"], a["lane"], a["capability"], a["segment"])
        if a["target"] == target and key in policy_rank and int(a["not_before"]) <= epoch <= int(a["not_after"]):
            risk = int(a["risk"])
            attestations.append(a | {"_index": i, "effective_risk": risk * (1 + guard_count_by_policy.get(key, 0) + handoff_count_by_policy.get(key, 0) + anchor_count_by_policy.get(key, 0))})
    attestations.sort(key=lambda a: (policy_rank[(a["component"], a["lane"], a["capability"], a["segment"])], -int(a["risk"]), a["_index"]))
    digest = hashlib.sha256(b"\0".join(raw + [target.encode()])).hexdigest()
    route_total = sum(int(r["weight"]) for r in routes)
    segments = list(dict.fromkeys(r["segment"] for r in routes))
    capacity_total = sum(int(r["capacity"]) for r in routes)
    handoff_weight_total = sum(int(h["weight"]) for h in handoffs)
    anchor_weight_total = sum(int(a["weight"]) for a in anchors)
    policy_score = sum(p["score"] for p in policies)
    policy_score = max(0, policy_score - sum(int(e["penalty"]) for e in exclusions))
    guard_strength_total = sum(int(g["strength"]) for g in guards)
    risk_total = sum(int(a["effective_risk"]) for a in attestations)
    manifest = (
        f"revision={rel['revision']}\nchannel={rel['channel']}\nschema={int(rel['schema'])}\ntarget={target}\n"
        f"profile={profile['profile']}\nepoch={epoch}\nlink_mode={profile['link_mode']}\ndigest={digest}\n"
        f"route_count={len(routes)}\nroute_weight_total={route_total}\nsegment_count={len(segments)}\ncapacity_total={capacity_total}\ncapability_count={len(caps)}\n"
        f"policy_count={len(policies)}\nhandoff_count={len(handoffs)}\nhandoff_weight_total={handoff_weight_total}\nanchor_count={len(anchors)}\nanchor_weight_total={anchor_weight_total}\nguard_count={len(guards)}\nattestation_count={len(attestations)}\nexclusion_count={len(exclusions)}\npolicy_score={policy_score}\nguard_strength_total={guard_strength_total}\nrisk_total={risk_total}\n"
    )
    manifest += "".join(f"component={c['component']} role={c['role']} priority={int(c['priority'])}\n" for c in comps)
    manifest += "".join(f"route target={r['target']} component={r['component']} lane={r['lane']} weight={int(r['weight'])} segment={r['segment']} capacity={int(r['capacity'])}\n" for r in routes)
    manifest += "".join(f"capability target={c['target']} component={c['component']} name={c['name']} ordinal={int(c['ordinal'])}\n" for c in caps)
    manifest += "".join(f"policy target={p['target']} component={p['component']} lane={p['lane']} segment={p['segment']} capability={p['capability']} severity={int(p['severity'])} score={p['score']}\n" for p in policies)
    manifest += "".join(f"handoff target={h['target']} component={h['component']} lane={h['lane']} segment={h['segment']} capability={h['capability']} handoff={h['handoff']} peer_component={h['peer_component']} peer_lane={h['peer_lane']} peer_segment={h['peer_segment']} peer_capability={h['peer_capability']} weight={int(h['weight'])}\n" for h in handoffs)
    manifest += "".join(f"anchor target={a['target']} component={a['component']} lane={a['lane']} segment={a['segment']} capability={a['capability']} anchor={a['anchor']} peer_component={a['peer_component']} peer_lane={a['peer_lane']} peer_segment={a['peer_segment']} peer_capability={a['peer_capability']} phase={int(a['phase'])} weight={int(a['weight'])}\n" for a in anchors)
    manifest += "".join(f"guard target={g['target']} component={g['component']} lane={g['lane']} segment={g['segment']} capability={g['capability']} guard={g['guard']} strength={int(g['strength'])}\n" for g in guards)
    manifest += "".join(f"attestation target={a['target']} component={a['component']} lane={a['lane']} segment={a['segment']} capability={a['capability']} attestation={a['attestation']} risk={int(a['risk'])} effective_risk={int(a['effective_risk'])}\n" for a in attestations)
    manifest_digest = hashlib.sha256(manifest.encode()).hexdigest()
    Path(manifest_out).write_text(manifest)
    Path(source_out).write_text(
        f'pub const revision = "{esc(rel["revision"])}";\n'
        f'pub const channel = "{esc(rel["channel"])}";\n'
        f'pub const schema: u32 = {int(rel["schema"])};\n'
        f'pub const target = "{target}";\n'
        f'pub const profile = "{esc(profile["profile"])}";\n'
        f'pub const epoch: u32 = {epoch};\n'
        f'pub const link_mode = "{esc(profile["link_mode"])}";\n'
        f'pub const digest = "{digest}";\n'
        f'pub const relay_role = "{esc(relay["role"])}";\n'
        f'pub const relay_priority: u32 = {int(relay["priority"])};\n'
        f'pub const inspect_role = "{esc(inspect["role"])}";\n'
        f'pub const inspect_priority: u32 = {int(inspect["priority"])};\n'
        f'pub const route_count: u32 = {len(routes)};\n'
        f'pub const route_weight_total: u32 = {route_total};\n'
        f'pub const segment_count: u32 = {len(segments)};\n'
        f'pub const capacity_total: u32 = {capacity_total};\n'
        f'pub const capability_count: u32 = {len(caps)};\n'
        f'pub const policy_count: u32 = {len(policies)};\n'
        f'pub const handoff_count: u32 = {len(handoffs)};\n'
        f'pub const handoff_weight_total: u32 = {handoff_weight_total};\n'
        f'pub const anchor_count: u32 = {len(anchors)};\n'
        f'pub const anchor_weight_total: u32 = {anchor_weight_total};\n'
        f'pub const guard_count: u32 = {len(guards)};\n'
        f'pub const attestation_count: u32 = {len(attestations)};\n'
        f'pub const exclusion_count: u32 = {len(exclusions)};\n'
        f'pub const policy_score: u32 = {policy_score};\n'
        f'pub const guard_strength_total: u32 = {guard_strength_total};\n'
        f'pub const risk_total: u32 = {risk_total};\n'
        f'pub const manifest_digest = "{manifest_digest}";\n'
    )

if __name__ == "__main__":
    main()
EOF
