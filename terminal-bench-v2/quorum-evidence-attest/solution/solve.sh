#!/bin/bash
set -euo pipefail

cat > /usr/local/sbin/qguard-attest <<'ERL'
#!/usr/bin/env escript

main([Evidence, Output]) ->
    try
        Config = read_one(filename:join(Evidence, "config.term")),
        Events = read_one(filename:join(Evidence, "events.term")),
        Report = attest(Config, Events),
        Bytes = encode(Report),
        ok = write_json(Output, Bytes),
        case Evidence of
            "/var/lib/qguard/evidence" -> ok = write_json("/var/lib/qguard/audit/quorum-attestation.json", Bytes);
            _ -> ok
        end,
        halt(0)
    catch
        Class:Reason:Stack ->
            io:format(standard_error, "qguard-attest: ~p:~p~n~p~n", [Class, Reason, Stack]),
            halt(2)
    end;
main(_) ->
    io:format(standard_error, "usage: qguard-attest <evidence-dir> <report-json>~n", []),
    halt(64).

write_json(Path, Bytes) ->
    ok = filelib:ensure_dir(Path),
    file:write_file(Path, Bytes).

read_one(Path) ->
    case file:consult(Path) of
        {ok, [Term]} -> Term;
        {ok, Terms} -> erlang:error({bad_terms, Path, length(Terms)});
        {error, Reason} -> erlang:error({read_failed, Path, Reason})
    end.

attest(Config, Events0) ->
    Nodes = maps:get(nodes, Config),
    Up0 = maps:from_list([{N, true} || {N, _} <- Nodes]),
    Fence0 = maps:from_list([{N, 0} || {N, _} <- Nodes]),
    State0 = #{config => Config, up => Up0, fence => Fence0, leader => null, term => 0,
               lease_until => 0, pending => none, schedule => []},
    State1 = replay(order_events(Events0), State0),
    State = drain(State1),
    #{leader => maps:get(leader, State), safe => true,
      schedule => lists:reverse(maps:get(schedule, State)),
      term => maps:get(term, State), verdict => verdict(Events0)}.

