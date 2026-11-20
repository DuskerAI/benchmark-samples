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
	"github.com/golang-migrate/migrate/v4"
	sqlitemigrate "github.com/golang-migrate/migrate/v4/database/sqlite"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/stretchr/testify/require"
)

type resetHarness struct {
	t *testing.T
	b *sqliteBackend
}

func newResetHarness(t *testing.T) *resetHarness {
	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	t.Cleanup(func() { b.Close() })
	return &resetHarness{t, b}
}

func (h *resetHarness) start(instanceID, execution string) *core.WorkflowInstance {
	instance := core.NewWorkflowInstance(instanceID, execution)
	start := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Reset", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(h.t, h.b.CreateWorkflowInstance(context.Background(), instance, start))
	return instance
}

type produceFunc = func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent)

// task runs one workflow task of the given execution. The produce callback receives the acquired task and returns
// the command events, activity, timer and message publications. Tasks of other executions are completed unchanged.
func (h *resetHarness) task(instance *core.WorkflowInstance, produce produceFunc) *backend.WorkflowTask {
	return h.taskWithState(instance, core.WorkflowInstanceStateActive, produce)
}

func (h *resetHarness) taskWithState(instance *core.WorkflowInstance, state core.WorkflowInstanceState, produce produceFunc) *backend.WorkflowTask {
	ctx := context.Background()
	for attempts := 0; attempts < 10; attempts++ {
		task, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
		require.NoError(h.t, err)
		require.NotNil(h.t, task)
		executed := []*history.Event{history.NewPendingEvent(time.Unix(2, 0), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}
		executed = append(executed, task.NewEvents...)
		var activities, timers []*history.Event
		var messages []*history.WorkflowEvent
		taskState := core.WorkflowInstanceStateActive
		if task.WorkflowInstance.ExecutionID == instance.ExecutionID {
			taskState = state
			if produce != nil {
				var commands []*history.Event
				commands, activities, timers, messages = produce(task)
				executed = append(executed, commands...)
			}
			if state == core.WorkflowInstanceStateFinished {
				executed = append(executed, history.NewPendingEvent(time.Now(), history.EventType_WorkflowExecutionFinished, &history.ExecutionCompletedAttributes{}))
			}
		}
		for i, e := range executed {
			e.SequenceID = task.LastSequenceID + int64(i) + 1
		}
		require.NoError(h.t, h.b.CompleteWorkflowTask(ctx, task, taskState, executed, activities, timers, messages))
		if task.WorkflowInstance.ExecutionID == instance.ExecutionID {
			return task
		}
	}
	h.t.Fatalf("no task for %s", instance.ExecutionID)
	return nil
}

func (h *resetHarness) history(instance *core.WorkflowInstance, after *int64) []*history.Event {
	events, err := h.b.GetWorkflowInstanceHistory(context.Background(), instance, after)
	require.NoError(h.t, err)
	return events
}

func (h *resetHarness) ids(events []*history.Event) []string {
	out := make([]string, len(events))
	for i, e := range events {
		out[i] = e.ID
	}
	return out
}

func (h *resetHarness) pending(instance *core.WorkflowInstance) []*history.Event {
	tx, err := h.b.db.Begin()
	require.NoError(h.t, err)
	defer tx.Rollback()
	events, err := getPendingForReset(context.Background(), tx, instance)
	require.NoError(h.t, err)
	return events
}

func (h *resetHarness) count(query string, args ...any) int {
	var n int
	require.NoError(h.t, h.b.db.QueryRow(query, args...).Scan(&n))
	return n
}

func activityEvent(sid int64) *history.Event {
	return history.NewPendingEvent(time.Unix(3, 0), history.EventType_ActivityScheduled, &history.ActivityScheduledAttributes{Name: "act"}, history.ScheduleEventID(sid))
}

func activityResult(sid int64, result string) *history.Event {
	return history.NewPendingEvent(time.Unix(4, 0), history.EventType_ActivityCompleted, &history.ActivityCompletedAttributes{}, history.ScheduleEventID(sid))
}

func signalEvent(name string) *history.Event {
	return history.NewPendingEvent(time.Unix(5, 0), history.EventType_SignalReceived, &history.SignalReceivedAttributes{Name: name})
}

func (h *resetHarness) acquireActivity() *backend.ActivityTask {
	task, err := h.b.GetActivityTask(context.Background(), []workflow.Queue{workflow.QueueDefault})
	require.NoError(h.t, err)
	return task
}

func TestResetSharesPrefixAcrossNestedResetsAndEarlierCuts(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("nested", "e0")
	a1 := activityEvent(1)
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{a1}, []*history.Event{a1}, nil, nil
	})
	require.NoError(t, h.b.SignalWorkflow(ctx, "nested", signalEvent("one")))
	h.task(root, nil)
	original := h.history(root, nil)
	require.Len(t, original, 5)

	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 5)
	require.NoError(t, err)
	require.NoError(t, h.b.SignalWorkflow(ctx, "nested", signalEvent("two")))
	h.task(e1, nil)
	e1History := h.history(e1, nil)
	require.Len(t, e1History, 7)
	require.Equal(t, h.ids(original), h.ids(e1History[:5]))
	for i, e := range e1History {
		require.Equal(t, int64(i+1), e.SequenceID)
	}

	// Cut before the inherited boundary.
	e2, err := h.b.ResetWorkflowInstance(ctx, e1, "e2", 3)
	require.NoError(t, err)
	e2History := h.history(e2, nil)
	require.Equal(t, h.ids(original[:3]), h.ids(e2History))
	after := int64(2)
	require.Equal(t, h.ids(original[2:3]), h.ids(h.history(e2, &after)))

	// The signal consumed after the cut is applied again, in order.
	pending := h.pending(e2)
	require.Len(t, pending, 2)
	require.Equal(t, history.EventType_SignalReceived, pending[0].Type)
	require.Equal(t, history.EventType_SignalReceived, pending[1].Type)
	require.Equal(t, e1History[4].ID, pending[0].ID)
	require.Equal(t, e1History[6].ID, pending[1].ID)

	e3, err := h.b.ResetWorkflowInstance(ctx, e2, "e3", 3)
	require.NoError(t, err)
	task, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, "e3", task.WorkflowInstance.ExecutionID)
	require.Equal(t, int64(3), task.LastSequenceID)
	require.Len(t, task.NewEvents, 2)
	require.NoError(t, h.b.ExtendWorkflowTask(ctx, task))
	require.Equal(t, h.ids(original[:3]), h.ids(h.history(e3, nil)))
}

