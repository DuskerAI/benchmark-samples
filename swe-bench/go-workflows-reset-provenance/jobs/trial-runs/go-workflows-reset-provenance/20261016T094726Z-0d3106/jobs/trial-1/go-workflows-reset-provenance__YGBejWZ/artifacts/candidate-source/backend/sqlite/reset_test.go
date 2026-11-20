package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metadata"
	"github.com/cschleiden/go-workflows/backend/payload"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/stretchr/testify/require"
)

var q = []workflow.Queue{workflow.QueueDefault}

type harness struct {
	t *testing.T
	b *sqliteBackend
}

func newHarness(t *testing.T) *harness {
	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	t.Cleanup(func() { b.Close() })
	return &harness{t, b}
}

func (h *harness) start(id string) *core.WorkflowInstance {
	instance := core.NewWorkflowInstance(id, "e1")
	start := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "W", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(h.t, h.b.CreateWorkflowInstance(context.Background(), instance, start))
	return instance
}

type commands struct {
	events     []*history.Event
	activities []*history.Event
	timers     []*history.Event
	messages   []*history.WorkflowEvent
}

func activityCmd(sid int64) (*history.Event, commands) {
	e := history.NewPendingEvent(time.Unix(2, 0), history.EventType_ActivityScheduled, &history.ActivityScheduledAttributes{Name: "act", Queue: workflow.QueueDefault}, history.ScheduleEventID(sid))
	return e, commands{events: []*history.Event{e}, activities: []*history.Event{e}}
}

func timerCmd(sid int64, at time.Time) (*history.Event, *history.Event, commands) {
	s := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: at}, history.ScheduleEventID(sid))
	f := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{At: at, ScheduledAt: time.Unix(2, 0)}, history.ScheduleEventID(sid), history.VisibleAt(at))
	return s, f, commands{events: []*history.Event{s}, timers: []*history.Event{f}}
}

func (c commands) and(o commands) commands {
	return commands{append(append([]*history.Event{}, c.events...), o.events...), append(append([]*history.Event{}, c.activities...), o.activities...), append(append([]*history.Event{}, c.timers...), o.timers...), append(append([]*history.WorkflowEvent{}, c.messages...), o.messages...)}
}

// run executes one workflow task, consuming the given pending events (all if nil).
func (h *harness) run(instance *core.WorkflowInstance, c commands, state core.WorkflowInstanceState) []*history.Event {
	ctx := context.Background()
	task, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(h.t, err)
	require.NotNil(h.t, task, "no workflow task")
	require.Equal(h.t, instance.ExecutionID, task.WorkflowInstance.ExecutionID)
	executed := []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
	executed = append(executed, task.NewEvents...)
	executed = append(executed, c.events...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i) + 1
	}
	require.NoError(h.t, h.b.CompleteWorkflowTask(ctx, task, state, executed, c.activities, c.timers, c.messages))
	return executed
}

func (h *harness) signal(id, name string) *history.Event {
	e := history.NewPendingEvent(time.Unix(4, 0), history.EventType_SignalReceived, &history.SignalReceivedAttributes{Name: name, Arg: payload.Payload(name)})
	require.NoError(h.t, h.b.SignalWorkflow(context.Background(), id, e))
	return e
}

func (h *harness) history(instance *core.WorkflowInstance) []*history.Event {
	e, err := h.b.GetWorkflowInstanceHistory(context.Background(), instance, nil)
	require.NoError(h.t, err)
	return e
}

func (h *harness) pendingIDs(instance *core.WorkflowInstance) []string {
	rows, err := h.b.db.Query("SELECT id FROM pending_events WHERE instance_id=? AND execution_id=? ORDER BY rowid", instance.InstanceID, instance.ExecutionID)
	require.NoError(h.t, err)
	defer rows.Close()
	ids := []string{}
	for rows.Next() {
		var id string
		require.NoError(h.t, rows.Scan(&id))
		ids = append(ids, id)
	}
	return ids
}

func (h *harness) count(query string, args ...any) int {
	var n int
	require.NoError(h.t, h.b.db.QueryRow(query, args...).Scan(&n))
	return n
}

func ids(es []*history.Event) []string {
	out := []string{}
	for _, e := range es {
		out = append(out, e.ID)
	}
	return out
}

func completion(sid int64, result string) *history.Event {
	return history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{Result: payload.Payload(result)}, history.ScheduleEventID(sid))
}

