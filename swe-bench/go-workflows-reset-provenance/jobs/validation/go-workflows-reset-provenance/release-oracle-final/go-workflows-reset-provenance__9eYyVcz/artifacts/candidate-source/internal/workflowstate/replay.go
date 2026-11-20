package workflowstate

import (
	"fmt"
	"github.com/cschleiden/go-workflows/backend/history"
	"github.com/cschleiden/go-workflows/backend/payload"
)

// Inline results belong to one committed task. Hydration preserves the original
// coroutine turn; acknowledgement still follows the serialized history order.
type replayInlineResult struct {
	kind         history.EventType
	value        payload.Payload
	claimed      bool
	acknowledged bool
}

func (wf *WfState) PrepareReplayTurn(events []*history.Event) error {
	wf.ClearReplayTurn()
	wf.replayInline = make(map[int64]*replayInlineResult)
	for _, event := range events {
		var value payload.Payload
		switch event.Type {
		case history.EventType_TraceStarted:
			attr, ok := event.Attributes.(*history.TraceStartedAttributes)
			if !ok || attr == nil {
				return fmt.Errorf("invalid trace receipt")
			}
			value = attr.SpanID
		case history.EventType_SideEffectResult:
			attr, ok := event.Attributes.(*history.SideEffectResultAttributes)
			if !ok || attr == nil {
				return fmt.Errorf("invalid side effect receipt")
			}
			value = attr.Result
		default:
			continue
		}
		if event.ScheduleEventID < 1 || wf.replayInline[event.ScheduleEventID] != nil {
			return fmt.Errorf("duplicate or invalid inline receipt: %d", event.ScheduleEventID)
		}
		wf.replayInline[event.ScheduleEventID] = &replayInlineResult{kind: event.Type, value: value}
	}
	return nil
}

func (wf *WfState) failReplay(err error) error {
	if wf.replayFailure == nil {
		wf.replayFailure = err
	}
	return err
}

// HydrateReplayInline is deliberately limited to immediate operations. Activity,
// timer and child results must continue to wake their futures at their own event.
func (wf *WfState) HydrateReplayInline(id int64, kind history.EventType) error {
	result := wf.replayInline[id]
	if result == nil || result.kind != kind || result.claimed {
		return wf.failReplay(fmt.Errorf("inline receipt does not match command %d (%s)", id, kind))
	}
	future, ok := wf.FutureByScheduleEventID(id)
	if !ok {
		return wf.failReplay(fmt.Errorf("no future for inline command %d", id))
	}
	if err := future.Set(result.value, nil); err != nil {
		return wf.failReplay(err)
	}
	result.claimed = true
	wf.RemoveFuture(id)
	return nil
}

func (wf *WfState) AcknowledgeReplayInline(id int64, kind history.EventType) error {
	result := wf.replayInline[id]
	if result == nil || result.kind != kind || !result.claimed || result.acknowledged {
		return wf.failReplay(fmt.Errorf("unclaimed or repeated inline receipt %d (%s)", id, kind))
	}
	result.acknowledged = true
	return nil
}

func (wf *WfState) ReplayFailure() error { return wf.replayFailure }

func (wf *WfState) FinishReplayTurn() error {
	if wf.replayFailure != nil {
		return wf.replayFailure
	}
	for id, result := range wf.replayInline {
		if !result.claimed || !result.acknowledged {
			return fmt.Errorf("unconsumed inline receipt %d", id)
		}
	}
	return nil
}

func (wf *WfState) ClearReplayTurn() {
	wf.replayInline = nil
	wf.replayFailure = nil
}
