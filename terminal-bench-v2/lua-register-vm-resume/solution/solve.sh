#!/bin/bash
# Repair the register machine: fix the signed-displacement decode, the
# snapshot restore of closure upvalues, and the tail-call chain bound, then
# rebuild the binary.
set -euo pipefail

cd /app

python3 - <<'PY'
import io, re, sys

def patch(path, old, new):
    with open(path) as f:
        s = f.read()
    if old not in s:
        sys.exit(f"anchor not found in {path}")
    s = s.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(s)

# Fix 1: sign-extend the backward-branch displacement. The negative case must
# subtract the field span so a set sign bit yields a negative offset.
patch("src/isa.nim",
"""  if (field and SbxSign) != 0'u32:
    result = int(int32(field))
  else:
    result = int(int32(field))""",
"""  if (field and SbxSign) != 0'u32:
    result = int(field) - (1 shl SbxBits)
  else:
    result = int(field)""")

# Fix 2: on resume, reconnect each restored closure's upvalues to the shared
# cells in the upvalue pool instead of minting fresh zero cells.
patch("src/state.nim",
"""      let uid = idn.getInt
      let fresh = Upvalue(id: uid, value: 0)
      cl.upvals.add fresh""",
"""      let uid = idn.getInt
      if vm.upvalPool.hasKey(uid):
        cl.upvals.add vm.upvalPool[uid]""")

# Fix 3: a tail-call chain reaching exactly the limit is permitted; only a
# chain past the limit faults.
patch("src/interp.nim",
"""  if vm.frames[^1].tailDepth >= MaxTailChain:""",
"""  if vm.frames[^1].tailDepth > MaxTailChain:""")
PY

bash scripts/build.sh
