const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const triple = target.query.zigTriple(b.allocator) catch @panic("out of memory");

    const generator = b.addExecutable(.{
        .name = "meta-gen",
        .root_source_file = b.path("tools/meta_gen.zig"),
        .target = target,
        .optimize = .ReleaseSafe,
    });
    const run_generator = b.addRunArtifact(generator);
    run_generator.addArg("config/release.cfg");
    run_generator.addArg("config/components.cfg");
    run_generator.addArg("config/targets.cfg");
    run_generator.addArg("config/routes.cfg");
    run_generator.addArg("config/capabilities.cfg");
    run_generator.addArg("config/handoffs.cfg");
    run_generator.addArg(triple);
    const generated_source = run_generator.addOutputFileArg("build_meta.zig");
    const generated_manifest = run_generator.addOutputFileArg("build-meta.txt");

    const relay = b.addExecutable(.{
        .name = "relay",
        .root_source_file = b.path("src/relay.zig"),
        .target = target,
        .optimize = optimize,
        .strip = true,
    });
    relay.root_module.addImport("build_meta", b.createModule(.{ .root_source_file = generated_source }));

    const inspect = b.addExecutable(.{
        .name = "relay-inspect",
        .root_source_file = b.path("src/inspect.zig"),
        .target = target,
        .optimize = optimize,
        .strip = true,
    });
    inspect.root_module.addImport("build_meta", b.createModule(.{ .root_source_file = b.path("src/fallback_meta.zig") }));

    const install_relay = b.addInstallArtifact(relay, .{});
    const install_inspect = b.addInstallArtifact(inspect, .{});
    const install_manifest = b.addInstallFileWithDir(generated_manifest, .prefix, "share/relay/build-meta.txt");
    const bundle = b.step("bundle", "Build and install the release bundle");
    bundle.dependOn(&install_relay.step);
    bundle.dependOn(&install_inspect.step);
    bundle.dependOn(&install_manifest.step);
}
