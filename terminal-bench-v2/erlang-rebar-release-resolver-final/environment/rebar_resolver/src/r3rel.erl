#!/usr/bin/env escript
%%! -noshell
-mode(compile).

main(["--selfcheck"]) -> ok;
main(Args) ->
    case args(Args, #{}) of
        #{workspace := W, profile := P, out := Out} ->
            case run(W, P, Out) of
                ok -> ok;
                {error, E} -> io:format(standard_error, "~p~n", [E]), halt(21)
            end;
        _ -> halt(2)
    end.

args(["--workspace", W | T], M) -> args(T, M#{workspace => W});
args(["--profile", P | T], M) -> args(T, M#{profile => P});
args(["--out", O | T], M) -> args(T, M#{out => O});
args([], M) -> M;
args(_, _) -> #{}.

run(W, Profile, Out) ->
    {ok, Terms0} = file:consult(filename:join(W, "rebar.config")),
    Terms = effective(Terms0, Profile),
    Rel = last_release(Terms),
    {release, RelName, Roots} = Rel,
    Packages = resolve(Roots, Terms, #{}),
    filelib:ensure_dir(filename:join(Out, "manifest.json")),
    Json = json(RelName, Profile, lists:sort(Packages)),
    ok = file:write_file(filename:join(Out, "manifest.json"), Json),
    ok = file:write_file(filename:join(Out, "manifest.lock"), lock_lines(lists:sort(Packages))),
    ok.

effective(Terms, Profile) ->
    Names = [list_to_atom(S) || S <- string:tokens(Profile, ","), S =/= "default", S =/= ""],
    Base = [T || T <- Terms, element(1, T) =/= profile],
    Profiles = lists:sort([{N, Ts} || {profile, N, Ts} <- Terms, lists:member(N, Names)]),
    Base ++ lists:append([Ts || {_, Ts} <- Profiles]).

last_release(Terms) -> lists:last([T || T = {release, _, _} <- Terms]).

resolve([], _Terms, Seen) -> maps:values(Seen);
resolve([Name | Rest], Terms, Seen) when is_map_key(Name, Seen) -> resolve(Rest, Terms, Seen);
resolve([Name | Rest], Terms, Seen) ->
    Cand = pick(Name, Terms),
    Deps = deps(Cand),
    Opt = opt(Cand),
    resolve(Deps ++ Opt ++ Rest, Terms, Seen#{Name => package(Cand, Terms)}).

pick(Name, Terms) ->
    Overrides = [V || {override, N, V} <- Terms, N =:= Name],
    Cands = [C || C <- Terms, is_candidate(Name, C)],
    case Overrides of
        [] -> lists:last(lists:sort(fun cand_lt/2, Cands));
        _ -> V = lists:last(Overrides), hd([C || C <- Cands, cand_v(C) =:= V])
    end.

is_candidate(N, {app, N, _, _, _}) -> true;
is_candidate(N, {hex, N, _, _, _}) -> true;
is_candidate(_, _) -> false.

cand_lt(A, B) -> cand_v(A) =< cand_v(B).
cand_v({app, _, V, _, _}) -> V;
cand_v({hex, _, V, _, _}) -> V.
deps({app, _, _, D, _}) -> [N || {N, _} <- D];
deps({hex, _, _, D, _}) -> [N || {N, _} <- D].
opt({app, _, _, _, O}) -> O;
opt(_) -> [].

package({app, N, V, _, _}, Terms) -> #{name => N, version => V, source => local, checksum => "local", provenance => prov(N, V, Terms)};
package({hex, N, V, _, C}, Terms) -> #{name => N, version => V, source => hex, checksum => C, provenance => prov(N, V, Terms)}.

prov(N, V, Terms) ->
    case [ok || {override, N0, V0} <- Terms, N0 =:= N, V0 =:= V] of
        [] -> case [ok || {lock, Es} <- Terms, {N0, V0, _, _} <- Es, N0 =:= N, V0 =:= V] of [] -> selected; _ -> lock end;
        _ -> override
    end.

json(Rel, Profile, Packages) ->
    B = ["{\n\"release\":", qs(atom_to_list(Rel)), ",\n\"profile\":", qs(Profile), ",\n\"packages\":[\n",
         join([pkg_json(P) || P <- Packages], ",\n"), "\n]}\n"],
    unicode:characters_to_binary(B).

pkg_json(P) ->
    ["{\"name\":", qs(atom_to_list(maps:get(name, P))), ",\"version\":", qs(maps:get(version, P)),
     ",\"source\":", qs(atom_to_list(maps:get(source, P))), ",\"checksum\":", qs(maps:get(checksum, P)),
     ",\"provenance\":", qs(atom_to_list(maps:get(provenance, P))), "}"].

lock_lines(Packages) -> unicode:characters_to_binary([[atom_to_list(maps:get(name, P)), " ", maps:get(version, P), " ", atom_to_list(maps:get(source, P)), " ", maps:get(checksum, P), " ", atom_to_list(maps:get(provenance, P)), "\n"] || P <- Packages]).

qs(S) when is_atom(S) -> qs(atom_to_list(S));
qs(S) -> [34, escape(S), 34].
escape([]) -> [];
escape([$" | T]) -> [$\\, $" | escape(T)];
escape([$\\ | T]) -> [$\\, $\\ | escape(T)];
escape([C | T]) -> [C | escape(T)].

join([], _) -> [];
join([X], _) -> X;
join([H | T], Sep) -> [H, Sep, join(T, Sep)].