func TestResetInvalidCuts(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("invalid", "e0")
	h.task(root, nil)
	require.NoError(t, h.b.SignalWorkflow(ctx, "invalid", signalEvent("s")))
	h.task(root, nil)
	before := h.history(root, nil)
	require.Len(t, before, 4)

	for _, cut := range []int64{-1, 0, 1, 3, 5, 100} {
		_, err := h.b.ResetWorkflowInstance(ctx, root, "bad", cut)
		require.Error(t, err, "cut %d", cut)
	}
	_, err := h.b.ResetWorkflowInstance(ctx, root, "e0", 2)
	require.Error(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, core.NewWorkflowInstance("invalid", "nope"), "x", 2)
	require.Error(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_branches"))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM instances WHERE state = ?", core.WorkflowInstanceStateActive))
	require.Equal(t, h.ids(before), h.ids(h.history(root, nil)))

	second, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 2)
	require.NoError(t, err)
	// Retry returns the same handle without a state change, also after progress.
	h.task(second, nil)
	again, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 2)
	require.NoError(t, err)
	require.Equal(t, second, again)
	_, err = h.b.ResetWorkflowInstance(ctx, root, "e1", 4)
	require.Error(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, second, "e1", 2)
	require.Error(t, err)
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM workflow_branches"))
}

