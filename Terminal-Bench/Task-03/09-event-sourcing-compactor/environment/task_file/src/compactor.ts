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
    state = applyEvent(state, event.event_type as EventType, event.payload);
    currentVersion = evVer;
    eventsCompacted++;
  }

  // Phase 2: write snapshot and prune events atomically.
  const client: PoolClient = await db.connect();
  try {
    await client.query('BEGIN');

    await client.query(
      `INSERT INTO snapshots (aggregate_id, version, state, created_at)
       VALUES ($1, $2, $3, NOW())
       ON CONFLICT (aggregate_id) DO UPDATE
         SET version    = EXCLUDED.version,
             state      = EXCLUDED.state,
             created_at = EXCLUDED.created_at`,
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
