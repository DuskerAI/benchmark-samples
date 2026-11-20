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

const unbounded = int64(math.MaxInt64)

// viewSegment is the part of one execution's physical history that belongs to a logical history. The
// events visible through a segment are those with floor < sequence_id <= limit.
type viewSegment struct {
	execution string
	limit     int64
	floor     int64
}

func branchOf(ctx context.Context, tx *sql.Tx, instanceID, execution string) (base string, cut int64, ok bool, err error) {
	err = tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instanceID, execution).Scan(&base, &cut)
	if err == sql.ErrNoRows {
		return "", 0, false, nil
	}
	if err != nil {
		return "", 0, false, err
	}
	return base, cut, true, nil
}

func historyExtent(ctx context.Context, tx *sql.Tx, instanceID, execution string, limit int64) (count, distinct, lowest, highest int64, err error) {
	err = tx.QueryRowContext(ctx,
		"SELECT COUNT(*), COUNT(DISTINCT h.sequence_id), COALESCE(MIN(h.sequence_id), 0), COALESCE(MAX(h.sequence_id), 0) FROM `history` h INNER JOIN `attributes` a ON a.id = h.id AND a.instance_id = h.instance_id AND a.execution_id = h.execution_id WHERE h.instance_id = ? AND h.execution_id = ? AND h.sequence_id <= ?",
		instanceID, execution, limit).Scan(&count, &distinct, &lowest, &highest)
	return
}

// resolveView walks the ancestry of an execution and returns the physical segments, oldest first, that make up its
// logical history together with the last sequence id of that history. The ancestry and every segment is validated:
// the logical sequence must start at one and must neither skip nor repeat a position.
func resolveView(ctx context.Context, tx *sql.Tx, instanceID, execution string) ([]viewSegment, int64, error) {
	chain := []viewSegment{}
	visited := map[string]bool{}
	current, limit := execution, unbounded
	for {
		if visited[current] {
			return nil, 0, fmt.Errorf("cyclic workflow history ancestry at execution %q", current)
		}
		visited[current] = true

		base, cut, branch, err := branchOf(ctx, tx, instanceID, current)
		if err != nil {
			return nil, 0, err
		}
		chain = append(chain, viewSegment{execution: current, limit: limit})
		if !branch {
			break
		}
		if cut < 1 {
			return nil, 0, fmt.Errorf("execution %q has an invalid reset cut %d", current, cut)
		}
		var present int
		if err := tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", instanceID, base).Scan(&present); err != nil {
			if err == sql.ErrNoRows {
				return nil, 0, fmt.Errorf("execution %q is missing its history ancestor %q", current, base)
			}
			return nil, 0, err
		}
		if cut < limit {
			limit = cut
		}
		current = base
	}
	for i, j := 0, len(chain)-1; i < j; i, j = i+1, j-1 {
		chain[i], chain[j] = chain[j], chain[i]
	}

	previous := int64(0)
	for i := range chain {
		s := &chain[i]
		s.floor = previous
		count, distinct, lowest, highest, err := historyExtent(ctx, tx, instanceID, s.execution, s.limit)
		if err != nil {
			return nil, 0, err
		}
		invalid := func() error {
			return fmt.Errorf("history of execution %q is incomplete or inconsistent after sequence %d", s.execution, previous)
		}
		switch {
		case s.limit != unbounded && s.limit <= previous:
			// Entirely shadowed by an earlier cut, nothing may be visible.
			if count != 0 {
				return nil, 0, invalid()
			}
		case s.limit == unbounded:
			if count > 0 {
				if lowest != previous+1 || highest-lowest+1 != count || distinct != count {
					return nil, 0, invalid()
				}
				previous = highest
			}
		default:
			if count != s.limit-previous || lowest != previous+1 || highest != s.limit || distinct != count {
				return nil, 0, invalid()
			}
			previous = s.limit
		}
	}
	return chain, previous, nil
}

