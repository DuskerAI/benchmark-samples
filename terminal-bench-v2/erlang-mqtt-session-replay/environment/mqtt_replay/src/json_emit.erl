-module(json_emit).
-export([encode/1, write_json/2, write_jsonl/2]).

write_json(Path, Term) ->
    file:write_file(Path, [encode(Term), "\n"]).

write_jsonl(Path, Rows) ->
    Body = case Rows of
        [] -> [];
        _ -> [[encode(Row), "\n"] || Row <- Rows]
    end,
    file:write_file(Path, Body).

encode(V) when is_map(V) ->
    Parts = [[$", escape(key(K)), $", $:, encode(maps:get(K, V))] || K <- lists:sort(maps:keys(V))],
    [${, join(Parts, $,), $}];
encode(V) when is_atom(V) ->
    case V of
        true -> "true";
        false -> "false";
        null -> "null";
        _ -> [$", escape(atom_to_list(V)), $"]
    end;
encode(V) when is_integer(V) -> integer_to_list(V);
encode(V) when is_binary(V) -> [$", escape(binary_to_list(V)), $"];
encode([]) -> "[]";
encode(V) when is_list(V) ->
    case printable(V) of
        true -> [$", escape(V), $"];
        false -> [$[, join([encode(X) || X <- V], $,), $]]
    end.

key(K) when is_atom(K) -> atom_to_list(K);
key(K) when is_integer(K) -> integer_to_list(K);
key(K) when is_binary(K) -> binary_to_list(K);
key(K) when is_list(K) -> K.

printable([]) -> true;
printable([H | T]) when is_integer(H), H >= 32, H =< 126 -> printable(T);
printable(_) -> false.

escape([]) -> [];
escape([$" | T]) -> [$\\, $" | escape(T)];
escape([$\\ | T]) -> [$\\, $\\ | escape(T)];
escape([10 | T]) -> [$\\, $n | escape(T)];
escape([13 | T]) -> [$\\, $r | escape(T)];
escape([H | T]) -> [H | escape(T)].

join([], _Sep) -> [];
join([X], _Sep) -> X;
join([X | Xs], Sep) -> [X, Sep, join(Xs, Sep)].
