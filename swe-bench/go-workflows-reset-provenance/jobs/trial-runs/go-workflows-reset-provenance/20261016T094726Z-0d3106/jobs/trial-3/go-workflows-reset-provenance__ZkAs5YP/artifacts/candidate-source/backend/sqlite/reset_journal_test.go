package sqlite

import (
	"context"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metadata"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/stretchr/testify/require"
)

type step struct {
	executed   []*history.Event
	activities []*history.Event
	timers     []*history.Event
	messages   []*history.WorkflowEvent
	state      core.WorkflowInstanceState
}

type synth struct {
	t *testing.T
	b *sqliteBackend
}

func newSynth(t *testing.T) *synth {
	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	t.Cleanup(func() { b.Close() })
	return &synth{t, b}
}

func (s *synth) start(id string) *core.WorkflowInstance {
	inst := core.NewWorkflowInstance(id, "e0")
	ev := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "W", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(s.t, s.b.CreateWorkflowInstance(context.Background(), inst, ev))
	return inst
}

// run executes one workflow task: build receives the acquired task and returns
// the commands; consumed inputs and the task marker are added automatically.
func (s *synth) run(build func(task *backend.WorkflowTask) step) *backend.WorkflowTask {
	s.t.Helper()
	task, err := s.b.GetWorkflowTask(context.Background(), []workflow.Queue{workflow.QueueDefault})
	require.NoError(s.t, err)
	require.NotNil(s.t, task)
	st := build(task)
	executed := append([]*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents...)
	executed = append(executed, st.executed...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i+1)
	}
	require.NoError(s.t, s.b.CompleteWorkflowTask(context.Background(), task, st.state, executed, st.activities, st.timers, st.messages))
	return task
}

// runFor drives workflow tasks, completing those of other instances without
// commands, until one for the given instance is built.
func (s *synth) runFor(id string, build func(task *backend.WorkflowTask) step) *backend.WorkflowTask {
	s.t.Helper()
	for {
		task, err := s.b.GetWorkflowTask(context.Background(), []workflow.Queue{workflow.QueueDefault})
		require.NoError(s.t, err)
		require.NotNil(s.t, task)
		st := step{}
		if task.WorkflowInstance.InstanceID == id {
			st = build(task)
		}
		executed := append([]*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents...)
		executed = append(executed, st.executed...)
		for i, e := range executed {
			e.SequenceID = task.LastSequenceID + int64(i+1)
		}
		require.NoError(s.t, s.b.CompleteWorkflowTask(context.Background(), task, st.state, executed, st.activities, st.timers, st.messages))
		if task.WorkflowInstance.InstanceID == id {
			return task
		}
	}
}

// drain completes all available workflow tasks without commands.
func (s *synth) drain() {
	for {
		task, err := s.b.GetWorkflowTask(context.Background(), []workflow.Queue{workflow.QueueDefault})
		require.NoError(s.t, err)
		if task == nil {
			return
		}
		executed := append([]*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents...)
		for i, e := range executed {
			e.SequenceID = task.LastSequenceID + int64(i+1)
		}
		require.NoError(s.t, s.b.CompleteWorkflowTask(context.Background(), task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))
	}
}

func (s *synth) pending(inst *core.WorkflowInstance) []*history.Event {
	tx, err := s.b.db.BeginTx(context.Background(), nil)
	require.NoError(s.t, err)
	defer tx.Rollback()
	p, err := getPendingForReset(context.Background(), tx, inst)
	require.NoError(s.t, err)
	return p
}

func (s *synth) count(query string, args ...any) int {
	var n int
	require.NoError(s.t, s.b.db.QueryRow(query, args...).Scan(&n))
	return n
}

func signal(name string) *history.Event {
	return history.NewPendingEvent(time.Unix(3, 0), history.EventType_SignalReceived, &history.SignalReceivedAttributes{Name: name})
}

func (s *synth) signal(id, name string) *history.Event {
	e := signal(name)
	require.NoError(s.t, s.b.SignalWorkflow(context.Background(), id, e))
	return e
}

func activityEvent(sid int64) *history.Event {
	return history.NewPendingEvent(time.Unix(2, 0), history.EventType_ActivityScheduled, &history.ActivityScheduledAttributes{Name: "act"}, history.ScheduleEventID(sid))
}

func ids(es []*history.Event) []string {
	out := []string{}
	for _, e := range es {
		out = append(out, e.ID)
	}
	return out
}

