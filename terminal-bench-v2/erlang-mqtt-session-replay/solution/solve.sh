#!/bin/bash
set -euo pipefail

cat > /app/mqtt_replay/src/frame_scan.erl <<'ERL'
-module(frame_scan).
-export([read_dir/1]).

read_dir(Dir) ->
    Files = lists:sort(filelib:wildcard(filename:join(Dir, "*.trace"))),
    case read_files(Files, []) of
        {ok, Events} -> {ok, lists:sort(Events)};
        Error -> Error
    end.

read_files([File | Rest], Acc) ->
    case read_file(File) of
        {ok, Events} -> read_files(Rest, Acc ++ Events);
        Error -> Error
    end;
read_files([], Acc) -> {ok, Acc}.

read_file(File) ->
    case file:read_file(File) of
        {ok, Bin} ->
            Lines = binary:split(Bin, <<"\n">>, [global]),
            parse_lines(Lines, filename:basename(File), 1, []);
        Error -> Error
    end.

parse_lines([Line | Rest], Base, N, Acc) ->
    Text0 = string:trim(binary_to_list(Line)),
    Text = string:trim(Text0, trailing, "\r"),
    case Text of
        [] -> parse_lines(Rest, Base, N + 1, Acc);
        _ ->
            case parse_term(Text) of
                {ok, {Time, Event}} -> parse_lines(Rest, Base, N + 1, Acc ++ [{Time, Base, N, Event}]);
                {ok, Other} -> {error, {bad_record, Base, N, Other}};
                Error -> Error
            end
    end;
parse_lines([], _Base, _N, Acc) -> {ok, Acc}.

parse_term(Text) ->
    case erl_scan:string(Text) of
        {ok, Tokens, _} -> erl_parse:parse_term(Tokens);
        Error -> Error
    end.
ERL

cat > /app/mqtt_replay/src/session_state.erl <<'ERL'
-module(session_state).
-export([replay/1]).

replay(Events) ->
    St0 = #{now => 0, sessions => #{}, retained => #{}, deliveries => [], violations => [], duplicates => 0, expired => 0},
    St1 = lists:foldl(fun step/2, St0, Events),
    St = advance(maps:get(now, St1), St1),
    {report(St), maps:get(deliveries, St)}.