func TestResetFencesOldWorkflowAcquisition(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("fence", "e0")
	h.task(root, nil)
	require.NoError(t, h.b.SignalWorkflow(ctx, "fence", signalEvent("s")))
	old, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.NotNil(t, old)

	_, err = h.b.ResetWorkflowInstance(ctx, root, "e1", 2)
	require.NoError(t, err)
	executed := append([]*history.Event{history.NewPendingEvent(time.Now(), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, old.NewEvents...)
	for i, e := range executed {
		e.SequenceID = old.LastSequenceID + int64(i) + 1
	}
	act := activityEvent(9)
	require.Error(t, h.b.CompleteWorkflowTask(ctx, old, core.WorkflowInstanceStateActive, append(executed, act), []*history.Event{act}, nil, nil))
	require.Error(t, h.b.ExtendWorkflowTask(ctx, old))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM activities"))
}

func TestResetAdoptsActivityLeaseAndRejectsAbandonedResult(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("slots", "e0")
	kept := activityEvent(1)
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{kept}, []*history.Event{kept}, nil, nil
	})
	leased := h.acquireActivity()
	require.NotNil(t, leased)
	require.Equal(t, kept.ID, leased.ActivityID)

	require.NoError(t, h.b.SignalWorkflow(ctx, "slots", signalEvent("s")))
	abandoned := activityEvent(2)
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{abandoned}, []*history.Event{abandoned}, nil, nil
	})
	staleLease := h.acquireActivity()
	require.NotNil(t, staleLease)
	require.Equal(t, abandoned.ID, staleLease.ActivityID)

	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.NoError(t, err)
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities"))

	// The replacement command reuses the numeric schedule id of the abandoned activity.
	require.NoError(t, h.b.SignalWorkflow(ctx, "slots", signalEvent("again")))
	replacement := activityEvent(2)
	h.task(e1, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{replacement}, []*history.Event{replacement}, nil, nil
	})
	require.Equal(t, 2, h.count("SELECT COUNT(*) FROM activities"))

	require.Error(t, h.b.CompleteActivityTask(ctx, staleLease, activityResult(2, "late")))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = ? AND event_type = ?", "e1", history.EventType_ActivityCompleted))
	require.Equal(t, 2, h.count("SELECT COUNT(*) FROM activities"))

	// The adopted lease is still usable.
	done := activityResult(1, "kept")
	require.NoError(t, h.b.CompleteActivityTask(ctx, leased, done))
	require.NoError(t, h.b.CompleteActivityTask(ctx, leased, done), "identical retry")
	conflicting := activityResult(1, "other")
	require.Error(t, h.b.CompleteActivityTask(ctx, leased, conflicting))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = ? AND event_type = ?", "e1", history.EventType_ActivityCompleted))
	var scheduleID int64
	require.NoError(t, h.b.db.QueryRow("SELECT schedule_event_id FROM pending_events WHERE execution_id = ? AND event_type = ?", "e1", history.EventType_ActivityCompleted).Scan(&scheduleID))
	require.Equal(t, int64(1), scheduleID)
}

func TestResetReusesCompletedActivityResult(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("done", "e0")
	act := activityEvent(1)
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{act}, []*history.Event{act}, nil, nil
	})
	work := h.acquireActivity()
	require.NoError(t, h.b.SignalWorkflow(ctx, "done", signalEvent("before")))
	result := activityResult(1, "x")
	require.NoError(t, h.b.CompleteActivityTask(ctx, work, result))
	require.NoError(t, h.b.SignalWorkflow(ctx, "done", signalEvent("pending")))
	// Consume the first two inputs, leave the last signal pending.
	task, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Len(t, task.NewEvents, 3)
	executed := append([]*history.Event{history.NewPendingEvent(time.Now(), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents[:2]...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i) + 1
	}
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))

	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.NoError(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM activities"))
	pending := h.pending(e1)
	require.Len(t, pending, 3)
	require.Equal(t, history.EventType_SignalReceived, pending[0].Type)
	require.Equal(t, history.EventType_ActivityCompleted, pending[1].Type)
	require.Equal(t, "pending", pending[2].Attributes.(*history.SignalReceivedAttributes).Name)
	require.Equal(t, "before", pending[0].Attributes.(*history.SignalReceivedAttributes).Name)

	// And again from the new execution.
	e2, err := h.b.ResetWorkflowInstance(ctx, e1, "e2", 3)
	require.NoError(t, err)
	require.Len(t, h.pending(e2), 3)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM activities"))
	require.NoError(t, h.b.CompleteActivityTask(ctx, work, result), "retry of accepted completion")
	require.Len(t, h.pending(e2), 3)
}

func TestResetRestoresCanceledTimerDelivery(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("timers", "e0")
	fireAt := time.Now().Add(time.Hour)
	scheduled := history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: fireAt}, history.ScheduleEventID(1))
	fire := history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{At: fireAt, ScheduledAt: time.Unix(3, 0)}, history.ScheduleEventID(1), history.VisibleAt(fireAt))
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{scheduled}, nil, []*history.Event{fire}, nil
	})
	require.NoError(t, h.b.SignalWorkflow(ctx, "timers", signalEvent("cancel")))
	canceled := history.NewPendingEvent(time.Unix(6, 0), history.EventType_TimerCanceled, &history.TimerCanceledAttributes{}, history.ScheduleEventID(1))
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{canceled}, nil, nil, nil
	})
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM pending_events WHERE visible_at IS NOT NULL"))

	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.NoError(t, err)
	var visible []*history.Event
	for _, e := range h.pending(e1) {
		if e.VisibleAt != nil {
			visible = append(visible, e)
		}
	}
	require.Len(t, visible, 1)
	require.Equal(t, fire.ID, visible[0].ID)
	require.WithinDuration(t, fireAt, *visible[0].VisibleAt, time.Millisecond)
	require.WithinDuration(t, fireAt, visible[0].Attributes.(*history.TimerFiredAttributes).At, time.Millisecond)
	// Only the original delivery, the cancellation signal is replayed as an input too.
	require.Len(t, h.pending(e1), 2)
}