func TestResetSignalOrderingAndInputDedup(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("sig")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	cut := int64(2)
	s1 := s.signal("sig", "s1")
	s2 := s.signal("sig", "s2")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	s3 := s.signal("sig", "s3")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	s4 := s.signal("sig", "s4")
	s5 := s.signal("sig", "s5")

	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", cut)
	require.NoError(t, err)
	require.Equal(t, []string{s1.ID, s2.ID, s3.ID, s4.ID, s5.ID}, ids(s.pending(r1)))
	h := logicalOf(t, s.b, r1)
	require.Len(t, h, 2)

	// Run r1 to consume two signals, then reset before an inherited boundary.
	task := s.run(func(*backend.WorkflowTask) step { return step{} })
	require.Equal(t, r1.ExecutionID, task.WorkflowInstance.ExecutionID)
	require.Equal(t, int64(2), task.LastSequenceID)
	require.Len(t, task.NewEvents, 5)
	r2, err := s.b.ResetWorkflowInstance(ctx, r1, "r2", 2)
	require.NoError(t, err)
	require.Equal(t, []string{s1.ID, s2.ID, s3.ID, s4.ID, s5.ID}, ids(s.pending(r2)))
	got := logicalOf(t, s.b, r2)
	require.Equal(t, ids(h), ids(got))
	// Idempotent retry after later progress.
	s.run(func(*backend.WorkflowTask) step { return step{} })
	again, err := s.b.ResetWorkflowInstance(ctx, r1, "r2", 2)
	require.NoError(t, err)
	require.Equal(t, r2, again)
	_, err = s.b.ResetWorkflowInstance(ctx, r1, "r2", 1)
	require.Error(t, err)
	_, err = s.b.ResetWorkflowInstance(ctx, inst, "r2", 2)
	require.Error(t, err)
}

func TestResetCutValidation(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("cuts")
	s.run(func(*backend.WorkflowTask) step { return step{executed: []*history.Event{signal("x")}} })
	s.signal("cuts", "a")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	for _, cut := range []int64{-1, 0, 1, 2, 4, 6, 99} {
		_, err := s.b.ResetWorkflowInstance(ctx, inst, "bad", cut)
		require.Error(t, err, "cut %d", cut)
	}
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM workflow_branches"))
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM instances"))
	_, err := s.b.ResetWorkflowInstance(ctx, inst, "ok", 3)
	require.NoError(t, err)
}

func TestResetCorruptAncestryIsAnError(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("corrupt")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 2)
	require.NoError(t, err)
	s.signal("corrupt", "a")
	s.run(func(*backend.WorkflowTask) step { return step{} })

	// Gap in the inherited view.
	_, err = s.b.db.Exec("UPDATE history SET sequence_id = 7 WHERE execution_id = 'e0' AND sequence_id = 2")
	require.NoError(t, err)
	_, err = s.b.GetWorkflowInstanceHistory(ctx, r1, nil)
	require.Error(t, err)
	_, err = s.b.ResetWorkflowInstance(ctx, r1, "r2", 2)
	require.Error(t, err)
	_, err = s.b.db.Exec("UPDATE history SET sequence_id = 2 WHERE execution_id = 'e0' AND sequence_id = 7")
	require.NoError(t, err)
	_, err = s.b.GetWorkflowInstanceHistory(ctx, r1, nil)
	require.NoError(t, err)

	// Cycle
	_, err = s.b.db.Exec("UPDATE workflow_branches SET base_execution = 'r1' WHERE execution_id = 'e0' OR execution_id = 'r1'")
	require.NoError(t, err)
	_, err = s.b.GetWorkflowInstanceHistory(ctx, r1, nil)
	require.Error(t, err)
	_, err = s.b.db.Exec("UPDATE workflow_branches SET base_execution = 'e0' WHERE execution_id = 'r1'")
	require.NoError(t, err)

	// Missing ancestor
	_, err = s.b.db.Exec("DELETE FROM history WHERE execution_id = 'e0'")
	require.NoError(t, err)
	_, err = s.b.GetWorkflowInstanceHistory(ctx, r1, nil)
	require.Error(t, err)
	s.signal("corrupt", "b")
	task, err := s.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.Error(t, err)
	require.Nil(t, task)
}

