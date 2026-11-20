ALTER TABLE instances ADD COLUMN workflow_lease_id TEXT NULL;

-- No foreign key: an acknowledged rollover remains retryable after automatic
-- removal of its old execution and history.
CREATE TABLE workflow_checkpoint_receipts (
 instance_id TEXT NOT NULL,
 execution_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 digest BLOB NOT NULL,
 PRIMARY KEY (instance_id, execution_id, attempt_id)
);