func TestResetDoesNotFireTimerTwice(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("fired", "e0")
	fireAt := time.Now().Add(-time.Second)
	scheduled := history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerScheduled, &history.TimerScheduledAttributes{At: fireAt}, history.ScheduleEventID(1))
	fire := history.NewPendingEvent(time.Unix(3, 0), history.EventType_TimerFired, &history.TimerFiredAttributes{At: fireAt, ScheduledAt: time.Unix(3, 0)}, history.ScheduleEventID(1), history.VisibleAt(fireAt))
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{scheduled}, nil, []*history.Event{fire}, nil
	})
	h.task(root, nil) // the timer fires and is consumed after the cut
	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.NoError(t, err)
	pending := h.pending(e1)
	require.Len(t, pending, 1)
	require.Equal(t, fire.ID, pending[0].ID)

	// The recovered execution fires it and a further reset keeps a single delivery.
	h.task(e1, nil)
	e2, err := h.b.ResetWorkflowInstance(ctx, e1, "e2", 3)
	require.NoError(t, err)
	require.Len(t, h.pending(e2), 1)
}

func TestResetChildOwnershipAndAbandonedChildResults(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("parent", "e0")
	child := core.NewSubWorkflowInstance("child", "c0", root, 1)
	scheduled := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: child, Name: "child"}, history.ScheduleEventID(1))
	started := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "child", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{scheduled}, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: child, HistoryEvent: started}}
	})
	require.NoError(t, h.b.SignalWorkflow(ctx, "parent", signalEvent("x")))
	abandonedChild := core.NewSubWorkflowInstance("child2", "c2", root, 2)
	scheduled2 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: abandonedChild, Name: "child"}, history.ScheduleEventID(2))
	started2 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "child", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{scheduled2}, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: abandonedChild, HistoryEvent: started2}}
	})

	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.NoError(t, err)

	// Reuse the slot with a different child.
	replacement := core.NewSubWorkflowInstance("child3", "c3", e1, 2)
	scheduled3 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_SubWorkflowScheduled, &history.SubWorkflowScheduledAttributes{SubWorkflowInstance: replacement, Name: "child"}, history.ScheduleEventID(2))
	started3 := history.NewPendingEvent(time.Unix(3, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "child", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(t, h.b.SignalWorkflow(ctx, "parent", signalEvent("y")))
	h.task(e1, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{scheduled3}, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: replacement, HistoryEvent: started3}}
	})

	finish := func(c *core.WorkflowInstance, parent *core.WorkflowInstance, sid int64) {
		require.NoError(t, h.b.SignalWorkflow(ctx, c.InstanceID, signalEvent("go")))
		result := history.NewPendingEvent(time.Now(), history.EventType_SubWorkflowCompleted, &history.SubWorkflowCompletedAttributes{}, history.ScheduleEventID(sid))
		h.taskWithState(c, core.WorkflowInstanceStateFinished, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
			return nil, nil, nil, []*history.WorkflowEvent{{WorkflowInstance: parent, HistoryEvent: result}}
		})
	}
	countResults := func(execution string) int {
		return h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = ? AND event_type = ? AND schedule_event_id = ?", execution, history.EventType_SubWorkflowCompleted, 2)
	}
	finish(abandonedChild, root, 2) // abandoned
	require.Equal(t, 0, countResults("e0"))
	require.Equal(t, 0, countResults("e1"))

	// The inherited child reports to the recovered parent.
	finish(child, root, 1)
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = ? AND event_type = ? AND schedule_event_id = ?", "e1", history.EventType_SubWorkflowCompleted, 1))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = ? AND event_type = ?", "e0", history.EventType_SubWorkflowCompleted))
	require.Equal(t, 0, countResults("e1"))
}