func TestResetFencesOldAcquisition(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("fence")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	s.signal("fence", "a")
	old, err := s.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.NotNil(t, old)
	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 2)
	require.NoError(t, err)
	executed := append([]*history.Event{history.NewPendingEvent(time.Now(), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, old.NewEvents...)
	for i, e := range executed {
		e.SequenceID = old.LastSequenceID + int64(i+1)
	}
	require.Error(t, s.b.CompleteWorkflowTask(ctx, old, core.WorkflowInstanceStateActive, executed, nil, nil, nil))
	require.Error(t, s.b.ExtendWorkflowTask(ctx, old))
	require.Len(t, logicalOf(t, s.b, inst), 2)
	require.Equal(t, []string{old.NewEvents[0].ID}, ids(s.pending(r1)))
}

func TestResetTimerCancellationAfterCutRestoresDelivery(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("timer")
	at := time.Now().Add(time.Hour)
	scheduled := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: at, Name: "t"}, history.ScheduleEventID(5))
	fired := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{ScheduledAt: time.Unix(2, 0), At: at, Name: "t"}, history.ScheduleEventID(5), history.VisibleAt(at))
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{scheduled}, timers: []*history.Event{fired}}
	})
	s.signal("timer", "cancel")
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerCanceled, &history.TimerCanceledAttributes{}, history.ScheduleEventID(5))}}
	})
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM pending_events WHERE visible_at IS NOT NULL"))

	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 3)
	require.NoError(t, err)
	p := s.pending(r1)
	require.Len(t, p, 2) // reapplied signal, restored timer
	require.Equal(t, history.EventType_SignalReceived, p[0].Type)
	require.Equal(t, fired.ID, p[1].ID)
	require.True(t, p[1].VisibleAt.Equal(at))
	require.JSONEq(t, tfa(fired.Attributes), tfa(p[1].Attributes))
	fe, err := s.b.GetFutureEvents(ctx)
	require.NoError(t, err)
	require.Len(t, fe, 1)
	require.Equal(t, fired.ID, fe[0].ID)
}

// tfa compares attributes via their serialized form.
func tfa(a any) string {
	d, _ := history.SerializeAttributes(a)
	return string(d)
}

func (s *synth) acquireActivity() *backend.ActivityTask {
	task, err := s.b.GetActivityTask(context.Background(), []workflow.Queue{workflow.QueueDefault})
	require.NoError(s.t, err)
	require.NotNil(s.t, task)
	return task
}

func activityResult(task *backend.ActivityTask, v string) *history.Event {
	return history.NewPendingEvent(time.Unix(5, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{}, history.ScheduleEventID(task.Event.ScheduleEventID))
}

func TestResetActivityOwnershipAndSlotReuse(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("act")
	a7 := activityEvent(7)
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{a7}, activities: []*history.Event{a7}}
	})
	s.signal("act", "go")
	a8 := activityEvent(8)
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{a8}, activities: []*history.Event{a8}}
	})
	acquired := map[string]*backend.ActivityTask{}
	for i := 0; i < 2; i++ {
		task := s.acquireActivity()
		acquired[task.ActivityID] = task
	}
	t7, t8 := acquired[a7.ID], acquired[a8.ID]
	require.NotNil(t, t7)
	require.NotNil(t, t8)
	var lockedBefore time.Time
	require.NoError(t, s.b.db.QueryRow("SELECT locked_until FROM activities WHERE id = ?", a7.ID).Scan(&lockedBefore))

	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 3)
	require.NoError(t, err)
	var exec, worker string
	var lockedAfter time.Time
	require.NoError(t, s.b.db.QueryRow("SELECT execution_id, worker, locked_until FROM activities WHERE id = ?", a7.ID).Scan(&exec, &worker, &lockedAfter))
	require.Equal(t, "r1", exec)
	require.Equal(t, s.b.workerName, worker)
	require.True(t, lockedBefore.Equal(lockedAfter))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM activities WHERE id = ?", a8.ID))
	require.NoError(t, s.b.ExtendActivityTask(ctx, t7))

	// r1 replays and schedules a command that reuses slot 8.
	n8 := activityEvent(8)
	s.signal("act", "go")
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{n8}, activities: []*history.Event{n8}}
	})
	// The abandoned request completes late: rejected, replacement untouched.
	require.Error(t, s.b.CompleteActivityTask(ctx, t8, activityResult(t8, "late")))
	require.Equal(t, 0, len(s.pending(r1)))
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM activities WHERE id = ?", n8.ID))

	// In-flight lease still completes through the old task handle.
	res7 := activityResult(t7, "x")
	require.NoError(t, s.b.CompleteActivityTask(ctx, t7, res7))
	require.Equal(t, []string{res7.ID}, ids(s.pending(r1)))
	// Identical retry: no duplicate. Conflicting retry: error, no effects.
	require.NoError(t, s.b.CompleteActivityTask(ctx, t7, res7))
	require.Len(t, s.pending(r1), 1)
	conflict := activityResult(t7, "y")
	conflict.Attributes = &history.ActivityCompletedAttributes{Result: []byte("different")}
	conflict.ID = res7.ID
	require.Error(t, s.b.CompleteActivityTask(ctx, t7, conflict))
	require.Len(t, s.pending(r1), 1)

	// Reset again: the accepted result is reused, not run again.
	r2, err := s.b.ResetWorkflowInstance(ctx, r1, "r2", 3)
	require.NoError(t, err)
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM activities WHERE id = ?", a7.ID))
	p := s.pending(r2)
	require.Contains(t, ids(p), res7.ID)
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM activities WHERE execution_id = 'r2'"))
	// The late result for the settled work stays a no-op.
	require.NoError(t, s.b.CompleteActivityTask(ctx, t7, res7))
	require.Len(t, s.pending(r2), len(p))
}

