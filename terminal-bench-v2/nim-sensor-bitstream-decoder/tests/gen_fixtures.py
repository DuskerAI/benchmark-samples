"""Deterministically generate SARC fixture archives covering the public
contract. Writes <name>.sar files and records channel counts. Used by the
verifier to produce inputs and independently compute expected output.
"""

from __future__ import annotations

import os

from sarlib import BACKREF, EOB, build_archive


def eob():
    return ("eob",)


def lit(v):
    return ("lit", v)


def ref(distance, length):
    return ("ref", distance, length)


def fixtures():
    """Return list of (name, channel_counts, blocks). Each blocks item is
    (entries, tokens, truncate)."""
    out = []

    # 1. Happy path: single block, unique code lengths, all codes short enough
    #    that no code and no field spans a byte boundary in a way that matters.
    #    Symbols: two literals + EOB with distinct lengths.
    entries = [(10, 1), (20, 2), (EOB, 3)]
    toks = [lit(10), lit(10), lit(20), lit(10), eob()]
    out.append(("happy_single", [4], [(entries, toks, False)]))

    # 2. Equal-length symbols: several literals share a code length, exercising
    #    the canonical tie-break among equal lengths.
    entries = [(1, 2), (2, 2), (3, 2), (EOB, 2)]
    toks = [lit(1), lit(2), lit(3), lit(1), lit(2), lit(3), lit(2), lit(1), eob()]
    out.append(("equal_len", [8], [(entries, toks, False)]))

    # 3. Codes spanning byte boundaries: a wider table with longer codes and a
    #    long literal run so many codes cross byte boundaries.
    entries = [
        (0, 3),
        (1, 3),
        (2, 3),
        (3, 3),
        (4, 4),
        (5, 4),
        (6, 4),
        (BACKREF, 4),
        (EOB, 4),
    ]
    seq_syms = [0, 1, 2, 3, 4, 5, 6, 0, 1, 2, 3, 4, 5, 6, 0, 1, 2, 3]
    toks = [lit(s) for s in seq_syms] + [eob()]
    out.append(("byte_span", [len(seq_syms)], [(entries, toks, False)]))

    # 3b. Boundary-spanning codes with strictly unique code lengths, so only
    #     the multi-byte code reading path governs correctness. A length-5 and
    #     length-6 code each cross a byte boundary in this stream.
    entries = [(50, 1), (51, 2), (52, 3), (53, 4), (54, 5), (EOB, 6)]
    span_syms = [54, 50, 53, 53, 54, 53, 54, 52, 54, 50, 50, 54]
    toks = [lit(s) for s in span_syms] + [eob()]
    out.append(("byte_span_pure", [len(span_syms)], [(entries, toks, False)]))

    # 4. Back-reference reaching the very start of the block window.
    #    After 4 literals, a reference of distance == current length copies from
    #    the block's first sample.
    entries = [(7, 2), (8, 2), (BACKREF, 2), (EOB, 2)]
    toks = [lit(7), lit(8), lit(7), lit(8), ref(4, 3), eob()]
    # output: 7 8 7 8 then copy 3 from start(offset 0): 7 8 7 -> total 7
    out.append(("ref_at_start", [7], [(entries, toks, False)]))

    # 4b. Back-reference reaching the block start with strictly unique code
    #     lengths and no boundary-spanning codes, isolating window-start
    #     resolution.
    entries = [(60, 1), (61, 2), (BACKREF, 3), (EOB, 4)]
    toks = [lit(60), lit(60), lit(61), lit(60), ref(4, 2), eob()]
    out.append(("ref_start_pure", [6], [(entries, toks, False)]))

    # 5. Overlapping back-reference not at the boundary (run fill).
    entries = [(9, 2), (BACKREF, 2), (EOB, 2)]
    toks = [lit(9), lit(9), ref(1, 4), eob()]
    # 9 9 then copy dist1 len4 -> 9 9 9 9 ; total 6
    out.append(("ref_overlap", [6], [(entries, toks, False)]))

    # 6. Multi-block with window reset; block 2 uses a reference that reaches
    #    its own block start (must not read block 1's tail).
    b1_entries = [(100, 2), (101, 2), (BACKREF, 2), (EOB, 2)]
    b1_toks = [lit(100), lit(101), lit(100), ref(3, 2), eob()]
    # block1: 100 101 100 then copy dist3 len2 from start -> 100 101 ; = 5 samples
    b2_entries = [(200, 2), (201, 2), (BACKREF, 2), (EOB, 2)]
    b2_toks = [lit(200), lit(201), ref(2, 3), eob()]
    # block2 resets: 200 201 then dist2 len3 from block start -> 200 201 200 ; 5
    out.append(
        (
            "multi_block",
            [10],
            [(b1_entries, b1_toks, False), (b2_entries, b2_toks, False)],
        )
    )

    # 7. Multi-channel split across a stream, multiple blocks.
    b1_entries = [(5, 2), (6, 2), (7, 2), (EOB, 2)]
    b1_toks = [lit(5), lit(6), lit(7), lit(5), lit(6), lit(7), eob()]
    b2_entries = [(8, 1), (EOB, 2), (BACKREF, 2)]
    b2_toks = [lit(8), lit(8), ref(2, 4), eob()]
    # block2: 8 8 then dist2 len4 from start -> 8 8 8 8 ; = 6 samples
    out.append(
        (
            "multi_channel",
            [4, 4, 4],
            [(b1_entries, b1_toks, False), (b2_entries, b2_toks, False)],
        )
    )

    # 8. Truncated final block: declared total exceeds what the payload can
    #    deliver; decoder emits produced samples and stops cleanly.
    b1_entries = [(30, 2), (31, 2), (EOB, 2)]
    b1_toks = [lit(30), lit(31), lit(30), lit(31), eob()]
    b2_entries = [(40, 2), (41, 2), (EOB, 2)]
    # This block is truncated: the trailing byte holding EOB is dropped.
    b2_toks = [lit(40), lit(41), lit(40), lit(41), lit(40), lit(41), eob()]
    out.append(
        (
            "truncated_final",
            [12],
            [(b1_entries, b1_toks, False), (b2_entries, b2_toks, True)],
        )
    )

    # 9. Larger deterministic multi-block archive stressing all paths together.
    big_blocks = []
    total = 0
    rng = 1234567
    for blk in range(4):
        entries = [
            (0, 3),
            (1, 3),
            (2, 3),
            (3, 3),
            (4, 4),
            (5, 4),
            (BACKREF, 4),
            (EOB, 4),
        ]
        toks = []
        produced = 0
        for step in range(12):
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            choice = rng % 10
            if choice < 7 or produced == 0:
                sym = (rng >> 5) % 6
                toks.append(lit(sym))
                produced += 1
            else:
                dist = (rng >> 3) % produced + 1
                length = (rng >> 7) % 3 + 1
                toks.append(ref(dist, length))
                produced += length
        toks.append(eob())
        big_blocks.append((entries, toks, False))
        total += produced
    out.append(("big_multi", [total], big_blocks))

    return out


def write_all(dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    manifest = []
    for name, counts, blocks in fixtures():
        archive = build_archive(counts, blocks)
        path = os.path.join(dest_dir, name + ".sar")
        with open(path, "wb") as f:
            f.write(archive)
        manifest.append((name, path, counts))
    return manifest


if __name__ == "__main__":
    import sys

    m = write_all(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sarfix_fixtures")
    for name, path, counts in m:
        print(name, path, counts)
