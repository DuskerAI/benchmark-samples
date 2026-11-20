// Behavior only: all expected outcomes are in the trusted parent process.
package main

import (
 "context"
 "encoding/json"
 "fmt"
 "io"
 "log/slog"
 "os"
 "strings"
 "time"

 "github.com/benbjohnson/clock"
 "github.com/cschleiden/go-workflows/backend"
 "github.com/cschleiden/go-workflows/backend/converter"
 "github.com/cschleiden/go-workflows/backend/history"
 "github.com/cschleiden/go-workflows/backend/metadata"
 "github.com/cschleiden/go-workflows/backend/payload"
 "github.com/cschleiden/go-workflows/backend/sqlite"
 "github.com/cschleiden/go-workflows/core"
 "github.com/cschleiden/go-workflows/registry"
 wf "github.com/cschleiden/go-workflows/workflow"
 "github.com/cschleiden/go-workflows/workflow/executor"
)

type Action struct {
 Kind,Label,Source,Cut,Queue,Value,Name string
 Sequence int64
 Cold bool
}
type State struct {
 Current *core.WorkflowInstance
 Instances map[string]*core.WorkflowInstance
 Cuts map[string]int64
 Tasks map[string]*backend.WorkflowTask
 Results map[string]*executor.ExecutionResult
 Activities map[string]*backend.ActivityTask
 Phase string
}
type Request struct {
 Database,Nonce,Program,Converter string
 Fanout int
 Due bool
 State State
 Actions []Action
}
type Observation struct {
 Kind,Label,Error string
 Nil bool
 Instance *core.WorkflowInstance
 History []*history.Event
 Executed []*history.Event
 Output any
 Activity *backend.ActivityTask
 Trace []string
}
type Response struct { Observations []Observation; State State; Calls []string; Error string }
type envelopeConverter struct{}
func(envelopeConverter)To(v any)(payload.Payload,error){b,e:=json.Marshal(v);return append([]byte("envelope:"),b...),e}
func(envelopeConverter)From(b payload.Payload,target any)error{if !strings.HasPrefix(string(b),"envelope:"){return fmt.Errorf("invalid envelope")};return json.Unmarshal(b[len("envelope:"):],target)}
func textError(e error)string{if e==nil{return ""};return e.Error()}

