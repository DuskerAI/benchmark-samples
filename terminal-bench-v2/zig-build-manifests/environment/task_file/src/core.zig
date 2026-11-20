const config = @import("config.zig");

extern fn relkit_codec_checksum(ptr: [*]const u8, len: usize, salt: u32) u64;

export fn relkit_abi() u32 {
    return config.abi;
}

export fn relkit_packet_limit() u32 {
    return config.packet_limit;
}

export fn relkit_feature_mask() u32 {
    return config.feature_mask;
}

export fn relkit_codec_salt() u32 {
    return config.codec_salt;
}

export fn relkit_dependency_hash() u64 {
    return config.dependency_hash;
}

export fn relkit_layout_signature() u64 {
    const target = config.target;
    const name = config.target_name;
    const profile = config.profile;
    const channel = config.channel;
    return relkit_codec_checksum(target.ptr, target.len, config.codec_salt) ^
        relkit_codec_checksum(name.ptr, name.len, config.codec_salt) ^
        relkit_codec_checksum(profile.ptr, profile.len, config.codec_salt) ^
        relkit_codec_checksum(channel.ptr, channel.len, config.codec_salt) ^
        relkit_codec_checksum(config.tier.ptr, config.tier.len, config.codec_salt) ^
        config.dependency_hash ^
        @as(u64, config.abi) ^
        (@as(u64, config.packet_limit) << 8) ^
        (@as(u64, config.feature_mask) << 32) ^
        (@as(u64, config.audit_salt) << 17);
}

export fn relkit_release_hash() u64 {
    return relkit_layout_signature() ^ 0x9e3779b97f4a7c15;
}

export fn relkit_policy_fingerprint() u64 {
    return config.policy_fingerprint;
}