func TestResetRetentionKeepsReferencedAncestry(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("retain", "e0")
	h.task(root, nil)
	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 2)
	require.NoError(t, err)
	e2, err := h.b.ResetWorkflowInstance(ctx, e1, "e2", 2)
	require.NoError(t, err)

	require.ErrorIs(t, h.b.RemoveWorkflowInstance(ctx, root), ErrHistoryReferenced)
	require.ErrorIs(t, h.b.RemoveWorkflowInstance(ctx, e1), ErrHistoryReferenced)
	require.Equal(t, 3, h.count("SELECT COUNT(*) FROM instances"))
	require.Len(t, h.history(e2, nil), 2)

	// Bulk removal keeps everything the active leaf needs.
	require.NoError(t, h.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour))))
	require.Equal(t, 3, h.count("SELECT COUNT(*) FROM instances"))

	// Finish the leaf and everything becomes reclaimable, leaves first.
	tx, err := h.b.db.Begin()
	require.NoError(t, err)
	_, err = tx.Exec("UPDATE instances SET state = ?, completed_at = ? WHERE execution_id = ?", core.WorkflowInstanceStateFinished, time.Now(), "e2")
	require.NoError(t, err)
	require.NoError(t, tx.Commit())
	require.NoError(t, h.b.RemoveWorkflowInstances(ctx, backend.RemoveFinishedBefore(time.Now().Add(time.Hour)), func(o *backend.RemovalOptions) { o.BatchSize = 1 }))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM instances"))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM history"))
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM workflow_branches"))
}

func TestResetDetectsCorruptAncestry(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("corrupt", "e0")
	h.task(root, nil)
	require.NoError(t, h.b.SignalWorkflow(ctx, "corrupt", signalEvent("s")))
	h.task(root, nil)
	e1, err := h.b.ResetWorkflowInstance(ctx, root, "e1", 4)
	require.NoError(t, err)
	require.Len(t, h.history(e1, nil), 4)

	exec := func(q string, args ...any) {
		_, err := h.b.db.Exec(q, args...)
		require.NoError(t, err)
	}
	// Gap in the inherited prefix.
	exec("UPDATE history SET sequence_id = 9 WHERE execution_id = 'e0' AND sequence_id = 3")
	_, err = h.b.GetWorkflowInstanceHistory(ctx, e1, nil)
	require.Error(t, err)
	_, err = h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err) // nothing pending
	exec("UPDATE history SET sequence_id = 3 WHERE execution_id = 'e0' AND sequence_id = 9")
	require.Len(t, h.history(e1, nil), 4)

	// Cycle.
	exec("INSERT INTO workflow_branches(instance_id,execution_id,base_execution,cut_sequence) VALUES('corrupt','e0','e1',2)")
	_, err = h.b.GetWorkflowInstanceHistory(ctx, e1, nil)
	require.Error(t, err)
	exec("DELETE FROM workflow_branches WHERE execution_id = 'e0'")

	// Missing ancestor.
	exec("DELETE FROM instances WHERE execution_id = 'e0'")
	_, err = h.b.GetWorkflowInstanceHistory(ctx, e1, nil)
	require.Error(t, err)
}

func TestResetLegacyHistoriesStayReadable(t *testing.T) {
	ctx := context.Background()
	b := NewSqliteBackend(t.TempDir()+"/legacy.sqlite", WithApplyMigrations(false), WithBackendOptions(backend.WithStickyTimeout(0)))
	defer b.Close()

	dbi, err := sqlitemigrate.WithInstance(b.db, &sqlitemigrate.Config{})
	require.NoError(t, err)
	source, err := iofs.New(migrationsFS, "db/migrations")
	require.NoError(t, err)
	m, err := migrate.NewWithInstance("iofs", source, "sqlite", dbi)
	require.NoError(t, err)
	require.NoError(t, m.Migrate(4))

	exec := func(q string, args ...any) {
		_, err := b.db.Exec(q, args...)
		require.NoError(t, err)
	}
	now := time.Now()
	for i, typ := range []history.EventType{history.EventType_WorkflowTaskStarted, history.EventType_WorkflowExecutionStarted, history.EventType_WorkflowTaskStarted, history.EventType_SignalReceived} {
		id := string(rune('a' + i))
		exec("INSERT INTO instances(id,execution_id,state,queue,metadata) VALUES('old','e0',0,'default','{}') ON CONFLICT DO NOTHING")
		exec("INSERT INTO history(id,sequence_id,instance_id,execution_id,event_type,timestamp,schedule_event_id) VALUES(?,?,?,?,?,?,0)", id, i+1, "old", "e0", typ, now)
		data := "{}"
		if typ == history.EventType_WorkflowExecutionStarted {
			data = `{"Name":"Reset","Queue":"default"}`
		}
		if typ == history.EventType_SignalReceived {
			data = `{"Name":"s"}`
		}
		exec("INSERT INTO attributes(id,instance_id,execution_id,data) VALUES(?,?,?,?)", id, "old", "e0", data)
	}
	require.NoError(t, b.Migrate())

	old := core.NewWorkflowInstance("old", "e0")
	events, err := b.GetWorkflowInstanceHistory(ctx, old, nil)
	require.NoError(t, err)
	require.Len(t, events, 4)
	// Legacy histories without outstanding work can be reset; with unknown publications they cannot.
	next, err := b.ResetWorkflowInstance(ctx, old, "e1", 2)
	require.NoError(t, err)
	events, err = b.GetWorkflowInstanceHistory(ctx, next, nil)
	require.NoError(t, err)
	require.Len(t, events, 2)
}