func childStart(parent *core.WorkflowInstance, sid int64, childID string) (*history.Event, *history.WorkflowEvent) {
	ci := core.NewSubWorkflowInstance(childID, childID+"-x", parent, sid)
	sched := history.NewPendingEvent(time.Unix(2, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: ci, Name: "C"}, history.ScheduleEventID(sid))
	msg := &history.WorkflowEvent{WorkflowInstance: ci, HistoryEvent: history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "C", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})}
	return sched, msg
}

func TestResetChildOwnershipAndParentIdentity(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("parent")
	c1sched, c1msg := childStart(inst, 4, "child1")
	s.runFor("parent", func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{c1sched}, messages: []*history.WorkflowEvent{c1msg}}
	})
	s.signal("parent", "go")
	c2sched, c2msg := childStart(inst, 5, "child2")
	s.runFor("parent", func(task *backend.WorkflowTask) step {
		return step{executed: []*history.Event{c2sched}, messages: []*history.WorkflowEvent{c2msg}}
	})
	// let the children start
	s.drain()
	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 3)
	require.NoError(t, err)

	finish := func(child *core.WorkflowInstance) {
		s.signal(child.InstanceID, "finish")
		task := s.runFor(child.InstanceID, func(task *backend.WorkflowTask) step {
			require.Equal(t, child.Parent, task.WorkflowInstance.Parent)
			done := history.NewPendingEvent(time.Now(), history.EventType_SubWorkflowCompleted, &history.SubWorkflowCompletedAttributes{}, history.ScheduleEventID(child.ParentEventID))
			return step{
				state:    core.WorkflowInstanceStateFinished,
				executed: []*history.Event{history.NewPendingEvent(time.Now(), history.EventType_WorkflowExecutionFinished, &history.ExecutionCompletedAttributes{})},
				messages: []*history.WorkflowEvent{{WorkflowInstance: child.Parent, HistoryEvent: done}},
			}
		})
		require.Equal(t, child.InstanceID, task.WorkflowInstance.InstanceID)
	}
	// Abandoned child result is dropped; adopted child's result goes to the recovered parent.
	finish(c2msg.WorkflowInstance)
	require.Len(t, s.pending(r1), 0)
	finish(c1msg.WorkflowInstance)
	p := s.pending(r1)
	require.Len(t, p, 1)
	require.Equal(t, history.EventType_SubWorkflowCompleted, p[0].Type)
	require.EqualValues(t, 4, p[0].ScheduleEventID)
	require.Len(t, s.pending(inst), 0)
}

func TestResetChildKeepsItsParent(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("parent2")
	csched, cmsg := childStart(inst, 4, "kid")
	s.runFor("parent2", func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{csched}, messages: []*history.WorkflowEvent{cmsg}}
	})
	kid := cmsg.WorkflowInstance
	s.drain()
	rk, err := s.b.ResetWorkflowInstance(ctx, kid, "kid-r", 2)
	require.NoError(t, err)
	require.Equal(t, kid.Parent, rk.Parent)
	require.Equal(t, kid.ParentEventID, rk.ParentEventID)
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM instances WHERE id = 'kid' AND state = ?", core.WorkflowInstanceStateActive))
}

