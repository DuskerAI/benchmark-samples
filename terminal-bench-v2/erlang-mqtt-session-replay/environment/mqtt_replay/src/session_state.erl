-module(session_state).
-export([replay/1]).

replay(Events) ->
    St0 = #{now => 0, sessions => #{}, retained => #{}, deliveries => [], violations => [], duplicates => 0, expired => 0},
    St = lists:foldl(fun step/2, St0, Events),
    {report(St), lists:reverse(maps:get(deliveries, St))}.

step({Time, _Base, _Line, Event}, St) ->
    handle(Event, St#{now => Time}).

handle({connect, Client, _CleanStart, SessionExpiry, Props}, St) ->
    Sessions = maps:get(sessions, St),
    S0 = maps:get(Client, Sessions, new_session(Client)),
    Will = proplists:get_value(will, Props, maps:get(will, S0, none)),
    S = S0#{present => true, expiry => SessionExpiry, expiry_at => infinity, will => Will, pending => none},
    St#{sessions => Sessions#{Client => S}};
handle({disconnect, Client, Reason}, St) ->
    {S0, St1} = get_session(Client, St),
    Now = maps:get(now, St1),
    Exp = maps:get(expiry, S0, 0),
    ExpAt = case Exp of 0 -> Now; _ -> Now + Exp end,
    S1 = S0#{present => false, expiry_at => ExpAt},
    S = case {Reason, maps:get(will, S1, none)} of
            {normal, _} -> S1#{pending => none};
            {_, none} -> S1;
            {_, {Topic, Payload, Retain, Delay, MsgExp}} -> S1#{pending => {Now + Delay, Topic, Payload, Retain, MsgExp}}
        end,
    put_session(Client, S, St1);
handle({tick}, St) ->
    St;
handle({publish, Client, PacketId, Dup, Qos, Topic0, Alias, Retain, Payload, Expiry}, St) ->
    {S0, St1} = get_session(Client, St),
    Aliases0 = maps:get(aliases, S0),
    {Topic, Aliases} = topic_for(Topic0, Alias, Aliases0),
    S = S0#{aliases => Aliases},
    case Topic of
        undefined -> put_session(Client, S, violation(Client, alias, St1));
        _ -> publish_known(Client, PacketId, Dup, Qos, Topic, Retain, Payload, Expiry, S, St1)
    end;
handle({puback, Client, PacketId}, St) -> remove_packet(Client, PacketId, St);
handle({pubrec, _Client, _PacketId}, St) -> St;
handle({pubrel, Client, PacketId}, St) ->
    {S0, St1} = get_session(Client, St),
    In = maps:get(inflight, S0),
    case maps:get(PacketId, In, undefined) of
        undefined -> violation(Client, release, St1);
        M ->
            St2 = deliver(Client, PacketId, 2, maps:get(topic, M), maps:get(retain, M), maps:get(payload, M), maps:get(expiry, M), St1),
            put_session(Client, S0#{inflight => maps:remove(PacketId, In)}, St2)
    end;
handle({pubcomp, Client, PacketId}, St) -> remove_packet(Client, PacketId, St);
handle(_, St) -> St.

topic_for([], Alias, Aliases) when Alias > 0 -> {maps:get(Alias, Aliases, undefined), Aliases};
topic_for(Topic, Alias, Aliases) when Alias > 0 -> {Topic, Aliases#{Alias => Topic}};
topic_for(Topic, _Alias, Aliases) -> {Topic, Aliases}.

publish_known(Client, PacketId, Dup, Qos, Topic, Retain, Payload, Expiry, S0, St0) ->
    In = maps:get(inflight, S0),
    St1 = case Dup of true -> St0#{duplicates => maps:get(duplicates, St0) + 1}; false -> St0 end,
    case Qos of
        0 ->
            St2 = deliver(Client, PacketId, Qos, Topic, Retain, Payload, Expiry, St1),
            put_session(Client, S0, St2);
        1 ->
            St2 = deliver(Client, PacketId, Qos, Topic, Retain, Payload, Expiry, St1),
            put_session(Client, S0#{inflight => In#{PacketId => msg(Topic, Retain, Payload, Expiry, qos1)}}, St2);
        2 ->
            St2 = deliver(Client, PacketId, Qos, Topic, Retain, Payload, Expiry, St1),
            put_session(Client, S0#{inflight => In#{PacketId => msg(Topic, Retain, Payload, Expiry, qos2)}}, St2);
        _ -> put_session(Client, S0, violation(Client, qos, St1))
    end.

msg(Topic, Retain, Payload, Expiry, Phase) -> #{topic => Topic, retain => Retain, payload => Payload, expiry => Expiry, phase => Phase}.

deliver(Client, PacketId, Qos, Topic, Retain, Payload, Expiry, St) ->
    Now = maps:get(now, St),
    Rec = #{client => Client, t => Now, topic => Topic, payload => Payload, qos => Qos, packet_id => PacketId, retain => Retain},
    St1 = St#{deliveries => [Rec | maps:get(deliveries, St)]},
    case Retain of
        true -> St1#{retained => (maps:get(retained, St1))#{Topic => #{payload => Payload, expiry_at => expiry_at(Now, Expiry)}}};
        false -> St1
    end.

expiry_at(_Now, 0) -> infinity;
expiry_at(Now, Expiry) -> Now + Expiry.

remove_packet(Client, PacketId, St) ->
    {S0, St1} = get_session(Client, St),
    put_session(Client, S0#{inflight => maps:remove(PacketId, maps:get(inflight, S0))}, St1).

get_session(Client, St) ->
    Sessions = maps:get(sessions, St),
    {maps:get(Client, Sessions, new_session(Client)), St}.

put_session(Client, S, St) ->
    Sessions = maps:get(sessions, St),
    St#{sessions => Sessions#{Client => S}}.

new_session(Client) -> #{client => Client, aliases => #{}, inflight => #{}, expiry => 0, expiry_at => infinity, will => none, pending => none, present => true}.

violation(Client, Reason, St) ->
    Now = maps:get(now, St),
    St#{violations => [#{client => Client, t => Now, reason => atom_to_list(Reason)} | maps:get(violations, St)]}.

report(St) ->
    #{clients => client_rows(maps:get(sessions, St)),
      retained => retained_rows(maps:get(retained, St)),
      totals => #{delivered => length(maps:get(deliveries, St)), duplicates => maps:get(duplicates, St), expired_sessions => maps:get(expired, St)},
      violations => lists:reverse(maps:get(violations, St))}.

client_rows(Sessions) -> [client_row(C, maps:get(C, Sessions)) || C <- lists:sort(maps:keys(Sessions))].

client_row(Client, S) ->
    #{client => Client, session_present => maps:get(present, S), aliases => alias_rows(maps:get(aliases, S)), inflight => inflight_rows(maps:get(inflight, S))}.

alias_rows(Aliases) -> [#{id => K, topic => maps:get(K, Aliases)} || K <- lists:sort(maps:keys(Aliases))].

inflight_rows(In) -> [#{packet_id => K, qos => phase_qos(maps:get(phase, maps:get(K, In))), phase => atom_to_list(maps:get(phase, maps:get(K, In))), topic => maps:get(topic, maps:get(K, In))} || K <- lists:sort(maps:keys(In))].

phase_qos(qos1) -> 1;
phase_qos(qos2) -> 2;
phase_qos(_) -> 0.

retained_rows(Retained) -> [#{topic => K, payload => maps:get(payload, maps:get(K, Retained))} || K <- lists:sort(maps:keys(Retained))].
