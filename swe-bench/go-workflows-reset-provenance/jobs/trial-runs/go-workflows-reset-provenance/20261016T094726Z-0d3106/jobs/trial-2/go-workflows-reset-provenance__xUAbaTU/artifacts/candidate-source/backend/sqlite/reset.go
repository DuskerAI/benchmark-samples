package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"slices"
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

// historySegment selects the events of one execution with lo < sequence <= hi.
type historySegment struct {
	execution string
	lo, hi    int64
}

const openEnded = int64(math.MaxInt64)

// historySegments resolves the ancestry of an execution, root first. Cuts are
// applied transitively, so a cut before an inherited boundary selects only the
// ancestors' events at or before it. The bool reports whether the execution is
// a reset branch.
func historySegments(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]historySegment, bool, error) {
	segments := []historySegment{}
	seen := map[string]bool{}
	current, limit, branch := instance.ExecutionID, openEnded, false
	for {
		if seen[current] {
			return nil, false, fmt.Errorf("workflow history ancestry of %s is cyclic", instance.InstanceID)
		}
		seen[current] = true
		var base string
		var cut int64
		err := tx.QueryRowContext(ctx, "SELECT base_execution,cut_sequence FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, current).Scan(&base, &cut)
		if err == sql.ErrNoRows {
			segments = append(segments, historySegment{current, 0, limit})
			break
		}
		if err != nil {
			return nil, false, err
		}
		if base == "" || cut <= 0 {
			return nil, false, fmt.Errorf("workflow history ancestry of %s is corrupt", instance.InstanceID)
		}
		if current == instance.ExecutionID {
			branch = true
		}
		segments = append(segments, historySegment{current, cut, limit})
		if cut < limit {
			limit = cut
		}
		current = base
	}
	slices.Reverse(segments)
	return segments, branch, nil
}

// validateSegment verifies that a segment is complete: contiguous, without
// duplicate positions and with attributes for every event. It returns the
// last sequence position of the segment.
func validateSegment(ctx context.Context, tx *sql.Tx, instanceID string, s historySegment) (int64, error) {
	if s.hi <= s.lo {
		return s.lo, nil
	}
	var count, distinct, min, max, attributes, outside int64
	err := tx.QueryRowContext(ctx, "SELECT COUNT(*),COUNT(DISTINCT h.sequence_id),COALESCE(MIN(h.sequence_id),0),COALESCE(MAX(h.sequence_id),0),COUNT(a.id) FROM history h LEFT JOIN attributes a ON a.id = h.id AND a.instance_id = h.instance_id AND a.execution_id = h.execution_id WHERE h.instance_id = ? AND h.execution_id = ? AND h.sequence_id > ? AND h.sequence_id <= ?", instanceID, s.execution, s.lo, s.hi).
		Scan(&count, &distinct, &min, &max, &attributes)
	if err != nil {
		return 0, err
	}
	if err = tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM history WHERE instance_id = ? AND execution_id = ? AND sequence_id <= ?", instanceID, s.execution, s.lo).Scan(&outside); err != nil {
		return 0, err
	}
	corrupt := func(reason string) error {
		return fmt.Errorf("workflow history of %s/%s is %s", instanceID, s.execution, reason)
	}
	switch {
	case outside != 0:
		return 0, corrupt("corrupt: events overlap an inherited prefix")
	case count != distinct:
		return 0, corrupt("corrupt: duplicate sequence positions")
	case attributes != count:
		return 0, corrupt("corrupt: event attributes are missing")
	case count > 0 && (min != s.lo+1 || max != s.lo+count):
		return 0, corrupt("incomplete: sequence positions have gaps")
	case s.hi != openEnded && count != s.hi-s.lo:
		return 0, corrupt("incomplete: selected prefix is missing events")
	}
	return s.lo + count, nil
}

// validatedSegments resolves and validates the whole ancestry, returning the
// last sequence position of the logical history.
func validatedSegments(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) ([]historySegment, int64, bool, error) {
	segments, branch, err := historySegments(ctx, tx, instance)
	if err != nil {
		return nil, 0, false, err
	}
	var last int64
	for _, s := range segments {
		if last, err = validateSegment(ctx, tx, instance.InstanceID, s); err != nil {
			return nil, 0, false, err
		}
	}
	return segments, last, branch, nil
}

