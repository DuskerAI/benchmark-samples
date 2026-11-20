package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metadata"
	"github.com/cschleiden/go-workflows/core"
	wf "github.com/cschleiden/go-workflows/workflow"
)

var ErrHistoryReferenced = errors.New("workflow history is referenced by a reset execution")

type ownedHistory struct {
	event  *history.Event
	origin string
}

func logicalHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]ownedHistory, error) {
	seen := map[string]bool{}
	execution := instance.ExecutionID
	upper := int64(1<<63 - 1)
	out := []ownedHistory{}
	type boundary struct {
		source string
		cut    int64
	}
	usedCuts := []boundary{}
	for {
		if seen[execution] {
			return nil, errors.New("cyclic reset ancestry")
		}
		seen[execution] = true
		var exists int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", instance.InstanceID, execution).Scan(&exists); err != nil {
			return nil, fmt.Errorf("missing reset history owner: %w", err)
		}
		var parent string
		var cut int64
		err := tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, execution).Scan(&parent, &cut)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		if err == nil && (cut <= 0 || parent == "") {
			return nil, errors.New("invalid inherited history cut")
		}
		events, err := getPhysicalHistory(ctx, tx, core.NewWorkflowInstance(instance.InstanceID, execution), nil)
		if err != nil {
			return nil, err
		}
		for _, e := range events {
			if e.SequenceID > cut && e.SequenceID <= upper {
				out = append(out, ownedHistory{e, execution})
			}
		}
		if parent == "" {
			break
		}
		if cut < upper {
			usedCuts = append(usedCuts, boundary{parent, cut})
			upper = cut
		}
		execution = parent
	}
	sort.Slice(out, func(i, j int) bool { return out[i].event.SequenceID < out[j].event.SequenceID })
	for i, r := range out {
		if r.event.SequenceID != int64(i+1) {
			return nil, errors.New("reset history has a gap or duplicate sequence")
		}
	}
	if upper != int64(1<<63-1) && int64(len(out)) < upper {
		return nil, errors.New("reset history is shorter than its inherited prefix")
	}
	if len(out) > 0 && out[0].event.Type != history.EventType_WorkflowTaskStarted {
		return nil, errors.New("history lacks its initial task boundary")
	}
	// Every inherited cut must end a task in the selected logical stream, including
	// a cut lying inside an older branch's inherited prefix.
	for _, selected := range usedCuts {
		last, err := historyPosition(ctx, tx, instance.InstanceID, selected.source, selected.cut)
		if err != nil {
			return nil, err
		}
		if last == nil {
			return nil, errors.New("reset cut lies beyond its source history")
		}
		next, err := historyPosition(ctx, tx, instance.InstanceID, selected.source, selected.cut+1)
		if err != nil {
			return nil, err
		}
		if next != nil && next.Type != history.EventType_WorkflowTaskStarted {
			return nil, errors.New("inherited cut splits a workflow task")
		}
	}

	return out, nil
}

func historyPosition(ctx context.Context, tx *sql.Tx, id, execution string, sequence int64) (*history.Event, error) {
	seen := map[string]bool{}
	for {
		if seen[execution] {
			return nil, errors.New("cyclic reset ancestry")
		}
		seen[execution] = true
		var parent string
		var cut int64
		err := tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", id, execution).Scan(&parent, &cut)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		if parent != "" && sequence <= cut {
			execution = parent
			continue
		}
		events, err := getPhysicalHistory(ctx, tx, core.NewWorkflowInstance(id, execution), nil)
		if err != nil {
			return nil, err
		}
		var found *history.Event
		for _, event := range events {
			if event.SequenceID == sequence {
				if found != nil {
					return nil, errors.New("duplicate history boundary")
				}
				found = event
			}
		}
		return found, nil
	}
}

func getHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after *int64) ([]*history.Event, error) {
	var isBranch int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&isBranch)
	if err == sql.ErrNoRows {
		return getPhysicalHistory(ctx, tx, instance, after)
	}
	if err != nil {
		return nil, err
	}
	h, err := logicalHistory(ctx, tx, instance)
	if err != nil {
		return nil, err
	}
	out := []*history.Event{}
	for _, r := range h {
		if after == nil || r.event.SequenceID > *after {
			out = append(out, r.event)
		}
	}
	return out, nil
}

func resolveEffect(_ context.Context, _ *sql.Tx, _ string, r effectRecord) (effectRecord, error) {
	return r, nil
}

type resetInstance struct {
	instance *core.WorkflowInstance
	queue    wf.Queue
	metadata *metadata.WorkflowMetadata
	state    core.WorkflowInstanceState
}

