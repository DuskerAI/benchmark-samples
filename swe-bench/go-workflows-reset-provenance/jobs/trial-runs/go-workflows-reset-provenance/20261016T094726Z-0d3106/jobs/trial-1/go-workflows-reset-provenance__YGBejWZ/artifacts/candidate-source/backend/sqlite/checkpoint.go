package sqlite

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"time"

	"github.com/cschleiden/go-workflows/backend"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/core"
)

// normalizeEvent returns a copy of the event with all times in UTC. Only the
// representation is normalized, never the data.
func normalizeEvent(event *history.Event) *history.Event {
	e := *event
	e.Timestamp = e.Timestamp.UTC()
	if e.VisibleAt != nil {
		at := e.VisibleAt.UTC()
		e.VisibleAt = &at
	}
	switch a := e.Attributes.(type) {
	case *history.TimerScheduledAttributes:
		copy := *a
		copy.At = copy.At.UTC()
		e.Attributes = &copy
	case *history.TimerFiredAttributes:
		copy := *a
		copy.At = copy.At.UTC()
		copy.ScheduledAt = copy.ScheduledAt.UTC()
		e.Attributes = &copy
	}
	return &e
}

// checkpointDigest binds an acknowledgement to both its consumed inputs and
// every publication. Normalize only representation, never event order or data.
func checkpointDigest(task *backend.WorkflowTask, state core.WorkflowInstanceState, executed, activities, timers []*history.Event, messages []*history.WorkflowEvent) ([]byte, error) {
	normalize := func(events []*history.Event) ([]*history.Event, error) {
		out := make([]*history.Event, 0, len(events))
		for _, event := range events {
			if event == nil {
				return nil, fmt.Errorf("nil checkpoint event")
			}
			out = append(out, normalizeEvent(event))
		}
		return out, nil
	}
	input := *task
	var err error
	input.CustomData = nil // An implementation detail, not persisted checkpoint data.
	if input.NewEvents, err = normalize(input.NewEvents); err != nil {
		return nil, err
	}
	if executed, err = normalize(executed); err != nil {
		return nil, err
	}
	if activities, err = normalize(activities); err != nil {
		return nil, err
	}
	if timers, err = normalize(timers); err != nil {
		return nil, err
	}
	normalizedMessages := make([]*history.WorkflowEvent, 0, len(messages))
	for _, message := range messages {
		if message == nil {
			return nil, fmt.Errorf("nil workflow publication")
		}
		events, err := normalize([]*history.Event{message.HistoryEvent})
		if err != nil {
			return nil, err
		}
		copy := *message
		copy.HistoryEvent = events[0]
		normalizedMessages = append(normalizedMessages, &copy)
	}
	data, err := json.Marshal(struct {
		Task                         backend.WorkflowTask
		State                        core.WorkflowInstanceState
		Executed, Activities, Timers []*history.Event
		Messages                     []*history.WorkflowEvent
	}{input, state, executed, activities, timers, normalizedMessages})
	if err != nil {
		return nil, fmt.Errorf("encoding checkpoint identity: %w", err)
	}
	sum := sha256.Sum256(data)
	return sum[:], nil
}

// Keep the wall clock shared by acquisition, renewal, and ownership predicates.
func leaseValidAt() time.Time { return time.Now() }
