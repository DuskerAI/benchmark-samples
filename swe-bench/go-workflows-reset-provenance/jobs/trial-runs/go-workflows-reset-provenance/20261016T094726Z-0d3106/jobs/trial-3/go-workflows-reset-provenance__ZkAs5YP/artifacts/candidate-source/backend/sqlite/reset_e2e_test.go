package sqlite

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/client"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/registry"
	"github.com/cschleiden/go-workflows/worker"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func eventually(t *testing.T, what string, f func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if f() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func logicalOf(t *testing.T, b *sqliteBackend, i *core.WorkflowInstance) []*history.Event {
	t.Helper()
	h, err := b.GetWorkflowInstanceHistory(context.Background(), i, nil)
	require.NoError(t, err)
	return h
}

func finishedResult(t *testing.T, b *sqliteBackend, i *core.WorkflowInstance) (string, bool) {
	t.Helper()
	for _, e := range logicalOf(t, b, i) {
		if e.Type == history.EventType_WorkflowExecutionFinished {
			a := e.Attributes.(*history.ExecutionCompletedAttributes)
			require.Nil(t, a.Error)
			var out string
			require.NoError(t, b.Options().Converter.From(a.Result, &out))
			return out, true
		}
	}
	return "", false
}

type resetHarness struct {
	b      *sqliteBackend
	w      *worker.Worker
	c      *client.Client
	cancel context.CancelFunc
	first  atomic.Int32
	second atomic.Int32
	gate   chan struct{}
}

func newResetHarness(t *testing.T, wfn func(workflow.Context, string) (string, error), h *resetHarness) {
	h.b = NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	ctx, cancel := context.WithCancel(context.Background())
	h.cancel = cancel
	h.c = client.New(h.b)
	h.w = worker.New(h.b, &worker.DefaultOptions)
	h.gate = make(chan struct{})
	require.NoError(t, h.w.RegisterWorkflow(wfn, registry.WithName("Reset")))
	require.NoError(t, h.w.RegisterActivity(func(ctx context.Context) (string, error) { h.first.Add(1); return "A", nil }, registry.WithName("first")))
	require.NoError(t, h.w.RegisterActivity(func(ctx context.Context, in string) (string, error) {
		n := h.second.Add(1)
		if n == 1 {
			<-h.gate
		}
		return "B" + in, nil
	}, registry.WithName("second")))
	require.NoError(t, h.w.Start(ctx))
	t.Cleanup(func() {
		select {
		case <-h.gate:
		default:
			close(h.gate)
		}
		cancel()
		h.w.WaitForCompletion()
		h.b.Close()
	})
}

func lastSeq(t *testing.T, b *sqliteBackend, i *core.WorkflowInstance) int64 {
	h := logicalOf(t, b, i)
	if len(h) == 0 {
		return 0
	}
	return h[len(h)-1].SequenceID
}

func hasType(h []*history.Event, ty history.EventType) bool {
	for _, e := range h {
		if e.Type == ty {
			return true
		}
	}
	return false
}

func TestResetE2EAbandonedActivityCannotSatisfyReusedSlot(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		a, _ := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{}, "first").Get(ctx)
		s, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		r, err := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{RetryOptions: workflow.RetryOptions{MaxAttempts: 1}}, "second", s).Get(ctx)
		return a + s + r, err
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	eventually(t, "first activity done", func() bool {
		return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityCompleted)
	})
	cut := lastSeq(t, h.b, inst)
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "S"))
	eventually(t, "second in flight", func() bool { return h.second.Load() == 1 })
	eventually(t, "second scheduled", func() bool {
		return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityScheduled) && lastSeq(t, h.b, inst) > cut
	})

	reset, err := h.b.ResetWorkflowInstance(ctx, inst, "reset-1", cut)
	require.NoError(t, err)
	eventually(t, "reset finished", func() bool { _, ok := finishedResult(t, h.b, reset); return ok })
	out, _ := finishedResult(t, h.b, reset)
	require.Equal(t, "ASBS", out)
	require.Equal(t, int32(1), h.first.Load())
	require.Equal(t, int32(2), h.second.Load())

	// The abandoned activity finishes late and must not be accepted anywhere.
	close(h.gate)
	time.Sleep(100 * time.Millisecond)
	require.Equal(t, out, mustResult(t, h.b, reset))
}

func mustResult(t *testing.T, b *sqliteBackend, i *core.WorkflowInstance) string {
	out, ok := finishedResult(t, b, i)
	require.True(t, ok)
	return out
}

func TestResetE2EInFlightActivityLeaseAdopted(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		a, _ := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{}, "first").Get(ctx)
		r, err := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{RetryOptions: workflow.RetryOptions{MaxAttempts: 1}}, "second", "in").Get(ctx)
		return a + r, err
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	eventually(t, "second in flight", func() bool { return h.second.Load() == 1 })
	eventually(t, "second scheduled", func() bool {
		h := logicalOf(t, h.b, inst)
		return hasType(h, history.EventType_ActivityScheduled) && len(h) > 4
	})
	cut := lastSeq(t, h.b, inst)
	r1, err := h.b.ResetWorkflowInstance(ctx, inst, "r1", cut)
	require.NoError(t, err)
	r2, err := h.b.ResetWorkflowInstance(ctx, r1, "r2", cut)
	require.NoError(t, err)
	again, err := h.b.ResetWorkflowInstance(ctx, r1, "r2", cut)
	require.NoError(t, err)
	require.Equal(t, r2, again)
	_, err = h.b.ResetWorkflowInstance(ctx, r1, "r2", cut-1)
	require.Error(t, err)
	close(h.gate)
	eventually(t, "finished", func() bool { _, ok := finishedResult(t, h.b, r2); return ok })
	require.Equal(t, "ABin", mustResult(t, h.b, r2))
	require.Equal(t, int32(1), h.first.Load())
	require.Equal(t, int32(1), h.second.Load())
}