func loadResetInstance(ctx context.Context, tx *sql.Tx, id, execution string) (resetInstance, error) {
	var r resetInstance
	var parentID, parentExecution *string
	var parentEvent *int64
	var encoded sql.NullString
	err := tx.QueryRowContext(ctx, "SELECT queue,parent_instance_id,parent_execution_id,parent_schedule_event_id,metadata,state FROM instances WHERE id = ? AND execution_id = ?", id, execution).Scan(&r.queue, &parentID, &parentExecution, &parentEvent, &encoded, &r.state)
	if err != nil {
		return r, err
	}
	if parentID != nil {
		r.instance = core.NewSubWorkflowInstance(id, execution, core.NewWorkflowInstance(*parentID, *parentExecution), *parentEvent)
	} else {
		r.instance = core.NewWorkflowInstance(id, execution)
	}
	r.metadata = &metadata.WorkflowMetadata{}
	if encoded.Valid {
		if err = json.Unmarshal([]byte(encoded.String), r.metadata); err != nil {
			return r, err
		}
	}
	return r, nil
}

// ResetWorkflowInstance rewinds one active execution to a committed task boundary.
// The caller supplies a fresh execution ID, which also makes retries identifiable.
func (sb *sqliteBackend) ResetWorkflowInstance(ctx context.Context, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, error) {
	if source == nil || newExecution == "" || newExecution == source.ExecutionID || cut <= 0 {
		return nil, errors.New("invalid workflow reset request")
	}
	tx, err := sb.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	// Acquire the write transaction before reading, also for WithDB connections.
	if _, err = tx.ExecContext(ctx, "UPDATE workflow_branches SET cut_sequence = cut_sequence WHERE 0"); err != nil {
		return nil, err
	}
	var previousSource string
	var previousCut int64
	err = tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", source.InstanceID, newExecution).Scan(&previousSource, &previousCut)
	if err == nil {
		if previousSource != source.ExecutionID || previousCut != cut {
			return nil, errors.New("reset request conflicts with an existing branch")
		}
		target, err := loadResetInstance(ctx, tx, source.InstanceID, newExecution)
		return target.instance, err
	}
	if err != sql.ErrNoRows {
		return nil, err
	}
	original, err := loadResetInstance(ctx, tx, source.InstanceID, source.ExecutionID)
	if err != nil {
		return nil, err
	}
	if original.state != core.WorkflowInstanceStateActive {
		return nil, errors.New("reset source is not active")
	}
	historyView, err := logicalHistory(ctx, tx, source)
	if err != nil {
		return nil, err
	}
	if cut > int64(len(historyView)) || cut < int64(len(historyView)) && historyView[cut].event.Type != history.EventType_WorkflowTaskStarted {
		return nil, errors.New("reset cut does not end a committed workflow task")
	}
	open := map[int64]ownedHistory{}
	for _, r := range historyView[:cut] {
		switch r.event.Type {
		case history.EventType_ActivityScheduled, history.EventType_TimerScheduled, history.EventType_SubWorkflowScheduled:
			open[r.event.ScheduleEventID] = r
		case history.EventType_ActivityCompleted, history.EventType_ActivityFailed, history.EventType_TimerFired, history.EventType_TimerCanceled, history.EventType_SubWorkflowCompleted, history.EventType_SubWorkflowFailed, history.EventType_SubWorkflowCancellationRequested:
			delete(open, r.event.ScheduleEventID)
		case history.EventType_WorkflowExecutionFinished, history.EventType_WorkflowExecutionContinuedAsNew:
			return nil, errors.New("reset prefix is already terminal")
		}
	}
	eligible := map[int64]effectRecord{}
	for sid, r := range open {
		kind := map[history.EventType]string{history.EventType_ActivityScheduled: "activity", history.EventType_TimerScheduled: "timer", history.EventType_SubWorkflowScheduled: "child"}[r.event.Type]
		effect, err := effectByOrigin(ctx, tx, source.InstanceID, r.origin, sid, kind)
		if err != nil {
			return nil, fmt.Errorf("prefix has no durable effect publication: %w", err)
		}
		if effect.Publication.Scheduled.ID != r.event.ID {
			return nil, errors.New("effect publication does not match reset history")
		}
		eligible[sid] = effect
	}
	pending, err := getPendingForReset(ctx, tx, source)
	if err != nil {
		return nil, err
	}
	// Preserve the committed external-input order, followed by queue arrival order.
	deliveries := []*history.Event{}
	seen := map[string]bool{}
	keep := func(e *history.Event) {
		accepted := e.Type == history.EventType_SignalReceived
		if r, ok := eligible[e.ScheduleEventID]; ok && (r.Result != nil && r.Result.ID == e.ID || r.Kind == "timer" && r.Publication.Delivery.ID == e.ID) {
			accepted = true
		}
		if accepted && !seen[e.ID] {
			seen[e.ID] = true
			copy := *e
			copy.SequenceID = 0
			deliveries = append(deliveries, &copy)
		}
	}
	for _, r := range historyView[cut:] {
		keep(r.event)
	}
	for _, e := range pending {
		keep(e)
	}
	// Known results can outlive a source delivery after another reset.
	sids := make([]int64, 0, len(eligible))
	for sid := range eligible {
		sids = append(sids, sid)
	}
	sort.Slice(sids, func(i, j int) bool { return sids[i] < sids[j] })
	for _, sid := range sids {
		if eligible[sid].Result != nil {
			keep(eligible[sid].Result)
		}
	}

	if _, err = tx.ExecContext(ctx, "UPDATE instances SET state = ?,completed_at = ?,locked_until = NULL,workflow_lease_id = NULL WHERE id = ? AND execution_id = ?", core.WorkflowInstanceStateFinished, time.Now(), source.InstanceID, source.ExecutionID); err != nil {
		return nil, err
	}
	target := core.NewWorkflowInstance(source.InstanceID, newExecution)
	if original.instance.SubWorkflow() {
		target = core.NewSubWorkflowInstance(source.InstanceID, newExecution, original.instance.Parent, original.instance.ParentEventID)
	}
	if err = createInstance(ctx, tx, original.queue, target, original.metadata); err != nil {
		return nil, err
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO workflow_branches(instance_id,execution_id,base_execution,cut_sequence) VALUES(?,?,?,?)", source.InstanceID, newExecution, source.ExecutionID, cut); err != nil {
		return nil, err
	}
	if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = NULL WHERE instance_id = ? AND owner_execution = ?", source.InstanceID, source.ExecutionID); err != nil {
		return nil, err
	}
	for _, sid := range sids {
		effect := eligible[sid]
		if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = ? WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", newExecution, source.InstanceID, effect.Origin, effect.ScheduleID, effect.Kind); err != nil {
			return nil, err
		}
		if effect.Result != nil {
			continue
		}
		switch effect.Kind {
		case "activity":
			res, err := tx.ExecContext(ctx, "UPDATE activities SET execution_id = ? WHERE instance_id = ? AND id = ?", newExecution, source.InstanceID, effect.Publication.Delivery.ID)
			if err != nil {
				return nil, err
			}
			n, err := res.RowsAffected()
			if err != nil {
				return nil, err
			}
			if n != 1 {
				return nil, errors.New("outstanding activity publication is missing")
			}
			attributes, err := history.SerializeAttributes(effect.Publication.Delivery.Attributes)
			if err != nil {
				return nil, err
			}
			if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) VALUES(?,?,?,?)", effect.Publication.Delivery.ID, source.InstanceID, newExecution, attributes); err != nil {
				return nil, err
			}
		case "timer":
			if !seen[effect.Publication.Delivery.ID] {
				deliveries = append(deliveries, effect.Publication.Delivery)
				seen[effect.Publication.Delivery.ID] = true
			}
		}
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM activities WHERE instance_id = ? AND execution_id = ?", source.InstanceID, source.ExecutionID); err != nil {
		return nil, err
	}
	if _, err = tx.ExecContext(ctx, "DELETE FROM pending_events WHERE instance_id = ? AND execution_id = ?", source.InstanceID, source.ExecutionID); err != nil {
		return nil, err
	}
	if err = insertPendingEvents(ctx, tx, target, deliveries); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return target, nil
}

