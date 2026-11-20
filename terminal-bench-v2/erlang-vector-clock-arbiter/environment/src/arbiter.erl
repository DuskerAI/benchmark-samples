#!/usr/bin/env escript
%%! -noshell
-mode(compile).

main(["adjudicate", Events, Report]) ->
    Ordered = order(load_events(Events)),
    State = replay(Ordered, initial(Ordered)),
    write_report(Report, ok, State),
    halt(0);
main(["checkpoint", Events, CountText, StatePath, Report]) ->
    Count = list_to_integer(CountText),
    Ordered = order(load_events(Events)),
    {Prefix, _Rest} = lists:split(min(Count, length(Ordered)), Ordered),
    State0 = initial(Ordered),
    State = replay(Prefix, State0),
    Saved = State#{count => length(Prefix), digest => weak_digest(Prefix)},
    file:write_file(StatePath, io_lib:format("~p.~n", [Saved])),
    write_report(Report, ok, State),
    halt(0);
main(["resume", Events, StatePath, Report]) ->
    Ordered = order(load_events(Events)),
    {ok, [Saved]} = file:consult(StatePath),
    Count = maps:get(count, Saved, 0),
    {_Prefix, Rest} = lists:split(min(Count, length(Ordered)), Ordered),
    State = replay(Rest, maps:remove(digest, maps:remove(count, Saved))),
    write_report(Report, ok, State),
    halt(0);
main(_) ->
    io:format(standard_error, "usage~n", []),
    halt(2).

load_events(Path) ->
    {ok, Terms} = file:consult(Path),
    lists:zipwith(fun(Term, I) -> make_event(Term, I) end, Terms, lists:seq(1, length(Terms))).

make_event({Player, Millis, ClockList, Action} = Raw, I) ->
    Clock = maps:from_list(ClockList),
    #{raw => Raw, idx => I, player => Player, millis => Millis, clock => Clock,
      action => Action, self => maps:get(Player, Clock, 0)}.

initial(Events) ->
    Players = lists:usort(lists:flatmap(fun players_in/1, Events)),
    #{players => Players, scores => maps:from_list([{P,0} || P <- Players]),
      zones => #{}, last => maps:from_list([{P,0} || P <- Players]), accepted => 0,
      rejected => 0, order => []}.

players_in(E) ->
    A = maps:get(action, E),
    FromAction = case A of {timeout, V} -> [V]; _ -> [] end,
    lists:usort([maps:get(player, E) | maps:keys(maps:get(clock, E)) ++ FromAction]).

order(Events) ->
    lists:sort(fun(A, B) -> sort_key(A) =< sort_key(B) end, Events).

sort_key(E) ->
    {maps:get(millis, E), atom_to_list(maps:get(player, E)), maps:get(idx, E)}.

replay(Events, State) ->
    lists:foldl(fun apply_event/2, State, Events).

apply_event(E, State) ->
    P = maps:get(player, E),
    Self = maps:get(self, E),
    Last = maps:get(P, maps:get(last, State), 0),
    case Self > Last of
        true -> accept(E, State);
        false -> reject(State)
    end.

accept(E, State) ->
    case maps:get(action, E) of
        {move, Zone, Points} -> accept_move(E, State, Zone, Points);
        {timeout, Victim} -> accept_timeout(E, State, Victim);
        _ -> reject(State)
    end.

accept_move(E, State, Zone, Points) ->
    P = maps:get(player, E),
    Scores0 = maps:get(scores, State),
    Zones0 = maps:get(zones, State),
    Scores1 = add_score(P, Points, Scores0),
    Scores2 = case maps:get(Zone, Zones0, undefined) of
        undefined -> Scores1;
        P -> Scores1;
        Other -> add_score(Other, -1, Scores1)
    end,
    finish_accept(E, State#{scores => Scores2, zones => Zones0#{Zone => P}}).

accept_timeout(E, State, Victim) ->
    Clock = maps:get(clock, E),
    Seen = maps:get(Victim, Clock, 0),
    Last = maps:get(Victim, maps:get(last, State), 0),
    case Seen =< Last of
        true ->
            P = maps:get(player, E),
            Scores1 = add_score(Victim, -2, maps:get(scores, State)),
            Scores2 = add_score(P, 1, Scores1),
            finish_accept(E, State#{scores => Scores2});
        false -> reject(State)
    end.

finish_accept(E, State) ->
    P = maps:get(player, E),
    Self = maps:get(self, E),
    Last1 = (maps:get(last, State))#{P => Self},
    State#{last => Last1, accepted => maps:get(accepted, State) + 1,
           order => maps:get(order, State) ++ [event_id(E)]}.

reject(State) ->
    State#{rejected => maps:get(rejected, State) + 1}.

add_score(P, Delta, Scores) ->
    Scores#{P => maps:get(P, Scores, 0) + Delta}.

event_id(E) ->
    P = atom_to_list(maps:get(player, E)),
    C = integer_to_list(maps:get(self, E)),
    I = integer_to_list(maps:get(idx, E)),
    P ++ ":" ++ C ++ ":" ++ I.

weak_digest(Prefix) ->
    length(Prefix).

write_report(Path, Status, State) ->
    file:write_file(Path, report_json(Status, State)).

report_json(Status, State) ->
    Scores = maps:get(scores, State),
    Zones = maps:get(zones, State),
    Parts = [
        "{\n",
        q("status"), ":", q(atom_to_list(Status)), ",\n",
        q("accepted"), ":", integer_to_list(maps:get(accepted, State)), ",\n",
        q("rejected"), ":", integer_to_list(maps:get(rejected, State)), ",\n",
        q("scores"), ":", scores_json(Scores), ",\n",
        q("zones"), ":", zones_json(Zones), ",\n",
        q("order"), ":", strings_json(maps:get(order, State)), "\n}"
    ],
    iolist_to_binary(Parts).

scores_json(Scores) ->
    Items = [{atom_to_list(P), S} || {P, S} <- lists:sort(maps:to_list(Scores))],
    "[" ++ join(["{" ++ q("player") ++ ":" ++ q(P) ++ "," ++ q("score") ++ ":" ++ integer_to_list(S) ++ "}" || {P,S} <- Items], ",") ++ "]".

zones_json(Zones) ->
    Items = [{atom_to_list(Z), atom_to_list(P)} || {Z, P} <- lists:sort(maps:to_list(Zones))],
    "[" ++ join(["{" ++ q("zone") ++ ":" ++ q(Z) ++ "," ++ q("owner") ++ ":" ++ q(P) ++ "}" || {Z,P} <- Items], ",") ++ "]".

strings_json(Items) ->
    "[" ++ join([q(I) || I <- Items], ",") ++ "]".

q(S) ->
    "\"" ++ S ++ "\"".

join([], _) -> "";
join([H], _) -> H;
join([H|T], Sep) -> H ++ Sep ++ join(T, Sep).