// readLogicalHistory returns the logical history of an execution after the given sequence id, along with the
// execution that physically stores each event.
func readLogicalHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after int64) ([]ownedHistory, int64, error) {
	segments, last, err := resolveView(ctx, tx, instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return nil, 0, err
	}
	out := []ownedHistory{}
	expected := int64(0)
	for _, s := range segments {
		lower := s.floor
		if after > lower {
			lower = after
		}
		if s.limit <= lower {
			continue
		}
		rows, err := tx.QueryContext(ctx,
			"SELECT h.*, a.data FROM `history` h INNER JOIN `attributes` a ON a.id = h.id AND a.instance_id = h.instance_id AND a.execution_id = h.execution_id WHERE h.instance_id = ? AND h.execution_id = ? AND h.sequence_id > ? AND h.sequence_id <= ? ORDER BY h.sequence_id",
			instance.InstanceID, s.execution, lower, s.limit)
		if err != nil {
			return nil, 0, fmt.Errorf("getting history: %w", err)
		}
		for rows.Next() {
			e, err := scanEvent(rows)
			if err != nil {
				rows.Close()
				return nil, 0, fmt.Errorf("reading event: %w", err)
			}
			if expected != 0 && e.SequenceID != expected+1 || expected == 0 && e.SequenceID != lower+1 {
				rows.Close()
				return nil, 0, fmt.Errorf("history of execution %q changed while reading", s.execution)
			}
			expected = e.SequenceID
			out = append(out, ownedHistory{e, s.execution})
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, 0, err
		}
	}
	if len(out) > 0 && out[len(out)-1].event.SequenceID != last {
		return nil, 0, errors.New("history is incomplete")
	}
	return out, last, nil
}

func getHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after *int64) ([]*history.Event, error) {
	_, _, branch, err := branchOf(ctx, tx, instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return nil, err
	}
	if !branch {
		return getPhysicalHistory(ctx, tx, instance, after)
	}
	var from int64
	if after != nil {
		from = *after
	}
	owned, _, err := readLogicalHistory(ctx, tx, instance, from)
	if err != nil {
		return nil, err
	}
	out := make([]*history.Event, 0, len(owned))
	for _, r := range owned {
		out = append(out, r.event)
	}
	return out, nil
}

