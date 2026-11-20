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

// branchOf returns the reset base and cut of an execution, if it is a branch.
func branchOf(ctx context.Context, tx *sql.Tx, instanceID, executionID string) (base string, cut int64, ok bool, err error) {
	err = tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instanceID, executionID).Scan(&base, &cut)
	if err == sql.ErrNoRows {
		return "", 0, false, nil
	}
	if err != nil {
		return "", 0, false, err
	}
	return base, cut, true, nil
}

// logicalHistory returns the complete selected history of an execution: the
// prefix it shares with its (possibly nested) ancestors, followed by its own
// events. Incomplete or corrupt ancestry and gaps or duplicates in the logical
// sequence are errors, the caller never receives a partial prefix.
func logicalHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]ownedHistory, error) {
	return logicalHistoryUpTo(ctx, tx, instance.InstanceID, instance.ExecutionID, -1, map[string]bool{})
}

// logicalHistoryUpTo selects the events with a sequence of at most limit. A negative
// limit selects everything.
func logicalHistoryUpTo(ctx context.Context, tx *sql.Tx, instanceID, executionID string, limit int64, visiting map[string]bool) ([]ownedHistory, error) {
	if visiting[executionID] {
		return nil, fmt.Errorf("workflow history ancestry of %s is cyclic", executionID)
	}
	visiting[executionID] = true
	defer delete(visiting, executionID)

	base, cut, isBranch, err := branchOf(ctx, tx, instanceID, executionID)
	if err != nil {
		return nil, err
	}

	own, err := getPhysicalHistory(ctx, tx, core.NewWorkflowInstance(instanceID, executionID), nil)
	if err != nil {
		return nil, err
	}

	out := []ownedHistory{}
	if isBranch {
		if base == "" || cut <= 0 {
			return nil, fmt.Errorf("workflow branch %s has an invalid base", executionID)
		}
		var exists int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", instanceID, base).Scan(&exists); err != nil {
			if err == sql.ErrNoRows {
				return nil, fmt.Errorf("workflow history ancestry of %s is missing execution %s", executionID, base)
			}
			return nil, err
		}

		baseLimit := cut
		if limit >= 0 && limit < baseLimit {
			baseLimit = limit
		}
		inherited, err := logicalHistoryUpTo(ctx, tx, instanceID, base, baseLimit, visiting)
		if err != nil {
			return nil, err
		}
		if int64(len(inherited)) != baseLimit {
			return nil, fmt.Errorf("workflow history ancestry of %s is incomplete: %d of %d inherited events", executionID, len(inherited), baseLimit)
		}
		out = append(out, inherited...)
	}

	for _, e := range own {
		if limit >= 0 && e.SequenceID > limit {
			continue
		}
		out = append(out, ownedHistory{e, executionID})
	}

	for i, r := range out {
		if r.event.SequenceID != int64(i+1) {
			return nil, fmt.Errorf("workflow history of %s has a gap or duplicate at position %d (sequence %d)", executionID, i+1, r.event.SequenceID)
		}
	}
	return out, nil
}

func getHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after *int64) ([]*history.Event, error) {
	_, _, isBranch, err := branchOf(ctx, tx, instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return nil, err
	}
	if !isBranch {
		return getPhysicalHistory(ctx, tx, instance, after)
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

// lastSequenceID returns the last position of the selected history of an execution.
func lastSequenceID(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) (int64, error) {
	_, _, isBranch, err := branchOf(ctx, tx, instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return 0, err
	}
	if !isBranch {
		var last sql.NullInt64
		if err := tx.QueryRowContext(ctx, "SELECT MAX(sequence_id) FROM history WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&last); err != nil {
			return 0, fmt.Errorf("getting most recent sequence id: %w", err)
		}
		return last.Int64, nil
	}
	h, err := logicalHistory(ctx, tx, instance)
	if err != nil {
		return 0, err
	}
	if len(h) == 0 {
		return 0, nil
	}
	return h[len(h)-1].event.SequenceID, nil
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

// resultKind reports whether the event type settles a publication of the given kind.
func resultKind(kind string, t history.EventType) bool {
	switch kind {
	case "activity":
		return t == history.EventType_ActivityCompleted || t == history.EventType_ActivityFailed
	case "child":
		return t == history.EventType_SubWorkflowCompleted || t == history.EventType_SubWorkflowFailed
	case "timer":
		return t == history.EventType_TimerFired
	}
	return false
}

// ResetWorkflowInstance rewinds one active execution to a committed task boundary.
// The caller supplies a fresh execution ID, which also makes retries identifiable.
func (sb *sqliteBackend) ResetWorkflowInstance(ctx context.Context, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, error) {
	if source == nil || source.InstanceID == "" || source.ExecutionID == "" || newExecution == "" || newExecution == source.ExecutionID || cut <= 0 {
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
	previousSource, previousCut, existing, err := branchOf(ctx, tx, source.InstanceID, newExecution)
	if err != nil {
		return nil, err
	}
	if existing {
		if previousSource != source.ExecutionID || previousCut != cut {
			return nil, errors.New("reset request conflicts with an existing branch")
		}
		target, err := loadResetInstance(ctx, tx, source.InstanceID, newExecution)
		return target.instance, err
	}
	var taken int
	switch err = tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", source.InstanceID, newExecution).Scan(&taken); err {
	case nil:
		return nil, errors.New("reset execution ID is already in use")
	case sql.ErrNoRows:
	default:
		return nil, err
	}
	original, err := loadResetInstance(ctx, tx, source.InstanceID, source.ExecutionID)
	if err != nil {
		return nil, err
	}
	if original.state != core.WorkflowInstanceStateActive {
		return nil, errors.New("reset source is not active")
	}
	view, err := logicalHistory(ctx, tx, source)
	if err != nil {
		return nil, err
	}
	if cut > int64(len(view)) || cut < int64(len(view)) && view[cut].event.Type != history.EventType_WorkflowTaskStarted {
		return nil, errors.New("reset cut does not end a committed workflow task")
	}

	// Publications scheduled in the retained prefix but not settled there.
	open := map[int64]ownedHistory{}
	for _, r := range view[:cut] {
		switch r.event.Type {
		case history.EventType_ActivityScheduled, history.EventType_TimerScheduled, history.EventType_SubWorkflowScheduled:
			open[r.event.ScheduleEventID] = r
		case history.EventType_ActivityCompleted, history.EventType_ActivityFailed, history.EventType_TimerFired, history.EventType_TimerCanceled, history.EventType_SubWorkflowCompleted, history.EventType_SubWorkflowFailed:
			delete(open, r.event.ScheduleEventID)
		case history.EventType_WorkflowExecutionFinished, history.EventType_WorkflowExecutionContinuedAsNew:
			return nil, errors.New("reset prefix is already terminal")
		}
	}
	sids := make([]int64, 0, len(open))
	for sid := range open {
		sids = append(sids, sid)
	}
	sort.Slice(sids, func(i, j int) bool { return sids[i] < sids[j] })
	eligible := map[int64]effectRecord{}
	for _, sid := range sids {
		r := open[sid]
		kind := map[history.EventType]string{history.EventType_ActivityScheduled: "activity", history.EventType_TimerScheduled: "timer", history.EventType_SubWorkflowScheduled: "child"}[r.event.Type]
		effect, err := effectByOrigin(ctx, tx, source.InstanceID, r.origin, sid, kind)
		if err != nil {
			return nil, fmt.Errorf("prefix has no durable effect publication: %w", err)
		}
		if effect.Publication.Scheduled == nil || effect.Publication.Scheduled.ID != r.event.ID || effect.Publication.Delivery == nil {
			return nil, errors.New("effect publication does not match reset history")
		}
		eligible[sid] = effect
	}

	pending, err := getPendingForReset(ctx, tx, source)
	if err != nil {
		return nil, err
	}

	// Inputs to recover: signals and results of retained publications. First those
	// consumed after the cut in logical order, then those still queued in queue order.
	deliveries := []*history.Event{}
	seen := map[string]bool{}
	add := func(e *history.Event) {
		if seen[e.ID] {
			return
		}
		seen[e.ID] = true
		copy := *e
		copy.SequenceID = 0
		deliveries = append(deliveries, &copy)
	}
	retained := func(e *history.Event) bool {
		if e.Type == history.EventType_SignalReceived {
			return true
		}
		effect, ok := eligible[e.ScheduleEventID]
		return ok && resultKind(effect.Kind, e.Type)
	}
	for _, r := range view[cut:] {
		if retained(r.event) {
			add(r.event)
		}
	}
	for _, e := range pending {
		if retained(e) {
			add(e)
		}
	}
	// Accepted results and timer deliveries that no longer exist in a queue, for
	// example a timer cancelled after the cut.
	for _, sid := range sids {
		effect := eligible[sid]
		if effect.Result != nil {
			add(effect.Result)
		} else if effect.Kind == "timer" {
			add(effect.Publication.Delivery)
		}
	}

	// Adopted activities must still be queued, never reissue unknown external work.
	for _, sid := range sids {
		effect := eligible[sid]
		if effect.Kind != "activity" || effect.Result != nil {
			continue
		}
		var queued int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM activities WHERE instance_id = ? AND id = ?", source.InstanceID, effect.Publication.Delivery.ID).Scan(&queued); err != nil {
			if err == sql.ErrNoRows {
				return nil, errors.New("outstanding activity is not queued and has no recorded result")
			}
			return nil, err
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
		if effect.Kind != "activity" || effect.Result != nil {
			continue
		}
		// Keep the queue entry, with its lease, and hand it to the recovered execution.
		if _, err = tx.ExecContext(ctx, "UPDATE activities SET execution_id = ? WHERE instance_id = ? AND id = ?", newExecution, source.InstanceID, effect.Publication.Delivery.ID); err != nil {
			return nil, err
		}
		attributes, err := history.SerializeAttributes(effect.Publication.Delivery.Attributes)
		if err != nil {
			return nil, err
		}
		if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) VALUES(?,?,?,?)", effect.Publication.Delivery.ID, source.InstanceID, newExecution, attributes); err != nil {
			return nil, err
		}
	}
	// Whatever was not adopted belongs to the abandoned suffix.
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
	if err := rows.Err(); err != nil {
		return nil, err
	}
	var queued int
	if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM pending_events WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&queued); err != nil {
		return nil, err
	}
	if queued != len(out) {
		return nil, errors.New("pending input is missing its attributes")
	}
	return out, nil
}

// checkHistoryUnreferenced fails while a retained execution reads its history through this one.
func checkHistoryUnreferenced(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	var referenced int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches b JOIN instances i ON i.id = b.instance_id AND i.execution_id = b.execution_id WHERE b.instance_id = ? AND b.base_execution = ? LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&referenced)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	return ErrHistoryReferenced
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
