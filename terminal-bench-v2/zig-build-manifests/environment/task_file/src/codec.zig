export fn relkit_codec_checksum(ptr: [*]const u8, len: usize, salt: u32) u64 {
    var h: u64 = 1469598103934665603 ^ @as(u64, salt);
    var i: usize = 0;
    while (i < len) : (i += 1) {
        h ^= ptr[i];
        h *%= 1099511628211;
        h +%= @as(u64, salt) << @intCast(i % 13);
    }
    return h;
}

export fn relkit_codec_version() u32 {
    return 3;
}
