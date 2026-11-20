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

func TestIncidentStaleCheckpointCannotCompleteReplacement(t *testing.T) {
 ctx := context.Background()
 b := NewInMemoryBackend(WithBackendOptions(backend.WithWorkerName("pool-7"),backend.WithStickyTimeout(0)))
 defer b.Close()
 instance := core.NewWorkflowInstance("incident", "execution-1")
 started := history.NewPendingEvent(time.Unix(1,0), history.EventType_WorkflowExecutionStarted,
  &history.ExecutionStartedAttributes{Name:"Incident",Queue:workflow.QueueDefault,Metadata:&metadata.WorkflowMetadata{}})
 if err:=b.CreateWorkflowInstance(ctx,instance,started);err!=nil{t.Fatal(err)}
 old,err:=b.GetWorkflowTask(ctx,[]workflow.Queue{workflow.QueueDefault});if err!=nil||old==nil{t.Fatalf("old acquisition: %v",err)}
 if _,err=b.db.ExecContext(ctx,"UPDATE instances SET locked_until = ?",time.Unix(0,0));err!=nil{t.Fatal(err)}
 replacement,err:=b.GetWorkflowTask(ctx,[]workflow.Queue{workflow.QueueDefault});if err!=nil||replacement==nil{t.Fatalf("replacement: %v",err)}
 started.SequenceID=1
 if err:=b.CompleteWorkflowTask(ctx,old,core.WorkflowInstanceStateActive,[]*history.Event{started},nil,nil,nil);err==nil{
  t.Fatal("stale acquisition checkpoint was accepted after replacement")
 }
 if err:=b.ExtendWorkflowTask(ctx,replacement);err!=nil{t.Fatalf("stale request damaged replacement lease: %v",err)}
}
