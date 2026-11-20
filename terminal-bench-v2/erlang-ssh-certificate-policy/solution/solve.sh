#!/bin/bash
set -euo pipefail
src=/app/src/ssh_policy.erl
perl -0pi -e 's/case maps:get\(active, St\) of\n                true -> St#\{pending => true/case maps:get(active, St) andalso not maps:get(pending, St) of\n                true -> St#{pending => true/' "$src"
perl -0pi -e 's/Final = records\(Records, St, Now, Principal, Address, Command, Trust\),\n        maps:get\(decision, Final\) andalso not maps:get\(invalid, Final\)/Final = records(Records, St, Now, Principal, Address, Command, Trust),\n        maps:get(decision, Final) andalso not maps:get(invalid, Final)\n          andalso maps:get(cbuf, Final) =:= <<>> andalso maps:get(sbuf, Final) =:= <<>>/' "$src"
perl -0pi -e 's/true -> St#\{active => true, pending => false\};/true -> St#{active => true, pending => false, sigalgs => undefined};/' "$src"
perl -0pi -e 's/case maps:get\(active, St\) of\n        true ->\n            case ext_info/case maps:get(active, St) andalso ext_negotiated(St) of\n        true ->\n            case ext_info/' "$src"
perl -0pi -e 's/choose\(\[\], _\) -> none;/ext_negotiated(St) ->\n    {CK,_} = maps:get(ckex, St), {SK,_} = maps:get(skex, St),\n    lists:member(<<"ext-info-c">>, CK) andalso lists:member(<<"ext-info-s">>, SK).\n\nchoose([], _) -> none;/' "$src"
perl -0pi -e 's/principal_member\(_, \[\]\) -> false;\nprincipal_member\(P, \[H\|T\]\) ->\n    case binary:match\(P,H\) of \{0,_\} -> true; _ -> principal_member\(P,T\) end\./principal_member(P, Ps) -> lists:member(P, Ps)./' "$src"
perl -0pi -e 's/After =< Now andalso Now =< Before/After =< Now andalso Now < Before/' "$src"
perl -0pi -e 's/try critical_entries\(Bin, Address, Command, undefined, undefined\)/try critical_entries(Bin, Address, Command, undefined, undefined, undefined)/; s/critical_entries\(<<>>, _, _, _, _\) -> true;\ncritical_entries\(B, A, C, SeenSource, SeenCommand\) ->\n    \{Name,R1\}=str\(B\), \{Value,R2\}=str\(R1\),/critical_entries(<<>>, _, _, _, _, _) -> true;\ncritical_entries(B, A, C, SeenSource, SeenCommand, Previous) ->\n    {Name,R1}=str(B), {Value,R2}=str(R1),\n    true = Previous =:= undefined orelse Previous < Name,/' "$src"
perl -0pi -e 's/critical_entries\(R2,A,C,true,SeenCommand\)/critical_entries(R2,A,C,true,SeenCommand,Name)/; s/critical_entries\(R2,A,C,SeenSource,true\)/critical_entries(R2,A,C,SeenSource,true,Name)/; s/_ -> critical_entries\(R2,A,C,SeenSource,SeenCommand\)/_ -> false/' "$src"
make -C /app
