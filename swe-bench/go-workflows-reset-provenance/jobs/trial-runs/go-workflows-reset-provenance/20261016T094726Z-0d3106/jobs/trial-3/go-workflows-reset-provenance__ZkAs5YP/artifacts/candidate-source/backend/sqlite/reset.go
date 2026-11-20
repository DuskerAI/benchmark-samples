package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/cschleiden/go-workflows/backend"
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

// resolveHistory returns the logical history of an execution limited to
// sequence positions <= limit. An execution created by a reset shares the
// selected prefix of its base execution, which may itself be a reset.
func resolveHistory(ctx context.Context, tx *sql.Tx, instanceID, executionID string, limit int64, path map[string]bool) ([]ownedHistory, error) {
	if path[executionID] {
		return nil, fmt.Errorf("cyclic history ancestry at execution %q", executionID)
	}
	path[executionID] = true
	defer delete(path, executionID)

	own, err := getPhysicalHistory(ctx, tx, core.NewWorkflowInstance(instanceID, executionID), nil)
	if err != nil {
		return nil, err
	}
	out := []ownedHistory{}
	var base string
	var cut int64
	err = tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instanceID, executionID).Scan(&base, &cut)
	switch {
	case err == nil:
		if base == "" || cut <= 0 {
			return nil, fmt.Errorf("corrupt branch metadata for execution %q", executionID)
		}
		effective := min(limit, cut)
		prefix, err := resolveHistory(ctx, tx, instanceID, base, effective, path)
		if err != nil {
			return nil, err
		}
		if len(prefix) == 0 || prefix[len(prefix)-1].event.SequenceID != effective {
			return nil, fmt.Errorf("history of execution %q does not reach sequence %d required by %q", base, effective, executionID)
		}
		out = prefix
	case err != sql.ErrNoRows:
		return nil, err
	}
	for _, e := range own {
		if e.SequenceID <= limit {
			out = append(out, ownedHistory{e, executionID})
		}
	}
	return out, nil
}

// logicalHistory returns the complete, validated history of an execution.
func logicalHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]ownedHistory, error) {
	out, err := resolveHistory(ctx, tx, instance.InstanceID, instance.ExecutionID, math.MaxInt64, map[string]bool{})
	if err != nil {
		return nil, err
	}
	for i, r := range out {
		if r.event.SequenceID != int64(i+1) {
			return nil, fmt.Errorf("history of execution %q is corrupt: expected sequence %d, found %d", instance.ExecutionID, i+1, r.event.SequenceID)
		}
	}
	return out, nil
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

type effectKey struct {
	kind string
	sid  int64
}

func scheduledKind(t history.EventType) (string, bool) {
	switch t {
	case history.EventType_ActivityScheduled:
		return "activity", true
	case history.EventType_TimerScheduled:
		return "timer", true
	case history.EventType_SubWorkflowScheduled:
		return "child", true
	}
	return "", false
}

// settledKind reports which kind of external work an event settles. A child
// cancellation request does not settle the child: it still reports a result.
func settledKind(t history.EventType) (string, bool) {
	switch t {
	case history.EventType_ActivityCompleted, history.EventType_ActivityFailed:
		return "activity", true
	case history.EventType_TimerFired, history.EventType_TimerCanceled:
		return "timer", true
	case history.EventType_SubWorkflowCompleted, history.EventType_SubWorkflowFailed:
		return "child", true
	}
	return "", false
}

// resultKind identifies events that deliver the result of external work.
func resultKind(t history.EventType) (string, bool) {
	if t == history.EventType_TimerCanceled {
		return "", false
	}
	return settledKind(t)
}

func (sb *sqliteBackend) completedReset(ctx context.Context, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, bool, error) {
	tx, err := sb.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, false, err
	}
	defer tx.Rollback()
	return completedReset(ctx, tx, source, newExecution, cut)
}

