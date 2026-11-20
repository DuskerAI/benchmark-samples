package sqlite

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metadata"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/stretchr/testify/require"
)

type resetRig struct {
	t   *testing.T
	b   *sqliteBackend
	ctx context.Context
}

func newResetRig(t *testing.T) *resetRig {
	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	t.Cleanup(func() { b.Close() })
	return &resetRig{t: t, b: b, ctx: context.Background()}
}

func (r *resetRig) create(id, execution string) *core.WorkflowInstance {
	instance := core.NewWorkflowInstance(id, execution)
	start := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Reset", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(r.t, r.b.CreateWorkflowInstance(r.ctx, instance, start))
	return instance
}

// run executes one workflow task that consumes all visible pending events.
func (r *resetRig) run(produced []*history.Event, activities, timers []*history.Event, messages []*history.WorkflowEvent) (*backend.WorkflowTask, []*history.Event) {
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(r.t, err)
	require.NotNil(r.t, task)
	executed := []*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
	executed = append(executed, task.NewEvents...)
	executed = append(executed, produced...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i) + 1
	}
	require.NoError(r.t, r.b.CompleteWorkflowTask(r.ctx, task, core.WorkflowInstanceStateActive, executed, activities, timers, messages))
	return task, executed
}

func activityEvent(sid int64) *history.Event {
	return history.NewPendingEvent(time.Unix(3, 0), history.EventType_ActivityScheduled, &history.ActivityScheduledAttributes{Name: "act"}, history.ScheduleEventID(sid))
}

func timerEvents(sid int64, at time.Time) (*history.Event, *history.Event) {
	return history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: at, Name: "t"}, history.ScheduleEventID(sid)),
		history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{ScheduledAt: time.Unix(3, 0), At: at, Name: "t"}, history.ScheduleEventID(sid), history.VisibleAt(at))
}

func signalEvent(name string) *history.Event {
	return history.NewPendingEvent(time.Unix(4, 0), history.EventType_SignalReceived, &history.SignalReceivedAttributes{Name: name})
}

func (r *resetRig) signal(id, name string) *history.Event {
	e := signalEvent(name)
	require.NoError(r.t, r.b.SignalWorkflow(r.ctx, id, e))
	return e
}

func (r *resetRig) reset(source *core.WorkflowInstance, execution string, cut int64) *core.WorkflowInstance {
	target, err := r.b.ResetWorkflowInstance(r.ctx, source, execution, cut)
	require.NoError(r.t, err)
	return target
}

func (r *resetRig) history(instance *core.WorkflowInstance, after *int64) []*history.Event {
	h, err := r.b.GetWorkflowInstanceHistory(r.ctx, instance, after)
	require.NoError(r.t, err)
	return h
}

func ids(events []*history.Event) []string {
	out := []string{}
	for _, e := range events {
		out = append(out, e.ID)
	}
	return out
}

func (r *resetRig) completeActivity(task *backend.ActivityTask, e *history.Event) error {
	result := history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{}, history.ScheduleEventID(e.ScheduleEventID))
	return r.b.CompleteActivityTask(r.ctx, task, result)
}

