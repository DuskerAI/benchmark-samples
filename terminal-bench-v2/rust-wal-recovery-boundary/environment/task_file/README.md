# Binary Watershed Log

`walfix replay [--json] <segments...>` reads Binary Watershed Log segment files and prints the recovered key/value state as a single JSON object with keys sorted lexicographically. The `--json` flag is optional and does not change the output format.

Every segment starts with this 24-byte header:

- bytes 0..8: ASCII magic `BWL2SEG\n`
- bytes 8..16: little-endian `u64` segment id
- bytes 16..24: little-endian `u64` base sequence

Frames follow immediately after the segment header. A frame has a 20-byte header followed by `payload_len` bytes of payload:

- bytes 0..4: little-endian `u32` payload length
- bytes 4..12: little-endian `u64` sequence number
- byte 12: operation, `1` for put, `2` for delete, and `3` for snapshot
- byte 13: flags; bit 0 means committed
- bytes 14..16: little-endian `u16` key length for put/delete frames, or zero for snapshot frames
- bytes 16..20: little-endian IEEE CRC-32

Put and delete payloads are `key || value`. Keys must be non-empty UTF-8. Values are UTF-8 and may be empty. Delete values are ignored. Snapshot payloads start with a little-endian `u16` entry count, followed by that many entries encoded as little-endian `u16` key length, little-endian `u32` value length, key bytes, and value bytes. Snapshot keys must be non-empty UTF-8 and snapshot values are UTF-8. Duplicate snapshot keys are applied in payload order.

The CRC is computed over the first 16 bytes of the frame header followed by the payload. The CRC field itself is excluded. Operation codes other than `1`, `2`, and `3`, invalid UTF-8, inconsistent lengths, oversized payloads, and sequence numbers lower than the segment base sequence are malformed frames.

Recovery scans each segment independently in ascending segment-id order, regardless of the order of paths on the command line. If a path cannot be read or its 24-byte segment header is invalid, replay fails. Within a valid segment, scanning stops at the first malformed, truncated, or checksum-invalid frame; already scanned committed frames from that segment remain usable, and other segments must still be processed. A frame's encoding and checksum are checked before its committed flag decides whether it contributes to the recovered state.

The final application order is `(sequence, segment id, frame offset)`. For a put, set the key to the value. For a delete, remove the key. For a snapshot, replace the entire recovered state with the snapshot entries. The JSON output must be deterministic and contain only the final keys.
