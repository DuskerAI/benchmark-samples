package sqlite

import (
	"context"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/client"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/worker"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/stretchr/testify/require"
)

var e2eCalls atomic.Int32

func e2eFirst(ctx context.Context) (string, error) { e2eCalls.Add(1); return "one", nil }
func e2eSecond(ctx context.Context, in string) (string, error) {
	return in + "+two", nil
}

func e2eWorkflow(ctx workflow.Context) (string, error) {
	r1, err := workflow.ExecuteActivity[string](ctx, workflow.DefaultActivityOptions, e2eFirst).Get(ctx)
	if err != nil {
		return "", err
	}
	workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
	return workflow.ExecuteActivity[string](ctx, workflow.DefaultActivityOptions, e2eSecond, r1).Get(ctx)
}

func waitFor(t *testing.T, what string, f func() bool) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		if f() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for %s", what)
}

func TestResetEndToEndFileBackedWarm(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	path := filepath.Join(t.TempDir(), "reset.sqlite")
	b := NewSqliteBackend(path)
	w := worker.New(b, nil)
	require.NoError(t, w.RegisterWorkflow(e2eWorkflow))
	require.NoError(t, w.RegisterActivity(e2eFirst))
	require.NoError(t, w.RegisterActivity(e2eSecond))
	require.NoError(t, w.Start(ctx))
	c := client.New(b)
	e2eCalls.Store(0)

	instance, err := c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: "e2e"}, e2eWorkflow)
	require.NoError(t, err)
	waitFor(t, "first activity result consumed", func() bool {
		h, _ := b.GetWorkflowInstanceHistory(ctx, instance, nil)
		for _, e := range h {
			if e.Type == history.EventType_ActivityCompleted {
				return true
			}
		}
		return false
	})
	h, err := b.GetWorkflowInstanceHistory(ctx, instance, nil)
	require.NoError(t, err)
	cut := int64(0)
	for i, e := range h {
		if e.Type == history.EventType_WorkflowTaskStarted && i > 0 {
			cut = h[i-1].SequenceID
			break
		}
	}
	require.NotZero(t, cut)

	reset, err := b.ResetWorkflowInstance(ctx, instance, "e2e-reset", cut)
	require.NoError(t, err)
	// reset again, nested, to the same boundary
	reset2, err := b.ResetWorkflowInstance(ctx, reset, "e2e-reset-2", cut)
	require.NoError(t, err)
	waitFor(t, "recovered execution to consume the result", func() bool {
		h, _ := b.GetWorkflowInstanceHistory(ctx, reset2, nil)
		return int64(len(h)) > cut+1
	})
	require.NoError(t, c.SignalWorkflow(ctx, "e2e", "go", "x"))
	res, err := client.GetWorkflowResult[string](ctx, c, reset2, 20*time.Second)
	require.NoError(t, err)
	require.Equal(t, "one+two", res)
	require.EqualValues(t, 1, e2eCalls.Load(), "completed activity ran again")
	state, err := b.GetWorkflowInstanceState(ctx, reset)
	require.NoError(t, err)
	require.Equal(t, core.WorkflowInstanceStateFinished, state)

	// history survives process exit
	require.NoError(t, b.Close())
	b2 := NewSqliteBackend(path)
	defer b2.Close()
	full, err := b2.GetWorkflowInstanceHistory(ctx, reset2, nil)
	require.NoError(t, err)
	require.Equal(t, full[len(full)-1].Type, history.EventType_WorkflowExecutionFinished)
	for i, e := range full {
		require.EqualValues(t, i+1, e.SequenceID)
	}
	_ = backend.ErrInstanceNotFound
}
