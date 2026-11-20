#!/bin/bash
# Oracle repair for the crlink mini static linker.
#
# Five interacting defect groups are corrected in src/linker.cr:
#   1. symbol resolution: strong definitions win over weak, and among weak
#      definitions the first in link order is kept (was: last definition wins).
#   2. COMDAT selection: a symbol or relocation site in a discarded COMDAT copy
#      is redirected to the surviving copy's address (was: not redirected).
#   3. relocation arithmetic: REL32 yields a signed 32-bit value and REL32/
#      ABS32/ABS16 range violations are reported (was: unsigned, no checks).
#   4. archive inclusion: an archive is rescanned to a fixpoint so a member made
#      newly required by a later pull is still included (was: a single pass).
#   5. tentative common definitions are coalesced with the right precedence and
#      allocated with alignment after ordinary sections.
#
# The corrected source lives beside this script; the repair is applied and the
# linker is rebuilt with the project build script.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$here/linker.cr" /app/src/linker.cr

cd /app
bash scripts/build.sh
