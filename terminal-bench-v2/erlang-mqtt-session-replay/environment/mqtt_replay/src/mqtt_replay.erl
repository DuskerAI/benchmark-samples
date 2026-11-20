-module(mqtt_replay).
-export([main_cli/0, main/1]).

main_cli() ->
    case main(init:get_plain_arguments()) of
        ok -> halt(0);
        {error, Message} ->
            io:format(standard_error, "~s~n", [flat(Message)]),
            halt(1)
    end.

main(Args) ->
    case args(Args, #{}) of
        #{input := Input, output := Output} -> run(Input, Output);
        _ -> {error, "usage: mqtt_replay --input <dir> --output <dir>"}
    end.

args(["--input", V | Rest], Acc) -> args(Rest, Acc#{input => V});
args(["--output", V | Rest], Acc) -> args(Rest, Acc#{output => V});
args([_ | Rest], Acc) -> args(Rest, Acc);
args([], Acc) -> Acc.

run(Input, Output) ->
    case frame_scan:read_dir(Input) of
        {ok, Events} ->
            ok = filelib:ensure_dir(filename:join(Output, "x")),
            {Report, Deliveries} = session_state:replay(Events),
            ok = json_emit:write_json(filename:join(Output, "report.json"), Report),
            ok = json_emit:write_jsonl(filename:join(Output, "deliveries.jsonl"), Deliveries),
            ok;
        Error -> Error
    end.

flat(Term) when is_list(Term) -> Term;
flat(Term) -> lists:flatten(io_lib:format("~p", [Term])).
