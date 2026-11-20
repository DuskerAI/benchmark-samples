package sqlite

import (
	"context"
	"fmt"
	"sync"
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

type resetE2E struct {
	first   atomic.Int32
	second  atomic.Int32
	gate    chan struct{}
	started chan struct{}
	once    sync.Once
}

func TestResetEndToEndRecoversWithoutRepeatingExternalWork(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	defer b.Close()

	env := &resetE2E{gate: make(chan struct{}), started: make(chan struct{})}
	var secondCalls atomic.Int32

	first := func(ctx context.Context) (string, error) {
		env.first.Add(1)
		return "one", nil
	}
	second := func(ctx context.Context) (string, error) {
		n := secondCalls.Add(1)
		if n == 1 {
			env.once.Do(func() { close(env.started) })
			select {
			case <-env.gate:
			case <-ctx.Done():
			}
		}
		return fmt.Sprintf("two-%d", n), nil
	}
	wfn := func(ctx workflow.Context) (string, error) {
		r1, err := workflow.ExecuteActivity[string](ctx, workflow.DefaultActivityOptions, "first").Get(ctx)
		if err != nil {
			return "", err
		}
		sig, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		r2, err := workflow.ExecuteActivity[string](ctx, workflow.DefaultActivityOptions, "second").Get(ctx)
		if err != nil {
			return "", err
		}
		return r1 + "/" + sig + "/" + r2, nil
	}

	options := worker.DefaultOptions
	options.WorkflowPollingInterval = 10 * time.Millisecond
	options.ActivityPollingInterval = 10 * time.Millisecond
	w := worker.New(b, &options)
	require.NoError(t, w.RegisterWorkflow(wfn, registry.WithName("resetE2E")))
	require.NoError(t, w.RegisterActivity(first, registry.WithName("first")))
	require.NoError(t, w.RegisterActivity(second, registry.WithName("second")))
	require.NoError(t, w.Start(ctx))

	c := client.New(b)
	instance, err := c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "resetE2E")
	require.NoError(t, err)

	require.Eventually(t, func() bool { return env.first.Load() == 1 }, 10*time.Second, 10*time.Millisecond)
	require.Eventually(t, func() bool {
		h, err := b.GetWorkflowInstanceHistory(ctx, instance, nil)
		return err == nil && len(h) >= 5
	}, 10*time.Second, 10*time.Millisecond)
	require.NoError(t, c.SignalWorkflow(ctx, instance.InstanceID, "go", "sig"))
	select {
	case <-env.started:
	case <-time.After(10 * time.Second):
		t.Fatal("second activity never started")
	}

	// Reset to the end of the first workflow task, the second activity is abandoned while its worker runs it.
	reset, err := b.ResetWorkflowInstance(ctx, instance, uuid.NewString(), 3)
	require.NoError(t, err)

	require.Eventually(t, func() bool { return secondCalls.Load() == 2 }, 10*time.Second, 10*time.Millisecond)
	result, err := client.GetWorkflowResult[string](ctx, c, reset, 10*time.Second)
	require.NoError(t, err)
	require.Equal(t, "one/sig/two-2", result)
	require.EqualValues(t, 1, env.first.Load(), "completed work was repeated")

	// The abandoned activity finishing late changes nothing.
	close(env.gate)
	time.Sleep(200 * time.Millisecond)
	state, err := b.GetWorkflowInstanceState(ctx, reset)
	require.NoError(t, err)
	require.Equal(t, core.WorkflowInstanceStateFinished, state)
	h, err := b.GetWorkflowInstanceHistory(ctx, reset, nil)
	require.NoError(t, err)
	for i, e := range h {
		require.EqualValues(t, i+1, e.SequenceID)
	}
}

func TestResetEndToEndAdoptsChildAndTimer(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	b := NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
	defer b.Close()

	childID := uuid.NewString()
	kid := func(ctx workflow.Context) (string, error) {
		workflow.NewSignalChannel[string](ctx, "kid-go").Receive(ctx)
		return "kid", nil
	}
	parent := func(ctx workflow.Context) (string, error) {
		f := workflow.CreateSubWorkflowInstance[string](ctx, workflow.SubWorkflowOptions{InstanceID: childID}, "kid")
		if err := workflow.Sleep(ctx, 500*time.Millisecond); err != nil {
			return "", err
		}
		sig, _ := workflow.NewSignalChannel[string](ctx, "go").Receive(ctx)
		r, err := f.Get(ctx)
		if err != nil {
			return "", err
		}
		return r + "/" + sig, nil
	}

	options := worker.DefaultOptions
	options.WorkflowPollingInterval = 10 * time.Millisecond
	options.ActivityPollingInterval = 10 * time.Millisecond
	w := worker.New(b, &options)
	require.NoError(t, w.RegisterWorkflow(parent, registry.WithName("parent")))
	require.NoError(t, w.RegisterWorkflow(kid, registry.WithName("kid")))
	require.NoError(t, w.Start(ctx))

	c := client.New(b)
	instance, err := c.CreateWorkflowInstance(ctx, client.WorkflowInstanceOptions{InstanceID: uuid.NewString()}, "parent")
	require.NoError(t, err)
	require.Eventually(t, func() bool {
		h, err := b.GetWorkflowInstanceHistory(ctx, instance, nil)
		return err == nil && len(h) >= 4
	}, 10*time.Second, 10*time.Millisecond)

	reset, err := b.ResetWorkflowInstance(ctx, instance, uuid.NewString(), 4)
	require.NoError(t, err)

	// Let the original delivery of the timer fire in the recovered execution.
	time.Sleep(700 * time.Millisecond)
	require.NoError(t, c.SignalWorkflow(ctx, instance.InstanceID, "go", "sig"))
	require.NoError(t, c.SignalWorkflow(ctx, childID, "kid-go", "x"))

	result, err := client.GetWorkflowResult[string](ctx, c, reset, 10*time.Second)
	require.NoError(t, err)
	require.Equal(t, "kid/sig", result)

	h, err := b.GetWorkflowInstanceHistory(ctx, reset, nil)
	require.NoError(t, err)
	fired, completed := 0, 0
	for _, e := range h {
		switch e.Type {
		case history.EventType_TimerFired:
			fired++
		case history.EventType_SubWorkflowCompleted:
			completed++
		}
	}
	require.Equal(t, 1, fired)
	require.Equal(t, 1, completed)
}