func TestResetNestedAnyCut(t *testing.T) {
	h := newHarness(t)
	s1 := h.start("n")
	a, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	_ = a
	h.signal("n", "x")
	h.run(s1, commands{}, core.WorkflowInstanceStateActive) // seq 4,5
	h.signal("n", "y")
	h.run(s1, commands{}, core.WorkflowInstanceStateActive) // seq 6,7
	full := h.history(s1)
	require.Len(t, full, 7)

	ctx := context.Background()
	_, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 2) // mid-task
	require.Error(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, s1, "s2", 8)
	require.Error(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, s1, "s2", 0)
	require.Error(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_branches"))

	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 5)
	require.NoError(t, err)
	require.Equal(t, ids(full[:5]), ids(h.history(s2)))
	// y was consumed after the cut and is recovered
	task, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, int64(5), task.LastSequenceID)
	require.Equal(t, []string{full[6].ID}, ids(task.NewEvents))
	require.Equal(t, int64(0), task.NewEvents[0].SequenceID)
	executed := []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
	executed = append(executed, task.NewEvents...)
	for i, e := range executed {
		e.SequenceID = 6 + int64(i)
	}
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))
	s2full := h.history(s2)
	require.Len(t, s2full, 7)

	// Reset before the inherited boundary: x and y are recovered in order
	_, err = h.b.ResetWorkflowInstance(ctx, s2, "bad", 4)
	require.Error(t, err)
	s3, err := h.b.ResetWorkflowInstance(ctx, s2, "s3", 3)
	require.NoError(t, err)
	require.Equal(t, ids(full[:3]), ids(h.history(s3)))
	task, err = h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, []string{full[4].ID, full[6].ID}, ids(task.NewEvents))
	require.NoError(t, h.b.ExtendWorkflowTask(ctx, task))

	s4, err := h.b.ResetWorkflowInstance(ctx, s3, "s4", 3)
	require.NoError(t, err)
	hist := h.history(s4)
	require.Equal(t, ids(full[:3]), ids(hist))
	after := int64(1)
	suffix, err := h.b.GetWorkflowInstanceHistory(ctx, s4, &after)
	require.NoError(t, err)
	require.Equal(t, ids(full[1:3]), ids(suffix))
	for i, e := range hist {
		require.Equal(t, int64(i+1), e.SequenceID)
		require.Equal(t, full[i].Attributes, e.Attributes)
	}
	// ancestors are untouched
	require.Equal(t, ids(s2full), ids(h.history(s2)))
	require.Equal(t, ids(full), ids(h.history(s1)))
}

func TestResetAdoptsActivityLeaseAndRejectsAbandoned(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("a")
	a1, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive) // seq 1-3
	cut := int64(3)
	// lease the activity
	at, err := h.b.GetActivityTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, a1.ID, at.ActivityID)
	// the suffix schedules a second activity in slot 2
	h.signal("a", "go")
	a2, c2 := activityCmd(2)
	h.run(s1, c2, core.WorkflowInstanceStateActive)

	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", cut)
	require.NoError(t, err)
	// the abandoned queue entry is gone, the lease stays with the adopted one
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities"))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities WHERE execution_id='s2' AND id=? AND worker IS NOT NULL", a1.ID))

	// the recovered workflow reuses slot 2 for a new activity
	h.run(s2, commands{}, core.WorkflowInstanceStateActive) // consumes the signal
	h.signal("a", "again")
	b2, c3 := activityCmd(2)
	h.run(s2, c3, core.WorkflowInstanceStateActive)
	_ = b2

	// late result of abandoned activity cannot satisfy the new command
	bt := &backend.ActivityTask{ID: a2.ID, ActivityID: a2.ID, WorkflowInstance: s1, Event: a2}
	require.Error(t, h.b.CompleteActivityTask(ctx, bt, completion(2, "late")))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities WHERE id=?", b2.ID))
	require.Empty(t, h.pendingIDs(s2))

	// the leased activity completes through the old task handle
	r1 := completion(1, "ok")
	require.NoError(t, h.b.CompleteActivityTask(ctx, at, r1))
	require.Equal(t, []string{r1.ID}, h.pendingIDs(s2))
	// idempotent retry and conflict
	require.NoError(t, h.b.CompleteActivityTask(ctx, at, r1))
	require.Equal(t, []string{r1.ID}, h.pendingIDs(s2))
	require.Error(t, h.b.CompleteActivityTask(ctx, at, completion(1, "other")))
	require.Equal(t, []string{r1.ID}, h.pendingIDs(s2))
	// replacement still runs
	nt, err := h.b.GetActivityTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, b2.ID, nt.ActivityID)
}

