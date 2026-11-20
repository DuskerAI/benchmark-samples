-module(ssh_policy).
-export([main/0]).

main() ->
    Result = case init:get_plain_arguments() of
        [Capture, Now0, Principal, Address, Command] ->
            try
                {Now, []} = string:to_integer(Now0),
                {ok, Bin} = file:read_file(Capture),
                {ok, Trust} = trusted_key(),
                run(Bin, Now, list_to_binary(Principal), Address, list_to_binary(Command), Trust)
            catch _:_ -> false end;
        _ -> false
    end,
    io:format(case Result of true -> "ACCEPT~n"; _ -> "REJECT~n" end),
    halt(0).

trusted_key() ->
    case file:read_file("/app/trusted_ca.pub") of
        {ok, Line} ->
            case binary:split(string:trim(Line, both, "\n\r "), <<" ">>, [global]) of
                [<<"ssh-ed25519">>, B64 | _] ->
                    try
                        Blob = base64:decode(B64),
                        {<<"ssh-ed25519">>, R1} = str(Blob),
                        {Key, <<>>} = str(R1),
                        {ok, Key}
                    catch _:_ -> error end;
                _ -> error
            end;
        _ -> error
    end.

run(<<"SSHC", Rest/binary>>, Now, Principal, Address, Command, Trust) ->
    try
        {Session, Records} = str(Rest),
        St = #{session => Session, cbuf => <<>>, sbuf => <<>>, ckex => undefined,
               skex => undefined, cn => false, sn => false, active => false,
               pending => false, sigalgs => undefined, decision => false,
               invalid => false},
        Final = records(Records, St, Now, Principal, Address, Command, Trust),
        maps:get(decision, Final) andalso not maps:get(invalid, Final)
    catch _:_ -> false end;
run(_, _, _, _, _, _) -> false.