func TestResetFailureKeepsLeaseAndOwnership(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	root := h.start("atomic", "e0")
	act := activityEvent(1)
	h.task(root, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{act}, []*history.Event{act}, nil, nil
	})
	leased := h.acquireActivity()
	require.NotNil(t, leased)
	require.NoError(t, h.b.SignalWorkflow(ctx, "atomic", signalEvent("s")))
	task, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.NotNil(t, task)

	// Missing journal coverage is an error, nothing changes.
	_, err = h.b.db.Exec("DELETE FROM workflow_effects")
	require.NoError(t, err)
	_, err = h.b.ResetWorkflowInstance(ctx, root, "e1", 3)
	require.Error(t, err)
	require.Equal(t, 0, h.count("SELECT COUNT(*) FROM instances WHERE execution_id = 'e1'"))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM activities WHERE execution_id = 'e0' AND worker IS NOT NULL"))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM instances WHERE execution_id = 'e0' AND state = ? AND workflow_lease_id IS NOT NULL", core.WorkflowInstanceStateActive))
	require.Equal(t, 1, h.count("SELECT COUNT(*) FROM pending_events WHERE execution_id = 'e0'"))

	// The original acquisition can still finish.
	executed := append([]*history.Event{history.NewPendingEvent(time.Now(), history.EventType_WorkflowTaskStarted, &history.WorkflowTaskStartedAttributes{})}, task.NewEvents...)
	for i, e := range executed {
		e.SequenceID = task.LastSequenceID + int64(i) + 1
	}
	require.NoError(t, h.b.CompleteWorkflowTask(ctx, task, core.WorkflowInstanceStateActive, executed, nil, nil, nil))
}

func TestResetAdoptsUnleasedActivityAndKeepsChildParent(t *testing.T) {
	h := newResetHarness(t)
	ctx := context.Background()
	parent := core.NewWorkflowInstance("p", "pe0")
	child := core.NewSubWorkflowInstance("kid", "k0", parent, 4)
	start := history.NewPendingEvent(time.Unix(1, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Reset", Queue: workflow.QueueDefault, Metadata: &metadata.WorkflowMetadata{}})
	require.NoError(t, h.b.CreateWorkflowInstance(ctx, child, start))
	act := activityEvent(1)
	h.task(child, func(*backend.WorkflowTask) ([]*history.Event, []*history.Event, []*history.Event, []*history.WorkflowEvent) {
		return []*history.Event{act}, []*history.Event{act}, nil, nil
	})
	reset, err := h.b.ResetWorkflowInstance(ctx, child, "k1", 3)
	require.NoError(t, err)
	require.True(t, reset.SubWorkflow())
	require.Equal(t, parent.InstanceID, reset.Parent.InstanceID)
	require.Equal(t, parent.ExecutionID, reset.Parent.ExecutionID)
	require.Equal(t, int64(4), reset.ParentEventID)

	work := h.acquireActivity()
	require.NotNil(t, work)
	require.Equal(t, act.ID, work.ActivityID)
	require.Equal(t, "k1", work.WorkflowInstance.ExecutionID)
	require.Equal(t, act.Attributes, work.Event.Attributes)
	require.NoError(t, h.b.CompleteActivityTask(ctx, work, activityResult(1, "r")))
	task, err := h.b.GetWorkflowTask(ctx, []workflow.Queue{workflow.QueueDefault})
	require.NoError(t, err)
	require.Equal(t, "k1", task.WorkflowInstance.ExecutionID)
	require.True(t, task.WorkflowInstance.SubWorkflow())
	require.Equal(t, parent.ExecutionID, task.WorkflowInstance.Parent.ExecutionID)
}
