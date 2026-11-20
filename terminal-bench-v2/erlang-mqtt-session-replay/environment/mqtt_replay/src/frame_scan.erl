-module(frame_scan).
-export([read_dir/1]).

read_dir(Dir) ->
    Files = lists:sort(filelib:wildcard(filename:join(Dir, "*.trace"))),
    case read_files(Files, []) of
        {ok, Events} -> {ok, Events};
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