step({Time, _Base, _Line, Event}, St0) ->
    St1 = advance(Time, St0#{now => Time}),
    handle(Event, St1).

handle({connect, Client, CleanStart, SessionExpiry, Props}, St) ->
    Sessions = maps:get(sessions, St),
    S0 = case {CleanStart, maps:find(Client, Sessions)} of
        {false, {ok, Existing}} -> Existing;
        _ -> new_session(Client)
    end,
    S = S0#{present => true, expiry => SessionExpiry, expiry_at => infinity, will => will_from(Props), pending => none},
    St#{sessions => Sessions#{Client => S}};
handle({disconnect, Client, Reason}, St) ->
    {S0, St1} = get_session(Client, St),
    Now = maps:get(now, St1),
    Exp = maps:get(expiry, S0, 0),
    ExpAt = case Exp of 0 -> Now; _ -> Now + Exp end,
    S1 = S0#{present => false, expiry_at => ExpAt},
    S = case {Reason, maps:get(will, S1, none)} of
        {normal, _} -> S1#{pending => none};
        {_, none} -> S1#{pending => none};
        {_, Will} -> S1#{pending => Will#{due => Now + maps:get(delay, Will)}}
    end,
    put_session(Client, S, St1);
handle({tick}, St) -> advance(maps:get(now, St), St);
handle({publish, Client, PacketId, Dup, Qos, Topic0, Alias, Retain, Payload, Expiry}, St) ->
    {S0, St1} = get_session(Client, St),
    case resolve_topic(Topic0, Alias, maps:get(aliases, S0)) of
        {error, Aliases} -> put_session(Client, S0#{aliases => Aliases}, violation(Client, alias, St1));
        {Topic, Aliases} ->
            Event = #{client => Client, packet_id => PacketId, dup => Dup, qos => Qos, topic => Topic, alias => Alias, retain => Retain, payload => Payload, expiry => Expiry},
            publish_known(Event, S0#{aliases => Aliases}, St1)
    end;
handle({puback, Client, PacketId}, St) -> remove_packet(Client, PacketId, St);
handle({pubrec, Client, PacketId}, St) ->
    {S0, St1} = get_session(Client, St),
    In = maps:get(inflight, S0),
    S = case maps:get(PacketId, In, undefined) of
        #{qos := 2} = M -> S0#{inflight => In#{PacketId => M#{phase => received}}};
        _ -> S0
    end,
    put_session(Client, S, St1);
handle({pubrel, Client, PacketId}, St) ->
    {S0, St1} = get_session(Client, St),
    In = maps:get(inflight, S0),
    case maps:get(PacketId, In, undefined) of
        #{qos := 2, phase := released} -> put_session(Client, S0, add_duplicate(St1));
        #{qos := 2} = M ->
            St2 = deliver(Client, maps:get(now, St1), PacketId, 2, maps:get(topic, M), maps:get(retain, M), maps:get(payload, M), maps:get(expiry, M), St1),
            put_session(Client, S0#{inflight => In#{PacketId => M#{phase => released}}}, St2);
        _ -> put_session(Client, S0, violation(Client, packet_flow, St1))
    end;
handle({pubcomp, Client, PacketId}, St) -> remove_packet(Client, PacketId, St);
handle(_, St) -> St.

will_from(Props) ->
    case proplists:get_value(will, Props, none) of
        none -> none;
        {Topic, Payload, Retain, Delay, Expiry} -> #{topic => Topic, payload => Payload, retain => Retain, delay => Delay, expiry => Expiry}
    end.

resolve_topic([], Alias, Aliases) when Alias > 0 ->
    case maps:get(Alias, Aliases, undefined) of
        undefined -> {error, Aliases};
        Topic -> {Topic, Aliases}
    end;
resolve_topic(Topic, Alias, Aliases) when Alias > 0 -> {Topic, Aliases#{Alias => Topic}};
resolve_topic(Topic, _Alias, Aliases) -> {Topic, Aliases}.

publish_known(#{client := Client, packet_id := PacketId, qos := 0, topic := Topic, retain := Retain, payload := Payload, expiry := Expiry}, S, St) ->
    put_session(Client, S, deliver(Client, maps:get(now, St), PacketId, 0, Topic, Retain, Payload, Expiry, St));
publish_known(#{client := Client, packet_id := PacketId, dup := Dup, qos := Qos, topic := Topic, retain := Retain, payload := Payload, expiry := Expiry} = Event, S0, St0) when Qos =:= 1; Qos =:= 2 ->
    In = maps:get(inflight, S0),
    case maps:get(PacketId, In, undefined) of
        undefined ->
            Phase = case Qos of 1 -> sent; 2 -> received end,
            M = #{topic => Topic, payload => Payload, qos => Qos, retain => Retain, expiry => Expiry, phase => Phase},
            S = S0#{inflight => In#{PacketId => M}},
            St = case Qos of
                1 -> deliver(Client, maps:get(now, St0), PacketId, Qos, Topic, Retain, Payload, Expiry, St0);
                2 -> St0
            end,
            put_session(Client, S, St);
        Old ->
            St = case Dup andalso same_message(Old, Event) of
                true -> add_duplicate(St0);
                false -> violation(Client, packet_id, St0)
            end,
            put_session(Client, S0, St)
    end;
publish_known(#{client := Client}, S, St) -> put_session(Client, S, violation(Client, qos, St)).

same_message(M, E) ->
    maps:get(topic, M) =:= maps:get(topic, E) andalso
    maps:get(payload, M) =:= maps:get(payload, E) andalso
    maps:get(qos, M) =:= maps:get(qos, E).

advance(Now, St0) ->
    St1 = expire_retained(Now, St0),
    St2 = fire_due(Now, St1),
    St3 = expire_retained(Now, St2),
    expire_sessions(Now, St3).

fire_due(Now, St) ->
    Sessions = maps:get(sessions, St),
    Due = due_items(Now, Sessions),
    lists:foldl(fun({DueAt, Client, P}, Acc0) ->
        case maps:find(Client, maps:get(sessions, Acc0)) of
            {ok, S0} ->
                Acc1 = deliver(Client, DueAt, 0, 0, maps:get(topic, P), maps:get(retain, P), maps:get(payload, P), maps:get(expiry, P), Acc0),
                put_session(Client, S0#{pending => none}, Acc1);
            error -> Acc0
        end
    end, St, Due).

due_items(Now, Sessions) ->
    Items = maps:fold(fun(Client, S, Acc) ->
        case maps:get(pending, S, none) of
            none -> Acc;
            P ->
                case maps:get(due, P) =< Now andalso due_before_expiry(P, S) of
                    true -> [{maps:get(due, P), Client, P} | Acc];
                    false -> Acc
                end
        end
    end, [], Sessions),
    lists:sort(Items).

due_before_expiry(P, S) ->
    case maps:get(expiry_at, S, infinity) of
        infinity -> true;
        ExpAt -> maps:get(due, P) =< ExpAt
    end.

expire_sessions(Now, St) ->
    Sessions = maps:get(sessions, St),
    {Kept, Count} = lists:foldl(fun(Client, {Acc, N}) ->
        S = maps:get(Client, Sessions),
        case should_expire(Now, S) of
            true -> {Acc, N + 1};
            false -> {Acc#{Client => S}, N}
        end
    end, {#{}, 0}, lists:sort(maps:keys(Sessions))),
    St#{sessions => Kept, expired => maps:get(expired, St) + Count}.

should_expire(Now, S) ->
    (not maps:get(present, S, false)) andalso case maps:get(expiry_at, S, infinity) of
        infinity -> false;
        ExpAt -> ExpAt =< Now
    end.

expire_retained(Now, St) ->
    Ret = maps:get(retained, St),
    Kept = maps:filter(fun(_Topic, Row) ->
        case maps:get(expiry_at, Row) of
            infinity -> true;
            ExpAt -> ExpAt > Now
        end
    end, Ret),
    St#{retained => Kept}.

deliver(Client, Time, PacketId, Qos, Topic, Retain, Payload, Expiry, St) ->
    Rec = #{client => Client, packet_id => PacketId, payload => Payload, qos => Qos, retain => Retain, t => Time, topic => Topic},
    St1 = St#{deliveries => maps:get(deliveries, St) ++ [Rec]},
    case Retain of
        true -> St1#{retained => (maps:get(retained, St1))#{Topic => #{payload => Payload, expiry_at => expiry_at(Time, Expiry)}}};
        false -> St1
    end.

expiry_at(_Now, 0) -> infinity;
expiry_at(Now, Expiry) -> Now + Expiry.

remove_packet(Client, PacketId, St) ->
    {S0, St1} = get_session(Client, St),
    put_session(Client, S0#{inflight => maps:remove(PacketId, maps:get(inflight, S0))}, St1).

get_session(Client, St) ->
    Sessions = maps:get(sessions, St),
    S = maps:get(Client, Sessions, new_session(Client)),
    {S, St#{sessions => Sessions#{Client => S}}}.

put_session(Client, S, St) ->
    Sessions = maps:get(sessions, St),
    St#{sessions => Sessions#{Client => S}}.

new_session(Client) -> #{client => Client, aliases => #{}, inflight => #{}, expiry => 0, expiry_at => infinity, will => none, pending => none, present => true}.

add_duplicate(St) -> St#{duplicates => maps:get(duplicates, St) + 1}.

violation(Client, Reason, St) ->
    Now = maps:get(now, St),
    St#{violations => maps:get(violations, St) ++ [#{client => Client, reason => atom_to_list(Reason), t => Now}]}.

report(St) ->
    #{clients => client_rows(maps:get(sessions, St)),
      retained => retained_rows(maps:get(retained, St)),
      totals => #{delivered => length(maps:get(deliveries, St)), duplicates => maps:get(duplicates, St), expired_sessions => maps:get(expired, St)},
      violations => maps:get(violations, St)}.

client_rows(Sessions) -> [client_row(C, maps:get(C, Sessions)) || C <- lists:sort(maps:keys(Sessions))].

client_row(Client, S) ->
    #{aliases => alias_rows(maps:get(aliases, S)),
      client => Client,
      inflight => inflight_rows(maps:get(inflight, S)),
      session_present => maps:get(present, S)}.

alias_rows(Aliases) -> [#{id => K, topic => maps:get(K, Aliases)} || K <- lists:sort(maps:keys(Aliases))].

inflight_rows(In) -> [inflight_row(K, maps:get(K, In)) || K <- lists:sort(maps:keys(In))].

inflight_row(K, M) -> #{packet_id => K, phase => atom_to_list(maps:get(phase, M)), qos => maps:get(qos, M), topic => maps:get(topic, M)}.

retained_rows(Retained) -> [#{payload => maps:get(payload, maps:get(K, Retained)), topic => K} || K <- lists:sort(maps:keys(Retained))].
ERL

make -C /app/mqtt_replay clean all
