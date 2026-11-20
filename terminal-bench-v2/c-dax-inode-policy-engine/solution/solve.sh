#!/bin/bash
set -euo pipefail

cat > /etc/dax-rollout/change.plan <<'PLAN'
REMOUNT inode
SETFLAG /pmem/payments on
SETFLAG /pmem/archive off
SETFLAG /pmem/archive/old off
CLOSE batch-cache
EVICT /pmem/payments/cache
OPEN /pmem/payments/cache verify-cache
EVICT /pmem/archive/old
OPEN /pmem/archive/old verify-archive
CLOSE verify-archive
CREATE /pmem/payments/canary
CREATE /pmem/archive/canary
PLAN

cat > /etc/dax-rollout/decisions.tsv <<'DECISIONS'
path	decision	reason
/pmem/archive/canary	APPLY	buffered
/pmem/archive/old	APPLY	buffered
/pmem/payments/cache	APPLY	dax-active
/pmem/payments/canary	APPLY	dax-active
/pmem/payments/ledger	DEFER	open-service
DECISIONS

/usr/local/sbin/dax-rollout
