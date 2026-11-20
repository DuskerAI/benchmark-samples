#!/bin/bash
set -euo pipefail

cat > /opt/rebar_resolver/src/r3rel.erl <<'ERL'
#!/usr/bin/env escript
%%! -noshell
-mode(compile).

main(["--selfcheck"]) -> ok;
main(Args) ->
    case args(Args, #{}) of
        #{workspace := W, profile := P, out := Out} ->
            case run(W, P, Out) of
                ok -> ok;
                {conflict, Name, Cs} ->
                    filelib:ensure_dir(filename:join(Out, "conflict.json")),
                    file:write_file(filename:join(Out, "conflict.json"), conflict_json(Name, Cs)),
                    halt(21);
                {error, E} -> io:format(standard_error, "~p~n", [E]), halt(22)
            end;
        _ -> halt(2)
    end.

args(["--workspace", W | T], M) -> args(T, M#{workspace => W});
args(["--profile", P | T], M) -> args(T, M#{profile => P});
args(["--out", O | T], M) -> args(T, M#{out => O});
args([], M) -> M;
args(_, _) -> #{}.

run(W, Profile, Out) ->
    case file:consult(filename:join(W, "rebar.config")) of
        {ok, Terms0} ->
            Terms = effective_terms(Terms0, Profile),
            Model = model(Terms),
            ok = reset_out(Out),
            #{release := {Rel, Roots}} = Model,
            case search(Model, #{}, Roots) of
                {ok, Selected0} ->
                    Constraints = constraints_from(Selected0, Roots, Model),
                    Names = lists:sort(fun name_lt/2, maps:keys(Constraints)),
                    Selected = maps:with(Names, Selected0),
                    Ordered = order_packages(Selected, Roots, Model),
                    filelib:ensure_dir(filename:join(Out, "manifest.json")),
                    ok = file:write_file(filename:join(Out, "manifest.json"), manifest_json(Rel, Profile, Ordered)),
                    ok = file:write_file(filename:join(Out, "manifest.lock"), lock_lines(Ordered)),
                    ok;
                {conflict, Name, Cs} -> {conflict, Name, Cs}
            end;
        Error -> {error, Error}
    end.

effective_terms(Terms, Profile) ->
    Names = [list_to_atom(string:trim(S)) || S <- string:tokens(Profile, ","), string:trim(S) =/= "", string:trim(S) =/= "default"],
    Base = [T || T <- Terms, not is_profile(T)],
    ProfileTerms = lists:append([lists:append([Ts || {profile, N, Ts} <- Terms, N =:= Name]) || Name <- Names]),
    Base ++ ProfileTerms.

reset_out(Out) ->
    _ = file:del_dir_r(Out),
    ok = filelib:ensure_dir(filename:join(Out, "x")),
    case file:make_dir(Out) of
        ok -> ok;
        {error, eexist} -> ok
    end.

is_profile({profile, _, _}) -> true;
is_profile(_) -> false.

model(Terms) -> lists:foldl(fun apply_term/2, #{candidates => #{}, locks => [], overrides => #{}, include => [], exclude => [], release => undefined}, Terms).

apply_term({release, R, Roots}, M) -> M#{release => {R, Roots}};
apply_term({include_optional, Names}, M) -> M#{include => maps:get(include, M) ++ Names};
apply_term({exclude, Names}, M) -> M#{exclude => maps:get(exclude, M) ++ Names};
apply_term({override, N, V}, M) -> M#{overrides => (maps:get(overrides, M))#{N => to_s(V)}};
apply_term({lock, Es}, M) -> M#{locks => maps:get(locks, M) ++ [norm_lock(E) || E <- Es]};
apply_term({app, N, V, Deps, Opt}, M) -> add_candidate(M, #{name => N, version => to_s(V), source => local, checksum => "local", deps => norm_deps(Deps), optional => Opt});
apply_term({hex, N, V, Deps, C}, M) -> add_candidate(M, #{name => N, version => to_s(V), source => hex, checksum => to_s(C), deps => norm_deps(Deps), optional => []});
apply_term(_, M) -> M.

add_candidate(M, C) ->
    Key = {maps:get(name, C), maps:get(version, C)},
    M#{candidates => (maps:get(candidates, M))#{Key => C}}.

norm_lock({N, V, S, C}) -> #{name => N, version => to_s(V), source => to_a(S), checksum => to_s(C)}.
norm_deps(Deps) -> [{N, norm_range(R)} || {N, R} <- Deps].
norm_range(any) -> any;
norm_range('*') -> any;
norm_range(R) -> to_s(R).

search(Model, Selected, Roots) ->
    Cs = constraints_from(Selected, Roots, Model),
    case problem_name(Selected, Cs) of
        none -> {ok, Selected};
        Name ->
            Ranges = maps:get(Name, Cs, []),
            case preferred_candidates(Name, Ranges, Model) of
                [] -> {conflict, Name, complete_conflict_ranges(Name, Selected, Roots, Model)};
                Cands -> try_candidates(Cands, Name, Model, Selected, Roots, none)
            end
    end.

try_candidates([], Name, Model, Selected, Roots, none) -> {conflict, Name, complete_conflict_ranges(Name, Selected, Roots, Model)};
try_candidates([], _Name, _Model, _Selected, _Roots, Last) -> Last;
try_candidates([C | Rest], Name, Model, Selected, Roots, _Last) ->
    case search(Model, Selected#{Name => C}, Roots) of
        {ok, _} = Ok -> Ok;
        Conflict -> try_candidates(Rest, Name, Model, Selected, Roots, Conflict)
    end.

constraints_from(Selected, Roots, Model) -> constraints_loop([{R, any} || R <- Roots], Selected, Model, #{}, [], Roots).

constraints_loop([], _Selected, _Model, Cs, _Seen, _Roots) -> Cs;
constraints_loop([Name | Rest], Selected, Model, Cs, Seen, Roots) when is_atom(Name) ->
    constraints_loop([{Name, any} | Rest], Selected, Model, Cs, Seen, Roots);
constraints_loop([{Name, Range} | Rest], Selected, Model, Cs0, Seen0, Roots0) ->
    Cs = add_constraint(Name, Range, Cs0),
    Roots = case Roots0 of [] -> [N || {N, _} <- [{Name, Range} | Rest], is_atom(N)]; _ -> Roots0 end,
    case lists:member(Name, Seen0) of
        true -> constraints_loop(Rest, Selected, Model, Cs, Seen0, Roots);
        false ->
            Seen = [Name | Seen0],
            More = case maps:find(Name, Selected) of
                {ok, C} -> candidate_edges(C, Roots, Model);
                error -> []
            end,
            constraints_loop(More ++ Rest, Selected, Model, Cs, Seen, Roots)
    end.

candidate_edges(C, Roots, Model) ->
    Deps = maps:get(deps, C),
    Opt = [{N, any} || N <- maps:get(optional, C), optional_allowed(N, Roots, Model)],
    Deps ++ Opt.

complete_conflict_ranges(Target, Selected, Roots, Model) ->
    complete_conflict_loop([{R, any} || R <- Roots], Target, Selected, Model, #{}, [], [], Roots).

complete_conflict_loop([], _Target, _Selected, _Model, _Cs, _Seen, Ranges, _Roots) -> Ranges;
complete_conflict_loop([Name | Rest], Target, Selected, Model, Cs, Seen, Ranges, Roots) when is_atom(Name) ->
    complete_conflict_loop([{Name, any} | Rest], Target, Selected, Model, Cs, Seen, Ranges, Roots);
complete_conflict_loop([{Target, Range} | Rest], Target, Selected, Model, Cs0, Seen0, Ranges, Roots) ->
    Cs = add_constraint(Target, Range, Cs0),
    {Seen, More} = case lists:member(Target, Seen0) of
        true -> {Seen0, []};
        false ->
            Seen1 = [Target | Seen0],
            Edges = case maps:find(Target, Selected) of
                {ok, C} -> candidate_edges(C, Roots, Model);
                error ->
                    case preferred_candidates(Target, maps:get(Target, Cs, []), Model) of
                        [C | _] -> candidate_edges(C, Roots, Model);
                        [] -> []
                    end
            end,
            {Seen1, Edges}
    end,
    complete_conflict_loop(More ++ Rest, Target, Selected, Model, Cs, Seen, Ranges ++ [Range], Roots);
complete_conflict_loop([{Name, Range} | Rest], Target, Selected, Model, Cs0, Seen0, Ranges, Roots) ->
    Cs = add_constraint(Name, Range, Cs0),
    case lists:member(Name, Seen0) of
        true -> complete_conflict_loop(Rest, Target, Selected, Model, Cs, Seen0, Ranges, Roots);
        false ->
            Seen = [Name | Seen0],
            More = case maps:find(Name, Selected) of
                {ok, C} -> candidate_edges(C, Roots, Model);
                error ->
                    case preferred_candidates(Name, maps:get(Name, Cs, []), Model) of
                        [C | _] -> candidate_edges(C, Roots, Model);
                        [] -> []
                    end
            end,
            complete_conflict_loop(More ++ Rest, Target, Selected, Model, Cs, Seen, Ranges, Roots)
    end.

optional_allowed(N, Roots, Model) -> lists:member(N, Roots) orelse (lists:member(N, maps:get(include, Model)) andalso not lists:member(N, maps:get(exclude, Model))).

add_constraint(Name, Range, Cs) -> maps:put(Name, maps:get(Name, Cs, []) ++ [Range], Cs).

problem_name(Selected, Cs) ->
    Bad = [N || N <- lists:sort(fun name_lt/2, maps:keys(Cs)), not selected_ok(N, maps:get(N, Cs), Selected)],
    case Bad of [] -> none; [H | _] -> H end.

selected_ok(N, Ranges, Selected) ->
    case maps:find(N, Selected) of
        {ok, C} -> ranges_ok(maps:get(version, C), Ranges);
        error -> false
    end.

preferred_candidates(Name, Ranges, Model) ->
    Cs = [C || C <- maps:values(maps:get(candidates, Model)), maps:get(name, C) =:= Name, ranges_ok(maps:get(version, C), Ranges)],
    case maps:find(Name, maps:get(overrides, Model)) of
        {ok, V} -> annotate([C || C <- Cs, maps:get(version, C) =:= V], override);
        error ->
            Locked = locked_candidates(Name, Ranges, Cs, maps:get(locks, Model)),
            case Locked of
                [] -> annotate(sort_candidates(Cs), selected);
                _ -> annotate(Locked, lock) ++ annotate([C || C <- sort_candidates(Cs), not same_any(C, Locked)], selected)
            end
    end.

locked_candidates(Name, Ranges, Cs, Locks) ->
    Matches = [C || L <- Locks, maps:get(name, L) =:= Name, ranges_ok(maps:get(version, L), Ranges), C <- Cs, lock_matches(L, C)],
    sort_candidates(unique_candidates(Matches)).

lock_matches(L, C) -> maps:get(version, L) =:= maps:get(version, C) andalso maps:get(source, L) =:= maps:get(source, C) andalso maps:get(checksum, L) =:= maps:get(checksum, C).
same_any(C, Cs) -> lists:any(fun(X) -> cand_key(X) =:= cand_key(C) end, Cs).
unique_candidates(Cs) -> maps:values(maps:from_list([{cand_key(C), C} || C <- Cs])).
cand_key(C) -> {maps:get(name, C), maps:get(version, C), maps:get(source, C), maps:get(checksum, C)}.

annotate(Cs, P) -> [C#{provenance => P} || C <- Cs].

sort_candidates(Cs) -> lists:sort(fun candidate_better/2, Cs).
candidate_better(A, B) ->
    case cmp_ver(maps:get(version, A), maps:get(version, B)) of
        gt -> true;
        lt -> false;
        eq -> source_rank(maps:get(source, A)) < source_rank(maps:get(source, B)) orelse (source_rank(maps:get(source, A)) =:= source_rank(maps:get(source, B)) andalso maps:get(checksum, A) =< maps:get(checksum, B))
    end.
source_rank(local) -> 0;
source_rank(hex) -> 1;
source_rank(_) -> 2.

ranges_ok(_V, []) -> true;
ranges_ok(V, Rs) -> lists:all(fun(R) -> range_ok(V, R) end, Rs).
range_ok(_V, any) -> true;
range_ok(V, R0) ->
    R = string:trim(to_s(R0)),
    R =:= "" orelse R =:= "*" orelse lists:all(fun(A) -> atom_ok(V, string:trim(A)) end, string:tokens(R, ",")).

atom_ok(_V, "") -> true;
atom_ok(V, [$>, $= | T]) -> cmp_in([gt, eq], cmp_ver(V, string:trim(T)));
atom_ok(V, [$<, $= | T]) -> cmp_in([lt, eq], cmp_ver(V, string:trim(T)));
atom_ok(V, [$= | T]) -> cmp_in([eq], cmp_ver(V, string:trim(T)));
atom_ok(V, [$> | T]) -> cmp_in([gt], cmp_ver(V, string:trim(T)));
atom_ok(V, [$< | T]) -> cmp_in([lt], cmp_ver(V, string:trim(T)));
atom_ok(V, T) -> cmp_in([eq], cmp_ver(V, string:trim(T))).
cmp_in(Want, Got) -> lists:member(Got, Want).

cmp_ver(A0, B0) ->
    A = split_version(to_s(A0)), B = split_version(to_s(B0)), cmp_parsed(A, B).

split_version(S0) ->
    [S | _] = string:split(S0, "+", leading),
    case string:split(S, "-", leading) of
        [Core] -> {core_parts(Core), release};
        [Core, Pre] -> {core_parts(Core), pre_parts(Pre)}
    end.
core_parts(Core) -> [to_int_or_text(P) || P <- string:tokens(Core, ".")].
pre_parts(Pre) -> [to_int_or_text(P) || P <- string:tokens(Pre, ".")].
to_int_or_text(S) -> case catch list_to_integer(S) of I when is_integer(I) -> {num, I}; _ -> {text, S} end.

cmp_parsed({CA, PA}, {CB, PB}) ->
    case cmp_core(CA, CB) of eq -> cmp_pre(PA, PB); X -> X end.

cmp_core([], []) -> eq;
cmp_core([], Bs) -> cmp_core([{num, 0}], Bs);
cmp_core(As, []) -> cmp_core(As, [{num, 0}]);
cmp_core([A | AT], [B | BT]) -> case cmp_part(A, B) of eq -> cmp_core(AT, BT); X -> X end.

cmp_pre(release, release) -> eq;
cmp_pre(release, _) -> gt;
cmp_pre(_, release) -> lt;
cmp_pre([], []) -> eq;
cmp_pre([], _) -> lt;
cmp_pre(_, []) -> gt;
cmp_pre([A | AT], [B | BT]) -> case cmp_part(A, B) of eq -> cmp_pre(AT, BT); X -> X end.

cmp_part({num, A}, {num, B}) when A > B -> gt;
cmp_part({num, A}, {num, B}) when A < B -> lt;
cmp_part({num, _}, {num, _}) -> eq;
cmp_part({num, _}, {text, _}) -> lt;
cmp_part({text, _}, {num, _}) -> gt;
cmp_part({text, A}, {text, B}) when A > B -> gt;
cmp_part({text, A}, {text, B}) when A < B -> lt;
cmp_part(_, _) -> eq.

order_packages(Selected, Roots, Model) ->
    Names = lists:sort(fun name_lt/2, maps:keys(Selected)),
    Graph = maps:from_list([{N, [D || {D, _} <- candidate_edges(maps:get(N, Selected), Roots, Model), maps:is_key(D, Selected)]} || N <- Names]),
    Comps = components(Names, Graph),
    OrderedComps = order_components(Comps, Graph),
    [maps:get(N, Selected) || Comp <- OrderedComps, N <- lists:sort(fun name_lt/2, Comp)].

components(Names, Graph) -> unique_components([lists:sort(fun name_lt/2, [M || M <- Names, reachable(N, M, Graph) andalso reachable(M, N, Graph)]) || N <- Names]).
unique_components(Cs) -> maps:values(maps:from_list([{C, C} || C <- Cs])).

reachable(A, B, Graph) -> reachable(A, B, Graph, []).
reachable(A, A, _Graph, _Seen) -> true;
reachable(A, B, Graph, Seen) ->
    lists:any(fun(N) -> not lists:member(N, Seen) andalso reachable(N, B, Graph, [A | Seen]) end, maps:get(A, Graph, [])).

order_components(Comps, Graph) ->
    Sorted = lists:sort(fun comp_lt/2, Comps),
    {_, Out} = lists:foldl(fun(C, {Seen, Acc}) -> visit_comp(C, Comps, Graph, Seen, Acc) end, {[], []}, Sorted),
    Out.

visit_comp(C, Comps, Graph, Seen, Acc) ->
    case lists:member(C, Seen) of
        true -> {Seen, Acc};
        false ->
            Deps = comp_deps(C, Comps, Graph),
            {Seen1, Acc1} = lists:foldl(fun(D, {S, A}) -> visit_comp(D, Comps, Graph, S, A) end, {[C | Seen], Acc}, Deps),
            {Seen1, Acc1 ++ [C]}
    end.

comp_deps(C, Comps, Graph) ->
    Ds = [comp_of(D, Comps) || N <- C, D <- maps:get(N, Graph, []), not lists:member(D, C)],
    lists:sort(fun comp_lt/2, unique_components(Ds)).
comp_of(N, [C | T]) -> case lists:member(N, C) of true -> C; false -> comp_of(N, T) end.
comp_lt([A | _], [B | _]) -> name_lt(A, B).
name_lt(A, B) -> atom_to_list(A) =< atom_to_list(B).

manifest_json(Rel, Profile, Packages) ->
    unicode:characters_to_binary(["{\n\"release\":", qs(atom_to_list(Rel)), ",\n\"profile\":", qs(Profile), ",\n\"packages\":[\n", join([pkg_json(P) || P <- Packages], ",\n"), "\n]}\n"]).

pkg_json(P) ->
    ["{\"name\":", qs(atom_to_list(maps:get(name, P))), ",\"version\":", qs(maps:get(version, P)), ",\"source\":", qs(atom_to_list(maps:get(source, P))), ",\"checksum\":", qs(maps:get(checksum, P)), ",\"provenance\":", qs(atom_to_list(maps:get(provenance, P))), "}"].

lock_lines(Packages) -> unicode:characters_to_binary([[atom_to_list(maps:get(name, P)), " ", maps:get(version, P), " ", atom_to_list(maps:get(source, P)), " ", maps:get(checksum, P), " ", atom_to_list(maps:get(provenance, P)), "\n"] || P <- Packages]).

conflict_json(Name, Cs) -> unicode:characters_to_binary(["{\n\"package\":", qs(atom_to_list(Name)), ",\n\"constraints\":[", join([qs(range_text(C)) || C <- Cs], ","), "]\n}\n"]).
range_text(any) -> "any";
range_text(R) -> to_s(R).

qs(S0) -> S = to_s(S0), [34, escape(S), 34].
escape([]) -> [];
escape([$" | T]) -> [$\\, $" | escape(T)];
escape([$\\ | T]) -> [$\\, $\\ | escape(T)];
escape([C | T]) -> [C | escape(T)].
join([], _) -> [];
join([X], _) -> X;
join([H | T], Sep) -> [H, Sep, join(T, Sep)].

to_s(V) when is_list(V) -> V;
to_s(V) when is_binary(V) -> binary_to_list(V);
to_s(V) when is_atom(V) -> atom_to_list(V);
to_s(V) when is_integer(V) -> integer_to_list(V).
to_a(V) when is_atom(V) -> V;
to_a(V) when is_list(V) -> list_to_atom(V);
to_a(V) when is_binary(V) -> list_to_atom(binary_to_list(V)).
ERL

make -C /opt/rebar_resolver clean all