func TestResetRecoversCompletedActivityWithoutRerun(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("c")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	at, err := h.b.GetActivityTask(ctx, q)
	require.NoError(t, err)
	r := completion(1, "done")
	require.NoError(t, h.b.CompleteActivityTask(ctx, at, r))
	h.signal("c", "s")
	h.run(s1, commands{}, core.WorkflowInstanceStateActive) // consumes result + signal (seq 4..6)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM activities"))
	task, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Len(t, task.NewEvents, 2)
	require.Equal(t, r.ID, task.NewEvents[0].ID)
	require.Equal(t, history.EventType_SignalReceived, task.NewEvents[1].Type)
	// second reset to the same boundary keeps the recorded result too
	_ = s2
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, task, core.WorkflowInstanceStateActive, append([]*history.Event{func() *history.Event {
		e := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})
		e.SequenceID = 4
		return e
	}()}, func() []*history.Event {
		for i, e := range task.NewEvents {
			e.SequenceID = int64(5 + i)
		}
		return task.NewEvents
	}()...), nil, nil, nil))
	s3, err := h.b.ResetWorkflowInstance(ctx, s2, "s3", 3)
	require.NoError(t, err)
	task, err = h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, "s3", task.WorkflowInstance.ExecutionID)
	require.Equal(t, r.ID, task.NewEvents[0].ID)
	require.Len(t, task.NewEvents, 2)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM activities"))
	_ = s3
}

func TestResetTimerRestoredAfterCancel(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("t")
	at := time.Now().Add(time.Hour)
	_, fired, c := timerCmd(1, at)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	h.signal("t", "s")
	cancel := history.NewPendingEvent(time.Unix(6, 0), history.EventType_TimerCanceled, &history.TimerCanceledAttributes{}, history.ScheduleEventID(1))
	h.run(s1, commands{events: []*history.Event{cancel}}, core.WorkflowInstanceStateActive)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM pending_events WHERE visible_at IS NOT NULL"))
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	future, err := h.b.GetFutureEvents(ctx)
	require.NoError(t, err)
	require.Len(t, future, 1)
	require.Equal(t, fired.ID, future[0].ID)
	require.Equal(t, at.UnixNano(), future[0].VisibleAt.UnixNano())
	require.Equal(t, fired.Attributes.(*history.TimerFiredAttributes).At.UnixNano(), future[0].Attributes.(*history.TimerFiredAttributes).At.UnixNano())
	// the consumed signal is recovered
	require.Len(t, h.pendingIDs(s2), 2)
}

func TestResetRetryAndConflicts(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("r")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	h.signal("r", "s")
	h.run(s1, commands{}, core.WorkflowInstanceStateActive)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	h.run(s2, commands{}, core.WorkflowInstanceStateActive)
	before := h.count("SELECT COUNT(*) FROM history")
	again, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	require.Equal(t, s2.ExecutionID, again.ExecutionID)
	require.Equal(t, before, h.count("SELECT COUNT(*) FROM history"))
	_, err = h.b.ResetWorkflowInstance(ctx, s1, "s2", 6)
	require.Error(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, s2, "e1", 3)
	require.Error(t, err)
	// state of s2 is untouched
	state, err := h.b.GetWorkflowInstanceState(ctx, s2)
	require.NoError(t, err)
	require.Equal(t, core.WorkflowInstanceStateActive, state)
}

func TestResetFencesOldAcquisition(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("f")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	h.signal("f", "s")
	task, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	executed := []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
	executed = append(executed, task.NewEvents...)
	for i, e := range executed {
		e.SequenceID = 4 + int64(i)
	}
	require.Error(t, h.b.CompleteWorkflowTask(ctx, task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))
	require.Error(t, h.b.ExtendWorkflowTask(ctx, task))
	require.Equal(t, 3, len(h.history(s1)))
}