func TestResetNestedCutsShareHistory(t *testing.T) {
	r := newResetRig(t)
	a := r.create("nested", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	r.signal("nested", "one")
	timer, fire := timerEvents(2, time.Now().Add(time.Hour))
	_, second := r.run([]*history.Event{timer}, nil, []*history.Event{fire}, nil)
	r.signal("nested", "two")
	_, third := r.run(nil, nil, nil, nil)
	full := append(append(append([]*history.Event{}, first...), second...), third...)
	require.Equal(t, int64(len(full)), full[len(full)-1].SequenceID)

	b := r.reset(a, "b", int64(len(first)+len(second)))
	require.Equal(t, ids(full[:len(first)+len(second)]), ids(r.history(b, nil)))
	r.signal("nested", "after-b")
	_, own := r.run(nil, nil, nil, nil)
	require.Equal(t, int64(len(first)+len(second)+1), own[0].SequenceID)

	// Cut before the boundary inherited from a.
	c := r.reset(b, "c", int64(len(first)))
	require.Equal(t, ids(full[:len(first)]), ids(r.history(c, nil)))
	after := int64(1)
	require.Equal(t, ids(full[1:len(first)]), ids(r.history(c, &after)))
	// Cut inside b's own history.
	d := r.reset(c, "d", int64(len(first)))
	require.Equal(t, ids(full[:len(first)]), ids(r.history(d, nil)))
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.NotNil(t, task)
	require.Equal(t, "d", task.WorkflowInstance.ExecutionID)
	require.Equal(t, int64(len(first)), task.LastSequenceID)
	_ = d

	for _, bad := range []int64{0, -1, 2, int64(len(full)) + 5} {
		_, err := r.b.ResetWorkflowInstance(r.ctx, d, "bad", bad)
		require.Error(t, err)
	}
}

func TestResetActivityLeaseAndAbandonedResult(t *testing.T) {
	r := newResetRig(t)
	a := r.create("act", "a")
	kept := activityEvent(1)
	_, first := r.run([]*history.Event{kept}, []*history.Event{kept}, nil, nil)
	leased, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, kept.ID, leased.ActivityID)

	r.signal("act", "go")
	stale := activityEvent(2)
	r.run([]*history.Event{stale}, []*history.Event{stale}, nil, nil)
	staleTask, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, stale.ID, staleTask.ActivityID)

	b := r.reset(a, "b", int64(len(first)))
	// The lease of the retained activity survives; the abandoned one is gone.
	require.NoError(t, r.b.ExtendActivityTask(r.ctx, leased))
	nothing, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Nil(t, nothing)

	// The recovered execution reuses slot 2 for a new activity.
	r.signal("act", "again")
	replacement := activityEvent(2)
	r.run([]*history.Event{replacement}, []*history.Event{replacement}, nil, nil)
	replacementTask, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, replacement.ID, replacementTask.ActivityID)

	require.Error(t, r.completeActivity(staleTask, stale))
	require.NoError(t, r.completeActivity(leased, kept))
	_, executed := r.run(nil, nil, nil, nil)
	require.Equal(t, history.EventType_ActivityCompleted, executed[1].Type)
	require.Equal(t, int64(1), executed[1].ScheduleEventID)
	// The replacement still waits for its own result.
	require.NoError(t, r.completeActivity(replacementTask, replacement))
	_, executed = r.run(nil, nil, nil, nil)
	require.Equal(t, int64(2), executed[1].ScheduleEventID)
	_ = b
}

func TestResetReusesAcceptedResults(t *testing.T) {
	r := newResetRig(t)
	a := r.create("done", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	task, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	result := history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{}, history.ScheduleEventID(1))
	require.NoError(t, r.b.CompleteActivityTask(r.ctx, task, result))
	// identical retries are idempotent, conflicting ones fail
	require.NoError(t, r.b.CompleteActivityTask(r.ctx, task, result))
	other := history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityFailed, &history.ActivityFailedAttributes{}, history.ScheduleEventID(1))
	require.Error(t, r.b.CompleteActivityTask(r.ctx, task, other))
	r.signal("done", "s")
	_, consumed := r.run(nil, nil, nil, nil)
	require.Equal(t, result.ID, consumed[1].ID)

	b := r.reset(a, "b", int64(len(first)))
	nothing, err := r.b.GetActivityTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Nil(t, nothing)
	_, executed := r.run(nil, nil, nil, nil)
	// completion first (consumed order), then the signal
	require.Equal(t, []string{result.ID, consumed[2].ID}, ids(executed[1:3]))
	require.NoError(t, r.b.CompleteActivityTask(r.ctx, task, result))
	require.Error(t, r.b.CompleteActivityTask(r.ctx, task, other))
	_ = b
}

func TestResetRestoresCanceledTimer(t *testing.T) {
	r := newResetRig(t)
	a := r.create("timer", "a")
	at := time.Now().Add(time.Hour).Truncate(time.Second)
	timer, fire := timerEvents(1, at)
	_, first := r.run([]*history.Event{timer}, nil, []*history.Event{fire}, nil)
	r.signal("timer", "cancel")
	canceled := history.NewPendingEvent(time.Unix(6, 0), history.EventType_TimerCanceled, &history.TimerCanceledAttributes{}, history.ScheduleEventID(1))
	r.run([]*history.Event{canceled}, nil, nil, nil)
	future, err := r.b.GetFutureEvents(r.ctx)
	require.NoError(t, err)
	require.Empty(t, future)

	b := r.reset(a, "b", int64(len(first)))
	future, err = r.b.GetFutureEvents(r.ctx)
	require.NoError(t, err)
	require.Len(t, future, 1)
	require.Equal(t, fire.ID, future[0].ID)
	require.True(t, at.Equal(*future[0].VisibleAt))
	want, _ := json.Marshal(fire.Attributes)
	got, _ := json.Marshal(future[0].Attributes)
	require.JSONEq(t, string(want), string(got))
	_ = b
}

