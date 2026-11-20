-module(qjson).
-export([encode/1]).

encode(V) when is_map(V) ->
    Pairs = maps:to_list(V),
    <<"{", (join([<< (key(K))/binary, ":", (encode(Val))/binary >> || {K, Val} <- Pairs]))/binary, "}">>;
encode(V) when is_list(V) ->
    <<"[", (join([encode(X) || X <- V]))/binary, "]">>;
encode(V) when is_binary(V) ->
    quote(V);
encode(V) when is_atom(V) ->
    quote(atom_to_binary(V, utf8));
encode(V) when is_integer(V) ->
    integer_to_binary(V);
encode(true) -> <<"true">>;
encode(false) -> <<"false">>;
encode(null) -> <<"null">>.

key(K) when is_atom(K) -> quote(atom_to_binary(K, utf8));
key(K) when is_binary(K) -> quote(K);
key(K) when is_list(K) -> quote(unicode:characters_to_binary(K)).

join([]) -> <<>>;
join([H]) -> H;
join([H|T]) -> <<H/binary, ",", (join(T))/binary>>.

quote(B) -> <<"\"", (esc(B))/binary, "\"">>.

esc(<<>>) -> <<>>;
esc(<<$", T/binary>>) -> <<"\\\"", (esc(T))/binary>>;
esc(<<$\\, T/binary>>) -> <<"\\\\", (esc(T))/binary>>;
esc(<<$\n, T/binary>>) -> <<"\\n", (esc(T))/binary>>;
esc(<<$\r, T/binary>>) -> <<"\\r", (esc(T))/binary>>;
esc(<<$\t, T/binary>>) -> <<"\\t", (esc(T))/binary>>;
esc(<<C, T/binary>>) when C < 32 -> <<"\\u00", (hex4(C))/binary, (esc(T))/binary>>;
esc(<<C, T/binary>>) -> <<C, (esc(T))/binary>>.

hex4(C) ->
    H = <<"0123456789abcdef">>,
    <<(binary:at(H, C bsr 4)), (binary:at(H, C band 15))>>.
