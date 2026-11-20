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
)

func TestIncidentNestedResetKeepsOriginalPrefix(t *testing.T){
 ctx:=context.Background()
 b:=NewInMemoryBackend(WithBackendOptions(backend.WithStickyTimeout(0)))
 defer b.Close()
 instance:=core.NewWorkflowInstance("incident-reset","initial")
 start:=history.NewPendingEvent(time.Unix(1,0),history.EventType_WorkflowExecutionStarted,&history.ExecutionStartedAttributes{Name:"Incident",Queue:workflow.QueueDefault,Metadata:&metadata.WorkflowMetadata{}})
 if err:=b.CreateWorkflowInstance(ctx,instance,start);err!=nil{t.Fatal(err)}
 task,err:=b.GetWorkflowTask(ctx,[]workflow.Queue{workflow.QueueDefault});if err!=nil||task==nil{t.Fatalf("acquire: %v",err)}
 turn:=history.NewPendingEvent(time.Unix(1,0),history.EventType_WorkflowTaskStarted,&history.WorkflowTaskStartedAttributes{})
 activity:=history.NewPendingEvent(time.Unix(1,0),history.EventType_ActivityScheduled,&history.ActivityScheduledAttributes{Name:"reserve"},history.ScheduleEventID(7))
 events:=[]*history.Event{turn,start,activity};for i,e:=range events{e.SequenceID=int64(i+1)}
 if err:=b.CompleteWorkflowTask(ctx,task,core.WorkflowInstanceStateActive,events,[]*history.Event{activity},nil,nil);err!=nil{t.Fatal(err)}
 second,err:=b.ResetWorkflowInstance(ctx,instance,"second",3);if err!=nil{t.Fatal(err)}
 third,err:=b.ResetWorkflowInstance(ctx,second,"third",3);if err!=nil{t.Fatal(err)}
 restored,err:=b.GetWorkflowInstanceHistory(ctx,third,nil);if err!=nil{t.Fatal(err)}
 if len(restored)!=len(events){t.Fatalf("nested reset lost inherited prefix: got %d events, want %d",len(restored),len(events))}
 for i,e:=range events{if restored[i].ID!=e.ID{t.Fatalf("event identity changed at sequence %d",i+1)}}
}