// completedReset recognizes a reset that was already applied with the same
// source, execution ID and cut. Reusing the ID for anything else is an error.
func completedReset(ctx context.Context, tx *sql.Tx, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, bool, error) {
	var previousSource string
	var previousCut int64
	err := tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", source.InstanceID, newExecution).Scan(&previousSource, &previousCut)
	if err == sql.ErrNoRows {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if previousSource != source.ExecutionID || previousCut != cut {
		return nil, true, errors.New("reset request conflicts with an existing branch")
	}
	target, err := loadResetInstance(ctx, tx, source.InstanceID, newExecution)
	if err != nil {
		return nil, true, err
	}
	return target.instance, true, nil
}

func detached(e *history.Event) *history.Event {
	c := *e
	c.SequenceID = 0
	return &c
}

// ResetWorkflowInstance rewinds one active execution to a committed task boundary.
// The caller supplies a fresh execution ID, which also makes retries identifiable.
func (sb *sqliteBackend) ResetWorkflowInstance(ctx context.Context, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, error) {
	if source == nil || source.InstanceID == "" || source.ExecutionID == "" || newExecution == "" || newExecution == source.ExecutionID || cut <= 0 {
		return nil, errors.New("invalid workflow reset request")
	}
	// A retry of a completed reset is answered without taking the write lock.
	if target, found, err := sb.completedReset(ctx, source, newExecution, cut); err != nil || found {
		return target, err
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
	if target, found, err := completedReset(ctx, tx, source, newExecution, cut); err != nil || found {
		return target, err
	}
	original, err := loadResetInstance(ctx, tx, source.InstanceID, source.ExecutionID)
	if err == sql.ErrNoRows {
		return nil, backend.ErrInstanceNotFound
	}
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
	prefix, suffix := view[:cut], view[cut:]

	// Work published by the prefix and not settled there still belongs to the recovered workflow.
	open := map[effectKey]ownedHistory{}
	order := []effectKey{}
	started := false
	for _, r := range prefix {
		if kind, ok := scheduledKind(r.event.Type); ok {
			k := effectKey{kind, r.event.ScheduleEventID}
			if _, dup := open[k]; !dup {
				order = append(order, k)
			}
			open[k] = r
			continue
		}
		if kind, ok := settledKind(r.event.Type); ok {
			delete(open, effectKey{kind, r.event.ScheduleEventID})
			continue
		}
		switch r.event.Type {
		case history.EventType_WorkflowExecutionStarted:
			started = true
		case history.EventType_WorkflowExecutionFinished, history.EventType_WorkflowExecutionContinuedAsNew:
			return nil, errors.New("reset prefix is already terminal")
		}
	}
	if !started {
		return nil, errors.New("reset prefix does not contain the workflow start")
	}
	eligible := map[effectKey]effectRecord{}
	keys := make([]effectKey, 0, len(open))
	for _, k := range order {
		r, ok := open[k]
		if !ok {
			continue
		}
		effect, err := effectByOrigin(ctx, tx, source.InstanceID, r.origin, k.sid, k.kind)
		if err != nil {
			return nil, fmt.Errorf("prefix has no durable effect publication: %w", err)
		}
		if effect.Publication.Scheduled == nil || effect.Publication.Scheduled.ID != r.event.ID || effect.Publication.Delivery == nil {
			return nil, errors.New("effect publication does not match reset history")
		}
		eligible[k] = effect
		keys = append(keys, k)
	}
	pending, err := getPendingForReset(ctx, tx, source)
	if err != nil {
		return nil, err
	}

	// Recover external inputs: those consumed after the cut in history order,
	// followed by those still queued in arrival order.
	deliveries := []*history.Event{}
	seen := map[string]bool{}
	delivered := map[effectKey]*history.Event{}
	add := func(e *history.Event) {
		seen[e.ID] = true
		deliveries = append(deliveries, detached(e))
	}
	consider := func(e *history.Event) {
		if seen[e.ID] {
			return
		}
		if e.Type == history.EventType_SignalReceived {
			add(e)
			return
		}
		kind, ok := resultKind(e.Type)
		if !ok {
			return
		}
		k := effectKey{kind, e.ScheduleEventID}
		effect, ok := eligible[k]
		if !ok || delivered[k] != nil {
			return
		}
		if effect.Result != nil && effect.Result.ID != e.ID {
			return
		}
		out := e
		if kind == "timer" && effect.Publication.Delivery.ID == e.ID {
			out = effect.Publication.Delivery
		}
		delivered[k] = out
		add(out)
	}
	for _, r := range suffix {
		consider(r.event)
	}
	for _, e := range pending {
		consider(e)
	}
	adopt := []effectKey{}
	for _, k := range keys {
		effect := eligible[k]
		if delivered[k] == nil {
			switch {
			case effect.Result != nil && k.kind == "timer" && effect.Publication.Delivery.ID == effect.Result.ID:
				delivered[k] = effect.Publication.Delivery
				add(effect.Publication.Delivery)
			case effect.Result != nil:
				delivered[k] = effect.Result
				add(effect.Result)
			case k.kind == "timer":
				// The original delivery may have been canceled locally after the cut.
				delivered[k] = effect.Publication.Delivery
				add(effect.Publication.Delivery)
			}
		}
		if k.kind != "timer" && effect.Result == nil && delivered[k] == nil {
			adopt = append(adopt, k)
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
	for _, k := range keys {
		effect := eligible[k]
		if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = ? WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", newExecution, source.InstanceID, effect.Origin, effect.ScheduleID, effect.Kind); err != nil {
			return nil, err
		}
		if k.kind == "timer" {
			continue
		}
		if effect.Result == nil && delivered[k] != nil {
			// An accepted result seen only in history or in the queue becomes durable.
			if _, err = saveEffectResult(ctx, tx, source.InstanceID, effect, delivered[k]); err != nil {
				return nil, err
			}
		}
	}
	for _, k := range keys {
		if k.kind == "activity" && delivered[k] != nil {
			// Completed work is never run again.
			if _, err = tx.ExecContext(ctx, "DELETE FROM activities WHERE instance_id = ? AND id = ?", source.InstanceID, eligible[k].Publication.Delivery.ID); err != nil {
				return nil, err
			}
		}
	}
	for _, k := range adopt {
		if k.kind != "activity" {
			continue
		}
		effect := eligible[k]
		res, err := tx.ExecContext(ctx, "UPDATE activities SET execution_id = ? WHERE instance_id = ? AND id = ?", newExecution, source.InstanceID, effect.Publication.Delivery.ID)
		if err != nil {
			return nil, err
		}
		n, err := res.RowsAffected()
		if err != nil {
			return nil, err
		}
		if n != 1 {
			return nil, errors.New("outstanding activity publication is missing from its queue")
		}
		if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) SELECT id,instance_id,?,data FROM attributes WHERE instance_id = ? AND execution_id = ? AND id = ?", newExecution, source.InstanceID, effect.Origin, effect.Publication.Delivery.ID); err != nil {
			return nil, err
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

// checkHistoryUnreferenced fails while a retained execution reads this
// execution's history as part of its own.
func checkHistoryUnreferenced(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	var n int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND base_execution = ? LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&n)
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
