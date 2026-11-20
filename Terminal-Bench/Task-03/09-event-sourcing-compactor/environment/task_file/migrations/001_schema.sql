CREATE TABLE IF NOT EXISTS events (
    id           SERIAL PRIMARY KEY,
    aggregate_id TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    event_type   TEXT    NOT NULL,
    payload      JSONB   NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_aggregate_version UNIQUE (aggregate_id, version)
);

CREATE INDEX IF NOT EXISTS idx_events_agg_ver ON events (aggregate_id, version);

CREATE TABLE IF NOT EXISTS snapshots (
    aggregate_id TEXT    PRIMARY KEY,
    version      INTEGER NOT NULL,
    state        JSONB   NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