// getLastSequenceID returns the last position of the logical history of an execution.
func getLastSequenceID(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) (int64, error) {
	_, _, branch, err := branchOf(ctx, tx, instance.InstanceID, instance.ExecutionID)
	if err != nil {
		return 0, err
	}
	if !branch {
		var last int64
		err := tx.QueryRowContext(ctx, "SELECT COALESCE(MAX(sequence_id), 0) FROM `history` WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&last)
		return last, err
	}
	_, last, err := resolveView(ctx, tx, instance.InstanceID, instance.ExecutionID)
	return last, err
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
	if err == sql.ErrNoRows {
		return r, backend.ErrInstanceNotFound
	}
	if err != nil {
		return r, err
	}
	if parentID != nil && parentExecution != nil && parentEvent != nil {
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

func scheduleKind(t history.EventType) string {
	switch t {
	case history.EventType_ActivityScheduled:
		return "activity"
	case history.EventType_TimerScheduled:
		return "timer"
	case history.EventType_SubWorkflowScheduled:
		return "child"
	}
	return ""
}

func resultKind(t history.EventType) string {
	switch t {
	case history.EventType_ActivityCompleted, history.EventType_ActivityFailed:
		return "activity"
	case history.EventType_TimerFired:
		return "timer"
	case history.EventType_SubWorkflowCompleted, history.EventType_SubWorkflowFailed:
		return "child"
	}
	return ""
}

type outstandingWork struct {
	scheduleID int64
	kind       string
	scheduled  ownedHistory
	effect     effectRecord
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

	previousSource, previousCut, branch, err := branchOf(ctx, tx, source.InstanceID, newExecution)
	if err != nil {
		return nil, err
	}
	if branch {
		if previousSource != source.ExecutionID || previousCut != cut {
			return nil, errors.New("reset request conflicts with an existing branch")
		}
		target, err := loadResetInstance(ctx, tx, source.InstanceID, newExecution)
		return target.instance, err
	}
	var present int
	switch err = tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", source.InstanceID, newExecution).Scan(&present); err {
	case nil:
		return nil, errors.New("reset execution id is already in use")
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

	view, last, err := readLogicalHistory(ctx, tx, source, 0)
	if err != nil {
		return nil, fmt.Errorf("reading reset source history: %w", err)
	}
	if cut > last || int64(len(view)) != last {
		return nil, errors.New("reset cut is beyond the source history")
	}
	if cut < last && view[cut].event.Type != history.EventType_WorkflowTaskStarted {
		return nil, errors.New("reset cut does not end a committed workflow task")
	}
	prefix, suffix := view[:cut], view[cut:]

	prefixIDs := make(map[string]bool, len(prefix))
	started := false
	var open []*outstandingWork
	openBySchedule := map[int64]*outstandingWork{}
	for _, r := range prefix {
		prefixIDs[r.event.ID] = true
		switch r.event.Type {
		case history.EventType_WorkflowExecutionStarted:
			started = true
		case history.EventType_WorkflowExecutionFinished, history.EventType_WorkflowExecutionContinuedAsNew:
			return nil, errors.New("reset prefix is already terminal")
		case history.EventType_ActivityScheduled, history.EventType_TimerScheduled, history.EventType_SubWorkflowScheduled:
			if _, dup := openBySchedule[r.event.ScheduleEventID]; dup {
				return nil, fmt.Errorf("reset prefix schedules command %d twice", r.event.ScheduleEventID)
			}
			w := &outstandingWork{scheduleID: r.event.ScheduleEventID, kind: scheduleKind(r.event.Type), scheduled: r}
			openBySchedule[w.scheduleID] = w
			open = append(open, w)
		case history.EventType_ActivityCompleted, history.EventType_ActivityFailed, history.EventType_TimerFired, history.EventType_TimerCanceled, history.EventType_SubWorkflowCompleted, history.EventType_SubWorkflowFailed:
			// A sub-workflow cancellation request does not settle the child, it still reports back.
			if w, ok := openBySchedule[r.event.ScheduleEventID]; ok {
				w.kind = ""
			}
			delete(openBySchedule, r.event.ScheduleEventID)
		}
	}
	if !started {
		return nil, errors.New("reset prefix does not contain the workflow start")
	}
	retainedWork := open[:0]
	for _, w := range open {
		if w.kind == "" || openBySchedule[w.scheduleID] != w {
			continue
		}
		retainedWork = append(retainedWork, w)
	}
	open = retainedWork
	for _, w := range open {
		effect, err := effectByOrigin(ctx, tx, source.InstanceID, w.scheduled.origin, w.scheduleID, w.kind)
		if err != nil {
			return nil, fmt.Errorf("prefix has no durable effect publication: %w", err)
		}
		if effect.Publication.Scheduled == nil || effect.Publication.Scheduled.ID != w.scheduled.event.ID {
			return nil, errors.New("effect publication does not match reset history")
		}
		w.effect = effect
	}

	pending, err := getPendingForReset(ctx, tx, source)
	if err != nil {
		return nil, err
	}

	// Recover the external inputs that were delivered after the cut: first those that were already consumed, in
	// their serialized order, then everything still pending in queue order.
	deliveries := []*history.Event{}
	seen := map[string]bool{}
	settled := map[int64]bool{}
	add := func(e *history.Event) {
		if e == nil || seen[e.ID] || prefixIDs[e.ID] {
			return
		}
		seen[e.ID] = true
		c := *e
		c.SequenceID = 0
		deliveries = append(deliveries, &c)
	}
	retain := func(e *history.Event) {
		if e.Type == history.EventType_SignalReceived {
			add(e)
			return
		}
		if w, ok := openBySchedule[e.ScheduleEventID]; ok && w.kind == resultKind(e.Type) {
			settled[w.scheduleID] = true
			add(e)
		}
	}
	for _, r := range suffix {
		retain(r.event)
	}
	for _, e := range pending {
		retain(e)
	}

	// Release everything the source owns, then adopt what the retained prefix still depends on.
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
	for _, w := range open {
		effect := w.effect
		if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = ? WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", newExecution, source.InstanceID, effect.Origin, effect.ScheduleID, effect.Kind); err != nil {
			return nil, err
		}
		if !settled[w.scheduleID] && effect.Result != nil {
			// The result was accepted, but is neither consumed nor pending anymore.
			settled[w.scheduleID] = true
			add(effect.Result)
		}
		if settled[w.scheduleID] {
			continue
		}
		switch w.kind {
		case "timer":
			// Not fired yet: restore the original delivery, a local cancellation after the cut is abandoned.
			add(effect.Publication.Delivery)
		case "activity":
			delivery := effect.Publication.Delivery
			res, err := tx.ExecContext(ctx, "UPDATE activities SET execution_id = ? WHERE instance_id = ? AND id = ? AND execution_id = ?", newExecution, source.InstanceID, delivery.ID, source.ExecutionID)
			if err != nil {
				return nil, err
			}
			n, err := res.RowsAffected()
			if err != nil {
				return nil, err
			}
			if n != 1 {
				return nil, fmt.Errorf("outstanding activity %s has no durable work item", delivery.ID)
			}
			res, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) SELECT id,instance_id,?,data FROM attributes WHERE id = ? AND instance_id = ? AND execution_id = ?", newExecution, delivery.ID, source.InstanceID, effect.Origin)
			if err != nil {
				return nil, err
			}
			if n, err = res.RowsAffected(); err != nil {
				return nil, err
			} else if n == 0 {
				attributes, err := history.SerializeAttributes(delivery.Attributes)
				if err != nil {
					return nil, err
				}
				if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) VALUES(?,?,?,?)", delivery.ID, source.InstanceID, newExecution, attributes); err != nil {
					return nil, err
				}
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

// checkHistoryUnreferenced fails if another execution still reads history from this one.
func checkHistoryUnreferenced(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	var referenced int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND base_execution = ? LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&referenced)
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
