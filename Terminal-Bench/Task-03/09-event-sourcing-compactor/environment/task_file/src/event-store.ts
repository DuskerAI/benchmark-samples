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
    const snapshotResult = await this.db.query(
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

    const eventsResult = await this.db.query(
      `SELECT * FROM events WHERE aggregate_id = $1 AND version > $2 ORDER BY version ASC`,
      [aggregateId, fromVersion]
    );

    let version = fromVersion;
    for (const event of eventsResult.rows) {
      state = applyEvent(state, event.event_type as EventType, event.payload);
      version = event.version;
    }

    return { state, version };
  }

  async getCurrentVersion(aggregateId: string): Promise<number> {
    const result = await this.db.query(
      `SELECT MAX(version) AS version FROM events WHERE aggregate_id = $1`,
      [aggregateId]
    );
    return (result.rows[0].version as number) ?? 0;
  }
}
