package worker

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/benbjohnson/clock"
	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/metrics"
	"github.com/cschleiden/go-workflows/core"
	"github.com/cschleiden/go-workflows/internal/log"
	"github.com/cschleiden/go-workflows/internal/metrickeys"
	im "github.com/cschleiden/go-workflows/internal/metrics"
	"github.com/cschleiden/go-workflows/registry"
	"github.com/cschleiden/go-workflows/workflow"
	"github.com/cschleiden/go-workflows/workflow/executor"
	"github.com/cschleiden/go-workflows/workflow/executor/cache"
)

type WorkflowWorkerOptions struct {
	WorkerOptions

	WorkflowExecutorCache     executor.Cache
	WorkflowExecutorCacheSize int
	WorkflowExecutorCacheTTL  time.Duration
}

func NewWorkflowWorker(
	b backend.Backend,
	registry *registry.Registry,
	options WorkflowWorkerOptions,
) *Worker[backend.WorkflowTask, executor.ExecutionResult] {
	if options.WorkflowExecutorCache == nil {
		options.WorkflowExecutorCache = cache.NewWorkflowExecutorLRUCache(b.Metrics(), options.WorkflowExecutorCacheSize, options.WorkflowExecutorCacheTTL)
	}

	tw := &WorkflowTaskWorker{
		backend:  b,
		registry: registry,
		cache:    options.WorkflowExecutorCache,
		logger:   b.Options().Logger,
	}

	return NewWorker(b, tw, &options.WorkerOptions)
}

type WorkflowTaskWorker struct {
	backend     backend.Backend
	registry    *registry.Registry
	cache       executor.Cache
	logger      *slog.Logger
	cacheMu     sync.Mutex
	attempts    map[workflowAttempt]executor.WorkflowExecutor
	cacheOwners map[workflowIdentity]string
}

func (wtw *WorkflowTaskWorker) Start(ctx context.Context, queues []workflow.Queue) error {
	if wtw.cache != nil {
		go wtw.cache.StartEviction(ctx)
	}

	if err := wtw.backend.PrepareWorkflowQueues(ctx, queues); err != nil {
		return fmt.Errorf("preparing workflow queues: %w", err)
	}

	return nil
}

// Complete implements TaskWorker.
func (wtw *WorkflowTaskWorker) Complete(ctx context.Context, result *executor.ExecutionResult, t *backend.WorkflowTask) error {
	logger := wtw.logger.With(
		slog.String(log.TaskIDKey, t.ID),
		slog.String(log.InstanceIDKey, t.WorkflowInstance.InstanceID),
		slog.String(log.ExecutionIDKey, t.WorkflowInstance.ExecutionID),
	)

	state := result.State
	if err := wtw.backend.CompleteWorkflowTask(
		ctx, t, state, result.Executed, result.ActivityEvents, result.TimerEvents, result.WorkflowEvents); err != nil {
		// The executor already advanced; the database may have committed or rolled
		// back. Neither outcome permits retaining speculative state on an error.
		wtw.evictFailed(ctx, t)
		logger.ErrorContext(ctx, "could not complete workflow task", "error", err)
		return fmt.Errorf("completing workflow task: %w", err)
	}

	if state == core.WorkflowInstanceStateFinished || state == core.WorkflowInstanceStateContinuedAsNew {
		if t.WorkflowInstanceState != state {
			// If the workflow is now finished, record
			wtw.backend.Metrics().Counter(metrickeys.WorkflowInstanceFinished, metrics.Tags{
				metrickeys.SubWorkflow:    fmt.Sprint(t.WorkflowInstance.SubWorkflow()),
				metrickeys.ContinuedAsNew: fmt.Sprint(state == core.WorkflowInstanceStateContinuedAsNew),
			}, 1)
		}

		wtw.retireAttempt(ctx, t, true)
	}

	wtw.backend.Metrics().Counter(metrickeys.ActivityTaskScheduled, metrics.Tags{}, int64(len(result.ActivityEvents)))
	if state == core.WorkflowInstanceStateActive {
		wtw.retireAttempt(ctx, t, false)
	}

	return nil
}

func (wtw *WorkflowTaskWorker) Execute(ctx context.Context, t *backend.WorkflowTask) (*executor.ExecutionResult, error) {
	// Record how long this task was in the queue
	firstEvent := t.NewEvents[0]
	var scheduledAt time.Time
	if firstEvent.Type == history.EventType_TimerFired {
		timerFiredAttributes := firstEvent.Attributes.(*history.TimerFiredAttributes)
		scheduledAt = timerFiredAttributes.At // Use the timestamp of the timer fired event as the schedule time
	} else {
		scheduledAt = firstEvent.Timestamp // Use the timestamp of the first event as the schedule time
	}

	eventName := fmt.Sprint(firstEvent.Type)

	timeInQueue := time.Since(scheduledAt)
	wtw.backend.Metrics().Distribution(metrickeys.WorkflowTaskDelay, metrics.Tags{
		metrickeys.EventName: eventName,
	}, float64(timeInQueue/time.Millisecond))

	timer := im.NewTimer(wtw.backend.Metrics(), metrickeys.WorkflowTaskProcessed, metrics.Tags{
		metrickeys.EventName: eventName,
	})

	executor, err := wtw.getExecutor(ctx, t)
	if err != nil {
		return nil, fmt.Errorf("getting executor: %w", err)
	}

	result, err := executor.ExecuteTask(ctx, t)
	if err != nil {
		wtw.evictFailed(ctx, t)
		return nil, fmt.Errorf("executing task: %w", err)
	}

	// Only record the time spent in the workflow code
	timer.Stop()

	return result, nil
}

