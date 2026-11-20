#!/bin/bash
set -euo pipefail

cat > /opt/quic_gate/src/quic_gate.erl <<'ERL'
-module(quic_gate).
-export([main/0]).

main() ->
    Args = init:get_plain_arguments(),
    case parse_args(Args, #{}) of
        {ok, Opts} ->
            case run(Opts) of
                ok -> halt(0);
                _ -> halt(1)
            end;
        _ -> halt(2)
    end.

parse_args([], Opts) ->
    case {maps:is_key(state, Opts), maps:is_key(input, Opts), maps:is_key(out, Opts)} of
        {true, true, true} -> {ok, Opts};
        _ -> error
    end;
parse_args(["--state", V | T], Opts) -> parse_args(T, Opts#{state => V});
parse_args(["--input", V | T], Opts) -> parse_args(T, Opts#{input => V});
parse_args(["--out", V | T], Opts) -> parse_args(T, Opts#{out => V});
parse_args(_, _) -> error.

run(#{state := StatePath, input := InputPath, out := OutPath}) ->
    {ok, Bin} = file:read_file(InputPath),
    State0 = load_state(StatePath),
    {Status, State1, Audit, Reasons, A, R} = scan(Bin, State0, 0, [], #{}, 0, 0),
    ok = filelib:ensure_dir(OutPath),
    case Status of
        ok -> ok = save_state(StatePath, State1);
        failed -> ok
    end,
    Report = #{status => atom_to_binary(Status), accepted => A, rejected => R,
        reasons => Reasons, audit => lists:reverse(Audit)},
    file:write_file(OutPath, [qjson:encode(Report), <<"\n">>]).

load_state(Path) ->
    case file:read_file(Path) of
        {ok, B} ->
            try binary_to_term(B) of S when is_map(S) -> S catch _:_ -> #{} end;
        _ -> #{}
    end.

save_state(Path, S) ->
    ok = filelib:ensure_dir(Path),
    file:write_file(Path, term_to_binary(S)).

scan(<<>>, State, _Ix, Audit, Reasons, A, R) -> {ok, State, Audit, Reasons, A, R};
scan(<<Len:16/big-unsigned, Rest/binary>>, State, Ix, Audit, Reasons, A, R) ->
    case Rest of
        <<Body:Len/binary, Tail/binary>> ->
            case decode_body(Body) of
                {ok, Epoch, Kp0, Dcid, PnLen, Trunc} ->
                    Kp = case Epoch of 2 -> Kp0; _ -> 0 end,
                    {State1, Entry, Accepted, Reason} = admit(State, Epoch, Kp, Dcid, PnLen, Trunc),
                    Reasons1 = bump_reason(Reasons, Reason, Accepted),
                    Audit1 = [Entry#{index => Ix} | Audit],
                    scan(Tail, State1, Ix + 1, Audit1, Reasons1, A + bool(Accepted), R + bool(not Accepted));
                error ->
                    Entry = #{index => Ix, verdict => <<"reject">>, reason => <<"invalid">>, pn => null},
                    scan(Tail, State, Ix + 1, [Entry | Audit], inc(Reasons, invalid), A, R + 1)
            end;
        _ -> {failed, State, Audit, inc(Reasons, incomplete), A, R + 1}
    end;
scan(_, State, _Ix, Audit, Reasons, A, R) -> {failed, State, Audit, inc(Reasons, incomplete), A, R + 1}.

decode_body(<<Epoch:8, Kp:8, Rest/binary>>) when Epoch >= 0, Epoch =< 2, Kp >= 0, Kp =< 1 ->
    case read_varint(Rest) of
        {Len, AfterLen} when Len >= 0, Len =< 255, byte_size(AfterLen) >= Len + 2 ->
            <<Dcid:Len/binary, PnLen:8, Tail/binary>> = AfterLen,
            case {Epoch, Kp, PnLen, Tail} of
                {E, P, _, _} when E =/= 2, P =/= 0 -> error;
                {_, _, L, _} when L < 1; L > 4 -> error;
                {_, _, L, T} when byte_size(T) =:= L ->
                    <<PnBytes:L/binary>> = T,
                    {ok, Epoch, Kp, Dcid, PnLen, binary:decode_unsigned(PnBytes, big)};
                _ -> error
            end;
        _ -> error
    end;
decode_body(_) -> error.

read_varint(<<B:8, Rest/binary>>) ->
    Prefix = B bsr 6,
    V0 = B band 16#3f,
    case Prefix of
        0 -> {V0, Rest};
        1 when byte_size(Rest) >= 1 ->
            <<B2:8, Tail/binary>> = Rest,
            {(V0 bsl 8) bor B2, Tail};
        2 when byte_size(Rest) >= 3 ->
            <<B2:24/big-unsigned, Tail/binary>> = Rest,
            {((V0 bsl 24) bor B2), Tail};
        3 when byte_size(Rest) >= 7 ->
            <<B2:56/big-unsigned, Tail/binary>> = Rest,
            {((V0 bsl 56) bor B2), Tail};
        _ -> error
    end;
read_varint(_) -> error.

admit(State, Epoch, Kp, Dcid, PnLen, Trunc) ->
    Key = {Dcid, Epoch, Kp},
    Rec = maps:get(Key, State, #{largest => -1, seen => []}),
    Largest = maps:get(largest, Rec),
    Pn = reconstruct(Trunc, PnLen, Largest),
    Seen = maps:get(seen, Rec),
    Base = #{epoch => Epoch, key_phase => Kp, dcid => hex(Dcid), pn => Pn},
    case lists:member(Pn, Seen) of
        true -> {State, Base#{verdict => <<"reject">>, reason => <<"duplicate">>}, false, duplicate};
        false ->
            if Pn < Largest - 63 ->
                    {State, Base#{verdict => <<"reject">>, reason => <<"stale">>}, false, stale};
               true ->
                    Largest1 = erlang:max(Largest, Pn),
                    Seen1 = prune([Pn | Seen], Largest1),
                    State1 = State#{Key => #{largest => Largest1, seen => Seen1}},
                    {State1, Base#{verdict => <<"accept">>, reason => <<"ok">>}, true, ok}
            end
    end.

reconstruct(Trunc, _Len, -1) -> Trunc;
reconstruct(Trunc, Len, Largest) ->
    Expected = Largest + 1,
    Win = 1 bsl (Len * 8),
    Half = Win div 2,
    Mask = Win - 1,
    Candidate = (Expected band bnot(Mask)) bor Trunc,
    if Candidate + Half =< Expected -> Candidate + Win;
       Candidate > Expected + Half andalso Candidate >= Win -> Candidate - Win;
       true -> Candidate
    end.

prune(Seen, Largest) -> [P || P <- lists:usort(Seen), P >= Largest - 63].

bump_reason(R, ok, true) -> R;
bump_reason(R, Reason, false) -> inc(R, Reason).

inc(R, Reason) ->
    K = atom_to_binary(Reason),
    R#{K => maps:get(K, R, 0) + 1}.

bool(true) -> 1;
bool(false) -> 0.

hex(B) -> hex(B, <<>>).
hex(<<>>, Acc) -> Acc;
hex(<<X:8, T/binary>>, Acc) ->
    H = <<"0123456789abcdef">>,
    hex(T, <<Acc/binary, (binary:at(H, X bsr 4)), (binary:at(H, X band 15))>>).
ERL

make -C /opt/quic_gate clean all