records(<<>>, St, _, _, _, _, _) -> St;
records(<<Dir:8, Len:16/big, Frag:Len/binary, Rest/binary>>, St0, Now, P, A, C, T)
  when Dir =:= 0; Dir =:= 1 ->
    Key = case Dir of 0 -> cbuf; 1 -> sbuf end,
    Buf = <<(maps:get(Key, St0))/binary, Frag/binary>>,
    {Packets, Left} = packets(Buf, []),
    St1 = lists:foldl(fun(Payload, S) -> message(Dir, Payload, S, Now, P, A, C, T) end,
                      St0#{Key => Left}, Packets),
    records(Rest, St1, Now, P, A, C, T);
records(_, St, _, _, _, _, _) -> St#{invalid => true}.

packets(<<Len:32/big, _/binary>> = Bin, Acc) when byte_size(Bin) >= Len + 4 ->
    <<_:32, Pad:8, Body:(Len-1)/binary, Rest/binary>> = Bin,
    PayloadLen = Len - Pad - 1,
    case Len >= 12 andalso Pad >= 4 andalso PayloadLen > 0 andalso (Len + 4) rem 8 =:= 0 of
        true ->
            <<Payload:PayloadLen/binary, _:Pad/binary>> = Body,
            packets(Rest, [Payload | Acc]);
        false -> {lists:reverse(Acc), <<16#ffffffff:32>>}
    end;
packets(Bin, Acc) -> {lists:reverse(Acc), Bin}.

message(Dir, <<20, Body/binary>>, St, _, _, _, _, _) ->
    case kexinit(Body) of
        {ok, Kex, Host} ->
            S0 = case maps:get(active, St) of
                true -> St#{pending => true, ckex => undefined, skex => undefined,
                            cn => false, sn => false};
                false -> St
            end,
            case Dir of
                0 -> S0#{ckex => {Kex, Host}};
                1 -> S0#{skex => {Kex, Host}}
            end;
        error -> St#{invalid => true}
    end;
message(Dir, <<21>>, St, _, _, _, _, _) ->
    S1 = case Dir of 0 -> St#{cn => true}; 1 -> St#{sn => true} end,
    activate(S1);
message(1, <<7, Body/binary>>, St, _, _, _, _, _) ->
    case maps:get(active, St) of
        true ->
            case ext_info(Body) of
                {ok, Algs} -> St#{sigalgs => Algs};
                error -> St#{invalid => true}
            end;
        false -> St#{invalid => true}
    end;
message(0, <<50, _/binary>> = Payload, St, Now, P, A, C, T) ->
    Allowed = maps:get(active, St) andalso not maps:get(pending, St)
              andalso maps:get(sigalgs, St) =/= undefined,
    D = Allowed andalso auth(Payload, maps:get(session, St), maps:get(sigalgs, St),
                             Now, P, A, C, T),
    St#{decision => maps:get(decision, St) orelse D};
message(_, _, St, _, _, _, _, _) -> St.

activate(St) ->
    case maps:get(cn, St) andalso maps:get(sn, St) andalso
         maps:get(ckex, St) =/= undefined andalso maps:get(skex, St) =/= undefined of
        true ->
            {CK, CH} = maps:get(ckex, St), {SK, SH} = maps:get(skex, St),
            case choose(CK, SK) =/= none andalso choose(CH, SH) =/= none of
                true -> St#{active => true, pending => false};
                false -> St#{invalid => true}
            end;
        false -> St
    end.

kexinit(<<_:16/binary, Rest/binary>>) ->
    try
        {Kex0, R1} = str(Rest), {Host0, R2} = str(R1),
        {_,R3}=str(R2), {_,R4}=str(R3), {_,R5}=str(R4), {_,R6}=str(R5),
        {_,R7}=str(R6), {_,R8}=str(R7), {_,R9}=str(R8), {_,R10}=str(R9),
        <<_:8, _:32, Tail/binary>> = R10, true = Tail =:= <<>>,
        {ok, names(Kex0), names(Host0)}
    catch _:_ -> error end;
kexinit(_) -> error.

choose([], _) -> none;
choose([H|T], Other) -> case lists:member(H, Other) of true -> H; false -> choose(T, Other) end.
names(<<>>) -> [];
names(B) -> binary:split(B, <<",">>, [global]).

ext_info(<<Count:32/big, Rest/binary>>) ->
    try
        {Map, <<>>} = ext_entries(Count, Rest, #{}),
        case maps:find(<<"server-sig-algs">>, Map) of
            {ok, V} -> {ok, names(V)};
            error -> error
        end
    catch _:_ -> error end;
ext_info(_) -> error.
ext_entries(0, R, M) -> {M, R};
ext_entries(N, R, M) ->
    {K,R1}=str(R), {V,R2}=str(R1), false=maps:is_key(K,M),
    ext_entries(N-1,R2,M#{K=>V}).

auth(<<50, Rest/binary>>, Session, SigAlgs, Now, Principal, Address, Command, Trust) ->
    try
        {_,R1}=str(Rest), {<<"ssh-connection">>,R2}=str(R1),
        {<<"publickey">>,R3}=str(R2), <<1,R4/binary>> = R3,
        {Alg,R5}=str(R4), {Cert,R6}=str(R5), {SigBlob,<<>>}=str(R6),
        true=lists:member(Alg,SigAlgs),
        Signed = <<(sshstr(Session))/binary, 50, (binary:part(Rest,0,byte_size(Rest)-byte_size(R6)))/binary>>,
        true=verify_sig_blob(SigBlob,Signed,cert_public(Cert)),
        cert(Cert, Alg, Now, Principal, Address, Command, Trust)
    catch _:_ -> false end.

cert_public(Cert) ->
    {<<"ssh-ed25519-cert-v01@openssh.com">>,R1}=str(Cert), {_,R2}=str(R1),
    {Pub,_}=str(R2), Pub.

cert(Cert, Alg, Now, Principal, Address, Command, Trust) ->
    {Alg,R1}=str(Cert), {_,R2}=str(R1), {_,R3}=str(R2),
    <<_:64,1:32,R4/binary>> = R3, {_,R5}=str(R4),
    {Principals0,R6}=str(R5), <<After:64,Before:64,R7/binary>> = R6,
    {Critical,R8}=str(R7), {_,R9}=str(R8), {Reserved,R10}=str(R9), true=Reserved=:=<<>>,
    {CaBlob,R11}=str(R10), SignedLen=byte_size(Cert)-byte_size(R11),
    {SigBlob,<<>>}=str(R11),
    {<<"ssh-ed25519">>,CR}=str(CaBlob), {Trust,<<>>}=str(CR),
    true=verify_sig_blob(SigBlob,binary:part(Cert,0,SignedLen),Trust),
    Ps = string_list(Principals0, []),
    principal_member(Principal, Ps) andalso After =< Now andalso Now =< Before
      andalso critical(Critical, Address, Command).

principal_member(_, []) -> false;
principal_member(P, [H|T]) ->
    case binary:match(P,H) of {0,_} -> true; _ -> principal_member(P,T) end.

critical(Bin, Address, Command) ->
    try critical_entries(Bin, Address, Command, undefined, undefined) catch _:_ -> false end.
critical_entries(<<>>, _, _, _, _) -> true;
critical_entries(B, A, C, SeenSource, SeenCommand) ->
    {Name,R1}=str(B), {Value,R2}=str(R1),
    case Name of
        <<"source-address">> ->
            false = SeenSource =:= true, {List,<<>>}=str(Value),
            true=source_ok(A,names(List)), critical_entries(R2,A,C,true,SeenCommand);
        <<"force-command">> ->
            false = SeenCommand =:= true, {Forced,<<>>}=str(Value),
            true=Forced=:=C, critical_entries(R2,A,C,SeenSource,true);
        _ -> critical_entries(R2,A,C,SeenSource,SeenCommand)
    end.

source_ok(_,[]) -> false;
source_ok(A,[H|T]) -> case cidr(A,H) of true -> true; false -> source_ok(A,T) end.
cidr(Address,Cidr) ->
    try
        [IP0,Bits0]=binary:split(Cidr,<<"/">>), {Bits,[]}=string:to_integer(binary_to_list(Bits0)),
        true=Bits>=0 andalso Bits=<32,
        {ok,IP}=inet:parse_ipv4_address(binary_to_list(IP0)),
        {ok,Given}=inet:parse_ipv4_address(Address),
        Mask = case Bits of 0 -> 0; _ -> (16#ffffffff bsl (32-Bits)) band 16#ffffffff end,
        (ipint(IP) band Mask) =:= (ipint(Given) band Mask)
    catch _:_ -> false end.
ipint({A,B,C,D}) -> (A bsl 24) bor (B bsl 16) bor (C bsl 8) bor D.

verify_sig_blob(Blob, Data, Pub) ->
    try
        {<<"ssh-ed25519">>,R}=str(Blob), {Sig,<<>>}=str(R), byte_size(Sig)=:=64
          andalso crypto:verify(eddsa,none,Data,Sig,[Pub,ed25519])
    catch _:_ -> false end.

string_list(<<>>, Acc) -> lists:reverse(Acc);
string_list(B, Acc) -> {S,R}=str(B), string_list(R,[S|Acc]).
str(<<N:32/big,S:N/binary,R/binary>>) -> {S,R}.
sshstr(B) -> <<(byte_size(B)):32/big,B/binary>>.
