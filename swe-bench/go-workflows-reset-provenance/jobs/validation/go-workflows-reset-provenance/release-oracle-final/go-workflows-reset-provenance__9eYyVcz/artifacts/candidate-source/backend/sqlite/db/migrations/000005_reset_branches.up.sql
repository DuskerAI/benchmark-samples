ALTER TABLE history RENAME TO history_before_reset;
CREATE TABLE history(id TEXT, sequence_id INTEGER NOT NULL, instance_id TEXT NOT NULL,
 execution_id TEXT NOT NULL, event_type INTEGER NOT NULL, timestamp DATETIME NOT NULL,
 schedule_event_id INT NOT NULL, visible_at DATETIME NULL,
 PRIMARY KEY(id,instance_id,execution_id));
INSERT INTO history SELECT * FROM history_before_reset;
DROP TABLE history_before_reset;
CREATE INDEX idx_history_instance_sequence_id ON history(instance_id,execution_id,sequence_id);
ALTER TABLE pending_events RENAME TO pending_events_before_reset;
CREATE TABLE pending_events(id TEXT, sequence_id INTEGER NOT NULL, instance_id TEXT NOT NULL,
 execution_id TEXT NOT NULL, event_type INTEGER NOT NULL, timestamp DATETIME NOT NULL,
 schedule_event_id INT NOT NULL, visible_at DATETIME NULL,
 PRIMARY KEY(id,instance_id,execution_id));
INSERT INTO pending_events SELECT * FROM pending_events_before_reset;
DROP TABLE pending_events_before_reset;
CREATE INDEX idx_pending_events_instance_id_execution_id_visible_at_schedule_event_id ON pending_events(instance_id,execution_id,visible_at,schedule_event_id);
CREATE TABLE workflow_branches (
 instance_id TEXT NOT NULL, execution_id TEXT NOT NULL,
 base_execution TEXT NOT NULL, cut_sequence INTEGER NOT NULL,
 PRIMARY KEY(instance_id,execution_id)
);
CREATE INDEX idx_branch_base ON workflow_branches(instance_id,base_execution);
CREATE TABLE workflow_effects (
 instance_id TEXT NOT NULL, origin_execution TEXT NOT NULL,
 schedule_id INTEGER NOT NULL, kind TEXT NOT NULL,
 publication BLOB NOT NULL, result BLOB NULL, owner_execution TEXT NULL,
 PRIMARY KEY(instance_id,origin_execution,schedule_id,kind)
);
CREATE INDEX idx_effect_owner ON workflow_effects(instance_id,owner_execution);