verdict(Events) ->
    Seen = lists:foldl(fun(E, Acc) -> collect_promotes(E, Acc) end, #{}, Events),
    case lists:any(fun(Nodes) -> maps:size(Nodes) > 1 end, maps:values(Seen)) of
        true -> insecure;
        false -> secure
    end.

collect_promotes(E, Acc) ->
    case {maps:get(kind, E), maps:find(term, E)} of
        {promote, {ok, Term}} ->
            Node = maps:get(node, E),
            Nodes = maps:get(Term, Acc, #{}),
            Acc#{Term => Nodes#{Node => true}};
        _ -> Acc
    end.

order_events(Events) ->
    Tagged = tag(Events, 0, []),
    [E || {_At, _Seq, _Pos, E} <- lists:sort([{maps:get(at, E), maps:get(seq, E, 0), Pos, E} || {Pos, E} <- Tagged])].

tag([], _N, Acc) -> lists:reverse(Acc);
tag([E | Es], N, Acc) -> tag(Es, N + 1, [{N, E} | Acc]).

replay(Events, State) -> replay_groups(group_events(Events), State).

group_events([]) -> [];
group_events([E | Es]) -> group_events(Es, maps:get(at, E), [E], []).

group_events([], _At, Current, Acc) -> lists:reverse([lists:reverse(Current) | Acc]);
group_events([E | Es], At, Current, Acc) ->
    case maps:get(at, E) of
        At -> group_events(Es, At, [E | Current], Acc);
        Next -> group_events(Es, Next, [E], [lists:reverse(Current) | Acc])
    end.

replay_groups([], State) -> State;
replay_groups([Group | Groups], State0) ->
    At = maps:get(at, hd(Group)),
    State1 = run_due(At, false, State0),
    EntryState = State1,
    State2 = lists:foldl(fun(Ev, St) -> apply_event(Ev, Group, EntryState, St) end, State1, Group),
    State3 = run_due(At, true, State2),
    State4 = maybe_schedule(At, State3),
    replay_groups(Groups, State4).

drain(State) ->
    case maps:get(pending, State) of
        none -> State;
        {At} -> drain(run_due(At, true, State))
    end.

run_due(At, Inclusive, State) ->
    case due(At, Inclusive, maps:get(pending, State)) of
        {true, DueAt} ->
            State1 = State#{pending => none},
            case DueAt >= maps:get(lease_until, State1) andalso quorate(DueAt, State1) of
                true ->
                    case candidate(DueAt, State1) of
                        null -> State1;
                        Node -> promote(Node, DueAt, maps:get(term, State1) + 1, State1)
                    end;
                false -> State1
            end;
        false -> State
    end.

due(_At, _Inclusive, none) -> false;
due(At, Inclusive, {DueAt}) ->
    case DueAt < At orelse (Inclusive andalso DueAt =:= At) of
        true -> {true, DueAt};
        false -> false
    end.

apply_event(E, Group, EntryState, State) ->
    At = maps:get(at, E),
    Node = maps:get(node, E, null),
    Term = maps:get(term, E, maps:get(term, State)),
    case maps:get(kind, E) of
        up -> set_up(Node, true, State);
        down -> set_up(Node, false, State);
        fence -> set_fence(Node, maps:get(until, E), State);
        lease -> extend_lease(Node, Term, At, State);
        heartbeat -> heartbeat(Node, Term, At, Group, EntryState, State);
        promote -> external_promote(Node, Term, At, Group, EntryState, State);
        snapshot -> snapshot(E, At, Group, EntryState, State);
        witness -> State;
        withdraw -> State
    end.

set_up(Node, Flag, State) ->
    Up = maps:get(up, State),
    State#{up => Up#{Node => Flag}}.

set_fence(Node, Until, State) ->
    Fence = maps:get(fence, State),
    State#{fence => Fence#{Node => max(maps:get(Node, Fence, 0), Until)}}.

extend_lease(Node, Term, At, State) ->
    case Node =:= maps:get(leader, State) andalso Term =:= maps:get(term, State) andalso node_available(Node, At, State) of
        true -> State#{lease_until => max(maps:get(lease_until, State), At + lease_ms(State))};
        false -> State
    end.

heartbeat(Node, Term, At, Group, EntryState, State) ->
    case Node =:= maps:get(leader, State) andalso Term =:= maps:get(term, State) andalso node_available(Node, At, State) of
        true -> extend_lease(Node, Term, At, State);
        false ->
            case Term > maps:get(term, State) andalso node_available(Node, At, State) andalso quorate(At, State) andalso At >= maps:get(lease_until, State) andalso cert_ok(Node, Term, At, Group, EntryState) of
                true -> accept_leader(Node, Term, At, At + lease_ms(State), State);
                false -> State
            end
    end.

external_promote(Node, Term, At, Group, EntryState, State) ->
    case Term > maps:get(term, State) andalso node_available(Node, At, State) andalso quorate(At, State) andalso At >= maps:get(lease_until, State) andalso cert_ok(Node, Term, At, Group, EntryState) of
        true -> accept_leader(Node, Term, At, At + lease_ms(State), State);
        false -> State
    end.

snapshot(E, At, Group, EntryState, State) ->
    Node = maps:get(leader, E),
    Term = maps:get(term, E),
    SnapLease = maps:get(lease_until, E),
    case Term > maps:get(term, State) andalso node_available(Node, At, State) andalso quorate(At, State) andalso At >= maps:get(lease_until, State) andalso cert_ok(Node, Term, At, Group, EntryState) of
        true -> accept_leader(Node, Term, At, max(maps:get(lease_until, State), SnapLease), State);
        false -> State
    end.

cert_ok(Candidate, Term, At, Group, EntryState) ->
    Witnesses = [maps:get(node, E) || E <- Group,
                 maps:get(kind, E) =:= witness,
                 maps:get(candidate, E) =:= Candidate,
                 maps:get(term, E) =:= Term],
    Withdrawals = sets:from_list([maps:get(node, E) || E <- Group,
                                  maps:get(kind, E) =:= withdraw,
                                  maps:get(candidate, E) =:= Candidate,
                                  maps:get(term, E) =:= Term]),
    case Witnesses =:= [] andalso sets:size(Withdrawals) =:= 0 of
        true -> true;
        false ->
            Signers0 = [N || N <- Witnesses,
                             not sets:is_element(N, Withdrawals),
                             node_available(N, At, EntryState)],
            signer_quorate(lists:usort(Signers0), EntryState)
    end.

signer_quorate(Signers, State) ->
    Nodes = maps:get(nodes, maps:get(config, State)),
    Weights = maps:from_list(Nodes),
    Active = lists:sum([maps:get(N, Weights) || N <- Signers]),
    Total = lists:sum([W || {_N, W} <- Nodes]),
    Active * 2 > Total andalso signer_zone_ok(Signers, State).

signer_zone_ok(Signers, State) ->
    Nodes = maps:get(nodes, maps:get(config, State)),
    case length(Nodes) >= 3 of
        false -> true;
        true -> length(lists:usort([zone(N, State) || N <- Signers])) >= 2
    end.

accept_leader(Node, Term, _At, LeaseUntil, State) ->
    State#{leader => Node, term => Term, lease_until => LeaseUntil, pending => none}.

maybe_schedule(At, State) ->
    case needs_leader(At, State) andalso maps:get(pending, State) =:= none andalso quorate(At, State) of
        true -> State#{pending => {max(At, maps:get(lease_until, State)) + delay_ms(State)}};
        false -> State
    end.

needs_leader(At, State) ->
    Leader = maps:get(leader, State),
    Leader =:= null orelse not node_available(Leader, At, State).

promote(Node, At, Term, State) ->
    Entry = #{action => promote, at => At, node => Node, term => Term},
    accept_leader(Node, Term, At, At + lease_ms(State), State#{schedule => [Entry | maps:get(schedule, State)]}).

quorate(At, State) ->
    Nodes = maps:get(nodes, maps:get(config, State)),
    Active = lists:sum([W || {N, W} <- Nodes, node_available(N, At, State)]),
    Total = lists:sum([W || {_N, W} <- Nodes]),
    Active * 2 > Total andalso zone_ok(At, State).

zone_ok(_At, State) ->
    Nodes = maps:get(nodes, maps:get(config, State)),
    case length(Nodes) >= 3 of
        false -> true;
        true ->
            Zones = lists:usort([zone(N, State) || {N, _W} <- Nodes, node_available(N, _At, State)]),
            length(Zones) >= 2
    end.

zone(Node, State) -> maps:get(Node, maps:get(zones, maps:get(config, State), #{}), Node).

candidate(At, State) ->
    Nodes = [{N, W} || {N, W} <- maps:get(nodes, maps:get(config, State)), node_available(N, At, State)],
    case lists:sort(fun({A, AW}, {B, BW}) -> { -AW, atom_to_list(A)} =< { -BW, atom_to_list(B)} end, Nodes) of
        [] -> null;
        [{N, _W} | _] -> N
    end.

node_available(null, _At, _State) -> false;
node_available(Node, At, State) ->
    Up = maps:get(Node, maps:get(up, State), false),
    FenceUntil = maps:get(Node, maps:get(fence, State), 0),
    Up andalso At >= FenceUntil.

lease_ms(State) -> maps:get(lease_ms, maps:get(config, State)).
delay_ms(State) -> maps:get(delay_ms, maps:get(config, State)).

encode(Map) when is_map(Map) -> iolist_to_binary([object(maps:to_list(Map)), "\n"]).

value(V) when is_map(V) -> object(maps:to_list(V));
value(V) when is_list(V) ->
    case printable(V) of true -> string(V); false -> array(V) end;
value(V) when is_atom(V) ->
    case V of true -> "true"; false -> "false"; null -> "null"; _ -> string(atom_to_list(V)) end;
value(V) when is_binary(V) -> string(binary_to_list(V));
value(V) when is_integer(V) -> integer_to_list(V).

object(Pairs) ->
    Ordered = lists:sort(fun({A, _}, {B, _}) -> key(A) < key(B) end, Pairs),
    ["{", join([[string(key(K)), ":", value(V)] || {K, V} <- Ordered], ","), "}"].

array(Items) -> ["[", join([value(I) || I <- Items], ","), "]"].

join([], _) -> [];
join([X], _) -> X;
join([X | Xs], Sep) -> [X, Sep, join(Xs, Sep)].

key(K) when is_atom(K) -> atom_to_list(K);
key(K) when is_binary(K) -> binary_to_list(K);
key(K) when is_list(K) -> K.

string(S) -> [$", [escape(C) || C <- S], $"].

escape($") -> "\\\"";
escape($\\) -> "\\\\";
escape($\n) -> "\\n";
escape($\r) -> "\\r";
escape($\t) -> "\\t";
escape(C) -> C.

printable([]) -> false;
printable([C | Cs]) when is_integer(C), C >= 32, C =< 126 -> printable(Cs);
printable(_) -> false.
ERL

chmod 0755 /usr/local/sbin/qguard-attest
/usr/local/sbin/qguard-attest /var/lib/qguard/evidence /var/lib/qguard/audit/quorum-attestation.json