func (wtw *WorkflowTaskWorker) Extend(ctx context.Context, t *backend.WorkflowTask) error {
	return wtw.backend.ExtendWorkflowTask(ctx, t)
}

func (wtw *WorkflowTaskWorker) Get(ctx context.Context, queues []workflow.Queue) (*backend.WorkflowTask, error) {
	t, err := wtw.backend.GetWorkflowTask(ctx, queues)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return nil, nil
		}

		return nil, err
	}

	return t, nil
}

func (wtw *WorkflowTaskWorker) getExecutor(ctx context.Context, t *backend.WorkflowTask) (executor.WorkflowExecutor, error) {
	wtw.cacheMu.Lock()
	defer wtw.cacheMu.Unlock()
	key := workflowAttempt{workflowIdentity{t.WorkflowInstance.InstanceID, t.WorkflowInstance.ExecutionID}, t.ID}
	if wtw.attempts == nil {
		wtw.attempts = map[workflowAttempt]executor.WorkflowExecutor{}
		wtw.cacheOwners = map[workflowIdentity]string{}
	}
	if _, ok := wtw.attempts[key]; ok {
		return nil, fmt.Errorf("workflow attempt is already executing")
	}

	// Try to get a cached executor
	e, ok, err := wtw.cache.Get(ctx, t.WorkflowInstance)
	if err != nil {
		wtw.logger.ErrorContext(ctx, "could not get cached workflow task executor", "error", err)
	}

	if owner, exists := wtw.cacheOwners[key.instance]; exists {
		if _, pending := wtw.attempts[workflowAttempt{key.instance, owner}]; pending {
			ok = false
		}
	}
	if ok {
		wtw.cacheOwners[key.instance] = t.ID
	}
	if !ok {
		e, err = executor.NewExecutor(
			wtw.logger.With(
				slog.String(log.InstanceIDKey, t.WorkflowInstance.InstanceID),
				slog.String(log.ExecutionIDKey, t.WorkflowInstance.ExecutionID),
			),
			wtw.backend.Tracer(),
			wtw.registry,
			wtw.backend.Options().Converter,
			wtw.backend.Options().ContextPropagators,
			wtw.backend,
			t.WorkflowInstance,
			t.Metadata,
			clock.New(),
			wtw.backend.Options().MaxHistorySize,
		)
		if err != nil {
			return nil, fmt.Errorf("creating workflow task executor: %w", err)
		}
		e = &managedWorkflowExecutor{WorkflowExecutor: e}
	}
	wtw.attempts[key] = e

	// Cache executor instance for future continuation tasks, or refresh last access time
	if err := wtw.cache.Store(ctx, t.WorkflowInstance, e); err != nil {
		wtw.logger.ErrorContext(ctx, "error while caching workflow task executor:", "error", err)
	} else {
		wtw.cacheOwners[key.instance] = t.ID
	}

	return e, nil
}

type workflowIdentity struct{ instance, execution string }
type workflowAttempt struct {
	instance workflowIdentity
	id       string
}
type managedWorkflowExecutor struct {
	executor.WorkflowExecutor
	closeOnce sync.Once
}

func (e *managedWorkflowExecutor) Close() { e.closeOnce.Do(e.WorkflowExecutor.Close) }

func (wtw *WorkflowTaskWorker) evictFailed(ctx context.Context, t *backend.WorkflowTask) {
	wtw.retireAttempt(ctx, t, true)
}

// Backend acknowledgement is outside this short bookkeeping critical section.
// A late old result can retire only its own executor, never a successor's cache.
func (wtw *WorkflowTaskWorker) retireAttempt(ctx context.Context, t *backend.WorkflowTask, discard bool) {
	wtw.cacheMu.Lock()
	defer wtw.cacheMu.Unlock()
	key := workflowAttempt{workflowIdentity{t.WorkflowInstance.InstanceID, t.WorkflowInstance.ExecutionID}, t.ID}
	e, pending := wtw.attempts[key]
	if !pending {
		return
	}
	delete(wtw.attempts, key)
	owner := wtw.cacheOwners[key.instance]
	if owner == t.ID {
		if discard {
			if err := wtw.cache.Evict(context.WithoutCancel(ctx), t.WorkflowInstance); err != nil {
				wtw.logger.ErrorContext(ctx, "could not evict unacknowledged workflow executor", "error", err)
				e.Close()
			}
			delete(wtw.cacheOwners, key.instance)
		}
	} else {
		e.Close()
	}
	for other := range wtw.attempts {
		if other.instance == key.instance {
			return
		}
	}
	delete(wtw.cacheOwners, key.instance)
}
