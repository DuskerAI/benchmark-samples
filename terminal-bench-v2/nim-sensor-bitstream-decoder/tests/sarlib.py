"""Deterministic encoder and independent reference decoder for the SARC
sensor-archive format used by the verifier.

This module is verifier-only. It builds archives byte-for-byte and computes the
expected reconstructed sample stream with an implementation that does not share
code with the task binary, so it can independently reject a wrong decoder.

Format summary (mirrors the public spec):
  header: b"SARC", version=1 (1 byte), channelCount (1 byte),
          then channelCount * uint32 big-endian declared sample counts.
  blocks: repeated until all declared samples are produced.
    block header (byte aligned):
      symCount uint16 big-endian,
      then symCount entries of (symbol uint16 BE, length uint8).
    payload (bit aligned, MSB-first within each byte, starts on a byte
      boundary): huffman-coded tokens terminated by EOB (symbol 256).
      literal symbols 0..255 -> one sample byte equal to the symbol.
      symbol 257 -> back-reference; then a 16-bit distance and 8-bit length
      field are read from the bit stream. distance is 1-based measured from the
      current block output end; length samples are copied, overlap allowed.
  window: resets at each block boundary; a reference must stay within the
  current block (its source must not reach before the block's first sample).
  A truncated final block (payload ending before EOB) contributes the samples
  it fully produced and decoding stops without error.
"""

from __future__ import annotations

EOB = 256
BACKREF = 257


def canonical_codes(entries):
    """Return {symbol: (code_int, length)} per the canonical Huffman rule.

    Symbols are ordered by increasing length then increasing symbol value; the
    first code of the shortest length is zero and each next code increments,
    shifting left when the length grows.
    """
    ordered = sorted(entries, key=lambda e: (e[1], e[0]))
    codes = {}
    code = 0
    prev_len = None
    for sym, length in ordered:
        if prev_len is None:
            code = 0
        else:
            code = (code + 1) << (length - prev_len)
        codes[sym] = (code, length)
        prev_len = length
    return codes


class BitWriter:
    def __init__(self):
        self.bits = []

    def write_code(self, code, length):
        for i in range(length - 1, -1, -1):
            self.bits.append((code >> i) & 1)

    def write_value(self, value, nbits):
        for i in range(nbits - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def to_bytes(self):
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            chunk = self.bits[i : i + 8]
            b = 0
            for j in range(8):
                bit = chunk[j] if j < len(chunk) else 0
                b = (b << 1) | bit
            out.append(b)
        return bytes(out)


def encode_block(entries, tokens, truncate=False):
    """Encode one block. entries: list of (symbol, length). tokens: list of
    ('lit', value) | ('ref', distance, length) | ('eob',). If truncate is set,
    the EOB and any trailing byte padding are dropped after the last full token
    to simulate a truncated final block."""
    codes = canonical_codes(entries)
    header = bytearray()
    header += len(entries).to_bytes(2, "big")
    for sym, length in entries:
        header += sym.to_bytes(2, "big")
        header += bytes([length])

    bw = BitWriter()
    for tok in tokens:
        if tok[0] == "lit":
            code, length = codes[tok[1]]
            bw.write_code(code, length)
        elif tok[0] == "ref":
            code, length = codes[BACKREF]
            bw.write_code(code, length)
            bw.write_value(tok[1], 16)
            bw.write_value(tok[2], 8)
        elif tok[0] == "eob":
            code, length = codes[EOB]
            bw.write_code(code, length)
        else:
            raise ValueError("bad token")
    payload = bw.to_bytes()
    if truncate and len(payload) > 1:
        # Drop the trailing byte(s) so the final EOB is unreadable.
        payload = payload[:-1]
    return bytes(header) + payload


def build_archive(channel_counts, blocks):
    """blocks: list of (entries, tokens, truncate). Returns archive bytes."""
    out = bytearray()
    out += b"SARC"
    out += bytes([1])
    out += bytes([len(channel_counts)])
    for c in channel_counts:
        out += c.to_bytes(4, "big")
    for entries, tokens, truncate in blocks:
        out += encode_block(entries, tokens, truncate)
    return bytes(out)


class BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def at_end(self):
        return self.pos >= len(self.data) * 8

    def read_bit(self):
        byte = self.data[self.pos // 8]
        off = self.pos & 7
        bit = (byte >> (7 - off)) & 1
        self.pos += 1
        return bit

    def read_value(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.read_bit()
        return v


def _decode_symbol(codes_by_len, max_len, r):
    code = 0
    length = 0
    while length < max_len:
        if r.at_end():
            return None
        code = (code << 1) | r.read_bit()
        length += 1
        if (length, code) in codes_by_len:
            return codes_by_len[(length, code)]
    return None


def _read_archive_header(archive):
    if len(archive) < 6 or archive[:4] != b"SARC" or archive[4] != 1:
        raise ValueError("bad archive")
    channels = archive[5]
    pos = 6
    counts = []
    for _ in range(channels):
        counts.append(int.from_bytes(archive[pos : pos + 4], "big"))
        pos += 4
    return counts, pos


def _read_block_entries(archive, pos):
    if pos + 2 > len(archive):
        return None, pos
    sym_count = int.from_bytes(archive[pos : pos + 2], "big")
    pos += 2
    entries = []
    for _ in range(sym_count):
        if pos + 3 > len(archive):
            return None, pos
        sym = int.from_bytes(archive[pos : pos + 2], "big")
        length = archive[pos + 2]
        pos += 3
        entries.append((sym, length))
    return entries, pos


def _append_backref(output, reader, base):
    distance = reader.read_value(16)
    length = reader.read_value(8)
    start = len(output) - distance
    if start < base:
        raise ValueError("reference before window")
    for i in range(length):
        output.append(output[start + i])


def _decode_block_payload(archive, pos, entries, output):
    codes = canonical_codes(entries)
    codes_by_len = {(length, code): sym for sym, (code, length) in codes.items()}
    max_len = max(length for _, length in entries)
    reader = BitReader(archive[pos:])
    base = len(output)
    while True:
        if reader.at_end():
            return len(archive)
        sym = _decode_symbol(codes_by_len, max_len, reader)
        if sym is None:
            return len(archive)
        if sym == EOB:
            return pos + ((reader.pos + 7) // 8)
        if sym == BACKREF:
            _append_backref(output, reader, base)
        else:
            output.append(sym)


def reference_decode(archive):
    """Independently reconstruct the sample stream. Returns list of ints."""
    counts, pos = _read_archive_header(archive)
    total = sum(counts)
    output = []
    while len(output) < total and pos < len(archive):
        entries, pos = _read_block_entries(archive, pos)
        if entries is None:
            break
        pos = _decode_block_payload(archive, pos, entries, output)
    return output


def split_channels(samples, counts):
    """Return list of per-channel sample lists honoring truncation (a short
    final stream yields shorter trailing channels)."""
    result = []
    idx = 0
    for c in counts:
        chan = []
        while len(chan) < c and idx < len(samples):
            chan.append(samples[idx])
            idx += 1
        result.append(chan)
    return result


def expected_output_text(archive, counts):
    """Render the expected decoder output text for an archive."""
    samples = reference_decode(archive)
    channels = split_channels(samples, counts)
    lines = []
    for i, chan in enumerate(channels):
        lines.append(f"channel {i}")
        lines.extend(str(s) for s in chan)
    return "\n".join(lines) + "\n"