func TestResetReappliesSignalsInOrder(t *testing.T) {
	r := newResetRig(t)
	a := r.create("sig", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	s1 := r.signal("sig", "1")
	s2 := r.signal("sig", "2")
	r.run(nil, nil, nil, nil)
	s3 := r.signal("sig", "3")
	s4 := r.signal("sig", "4")
	want := []string{s1.ID, s2.ID, s3.ID, s4.ID}

	b := r.reset(a, "b", int64(len(first)))
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, want, ids(task.NewEvents))
	require.Equal(t, int64(len(first)), task.LastSequenceID)
	require.Equal(t, "b", task.WorkflowInstance.ExecutionID)
	executed := append([]*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i) + 1
	}
	require.NoError(t, r.b.CompleteWorkflowTask(r.ctx, task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))

	// b consumed all of them after the cut; a second reset applies them again, once.
	r.signal("sig", "5")
	r.reset(b, "c", int64(len(first)))
	task, err = r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Len(t, task.NewEvents, 5)
	require.Equal(t, want, ids(task.NewEvents[:4]))
}

func TestResetFencesAndRetries(t *testing.T) {
	r := newResetRig(t)
	a := r.create("fence", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	r.signal("fence", "x")
	old, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)

	// invalid requests have no effects
	for _, tc := range []struct {
		exec string
		cut  int64
	}{{"a", 3}, {"", 3}, {"x", 2}, {"x", 99}, {"x", 0}} {
		_, err := r.b.ResetWorkflowInstance(r.ctx, a, tc.exec, tc.cut)
		require.Error(t, err)
	}
	_, err = r.b.ResetWorkflowInstance(r.ctx, core.NewWorkflowInstance("fence", "nope"), "x", 3)
	require.Error(t, err)
	_, err = r.b.ResetWorkflowInstance(r.ctx, core.NewWorkflowInstance("other", "a"), "x", 3)
	require.Error(t, err)
	require.NoError(t, r.b.ExtendWorkflowTask(r.ctx, old))

	b := r.reset(a, "b", int64(len(first)))
	snapshot := dump(t, r.b)
	again, err := r.b.ResetWorkflowInstance(r.ctx, a, "b", int64(len(first)))
	require.NoError(t, err)
	require.Equal(t, b, again)
	require.Equal(t, snapshot, dump(t, r.b))
	_, err = r.b.ResetWorkflowInstance(r.ctx, a, "b", int64(len(first))+1)
	require.Error(t, err)
	_, err = r.b.ResetWorkflowInstance(r.ctx, b, "b", int64(len(first)))
	require.Error(t, err)
	require.Equal(t, snapshot, dump(t, r.b))

	require.Error(t, r.b.ExtendWorkflowTask(r.ctx, old))
	require.Error(t, r.b.CompleteWorkflowTask(r.ctx, old, core.WorkflowInstanceStateActive, nil, nil, nil, nil))
	require.Equal(t, snapshot, dump(t, r.b))

	// retry after progress
	r.run(nil, nil, nil, nil)
	snapshot = dump(t, r.b)
	again, err = r.b.ResetWorkflowInstance(r.ctx, a, "b", int64(len(first)))
	require.NoError(t, err)
	require.Equal(t, b, again)
	require.Equal(t, snapshot, dump(t, r.b))
}

func dump(t *testing.T, b *sqliteBackend) map[string][]string {
	out := map[string][]string{}
	for _, table := range []string{"instances", "history", "pending_events", "attributes", "activities", "workflow_branches", "workflow_effects", "workflow_checkpoint_receipts"} {
		rows, err := b.db.Query("SELECT * FROM " + table + " ORDER BY rowid")
		require.NoError(t, err)
		cols, _ := rows.Columns()
		for rows.Next() {
			vals := make([]any, len(cols))
			ptrs := make([]any, len(cols))
			for i := range vals {
				ptrs[i] = &vals[i]
			}
			require.NoError(t, rows.Scan(ptrs...))
			line := ""
			for _, v := range vals {
				line += "|" + fmtAny(v)
			}
			out[table] = append(out[table], line)
		}
		require.NoError(t, rows.Close())
	}
	return out
}

