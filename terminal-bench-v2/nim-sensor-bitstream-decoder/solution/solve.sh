#!/bin/bash
# Oracle: repair the SARC decoder so reconstructed samples match the format
# specification, then rebuild the binary.
set -euo pipefail

cd /app

python3 - <<'PY'
# --- Canonical Huffman tie-break: within a code length, order symbols by
#     increasing symbol value, not decreasing. ---
p = "src/huffman.nim"
s = open(p).read()
s = s.replace("  cmp(b.sym, a.sym)\n", "  cmp(a.sym, b.sym)\n")

# --- Multi-byte code reading: bits that continue into a following byte must be
#     appended in the same most-significant-first order as the first byte, not
#     reversed. ---
s = s.replace(
    """    if carry:
      # Bits carried over from a preceding byte are folded back in as they were
      # buffered while the previous byte was being drained.
      for i in 0 ..< runLen:
        result.add(group[runLen - 1 - i])
    else:
      for g in group:
        result.add(g)
    carry = true""",
    """    for g in group:
      result.add(g)
    carry = true""",
)
open(p, "w").write(s)

# --- Block-window boundary: a back-reference whose source reaches exactly the
#     first sample of the current block is valid and must copy from that sample;
#     one that would reach further back is invalid. ---
p = "src/lz.nim"
s = open(p).read()
s = s.replace(
    """  var start = w.output.len - distance
  if start <= w.base:
    start = w.base + 1
  for i in 0 ..< length:""",
    """  let start = w.output.len - distance
  if start < w.base:
    raise newException(ValueError, "back-reference before block window")
  for i in 0 ..< length:""",
)
open(p, "w").write(s)
PY

bash /app/scripts/build.sh