func TestResetChildKeepsParentAndRoutesResult(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	p1 := h.start("p")
	child := core.NewSubWorkflowInstance("child", "ce", p1, 1)
	sched := history.NewPendingEvent(time.Unix(2, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: child, Name: "C"}, history.ScheduleEventID(1))
	startMsg := &history.WorkflowEvent{WorkflowInstance: child, HistoryEvent: history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "C", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})}
	h.run(p1, commands{events: []*history.Event{sched}, messages: []*history.WorkflowEvent{startMsg}}, core.WorkflowInstanceStateActive)
	p2, err := h.b.ResetWorkflowInstance(ctx, p1, "p2", 4-1)
	require.NoError(t, err)
	// child task
	ct, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, "child", ct.WorkflowInstance.InstanceID)
	require.Equal(t, "e1", ct.WorkflowInstance.Parent.ExecutionID)
	done := history.NewPendingEvent(time.Unix(7, 0), history.EventType_SubWorkflowCompleted, &history.SubWorkflowCompletedAttributes{Result: payload.Payload("r")}, history.ScheduleEventID(1))
	executed := []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{}), ct.NewEvents[0]}
	executed[0].SequenceID, executed[1].SequenceID = 1, 2
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, ct, core.WorkflowInstanceStateFinished, executed, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: p1, HistoryEvent: done}}))
	require.Equal(t, []string{done.ID}, h.pendingIDs(p2))
	require.Empty(t, h.pendingIDs(p1))

	// reset the child: it keeps its parent
	_ = p2
}

func TestResetChildResetKeepsParent(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	p := core.NewWorkflowInstance("p", "e1")
	child := core.NewSubWorkflowInstance("child", "ce", p, 7)
	start := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "C", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(t, h.b.CreateWorkflowInstance(ctx, child, start))
	_, c := activityCmd(1)
	h.run(child, c, core.WorkflowInstanceStateActive)
	c2, err := h.b.ResetWorkflowInstance(ctx, child, "ce2", 3)
	require.NoError(t, err)
	require.True(t, c2.SubWorkflow())
	require.Equal(t, "e1", c2.Parent.ExecutionID)
	require.Equal(t, int64(7), c2.ParentEventID)
}

func TestResetRetentionAndReferences(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("x")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	s3, err := h.b.ResetWorkflowInstance(ctx, s2, "s3", 3)
	require.NoError(t, err)
	before := h.count("SELECT COUNT(*) FROM history")
	err = h.b.RemoveWorkflowInstance(ctx, s1)
	require.ErrorIs(t, err, ErrHistoryReferenced)
	require.ErrorIs(t, h.b.RemoveWorkflowInstance(ctx, s2), ErrHistoryReferenced)
	require.Equal(t, before, h.count("SELECT COUNT(*) FROM history"))
	require.Len(t, h.history(s3), 3)
	// bulk: s3 is active so nothing is removed
	require.NoError(t, h.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour))))
	require.Equal(t, 3, h.count("SELECT COUNT(*) FROM instances"))
	// finish s3
	h.signal("x", "fin")
	h.run(s3, commands{}, core.WorkflowInstanceStateFinished)
	require.NoError(t, h.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour)), backend.RemoveFinishedBatchSize(1)))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM instances"))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM history"))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_branches"))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_effects"))
}

func TestResetCorruptAncestry(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("k")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	_, err = h.b.db.Exec("DELETE FROM history WHERE instance_id='k' AND execution_id='e1' AND sequence_id=2")
	require.NoError(t, err)
	_, err = h.b.GetWorkflowInstanceHistory(ctx, s2, nil)
	require.Error(t, err)
	h.signal("k", "go")
	_, err = h.b.GetWorkflowTask(ctx, q)
	require.Error(t, err)
	_, err = h.b.db.Exec("UPDATE workflow_branches SET base_execution='s2' WHERE execution_id='s2'")
	require.NoError(t, err)
	_, err = h.b.GetWorkflowInstanceHistory(ctx, s2, nil)
	require.Error(t, err)
}

