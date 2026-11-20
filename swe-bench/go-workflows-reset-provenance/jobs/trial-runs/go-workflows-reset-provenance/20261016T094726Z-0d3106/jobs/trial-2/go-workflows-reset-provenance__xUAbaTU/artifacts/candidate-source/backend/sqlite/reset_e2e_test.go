package sqlite

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/client"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/worker"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestResetRecoveredWorkflowCompletes(t *testing.T) {
	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	ctx, cancel := context.WithCancel(context.Background())
	c := client.New(b)
	w := worker.New(b, &worker.DefaultOptions)
	t.Cleanup(func() {
		cancel()
		require.NoError(t, w.WaitForCompletion())
		b.Close()
	})

	release := make(chan struct{})
	started := make(chan struct{}, 4)
	var runs atomic.Int32
	first := func(ctx context.Context) (int, error) {
		runs.Add(1)
		started <- struct{}{}
		<-release
		return 7, nil
	}
	second := func(ctx context.Context) (int, error) { return 35, nil }
	wf := func(ctx workflow.Context) (int, error) {
		a := workflow.ExecuteActivity[int](ctx, workflow.DefaultActivityOptions, first)
		workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		v1, err := a.Get(ctx)
		if err != nil {
			return 0, err
		}
		v2, err := workflow.ExecuteActivity[int](ctx, workflow.DefaultActivityOptions, second).Get(ctx)
		return v1 + v2, err
	}
	require.NoError(t, w.RegisterWorkflow(wf))
	require.NoError(t, w.RegisterActivity(first))
	require.NoError(t, w.RegisterActivity(second))
	require.NoError(t, w.Start(ctx))

	instance, err := c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, wf)
	require.NoError(t, err)
	<-started

	h, err := b.GetWorkflowInstanceHistory(ctx, instance, nil)
	require.NoError(t, err)
	cut := h[len(h)-1].SequenceID
	reset, err := b.ResetWorkflowInstance(ctx, instance, uuid.NewString(), cut)
	require.NoError(t, err)
	// The in-flight activity completes into the recovered execution.
	close(release)
	require.NoError(t, c.SignalWorkflow(ctx, instance.InstanceID, "go", "now"))

	result, err := client.GetWorkflowResult[int](ctx, c, reset, 10*time.Second)
	require.NoError(t, err)
	require.Equal(t, 42, result)
	require.Equal(t, int32(1), runs.Load())
	state, err := b.GetWorkflowInstanceState(ctx, instance)
	require.NoError(t, err)
	require.Equal(t, core.WorkflowInstanceStateFinished, state)
	h, err = b.GetWorkflowInstanceHistory(ctx, instance, nil)
	require.NoError(t, err)
	require.Len(t, h, int(cut)) // the retired source gained no completion
}
