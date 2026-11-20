package sqlite

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/core"
)

type effectPublication struct {
	Scheduled *history.Event
	Delivery  *history.Event
	Target    *core.WorkflowInstance
}
type effectRecord struct {
	Origin      string
	ScheduleID  int64
	Kind        string
	Publication effectPublication
	Result      *history.Event
	Owner       sql.NullString
}

func recordBranchEffects(ctx context.Context, tx *sql.Tx, t *backend.WorkflowTask, executed, activities, timers []*history.Event, messages []*history.WorkflowEvent) error {
	put := func(kind string, sid int64, p effectPublication) error {
		if p.Scheduled == nil {
			return nil
		} // Legacy direct backend calls have no scheduling history.
		data, err := json.Marshal(p)
		if err != nil {
			return err
		}
		_, err = tx.ExecContext(ctx, "INSERT INTO workflow_effects(instance_id,origin_execution,schedule_id,kind,publication,owner_execution) VALUES(?,?,?,?,?,?)", t.WorkflowInstance.InstanceID, t.WorkflowInstance.ExecutionID, sid, kind, data, t.WorkflowInstance.ExecutionID)
		return err
	}
	scheduled := func(sid int64, kind history.EventType) *history.Event {
		for _, e := range executed {
			if e.Type == kind && e.ScheduleEventID == sid {
				return e
			}
		}
		return nil
	}
	for _, e := range activities {
		if err := put("activity", e.ScheduleEventID, effectPublication{Scheduled: e, Delivery: e}); err != nil {
			return err
		}
	}
	for _, e := range timers {
		if err := put("timer", e.ScheduleEventID, effectPublication{Scheduled: scheduled(e.ScheduleEventID, history.EventType_TimerScheduled), Delivery: e}); err != nil {
			return err
		}
	}
	for _, m := range messages {
		if m.HistoryEvent.Type == history.EventType_WorkflowExecutionStarted && m.WorkflowInstance.SubWorkflow() {
			if err := put("child", m.WorkflowInstance.ParentEventID, effectPublication{Scheduled: scheduled(m.WorkflowInstance.ParentEventID, history.EventType_SubWorkflowScheduled), Delivery: m.HistoryEvent, Target: m.WorkflowInstance}); err != nil {
				return err
			}
		}
	}
	// A fired timer is durable only when its delivery is checkpointed.
	for _, e := range executed {
		if e.Type == history.EventType_TimerFired {
			records, err := effectsForOwner(ctx, tx, t.WorkflowInstance.InstanceID, t.WorkflowInstance.ExecutionID)
			if err != nil {
				return err
			}
			for _, r := range records {
				if r.Kind == "timer" && r.ScheduleID == e.ScheduleEventID {
					if _, err = saveEffectResult(ctx, tx, t.WorkflowInstance.InstanceID, r, e); err != nil {
						return err
					}
				}
			}
		}
	}
	return nil
}

func effectsForOwner(ctx context.Context, tx *sql.Tx, id, owner string) ([]effectRecord, error) {
	rows, err := tx.QueryContext(ctx, "SELECT origin_execution,schedule_id,kind,publication,result,owner_execution FROM workflow_effects WHERE instance_id = ? AND owner_execution = ? ORDER BY rowid", id, owner)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []effectRecord{}
	for rows.Next() {
		r, err := scanEffect(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, r)
	}
	return result, rows.Err()
}
func scanEffect(row Scanner) (effectRecord, error) {
	var r effectRecord
	var publication, result []byte
	if err := row.Scan(&r.Origin, &r.ScheduleID, &r.Kind, &publication, &result, &r.Owner); err != nil {
		return r, err
	}
	if err := json.Unmarshal(publication, &r.Publication); err != nil {
		return r, err
	}
	if result != nil {
		if err := json.Unmarshal(result, &r.Result); err != nil {
			return r, err
		}
	}
	return r, nil
}
func effectByOrigin(ctx context.Context, tx *sql.Tx, id, origin string, sid int64, kind string) (effectRecord, error) {
	return scanEffect(tx.QueryRowContext(ctx, "SELECT origin_execution,schedule_id,kind,publication,result,owner_execution FROM workflow_effects WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", id, origin, sid, kind))
}

func saveEffectResult(ctx context.Context, tx *sql.Tx, id string, r effectRecord, e *history.Event) (bool, error) {
	// Sequence belongs to a logical history position, not to result delivery identity.
	canonical := func(event *history.Event) ([]byte, error) {
		copy := *event
		copy.SequenceID = 0
		copy.Timestamp = copy.Timestamp.UTC()
		return json.Marshal(copy)
	}
	if r.Result != nil {
		a, err := canonical(r.Result)
		if err != nil {
			return false, err
		}
		b, err := canonical(e)
		if err != nil {
			return false, err
		}
		if !bytes.Equal(a, b) {
			return false, errors.New("conflicting effect completion")
		}
		return false, nil
	}
	data, err := json.Marshal(e)
	if err != nil {
		return false, err
	}
	_, err = tx.ExecContext(ctx, "UPDATE workflow_effects SET result = ? WHERE instance_id = ? AND origin_execution = ? AND schedule_id = ? AND kind = ?", data, id, r.Origin, r.ScheduleID, r.Kind)
	return true, err
}