func TestResetRetentionKeepsReferencedAncestry(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("ret")
	s.run(func(*backend.WorkflowTask) step { return step{} })
	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 2)
	require.NoError(t, err)
	r2, err := s.b.ResetWorkflowInstance(ctx, r1, "r2", 2)
	require.NoError(t, err)

	require.ErrorIs(t, s.b.RemoveWorkflowInstance(ctx, inst), ErrHistoryReferenced)
	require.ErrorIs(t, s.b.RemoveWorkflowInstance(ctx, r1), ErrHistoryReferenced)
	require.ErrorIs(t, s.b.RemoveWorkflowInstance(ctx, r2), backend.ErrInstanceNotFinished)
	require.Len(t, logicalOf(t, s.b, r2), 2)

	// finish r2
	s.signal("ret", "x")
	s.run(func(*backend.WorkflowTask) step { return step{state: core.WorkflowInstanceStateFinished} })
	require.NoError(t, s.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(-time.Hour))))
	require.Len(t, logicalOf(t, s.b, r2), 4)
	require.NoError(t, s.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour)), backend.RemoveFinishedBatchSize(1)))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM instances"))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM history"))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM workflow_branches"))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM workflow_effects"))
}

func TestResetFiredTimerSuppliesDeliveryOnce(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("fired")
	at := time.Now().Add(-time.Second) // already due
	scheduled := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: at, Name: "t"}, history.ScheduleEventID(5))
	fired := history.NewPendingEvent(time.Unix(2, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{ScheduledAt: time.Unix(2, 0), At: at, Name: "t"}, history.ScheduleEventID(5), history.VisibleAt(at))
	s.run(func(*backend.WorkflowTask) step {
		// Not consumed yet: the delivery is only a pending timer.
		return step{executed: []*history.Event{scheduled}, timers: []*history.Event{fired}}
	})
	s.run(func(*backend.WorkflowTask) step { return step{} }) // consumes the fired timer
	s.signal("fired", "after")
	s.run(func(*backend.WorkflowTask) step { return step{} })

	r1, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 3)
	require.NoError(t, err)
	p := s.pending(r1)
	require.Equal(t, []string{fired.ID, p[1].ID}, ids(p))
	require.Equal(t, history.EventType_SignalReceived, p[1].Type)
	// Consuming it again does not conflict with the journaled firing.
	s.run(func(*backend.WorkflowTask) step { return step{} })
	h := logicalOf(t, s.b, r1)
	require.Equal(t, fired.ID, h[4].ID)
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM history WHERE execution_id = 'r1' AND event_type = ?", history.EventType_TimerFired))
}

func TestResetFailureLeavesSourceUntouched(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("atomic")
	a7 := activityEvent(7)
	s.run(func(*backend.WorkflowTask) step {
		return step{executed: []*history.Event{a7}, activities: []*history.Event{a7}}
	})
	s.signal("atomic", "go")
	task, err := s.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	acq := s.acquireActivity()

	// Journal coverage is missing: reset is refused rather than rescheduling.
	_, err = s.b.db.Exec("DELETE FROM workflow_effects")
	require.NoError(t, err)
	_, err = s.b.ResetWorkflowInstance(ctx, inst, "r1", 3)
	require.Error(t, err)
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM instances"))
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM instances WHERE state = ? AND workflow_lease_id = ?", core.WorkflowInstanceStateActive, task.ID))
	require.Equal(t, 1, s.count("SELECT COUNT(*) FROM activities WHERE execution_id = 'e0' AND id = ?", acq.ActivityID))
	require.Equal(t, 0, s.count("SELECT COUNT(*) FROM workflow_branches"))
	require.NoError(t, s.b.ExtendWorkflowTask(ctx, task))
}

func TestResetOldAcknowledgedCheckpointRetryStaysReadOnly(t *testing.T) {
	s := newSynth(t)
	ctx := context.Background()
	inst := s.start("receipt")
	committed := s.run(func(task *backend.WorkflowTask) step { return step{} })
	committedEvents := logicalOf(t, s.b, inst)
	_, err := s.b.ResetWorkflowInstance(ctx, inst, "r1", 2)
	require.NoError(t, err)
	// Retrying the identical acknowledged checkpoint is a no-op.
	require.NoError(t, s.b.CompleteWorkflowTask(ctx, committed, core.WorkflowInstanceStateActive, committedEvents, nil, nil, nil))
	require.Len(t, logicalOf(t, s.b, inst), 2)
}
