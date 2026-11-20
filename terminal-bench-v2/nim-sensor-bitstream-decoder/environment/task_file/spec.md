# SARC sensor-archive format

A `.sar` archive stores one or more sensor channels as a single stream of
8-bit samples that has been compressed with a canonical-Huffman symbol table
followed by an LZ-style sliding-window back-reference scheme, divided into
blocks. This document is the authoritative wire specification. All integers are
unsigned and big-endian unless stated otherwise.

## Header

The archive begins with a byte-aligned header:

- 4 bytes: the ASCII magic `SARC`.
- 1 byte: format version, always `1`.
- 1 byte: `channelCount`, the number of channels (1 to 255).
- `channelCount` entries, each a 4-byte value giving that channel's declared
  sample count, in channel order starting at channel 0.

The decoded sample stream is partitioned into channels in order: channel 0
consumes its declared count of samples from the front of the stream, channel 1
the next block of samples, and so on.

## Blocks

Immediately after the header, the archive is a sequence of blocks. Blocks are
decoded in order until the declared total number of samples has been produced or
the archive ends. Each block has a byte-aligned header followed by a bit-aligned
payload; the payload of one block ends and the header of the next begins on a
byte boundary.

### Block header

- 2 bytes: `symbolCount`, the number of symbol/length pairs that define this
  block's Huffman code table.
- `symbolCount` entries, each:
  - 2 bytes: `symbol`, in the range 0 to 257.
  - 1 byte: `length`, the symbol's canonical code length in bits (1 to 15).

### Canonical Huffman codes

The code table is canonical. Order the symbols by increasing code length, and
within a single length by increasing symbol value. The first code of the
shortest length is all zeros. Each subsequent symbol's code is the previous
code plus one; when moving to a longer length, the code is additionally shifted
left by the increase in length. Codes are read most-significant-bit first, and
within each payload byte bits are consumed from the most significant bit to the
least significant.

### Payload symbols

The payload is the Huffman-coded token stream. Decoding each symbol yields:

- Symbols 0 to 255: a literal. The symbol value is one output sample.
- Symbol 256: end of block. The block's payload stops here; the next block
  header begins on the following byte boundary.
- Symbol 257: a back-reference marker. It is immediately followed, in the same
  bit stream, by a 16-bit `distance` field and then an 8-bit `length` field
  (each read most-significant-bit first).

### Back-references and the block window

A back-reference copies `length` samples into the output. The source begins
`distance` samples before the current end of the output, where `distance` is
1-based (a distance of 1 refers to the sample most recently emitted). Copies may
overlap the region being written, so a back-reference can extend a run.

The window is local to the block. When a block begins, its window starts at the
current end of the output. A back-reference must not read before the first
sample the current block produced; a reference whose source reaches exactly that
first sample is valid, and one that would reach further back is invalid.

## Truncated final block

An archive may end before its declared samples are fully produced: the final
block's payload can stop before its end-of-block symbol, or the archive bytes
can run out mid-block. In that case the samples that were fully decoded before
the stream ended are kept, decoding stops without error, and any channel whose
declared count was not reached simply receives fewer samples.

## Output

The reconstructed samples are written as text, grouped by channel. For each
channel, in order, a line `channel <index>` (index starting at 0) is written,
followed by one line per reconstructed sample of that channel holding the
sample's decimal value (0 to 255). The output ends with a trailing newline.
