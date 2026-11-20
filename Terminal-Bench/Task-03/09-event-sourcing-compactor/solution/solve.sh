#!/usr/bin/env bash
# Oracle solution: fix compactor.ts and event-store.ts.
set -euo pipefail

cat > /app/src/compactor.ts << 'TYPESCRIPT'
import { Pool, PoolClient } from 'pg';
import { applyEvent, initialState } from './aggregate';
import { OrderState, EventType, CompactionResult } from './types';

export async function compactAggregate(
  aggregateId: string,
  db: Pool
): Promise<CompactionResult> {
  // Phase 1: read snapshot + events and validate sequence — no transaction needed.
  const snapshotResult = await db.query(
    `SELECT * FROM snapshots WHERE aggregate_id = $1`,
    [aggregateId]
  );

  const currentSnapshot = snapshotResult.rows[0];
  const fromVersion: number = currentSnapshot?.version ?? 0;
  let state: OrderState = currentSnapshot
    ? (currentSnapshot.state as OrderState)
    : initialState(aggregateId);

  const eventsResult = await db.query(
    `SELECT * FROM events WHERE aggregate_id = $1 AND version > $2 ORDER BY version ASC`,
    [aggregateId, fromVersion]
  );

  let currentVersion = fromVersion;
  let eventsCompacted = 0;

  for (const event of eventsResult.rows) {
    const evVer = Number(event.version);
    const expected = Number(currentVersion) + 1;
    if (evVer !== expected) {
      return {
        aggregateId,
        snapshotVersion: 0,
        eventsCompacted: 0,
        success: false,
        error: `Missing event: expected version ${expected}, got ${evVer}`,
      };
    }
    state = applyEvent(state, event.event_type as EventType, event.payload);
    currentVersion = evVer;
    eventsCompacted++;
  }

  // Phase 2: write snapshot and prune events atomically.
  const client: PoolClient = await db.connect();
  try {
    await client.query('BEGIN');

    // WHERE clause prevents overwriting a newer snapshot with an older one.
    await client.query(
      `INSERT INTO snapshots (aggregate_id, version, state, created_at)
       VALUES ($1, $2, $3, NOW())
       ON CONFLICT (aggregate_id) DO UPDATE
         SET version    = EXCLUDED.version,
             state      = EXCLUDED.state,
             created_at = EXCLUDED.created_at
       WHERE snapshots.version < EXCLUDED.version`,
      [aggregateId, currentVersion, JSON.stringify(state)]
    );

    await client.query(
      `DELETE FROM events WHERE aggregate_id = $1 AND version <= $2`,
      [aggregateId, currentVersion]
    );

    await client.query('COMMIT');
    return { aggregateId, snapshotVersion: currentVersion, eventsCompacted, success: true };
  } catch (err: unknown) {
    await client.query('ROLLBACK').catch(() => undefined);
    return { aggregateId, snapshotVersion: 0, eventsCompacted: 0, success: false, error: (err as Error).message };
  } finally {
    client.release();
  }
}

export async function compactAll(db: Pool): Promise<CompactionResult[]> {
  const rows = await db.query(`SELECT DISTINCT aggregate_id FROM events`);
  const results: CompactionResult[] = [];
  for (const row of rows.rows) {
    results.push(await compactAggregate(row.aggregate_id as string, db));
  }
  return results;
}
TYPESCRIPT

cat > /app/src/event-store.ts << 'TYPESCRIPT'
import { Pool, PoolClient } from 'pg';
import { OrderState, EventType } from './types';
import { applyEvent, initialState } from './aggregate';

export class EventStore {
  constructor(private db: Pool) {}

  async appendEvent(
    aggregateId: string,
    expectedVersion: number,
    eventType: EventType,
    payload: Record<string, unknown>
  ): Promise<number> {
    const version = expectedVersion + 1;
    try {
      const result = await this.db.query(
        `INSERT INTO events (aggregate_id, version, event_type, payload)
         VALUES ($1, $2, $3, $4) RETURNING version`,
        [aggregateId, version, eventType, JSON.stringify(payload)]
      );
      return result.rows[0].version as number;
    } catch (err: unknown) {
      if ((err as { code?: string }).code === '23505') {
        throw new Error(`CONFLICT: Expected version ${expectedVersion}, concurrent write detected`);
      }
      throw err;
    }
  }

  async loadAggregate(aggregateId: string): Promise<{ state: OrderState; version: number }> {
    const client: PoolClient = await this.db.connect();
    try {
      await client.query('BEGIN');
      await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');

      const snapshotResult = await client.query(
        `SELECT * FROM snapshots WHERE aggregate_id = $1`,
        [aggregateId]
      );

      let state: OrderState;
      let fromVersion: number;

      if (snapshotResult.rows.length > 0) {
        const snap = snapshotResult.rows[0];
        state = snap.state as OrderState;
        fromVersion = snap.version as number;
      } else {
        state = initialState(aggregateId);
        fromVersion = 0;
      }

      const eventsResult = await client.query(
        `SELECT * FROM events WHERE aggregate_id = $1 AND version > $2 ORDER BY version ASC`,
        [aggregateId, fromVersion]
      );

      let version = fromVersion;
      for (const event of eventsResult.rows) {
        if (event.version !== version + 1) {
          throw new Error(`Event version gap: expected ${version + 1}, got ${event.version}`);
        }
        state = applyEvent(state, event.event_type as EventType, event.payload);
        version = event.version;
      }

      await client.query('COMMIT');
      return { state, version };
    } catch (err: unknown) {
      await client.query('ROLLBACK').catch(() => undefined);
      throw err;
    } finally {
      client.release();
    }
  }

  async getCurrentVersion(aggregateId: string): Promise<number> {
    const result = await this.db.query(
      `SELECT MAX(version) AS version FROM events WHERE aggregate_id = $1`,
      [aggregateId]
    );
    return (result.rows[0].version as number) ?? 0;
  }
}
TYPESCRIPT

echo "Fixed compactor.ts and event-store.ts written."
cd /app && npm run build
echo "Build complete."

# Restart the running node service so it picks up the new build.
# pkill is not available in this image, so find the PID via /proc and use kill.
# Use exact prefix match to avoid matching this shell's own cmdline.
for f in /proc/[0-9]*/cmdline; do
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in "node /app/dist/index.js"*)
    pid=$(echo "$f" | grep -o '[0-9]*')
    echo "Killing node PID $pid..."
    kill "$pid" 2>/dev/null || true
    break
  ;; esac
done

# Wait for service to come back up (entrypoint while-loop restarts it)
for i in $(seq 1 20); do
  sleep 1
  if curl -sf http://localhost:3000/health > /dev/null 2>&1; then
    echo "Service restarted after ${i}s."
    break
  fi
done