func deliverBranchActivity(ctx context.Context, tx *sql.Tx, worker string, t *backend.ActivityTask, result *history.Event) (bool, error) {
	records, err := allEffects(ctx, tx, t.WorkflowInstance.InstanceID)
	if err != nil {
		return true, err
	}
	var found *effectRecord
	for i := range records {
		r := &records[i]
		if r.Kind == "activity" && r.Publication.Delivery.ID == t.ActivityID {
			found = r
			break
		}
	}
	if found == nil {
		return false, nil
	} // Ordinary histories written before the prototype.
	r, err := resolveEffect(ctx, tx, t.WorkflowInstance.InstanceID, *found)
	if err != nil {
		return true, err
	}
	if !r.Owner.Valid {
		return true, errors.New("activity belongs to an abandoned publication")
	}
	if r.Result != nil {
		_, err := saveEffectResult(ctx, tx, t.WorkflowInstance.InstanceID, r, result)
		return true, err
	}
	res, err := tx.ExecContext(ctx, "DELETE FROM activities WHERE instance_id = ? AND execution_id = ? AND id = ? AND worker = ?", t.WorkflowInstance.InstanceID, r.Owner.String, r.Publication.Delivery.ID, worker)
	if err != nil {
		return true, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return true, err
	}
	if n != 1 {
		return true, errors.New("activity acquisition is not current")
	}
	if _, err = saveEffectResult(ctx, tx, t.WorkflowInstance.InstanceID, r, result); err != nil {
		return true, err
	}
	target := core.NewWorkflowInstance(t.WorkflowInstance.InstanceID, r.Owner.String)
	if err = insertPendingEvents(ctx, tx, target, []*history.Event{result}); err != nil {
		return true, err
	}
	return true, nil
}

func routeBranchMessages(ctx context.Context, tx *sql.Tx, messages []*history.WorkflowEvent) ([]*history.WorkflowEvent, error) {
	out := []*history.WorkflowEvent{}
	for _, m := range messages {
		if m.HistoryEvent.Type != history.EventType_SubWorkflowCompleted && m.HistoryEvent.Type != history.EventType_SubWorkflowFailed {
			out = append(out, m)
			continue
		}
		r, err := effectByOrigin(ctx, tx, m.WorkflowInstance.InstanceID, m.WorkflowInstance.ExecutionID, m.HistoryEvent.ScheduleEventID, "child")
		if err == sql.ErrNoRows {
			var state core.WorkflowInstanceState
			stateErr := tx.QueryRowContext(ctx, "SELECT state FROM instances WHERE id = ? AND execution_id = ?", m.WorkflowInstance.InstanceID, m.WorkflowInstance.ExecutionID).Scan(&state)
			if stateErr == sql.ErrNoRows || stateErr == nil && state != core.WorkflowInstanceStateActive {
				continue
			}
			if stateErr != nil {
				return nil, stateErr
			}
			out = append(out, m)
			continue
		}
		if err != nil {
			return nil, err
		}
		r, err = resolveEffect(ctx, tx, m.WorkflowInstance.InstanceID, r)
		if err != nil {
			return nil, err
		}
		fresh, err := saveEffectResult(ctx, tx, m.WorkflowInstance.InstanceID, r, m.HistoryEvent)
		if err != nil {
			return nil, err
		}
		if !fresh || !r.Owner.Valid {
			continue
		}
		var state core.WorkflowInstanceState
		err = tx.QueryRowContext(ctx, "SELECT state FROM instances WHERE id = ? AND execution_id = ?", m.WorkflowInstance.InstanceID, r.Owner.String).Scan(&state)
		if err == sql.ErrNoRows {
			continue
		}
		if err != nil {
			return nil, err
		}
		if state != core.WorkflowInstanceStateActive {
			continue
		}
		out = append(out, &history.WorkflowEvent{WorkflowInstance: core.NewWorkflowInstance(m.WorkflowInstance.InstanceID, r.Owner.String), HistoryEvent: m.HistoryEvent})
	}
	return out, nil
}

func allEffects(ctx context.Context, tx *sql.Tx, id string) ([]effectRecord, error) {
	rows, err := tx.QueryContext(ctx, "SELECT origin_execution,schedule_id,kind,publication,result,owner_execution FROM workflow_effects WHERE instance_id = ? ORDER BY rowid", id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []effectRecord{}
	for rows.Next() {
		r, err := scanEffect(rows)
		if err != nil {
			return nil, fmt.Errorf("reading effect publication: %w", err)
		}
		result = append(result, r)
	}
	return result, rows.Err()
}