func execute(req Request)Response{
 out:=Response{State:req.State,Observations:[]Observation{},Calls:[]string{}}
 s:=&out.State
 if s.Instances==nil{s.Instances=map[string]*core.WorkflowInstance{}}
 if s.Cuts==nil{s.Cuts=map[string]int64{}}
 if s.Tasks==nil{s.Tasks=map[string]*backend.WorkflowTask{}}
 if s.Results==nil{s.Results=map[string]*executor.ExecutionResult{}}
 if s.Activities==nil{s.Activities=map[string]*backend.ActivityTask{}}
 if s.Phase==""{s.Phase="old"}
 cv:=converter.DefaultConverter
 if req.Converter=="envelope"{cv=envelopeConverter{}}
 log:=slog.New(slog.NewTextHandler(io.Discard,nil))
 b:=sqlite.NewSqliteBackend(req.Database,sqlite.WithBackendOptions(backend.WithWorkerName("probe-worker"),backend.WithStickyTimeout(0),backend.WithLogger(log),backend.WithConverter(cv)))
 defer b.Close()
 ctx:=context.Background();executionClock:=clock.NewMock();executionClock.Set(time.Date(2025,1,1,0,0,0,0,time.UTC))
 traces:=map[string]*[]string{}
 active:=map[string]executor.WorkflowExecutor{}
 defer func(){for _,e:=range active{e.Close()}}()
 registryFor:=func(observed *[]string)*registry.Registry{
  r:=registry.New()
  root:=func(ctx wf.Context)([]string,error){
   trace:=[]string{}
   note:=func(value string){trace=append(trace,value);*observed=append([]string{},trace...)}
   seed,err:=wf.SideEffect(ctx,func(wf.Context)string{out.Calls=append(out.Calls,"seed");return "seed:"+req.Nonce}).Get(ctx);if err!=nil{return nil,err};note(seed)
   receive:=func(name string)(string,error){value,ok:=wf.NewSignalChannel[string](ctx,name).Receive(ctx);if !ok{return "",fmt.Errorf("signal closed")};note("signal:"+name+":"+value);return value,nil}
   switch req.Program{
   case "signals":
    for i:=0;i<3;i++{if _,err:=receive("steps");err!=nil{return nil,err}}
   case "parallel":
    count:=req.Fanout;if count==0{count=3};values:=make([]string,count);failures:=make([]error,count);wg:=wf.NewWaitGroup()
    for i:=0;i<count;i++{index:=i;wg.Add(1);wf.Go(ctx,func(ctx wf.Context){defer wg.Done();values[index],failures[index]=wf.ExecuteActivity[string](ctx,wf.ActivityOptions{RetryOptions:wf.RetryOptions{MaxAttempts:1}},fmt.Sprintf("work-%d",index),req.Nonce).Get(ctx)})}
    wg.Wait(ctx)
    for i,value:=range values{if failures[i]!=nil{return nil,failures[i]};note(fmt.Sprintf("actor:%d:%s",i,value))}
   case "timer":
    timerCtx,cancel:=wf.WithCancel(ctx)
    defer cancel()
    delay:=time.Duration(100*365*24)*time.Hour;if req.Due{delay=0}
    timer:=wf.ScheduleTimer(timerCtx,delay,wf.WithTimerName("hold"))
    if _,err:=receive("go");err!=nil{return nil,err}
    phase,err:=wf.SideEffect(ctx,func(wf.Context)string{out.Calls=append(out.Calls,"phase:"+s.Phase);return s.Phase}).Get(ctx);if err!=nil{return nil,err};note("phase:"+phase)
    if phase=="cancel"{cancel();note("timer:canceled")}
    if phase=="wait"{if _,err:=timer.Get(ctx);err!=nil{return nil,err};note("timer:fired")}
   default:
    anchor,err:=wf.ExecuteActivity[string](ctx,wf.ActivityOptions{RetryOptions:wf.RetryOptions{MaxAttempts:1}},"anchor",req.Nonce).Get(ctx);if err!=nil{return nil,err};note("anchor:"+anchor)
    phase,err:=wf.SideEffect(ctx,func(wf.Context)string{out.Calls=append(out.Calls,"phase:"+s.Phase);return s.Phase}).Get(ctx);if err!=nil{return nil,err};note("phase:"+phase)
    var value string
    if req.Program=="child"{
     value,err=wf.CreateSubWorkflowInstance[string](ctx,wf.SubWorkflowOptions{Queue:wf.Queue("audit"),RetryOptions:wf.RetryOptions{MaxAttempts:1}},"Child",phase).Get(ctx)
    }else{value,err=wf.ExecuteActivity[string](ctx,wf.ActivityOptions{RetryOptions:wf.RetryOptions{MaxAttempts:1}},"dispatch-"+phase,req.Nonce).Get(ctx)}
    if err!=nil{return nil,err};note("result:"+value)
   }
   if _,err:=receive("finish");err!=nil{return nil,err}
   return trace,nil
  }
  child:=func(ctx wf.Context,value string)(string,error){
   result,err:=wf.ExecuteActivity[string](ctx,wf.ActivityOptions{Queue:wf.Queue("audit"),RetryOptions:wf.RetryOptions{MaxAttempts:1}},"child-work",value).Get(ctx)
   return "child:"+result,err
  }
  if err:=r.RegisterWorkflow(root,registry.WithName("Root"));err!=nil{panic(err)}
  if err:=r.RegisterWorkflow(child,registry.WithName("Child"));err!=nil{panic(err)}
  return r
 }
 source:=func(label string)*core.WorkflowInstance{if label==""{return s.Current};return s.Instances[label]}
 event:=func(label string,kind history.EventType,attributes any)*history.Event{e:=history.NewPendingEvent(time.Date(2025,1,1,0,0,0,0,time.UTC),kind,attributes);e.ID=req.Nonce+"-"+label;return e}
 commit:=func(label string)error{task,result:=s.Tasks[label],s.Results[label];return b.CompleteWorkflowTask(ctx,task,result.State,result.Executed,result.ActivityEvents,result.TimerEvents,result.WorkflowEvents)}
 for _,a:=range req.Actions{
  observed:=Observation{Kind:a.Kind,Label:a.Label}
  switch a.Kind{
  case "create":
   instance:=core.NewWorkflowInstance(req.Nonce,"initial")
   observed.Error=textError(b.CreateWorkflowInstance(ctx,instance,event("start",history.EventType_WorkflowExecutionStarted,&history.ExecutionStartedAttributes{Name:"Root",Queue:wf.QueueDefault,Metadata:&metadata.WorkflowMetadata{"nonce":req.Nonce}})))
   if observed.Error==""{s.Current=instance;s.Instances["root"]=instance}
  case "phase":s.Phase=a.Value
  case "tick","execute":
   queue:=wf.QueueDefault;if a.Queue!=""{queue=wf.Queue(a.Queue)}
   task,err:=b.GetWorkflowTask(ctx,[]wf.Queue{queue});observed.Error=textError(err);observed.Nil=task==nil
   if err==nil&&task!=nil{
    observed.Instance=task.WorkflowInstance;key:=task.WorkflowInstance.InstanceID+"/"+task.WorkflowInstance.ExecutionID
    if a.Cold&&active[key]!=nil{active[key].Close();delete(active,key)}
    e:=active[key]
    if e==nil{
     trace:=&[]string{};traces[key]=trace
     e,err=executor.NewExecutor(log,b.Tracer(),registryFor(trace),cv,b.Options().ContextPropagators,b,task.WorkflowInstance,task.Metadata,executionClock,100000)
     if err==nil{active[key]=e}
    }
    if err==nil{
     result,executionErr:=e.ExecuteTask(ctx,task);observed.Error=textError(executionErr)
     if executionErr==nil{
      s.Tasks[a.Label]=task;s.Results[a.Label]=result;observed.Executed=result.Executed
      for _,e:=range result.Executed{if value,ok:=e.Attributes.(*history.ExecutionCompletedAttributes);ok{if value.Error!=nil{observed.Error=value.Error.Message}else{if err:=cv.From(value.Result,&observed.Output);err!=nil{observed.Error=err.Error()}}}}
      if a.Kind=="tick"&&observed.Error==""{observed.Error=textError(commit(a.Label))}
      if observed.Error==""&&a.Kind=="tick"{
       h,err:=b.GetWorkflowInstanceHistory(ctx,task.WorkflowInstance,nil);observed.Error=textError(err);observed.History=h
       if len(h)>0{s.Cuts[a.Label]=h[len(h)-1].SequenceID}
      }
     }
    }else{observed.Error=err.Error()}
    observed.Trace=append([]string{},(*traces[key])...)
   }
  case "commit":observed.Error=textError(commit(a.Source))
  case "activity":
   queue:=wf.QueueDefault;if a.Queue!=""{queue=wf.Queue(a.Queue)}
   task,err:=b.GetActivityTask(ctx,[]wf.Queue{queue});observed.Error=textError(err);observed.Nil=task==nil;observed.Activity=task
   if task!=nil{s.Activities[a.Label]=task}
  case "complete":
   task:=s.Activities[a.Source];value,err:=cv.To(a.Value);if err!=nil{panic(err)}
   result:=event("complete-"+a.Source+"-"+a.Value,history.EventType_ActivityCompleted,&history.ActivityCompletedAttributes{Result:value});result.ScheduleEventID=task.Event.ScheduleEventID
   observed.Error=textError(b.CompleteActivityTask(ctx,task,result))
  case "signal":
   value,err:=cv.To(a.Value);if err!=nil{panic(err)}
   observed.Error=textError(b.SignalWorkflow(ctx,req.Nonce,event("signal-"+a.Label,history.EventType_SignalReceived,&history.SignalReceivedAttributes{Name:a.Name,Arg:value})))
  case "reset":
   cut:=a.Sequence;if a.Cut!=""{cut=s.Cuts[a.Cut]}
   instance,err:=b.ResetWorkflowInstance(ctx,source(a.Source),req.Nonce+"-"+a.Label,cut)
   observed.Error=textError(err);observed.Instance=instance
   if err==nil{old:=source(a.Source);s.Instances[a.Label]=instance;if s.Current==nil||old.InstanceID==s.Current.InstanceID&&old.ExecutionID==s.Current.ExecutionID{s.Current=instance}}
  case "history":
   var after *int64;if a.Sequence>0{after=&a.Sequence}
   h,err:=b.GetWorkflowInstanceHistory(ctx,source(a.Source),after);observed.History=h;observed.Error=textError(err)
  case "child":
   for _,e:=range s.Results[a.Source].WorkflowEvents{if e.HistoryEvent.Type==history.EventType_WorkflowExecutionStarted{s.Instances[a.Label]=e.WorkflowInstance;observed.Instance=e.WorkflowInstance;break}}
   observed.Nil=observed.Instance==nil
  case "remove":observed.Error=textError(b.RemoveWorkflowInstance(ctx,source(a.Source)))
  case "collect":observed.Error=textError(b.RemoveWorkflowInstances(ctx,backend.RemoveFinishedBefore(time.Now().Add(time.Hour))))
  case "clear":for key,e:=range active{e.Close();delete(active,key)}
  default:panic("unknown action: "+a.Kind)
  }
  out.Observations=append(out.Observations,observed)
 }
 return out
}

func main(){out:=Response{};defer func(){if r:=recover();r!=nil{out.Error=fmt.Sprint(r)};json.NewEncoder(os.Stdout).Encode(out)}();var req Request;if err:=json.NewDecoder(os.Stdin).Decode(&req);err!=nil{out.Error=err.Error();return};out=execute(req)}