func fmtAny(v any) string {
	switch v := v.(type) {
	case []byte:
		return string(v)
	default:
		return fmt.Sprint(v)
	}
}

func TestResetChildrenKeepParentIdentity(t *testing.T) {
	r := newResetRig(t)
	a := r.create("parent", "a")
	child := core.NewSubWorkflowInstance("child", "c1", a, 1)
	scheduled := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: child, Name: "child"}, history.ScheduleEventID(1))
	started := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Child", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	_, first := r.run([]*history.Event{scheduled}, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: child, HistoryEvent: started}})

	lock := func(where string, locked bool) {
		var until any
		if locked {
			until = time.Now().Add(time.Hour)
		}
		_, err := r.b.db.Exec("UPDATE instances SET locked_until = ? WHERE "+where, until)
		require.NoError(t, err)
	}
	lock("id LIKE 'child%'", true)
	r.signal("parent", "go")
	child2 := core.NewSubWorkflowInstance("child2", "c2", a, 2)
	scheduled2 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: child2, Name: "child"}, history.ScheduleEventID(2))
	started2 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Child", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	r.run([]*history.Event{scheduled2}, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: child2, HistoryEvent: started2}})

	b := r.reset(a, "b", int64(len(first)))
	lock("id LIKE 'child%'", false)
	lock("execution_id = 'b'", true)

	finish := func() {
		task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
		require.NoError(t, err)
		c := task.WorkflowInstance
		require.Contains(t, []string{"child", "child2"}, c.InstanceID)
		require.Equal(t, "a", task.WorkflowInstance.Parent.ExecutionID)
		completed := history.NewPendingEvent(time.Unix(7, 0), history.EventType_SubWorkflowCompleted, &history.SubWorkflowCompletedAttributes{}, history.ScheduleEventID(c.ParentEventID))
		executed := []*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
		executed = append(executed, task.NewEvents...)
		for i, e := range executed {
			e.SequenceID = int64(i + 1)
		}
		require.NoError(t, r.b.CompleteWorkflowTask(r.ctx, task, core.WorkflowInstanceStateFinished, executed, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: task.WorkflowInstance.Parent, HistoryEvent: completed}}))
	}
	finish() // child2 is abandoned: dropped
	finish() // child is retained: reaches the recovered parent
	lock("execution_id = 'b'", false)
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, b, task.WorkflowInstance)
	require.Len(t, task.NewEvents, 2) // the signal and the child result
	require.Equal(t, history.EventType_SignalReceived, task.NewEvents[0].Type)
	require.Equal(t, int64(1), task.NewEvents[1].ScheduleEventID)
	require.Equal(t, history.EventType_SubWorkflowCompleted, task.NewEvents[1].Type)
}

func TestResetRetentionKeepsAncestors(t *testing.T) {
	r := newResetRig(t)
	a := r.create("keep", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	b := r.reset(a, "b", int64(len(first)))
	c := r.reset(b, "c", int64(len(first)))

	require.ErrorIs(t, r.b.RemoveWorkflowInstance(r.ctx, a), ErrHistoryReferenced)
	require.ErrorIs(t, r.b.RemoveWorkflowInstance(r.ctx, b), ErrHistoryReferenced)
	require.Len(t, r.history(c, nil), len(first))

	require.NoError(t, r.b.RemoveWorkflowInstances(r.ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour))))
	require.Len(t, r.history(c, nil), len(first))
	_, err := r.b.GetWorkflowInstanceState(r.ctx, a)
	require.NoError(t, err)

	// finish c; now everything is reclaimable, leaf first then its ancestors
	r.signal("keep", "x")
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.NoError(t, r.b.CompleteWorkflowTask(r.ctx, task, core.WorkflowInstanceStateFinished, []*history.Event{{ID: "fin", SequenceID: int64(len(first)) + 1, Type: history.EventType_WorkflowTaskStarted, Timestamp: time.Now(), Attributes: &history.WorkflowTaskStartedAttributes{}}}, nil, nil, nil))
	require.NoError(t, r.b.RemoveWorkflowInstances(r.ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour)), func(o *backend.RemovalOptions) { o.BatchSize = 1 }))
	for _, i := range []*core.WorkflowInstance{a, b, c} {
		_, err := r.b.GetWorkflowInstanceState(r.ctx, i)
		require.ErrorIs(t, err, backend.ErrInstanceNotFound)
	}
	for table, rows := range dump(t, r.b) {
		if table != "workflow_checkpoint_receipts" {
			require.Empty(t, rows, table)
		}
	}
	// a late result does not resurrect anything
	late := history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{}, history.ScheduleEventID(1))
	require.Error(t, r.b.CompleteActivityTask(r.ctx, &backend.ActivityTask{ActivityID: act.ID, WorkflowInstance: a}, late))
	require.Empty(t, dump(t, r.b)["pending_events"])
}