func TestResetMissingJournalCoverage(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("j")
	_, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	_, err := h.b.db.Exec("DELETE FROM workflow_effects")
	require.NoError(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.Error(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_branches"))
	state, _ := h.b.GetWorkflowInstanceState(ctx, s1)
	require.Equal(t, core.WorkflowInstanceStateActive, state)
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities WHERE execution_id='e1'"))
	_ = sql.ErrNoRows
	_ = errors.New
}

func TestResetTimerFiredAfterCutIsNotFiredTwice(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("tf")
	at := time.Now().Add(-time.Second)
	_, fired, c := timerCmd(1, at)
	// the timer is scheduled with an already elapsed time; it fires in the next task
	h.run(s1, c, core.WorkflowInstanceStateActive)
	h.run(s1, commands{}, core.WorkflowInstanceStateActive) // consumes TimerFired (seq 4,5)
	require.Equal(t, history.EventType_TimerFired, h.history(s1)[4].Type)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	require.Equal(t, []string{fired.ID}, h.pendingIDs(s2))
	// consume it in s2: no conflict, then a reset back to the same cut delivers it once
	h.run(s2, commands{}, core.WorkflowInstanceStateActive)
	require.Empty(t, h.pendingIDs(s2))
	s3, err := h.b.ResetWorkflowInstance(ctx, s2, "s3", 3)
	require.NoError(t, err)
	require.Equal(t, []string{fired.ID}, h.pendingIDs(s3))
	future, err := h.b.GetFutureEvents(ctx)
	require.NoError(t, err)
	require.Len(t, future, 1) // the delivery itself carries its original visibility
}

func TestResetNestedAdoptionKeepsLease(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	s1 := h.start("na")
	a1, c := activityCmd(1)
	h.run(s1, c, core.WorkflowInstanceStateActive)
	at, err := h.b.GetActivityTask(ctx, q)
	require.NoError(t, err)
	s2, err := h.b.ResetWorkflowInstance(ctx, s1, "s2", 3)
	require.NoError(t, err)
	s3, err := h.b.ResetWorkflowInstance(ctx, s2, "s3", 3)
	require.NoError(t, err)
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities WHERE execution_id='s3' AND id=? AND worker IS NOT NULL", a1.ID))
	require.NoError(t, h.b.ExtendActivityTask(ctx, at))
	r := completion(1, "ok")
	require.NoError(t, h.b.CompleteActivityTask(ctx, at, r))
	require.Equal(t, []string{r.ID}, h.pendingIDs(s3))
	require.Empty(t, h.pendingIDs(s2))
	// a late retry through the newest handle is idempotent too
	at3 := &backend.ActivityTask{ID: at.ID, ActivityID: at.ActivityID, WorkflowInstance: s3, Event: at.Event}
	require.NoError(t, h.b.CompleteActivityTask(ctx, at3, r))
	require.Equal(t, []string{r.ID}, h.pendingIDs(s3))
}

func TestResetChildResultsAndCancellation(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	p1 := h.start("pc")
	child := core.NewSubWorkflowInstance("cc", "ce", p1, 1)
	sched := history.NewPendingEvent(time.Unix(2, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: child, Name: "C"}, history.ScheduleEventID(1))
	startMsg := &history.WorkflowEvent{WorkflowInstance: child, HistoryEvent: history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "C", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})}
	h.run(p1, commands{events: []*history.Event{sched}, messages: []*history.WorkflowEvent{startMsg}}, core.WorkflowInstanceStateActive)
	// hold the child's task, so the parent is the only acquirable instance
	ct, err := h.b.GetWorkflowTask(ctx, q)
	require.NoError(t, err)
	require.Equal(t, "cc", ct.WorkflowInstance.InstanceID)
	// the parent requests cancellation of the child inside the retained prefix
	h.signal("pc", "cancel")
	cancelReq := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowCancellationRequested, &history.SubWorkflowCancellationRequestedAttributes{SubWorkflowInstance: child}, history.ScheduleEventID(1))
	h.run(p1, commands{events: []*history.Event{cancelReq}}, core.WorkflowInstanceStateActive) // seq 4,5,6 -> cut 6
	// the child finishes (cancelled) and its result is consumed after the cut
	failed := history.NewPendingEvent(time.Unix(7, 0), history.EventType_SubWorkflowFailed, &history.SubWorkflowFailedAttributes{}, history.ScheduleEventID(1))
	ex := []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{}), ct.NewEvents[0]}
	ex[0].SequenceID, ex[1].SequenceID = 1, 2
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, ct, core.WorkflowInstanceStateFinished, ex, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: p1, HistoryEvent: failed}}))
	h.run(p1, commands{}, core.WorkflowInstanceStateActive) // consumes the failure
	require.Len(t, h.history(p1), 8)
	// a pending cancellation is execution addressed and not carried over
	require.NoError(t, h.b.CancelWorkflowInstance(ctx, p1, history.NewWorkflowCancellationEvent(time.Now())))
	p2, err := h.b.ResetWorkflowInstance(ctx, p1, "p2", 6)
	require.NoError(t, err)
	require.Equal(t, []string{failed.ID}, h.pendingIDs(p2))
	// also the retained prefix without the cancellation request keeps the child open
	_, err = h.b.ResetWorkflowInstance(ctx, p2, "p3", 3)
	require.NoError(t, err)
}