func readSegment(ctx context.Context, tx *sql.Tx, instanceID string, s historySegment, after int64) ([]*history.Event, error) {
	from := s.lo
	if after > from {
		from = after
	}
	if s.hi <= from {
		return nil, nil
	}
	rows, err := tx.QueryContext(ctx, "SELECT h.*, a.data FROM history h INNER JOIN attributes a ON a.id = h.id AND a.instance_id = h.instance_id AND a.execution_id = h.execution_id WHERE h.instance_id = ? AND h.execution_id = ? AND h.sequence_id > ? AND h.sequence_id <= ? ORDER BY h.sequence_id", instanceID, s.execution, from, s.hi)
	if err != nil {
		return nil, fmt.Errorf("getting history: %w", err)
	}
	defer rows.Close()
	out := []*history.Event{}
	for rows.Next() {
		e, err := scanEvent(rows)
		if err != nil {
			return nil, fmt.Errorf("reading event: %w", err)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// logicalHistory returns the events after the given sequence position of the
// complete logical history of an execution, validating its whole ancestry.
func logicalHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after int64) ([]ownedHistory, int64, error) {
	segments, last, _, err := validatedSegments(ctx, tx, instance)
	if err != nil {
		return nil, 0, err
	}
	out := []ownedHistory{}
	for _, s := range segments {
		events, err := readSegment(ctx, tx, instance.InstanceID, s, after)
		if err != nil {
			return nil, 0, err
		}
		for _, e := range events {
			out = append(out, ownedHistory{e, s.execution})
		}
	}
	return out, last, nil
}

func isBranch(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) (bool, error) {
	var one int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&one)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return err == nil, err
}

func getHistory(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance, after *int64) ([]*history.Event, error) {
	branch, err := isBranch(ctx, tx, instance)
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
	h, _, err := logicalHistory(ctx, tx, instance, from)
	if err != nil {
		return nil, err
	}
	out := make([]*history.Event, 0, len(h))
	for _, r := range h {
		out = append(out, r.event)
	}
	return out, nil
}

// lastSequenceID returns the last sequence position of the logical history
// without reading it. Reset branches validate their complete ancestry.
func lastSequenceID(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) (int64, error) {
	branch, err := isBranch(ctx, tx, instance)
	if err != nil {
		return 0, err
	}
	if branch {
		_, last, _, err := validatedSegments(ctx, tx, instance)
		return last, err
	}
	var last int64
	err = tx.QueryRowContext(ctx, "SELECT sequence_id FROM `history` WHERE instance_id = ? AND execution_id = ? ORDER BY rowid DESC LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&last)
	if err != nil && err != sql.ErrNoRows {
		return 0, fmt.Errorf("getting most recent sequence id: %w", err)
	}
	return last, nil
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

var scheduleKinds = map[history.EventType]string{
	history.EventType_ActivityScheduled:    "activity",
	history.EventType_TimerScheduled:       "timer",
	history.EventType_SubWorkflowScheduled: "child",
}

var settleKinds = map[history.EventType]string{
	history.EventType_ActivityCompleted:    "activity",
	history.EventType_ActivityFailed:       "activity",
	history.EventType_TimerFired:           "timer",
	history.EventType_TimerCanceled:        "timer",
	history.EventType_SubWorkflowCompleted: "child",
	history.EventType_SubWorkflowFailed:    "child",
}

// ResetWorkflowInstance rewinds one active execution to a committed task boundary.
// The caller supplies a fresh execution ID, which also makes retries identifiable.
func (sb *sqliteBackend) ResetWorkflowInstance(ctx context.Context, source *core.WorkflowInstance, newExecution string, cut int64) (*core.WorkflowInstance, error) {
	if source == nil || source.InstanceID == "" || newExecution == "" || newExecution == source.ExecutionID || cut <= 0 {
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
		if err != nil {
			return nil, err
		}
		return target.instance, nil
	}
	if err != sql.ErrNoRows {
		return nil, err
	}
	var exists int
	err = tx.QueryRowContext(ctx, "SELECT 1 FROM instances WHERE id = ? AND execution_id = ?", source.InstanceID, newExecution).Scan(&exists)
	if err == nil {
		return nil, errors.New("reset execution ID is already in use")
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
	view, last, err := logicalHistory(ctx, tx, source, 0)
	if err != nil {
		return nil, err
	}
	// The view is validated to be contiguous from sequence 1.
	if cut > last || cut < last && view[cut].event.Type != history.EventType_WorkflowTaskStarted {
		return nil, errors.New("reset cut does not end a committed workflow task")
	}

	// Publications scheduled in the retained prefix, and those not yet settled in it.
	retained := map[effectKey]ownedHistory{}
	open := map[effectKey]ownedHistory{}
	for _, r := range view[:cut] {
		kind, scheduled := scheduleKinds[r.event.Type]
		key := effectKey{kind, r.event.ScheduleEventID}
		if scheduled {
			retained[key] = r
			open[key] = r
		} else if kind, settled := settleKinds[r.event.Type]; settled {
			delete(open, effectKey{kind, r.event.ScheduleEventID})
		}
		switch r.event.Type {
		case history.EventType_WorkflowExecutionFinished, history.EventType_WorkflowExecutionContinuedAsNew:
			return nil, errors.New("reset prefix is already terminal")
		}
	}
	eligible := map[effectKey]effectRecord{}
	for key, r := range open {
		effect, err := effectByOrigin(ctx, tx, source.InstanceID, r.origin, key.sid, key.kind)
		if err != nil {
			return nil, fmt.Errorf("prefix has no durable effect publication: %w", err)
		}
		if effect.Publication.Scheduled == nil || effect.Publication.Delivery == nil || effect.Publication.Scheduled.ID != r.event.ID {
			return nil, errors.New("effect publication does not match reset history")
		}
		eligible[key] = effect
	}
	keys := make([]effectKey, 0, len(eligible))
	for key := range eligible {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].sid != keys[j].sid {
			return keys[i].sid < keys[j].sid
		}
		return keys[i].kind < keys[j].kind
	})

	// Recover external inputs: those consumed after the cut in history order,
	// then those still pending in queue order, then accepted results that have
	// not been seen anywhere else.
	pending, err := getPendingForReset(ctx, tx, source)
	if err != nil {
		return nil, err
	}
	deliveries := []*history.Event{}
	seen := map[string]bool{}
	delivered := map[effectKey]bool{}
	add := func(e *history.Event) {
		if !seen[e.ID] {
			seen[e.ID] = true
			copy := *e
			copy.SequenceID = 0
			deliveries = append(deliveries, &copy)
		}
	}
	consider := func(e *history.Event) {
		if e.Type == history.EventType_SignalReceived {
			add(e)
			return
		}
		kind, ok := settleKinds[e.Type]
		key := effectKey{kind, e.ScheduleEventID}
		effect, outstanding := eligible[key]
		if !ok || !outstanding || delivered[key] {
			return
		}
		switch kind {
		case "timer":
			if e.Type == history.EventType_TimerFired {
				delivered[key] = true
				add(effect.Publication.Delivery)
			}
		default:
			if effect.Result != nil && effect.Result.ID != e.ID {
				return
			}
			delivered[key] = true
			if effect.Result != nil {
				e = effect.Result
			}
			add(e)
		}
	}
	for _, r := range view[cut:] {
		consider(r.event)
	}
	for _, e := range pending {
		consider(e)
	}
	adopt := []effectRecord{}
	for _, key := range keys {
		effect := eligible[key]
		if delivered[key] {
			continue
		}
		switch {
		case key.kind == "timer":
			add(effect.Publication.Delivery)
		case effect.Result != nil:
			add(effect.Result)
		case key.kind == "activity":
			adopt = append(adopt, effect)
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
	// Publications of the retained prefix, settled or not, belong to the new
	// execution. Everything else the source published is abandoned.
	if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = NULL WHERE instance_id = ? AND owner_execution = ?", source.InstanceID, source.ExecutionID); err != nil {
		return nil, err
	}
	for key, r := range retained {
		if _, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET owner_execution = ? WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", newExecution, source.InstanceID, r.origin, key.sid, key.kind); err != nil {
			return nil, err
		}
	}
	// Outstanding activities keep their queue entry and lease.
	for _, effect := range adopt {
		id := effect.Publication.Delivery.ID
		res, err := tx.ExecContext(ctx, "UPDATE activities SET execution_id = ? WHERE instance_id = ? AND id = ?", newExecution, source.InstanceID, id)
		if err != nil {
			return nil, err
		}
		if n, err := res.RowsAffected(); err != nil {
			return nil, err
		} else if n != 1 {
			return nil, errors.New("outstanding activity of the reset prefix is no longer queued")
		}
		attributes, err := history.SerializeAttributes(effect.Publication.Delivery.Attributes)
		if err != nil {
			return nil, err
		}
		if _, err = tx.ExecContext(ctx, "INSERT OR IGNORE INTO attributes(id,instance_id,execution_id,data) VALUES(?,?,?,?)", id, source.InstanceID, newExecution, attributes); err != nil {
			return nil, err
		}
	}
	// Remaining entries were published by the abandoned suffix or are completed.
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
	var total int
	if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM pending_events WHERE instance_id = ? AND execution_id = ?", instance.InstanceID, instance.ExecutionID).Scan(&total); err != nil {
		return nil, err
	}
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
	if len(out) != total {
		return nil, errors.New("pending events of the reset source are missing attributes")
	}
	return out, nil
}

// checkHistoryUnreferenced keeps the ancestry that retained branches read.
func checkHistoryUnreferenced(ctx context.Context, tx *sql.Tx, instance *core.WorkflowInstance) error {
	var one int
	err := tx.QueryRowContext(ctx, "SELECT 1 FROM workflow_branches WHERE instance_id = ? AND base_execution = ? LIMIT 1", instance.InstanceID, instance.ExecutionID).Scan(&one)
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