func TestResetDetectsCorruptAncestry(t *testing.T) {
	r := newResetRig(t)
	a := r.create("corrupt", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	b := r.reset(a, "b", int64(len(first)))
	_, err := r.b.db.Exec("DELETE FROM history WHERE execution_id = 'a' AND sequence_id = 2")
	require.NoError(t, err)
	_, err = r.b.GetWorkflowInstanceHistory(r.ctx, b, nil)
	require.Error(t, err)
	r.signal("corrupt", "x")
	_, err = r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.Error(t, err)

	r2 := newResetRig(t)
	a2 := r2.create("cycle", "a")
	_, first = r2.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	b2 := r2.reset(a2, "b", int64(len(first)))
	_, err = r2.b.db.Exec("INSERT INTO workflow_branches VALUES('cycle','a','b',1)")
	require.NoError(t, err)
	_, err = r2.b.GetWorkflowInstanceHistory(r2.ctx, b2, nil)
	require.Error(t, err)
}

func TestResetSharesPrefixAfterRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "reset.sqlite")
	ctx := context.Background()
	b := NewSqliteBackend(path, WithBackendOptions(backend.WithStickyTimeout(0)))
	r := &resetRig{t: t, b: b, ctx: ctx}
	a := r.create("restart", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	r.signal("restart", "x")
	r.run(nil, nil, nil, nil)
	x := r.reset(a, "b", int64(len(first)))
	y := r.reset(x, "c", int64(len(first)))
	require.NoError(t, b.Close())

	b = NewSqliteBackend(path, WithBackendOptions(backend.WithStickyTimeout(0)))
	defer b.Close()
	r = &resetRig{t: t, b: b, ctx: ctx}
	require.Len(t, r.history(y, nil), len(first))
	z := r.reset(y, "d", int64(len(first)))
	require.Len(t, r.history(z, nil), len(first))
}

func TestResetTimerFiredAfterCutIsDeliveredOnce(t *testing.T) {
	r := newResetRig(t)
	a := r.create("fired", "a")
	timer, fire := timerEvents(1, time.Now().Add(-time.Second))
	_, first := r.run([]*history.Event{timer}, nil, []*history.Event{fire}, nil)
	_, consumed := r.run(nil, nil, nil, nil)
	require.Equal(t, fire.ID, consumed[1].ID)
	r.signal("fired", "late")

	r.reset(a, "b", int64(len(first)))
	task, err := r.b.GetWorkflowTask(r.ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, []history.EventType{history.EventType_TimerFired, history.EventType_SignalReceived}, []history.EventType{task.NewEvents[0].Type, task.NewEvents[1].Type})
	require.Len(t, task.NewEvents, 2)
	require.Equal(t, fire.ID, task.NewEvents[0].ID)
}

func TestResetRequiresJournalCoverage(t *testing.T) {
	r := newResetRig(t)
	a := r.create("legacy", "a")
	act := activityEvent(1)
	_, first := r.run([]*history.Event{act}, []*history.Event{act}, nil, nil)
	_, err := r.b.db.Exec("DELETE FROM workflow_effects")
	require.NoError(t, err)
	before := dump(t, r.b)
	_, err = r.b.ResetWorkflowInstance(r.ctx, a, "b", int64(len(first)))
	require.Error(t, err)
	require.Equal(t, before, dump(t, r.b))
}
