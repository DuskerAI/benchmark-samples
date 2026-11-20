package executor_test

import (
	"context"
	"github.com/benbjohnson/clock"
	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/converter"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metadata"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/registry"
	wf "github.com/cschleiden/go-workflows/workflow"
	"github.com/cschleiden/go-workflows/workflow/executor"
	"go.opentelemetry.io/otel/trace/noop"
	"io"
	"log/slog"
	"testing"
	"time"
)

type journal struct{ events []*history.Event }

func (j *journal) GetWorkflowInstanceHistory(_ context.Context, _ *core.WorkflowInstance, after *int64) ([]*history.Event, error) {
	out := []*history.Event{}
	for _, e := range j.events {
		if after == nil || e.SequenceID > *after {
			out = append(out, e)
		}
	}
	return out, nil
}
func TestIncidentColdReplayPreservesActorCommands(t *testing.T) {
	ctx := context.Background()
	instance := core.NewWorkflowInstance("incident", "generation-1")
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	tracer := noop.NewTracerProvider().Tracer("incident")
	var calls int
	workflow := func(ctx wf.Context) error {
		wg := wf.NewWaitGroup()
		wg.Add(2)
		wf.Go(ctx, func(ctx wf.Context) {
			defer wg.Done()
			v, _ := wf.SideEffect(ctx, func(wf.Context) string { calls++; return "A" }).Get(ctx)
			wf.ExecuteActivity[string](ctx, wf.ActivityOptions{RetryOptions: wf.RetryOptions{MaxAttempts: 1}}, "deliver-A", v).Get(ctx)
		})
		wf.Go(ctx, func(ctx wf.Context) {
			defer wg.Done()
			wf.ExecuteActivity[string](ctx, wf.ActivityOptions{RetryOptions: wf.RetryOptions{MaxAttempts: 1}}, "deliver-B", "B").Get(ctx)
		})
		wg.Wait(ctx)
		return nil
	}
	r := registry.New()
	r.RegisterWorkflow(workflow, registry.WithName("Incident"))
	j := &journal{}
	makeExecutor := func() executor.WorkflowExecutor {
		e, err := executor.NewExecutor(log, tracer, r, converter.DefaultConverter, nil, j, instance, &metadata.WorkflowMetadata{}, clock.NewMock(), 10000)
		if err != nil {
			t.Fatal(err)
		}
		return e
	}
	hot := makeExecutor()
	defer hot.Close()
	first, err := hot.ExecuteTask(ctx, &backend.WorkflowTask{ID: "1", WorkflowInstance: instance, Metadata: &metadata.WorkflowMetadata{}, NewEvents: []*history.Event{history.NewPendingEvent(time.Unix(0, 0), history.EventType_WorkflowExecutionStarted, &history.ExecutionStartedAttributes{Name: "Incident"})}})
	if err != nil {
		t.Fatal(err)
	}
	j.events = first.Executed
	cold := makeExecutor()
	defer cold.Close()
	replay, err := cold.ExecuteTask(ctx, &backend.WorkflowTask{ID: "2", WorkflowInstance: instance, Metadata: &metadata.WorkflowMetadata{}, LastSequenceID: j.events[len(j.events)-1].SequenceID})
	if calls != 1 {
		t.Fatalf("committed callback replayed: %d calls", calls)
	}
	if len(first.ActivityEvents) != 2 {
		t.Fatalf("live actor requests: %d", len(first.ActivityEvents))
	}
	for _, event := range replay.Executed {
		if a, ok := event.Attributes.(*history.ExecutionCompletedAttributes); ok && a.Error != nil {
			t.Fatalf("cold replay changed command ownership: %v", a.Error)
		}
	}
	if len(replay.ActivityEvents) != 0 {
		t.Fatalf("replay rescheduled committed requests: %d", len(replay.ActivityEvents))
	}
}
