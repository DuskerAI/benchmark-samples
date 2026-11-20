# qtrace packet note

This project uses a small offline trace format for the validator tests. Integers named `varint` use QUIC's two-bit length prefix: `00` is 1 byte, `01` is 2 bytes, `10` is 4 bytes, and `11` is 8 bytes. The remaining bits are the unsigned value in network byte order.

A trace bundle starts with the four bytes `QTV1`, then a varint packet count. Each record is `varint length || packet bytes`.

Packets use QUIC long-header layout without encryption. The first byte has the long-header and fixed bits set. Bits 5..4 are the packet type: `0` for Initial and `3` for Retry. For Initial packets, bits 1..0 encode packet-number length minus one. The next fields are `version(4)`, `dcid_len(1)`, `dcid`, `scid_len(1)`, and `scid`.

An Initial continues with `token_len(varint)`, `token`, `length(varint)`, truncated packet number, and payload. Packet numbers are reconstructed per connection and endpoint from the truncated value using the QUIC recovery rule: choose the value closest to the next expected number, where the first Initial for that endpoint uses the truncated value as-is. After that, the next expected number is the largest accepted packet number for that connection and endpoint plus one; accepting a smaller reconstructed packet number does not lower the largest value used for later reconstruction. The payload starts with endpoint byte `1` for client or `2` for server, then a varint count followed by transport parameters. Each parameter is `id(varint)`, `length(varint)`, and raw bytes.

A Retry continues with `token_len(varint)`, `token`, and a 16-byte integrity tag. The tag is the first 16 bytes of SHA-256 over `"retry-v1" || odcid_len(1) || original_dcid || retry_packet_without_tag`. The original destination CID is the destination CID from the first client Initial that uses the same client source CID.