func getPendingForReset(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]*history.Event, error) {
	rows, err := tx.QueryContext(ctx, "SELECT pe.*,a.data FROM pending_events pe JOIN attributes a ON a.id=pe.id AND a.instance_id=pe.instance_id AND a.execution_id=pe.execution_id WHERE pe.instance_id = ? AND pe.execution_id = ? ORDER BY pe.rowid", instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*history.Event{}
	for rows.Next() {
		e, err := scanEvent(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func checkHistoryUnreferenced(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	var exists int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND base_execution = ? LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&exists)
	if err == nil {
		return ErrHistoryReferenced
	}
	if err != sql.ErrNoRows {
		return err
	}
	return nil
}
func removeBranchMetadata(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	for _, query := range []string{
		"DELETE FROM workflow_branches WHERE instance_id = ? AND execution_id = ?",
		"DELETE FROM pending_events WHERE instance_id = ? AND execution_id = ?",
		"DELETE FROM activities WHERE instance_id = ? AND execution_id = ?",
		"DELETE FROM workflow_effects WHERE instance_id = ? AND origin_execution = ?",
		"UPDATE workflow_effects SET owner_execution = NULL WHERE instance_id = ? AND owner_execution = ?",
	} {
		if _, err := tx.ExecContext(ctx, query, instance.InstanceID, instance.ExecutionID); err != nil {
			return err
		}
	}
	return nil
}
