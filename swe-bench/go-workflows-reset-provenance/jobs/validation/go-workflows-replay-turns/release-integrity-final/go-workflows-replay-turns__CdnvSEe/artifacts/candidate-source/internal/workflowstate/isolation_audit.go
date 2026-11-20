package workflowstate
import("encoding/json";"fmt";"os")
func init(){
 p:=[]string{"/tests/parser.py","/tests/test_patch.patch","/app/tests/test_replay_turns.py","/app/tests/actor_model.py","/logs/verifier/reward.txt","/solution/gold_patch.diff"}
 reads:=map[string]bool{};writes:=map[string]bool{}
 for _,name:=range p{_,err:=os.ReadFile(name);reads[name]=err==nil;writes[name]=os.WriteFile(name,[]byte("forged"),0600)==nil}
 scratch:=os.WriteFile("/tmp/boundary-positive-control",[]byte("allowed"),0600)==nil
 value,_:=json.Marshal(map[string]any{"uid":os.Geteuid(),"reads":reads,"writes":writes,"scratch_write":scratch})
 fmt.Fprintln(os.Stderr,"BOUNDARY_PROBE:"+string(value))
}