func TestResetE2ECompletedAfterCutNotRepeated(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		r, err := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{RetryOptions: workflow.RetryOptions{MaxAttempts: 1}}, "second", "in").Get(ctx)
		s, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		return r + s, err
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	eventually(t, "second in flight", func() bool { return h.second.Load() == 1 })
	eventually(t, "scheduled", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityScheduled) })
	cut := lastSeq(t, h.b, inst)
	close(h.gate)
	eventually(t, "completed", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityCompleted) })
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "S"))
	eventually(t, "finished", func() bool { _, ok := finishedResult(t, h.b, inst); return ok })

	// Original finished: not resettable.
	_, err = h.b.ResetWorkflowInstance(ctx, inst, "nope", cut)
	require.Error(t, err)
}

func TestResetE2ECompletedAndSignalAfterCutReplayed(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		r, err := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{RetryOptions: workflow.RetryOptions{MaxAttempts: 1}}, "second", "in").Get(ctx)
		s1, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		s2, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		s3, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		return r + s1 + s2 + s3, err
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	eventually(t, "second in flight", func() bool { return h.second.Load() == 1 })
	eventually(t, "scheduled", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityScheduled) })
	cut := lastSeq(t, h.b, inst)
	close(h.gate)
	eventually(t, "completed", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityCompleted) })
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "1"))
	eventually(t, "signal consumed", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_SignalReceived) })

	r1, err := h.b.ResetWorkflowInstance(ctx, inst, "r1", cut)
	require.NoError(t, err)
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "2"))
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "3"))
	eventually(t, "finished", func() bool { _, ok := finishedResult(t, h.b, r1); return ok })
	require.Equal(t, "Bin123", mustResult(t, h.b, r1))
	require.Equal(t, int32(1), h.second.Load())
}

func TestResetE2ENestedCutBeforeInheritedBoundary(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		a, _ := workflow.ExecuteActivity[string](ctx, workflow.ActivityOptions{}, "first").Get(ctx)
		ch := workflow.NewSignalChannel[string](ctx, "go")
		out := a
		for i := 0; i < 3; i++ {
			s, _ := ch.Receive(ctx)
			out += s
		}
		return out, nil
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	signals := func(i *core.WorkflowInstance) int {
		n := 0
		for _, e := range logicalOf(t, h.b, i) {
			if e.Type == history.EventType_SignalReceived {
				n++
			}
		}
		return n
	}
	eventually(t, "first done", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_ActivityCompleted) })
	c0 := lastSeq(t, h.b, inst)
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "1"))
	eventually(t, "s1", func() bool { return signals(inst) == 1 })
	c1 := lastSeq(t, h.b, inst)
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "2"))
	eventually(t, "s2", func() bool { return signals(inst) == 2 })

	r1, err := h.b.ResetWorkflowInstance(ctx, inst, "r1", c1)
	require.NoError(t, err)
	eventually(t, "r1 replays s2", func() bool { return signals(r1) == 2 })
	r2, err := h.b.ResetWorkflowInstance(ctx, r1, "r2", c0)
	require.NoError(t, err)
	require.Equal(t, c0, int64(len(logicalOf(t, h.b, r2))))
	eventually(t, "r2 replays both", func() bool { return signals(r2) == 2 })
	require.NoError(t, h.c.SignalWorkflow(ctx, inst.InstanceID, "go", "3"))
	eventually(t, "finished", func() bool { _, ok := finishedResult(t, h.b, r2); return ok })
	require.Equal(t, "A123", mustResult(t, h.b, r2))
	require.Equal(t, int32(1), h.first.Load())
	// Event identity is shared with the original history.
	orig := logicalOf(t, h.b, inst)
	got := logicalOf(t, h.b, r2)
	for i := int64(0); i < c0; i++ {
		require.Equal(t, orig[i].ID, got[i].ID)
	}
}

func TestResetE2ETimerAdopted(t *testing.T) {
	h := &resetHarness{}
	newResetHarness(t, func(ctx workflow.Context, _ string) (string, error) {
		if err := workflow.Sleep(ctx, 700*time.Millisecond); err != nil {
			return "", err
		}
		return "slept", nil
	}, h)
	ctx := context.Background()
	inst, err := h.c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "Reset", "x")
	require.NoError(t, err)
	eventually(t, "timer scheduled", func() bool { return hasType(logicalOf(t, h.b, inst), history.EventType_TimerScheduled) })
	r1, err := h.b.ResetWorkflowInstance(ctx, inst, "r1", lastSeq(t, h.b, inst))
	require.NoError(t, err)
	r2, err := h.b.ResetWorkflowInstance(ctx, r1, "r2", lastSeq(t, h.b, r1))
	require.NoError(t, err)
	eventually(t, "finished", func() bool { _, ok := finishedResult(t, h.b, r2); return ok })
	require.Equal(t, 1, countType(logicalOf(t, h.b, r2), history.EventType_TimerFired))
}

func countType(h []*history.Event, ty history.EventType) int {
	n := 0
	for _, e := range h {
		if e.Type == ty {
			n++
		}
	}
	return n
}
