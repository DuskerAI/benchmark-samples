:- use_module(library(http/json)).
:- use_module(library(readutil)).
:- use_module(library(lists)).
:- initialization(main, main).

main :-
    current_prolog_flag(argv, Raw),
    maplist(as_atom, Raw, Args),
    run(Args),
    halt(0).

as_atom(X, X) :- atom(X), !.
as_atom(X, A) :- string(X), atom_string(A, X).

run([adjudicate, In, Out]) :-
    read_terms(In, Terms),
    adjudicate(Terms, Report),
    write_json(Out, Report).
run([checkpoint, In, CountAtom, StateOut, ReportOut]) :-
    atom_number(CountAtom, Count),
    read_terms(In, Terms),
    prefix_count(Count, Terms, PrefixTerms),
    adjudicate(PrefixTerms, Report),
    record_lines(In, Lines),
    prefix_count(Count, Lines, PrefixLines),
    write_json(StateOut, json{count:Count,prefix:PrefixLines}),
    write_json(ReportOut, Report).
run([resume, In, StateIn, ReportOut]) :-
    read_json(StateIn, Saved),
    _Count = Saved.count,
    read_terms(In, Terms),
    adjudicate(Terms, Report),
    write_json(ReportOut, Report).

read_terms(File, Terms) :-
    open(File, read, S),
    read_all_terms(S, [], Rev),
    close(S),
    reverse(Rev, Terms).

read_all_terms(S, Acc, Terms) :-
    read_term(S, Term, []),
    ( Term == end_of_file -> Terms = Acc ; read_all_terms(S, [Term|Acc], Terms) ).

record_lines(File, Lines) :-
    read_file_to_string(File, Text, []),
    split_string(Text, "\n", "\r\t ", Raw),
    exclude(=(""), Raw, Lines).

prefix_count(N, List, Prefix) :- length(Prefix, N), append(Prefix, _, List), !.
prefix_count(_, List, List).

write_json(File, Dict) :-
    open(File, write, S),
    json_write_dict(S, Dict, [width(0)]),
    nl(S),
    close(S).

read_json(File, Dict) :- open(File, read, S), json_read_dict(S, Dict), close(S).

initial_state(state{games:[],players:[],current:none}).

adjudicate(Terms, Report) :-
    initial_state(S0),
    foldl(step, Terms, S0, S),
    reverse(S.games, Games),
    standings(S.players, Games, Standings),
    Report = json{games:Games,standings:Standings}.

step(game(Id, Size, Komi, Black, White, Rule), S0, S) :-
    empty_board(Board),
    add_unique(Black, S0.players, P1),
    add_unique(White, P1, Players),
    Cur = cur{id:Id,size:Size,komi:Komi,black:Black,white:White,rule:Rule,board:Board,turn:b,passes:0,phase:play,capt_b:0,capt_w:0,dead:[],penalties:[b-0,w-0],history:[Board]},
    S = S0.put(_{players:Players,current:Cur}).
step(move(Color, Coord), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 == none -> S = S0
    ; apply_move_weak(Color, Coord, Cur0, Cur) -> S = S0.put(current, Cur)
    ; illegal_game(Color, Cur0, Game), add_game(S0, Game, S)
    ).
step(pass(Color), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 \== none, Cur0.turn == Color ->
        opponent(Color, Next),
        P is Cur0.passes + 1,
        ( P >= 2 -> Phase = scoring ; Phase = play ),
        Cur = Cur0.put(_{turn:Next,passes:P,phase:Phase}),
        S = S0.put(current, Cur)
    ; Cur0 \== none -> illegal_game(Color, Cur0, Game), add_game(S0, Game, S)
    ; S = S0
    ).
step(timeout(Color, Points), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 == none -> S = S0
    ; update_penalty(Color, Points, Cur0.penalties, Ps), Cur = Cur0.put(penalties, Ps), S = S0.put(current, Cur)
    ).
step(resign(Color), S0, S) :-
    Cur = S0.current,
    ( Cur == none -> S = S0 ; forfeit_game(Color, Cur, Game), add_game(S0, Game, S) ).
step(status(_Coord, dead), S0, S0).
step(score, S0, S) :-
    Cur = S0.current,
    ( Cur == none -> S = S0 ; score_current_weak(Cur, Game), add_game(S0, Game, S) ).
step(_, S, S).

empty_board([]).
opponent(b, w).
opponent(w, b).

add_unique(X, Xs, Xs) :- memberchk(X, Xs), !.
add_unique(X, Xs, [X|Xs]).

coord(A, X, Y) :-
    atom_chars(A, [CX,CY]),
    char_code(CX, C1), char_code(CY, C2),
    X is C1 - 97, Y is C2 - 97.

in_bounds(N, X, Y) :- integer(N), X >= 0, Y >= 0, X < N, Y < N.
occupied(Board, X, Y, C) :- memberchk(s(X,Y,C), Board).
adjacent(X,Y,NX,Y) :- NX is X + 1.
adjacent(X,Y,NX,Y) :- NX is X - 1.
adjacent(X,Y,X,NY) :- NY is Y + 1.
adjacent(X,Y,X,NY) :- NY is Y - 1.

apply_move_weak(Color, Coord, Cur0, Cur) :-
    Cur0.phase == play,
    Cur0.turn == Color,
    coord(Coord, X, Y),
    in_bounds(Cur0.size, X, Y),
    \+ occupied(Cur0.board, X, Y, _),
    sort([s(X,Y,Color)|Cur0.board], Placed),
    opponent(Color, Opp),
    capture_all(Placed, Cur0.size, Opp, Board1, Captured),
    group_at(Board1, X, Y, Group),
    group_liberties(Board1, Cur0.size, Group, Libs),
    Libs \= [],
    opponent(Color, Next),
    add_capture(Color, Captured, Cur0, Cur1),
    Cur = Cur1.put(_{board:Board1,turn:Next,passes:0,history:[Board1|Cur0.history]}).

add_capture(b, N, Cur, Cur.put(capt_b, V)) :- V is Cur.capt_b + N.
add_capture(w, N, Cur, Cur.put(capt_w, V)) :- V is Cur.capt_w + N.

capture_all(Board, N, Color, After, Count) :-
    groups_of_color(Board, Color, Groups),
    include(no_liberties(Board, N), Groups, DeadGroups),
    append(DeadGroups, Dead),
    length(Dead, Count),
    remove_stones(Board, Dead, Tmp),
    sort(Tmp, After).

no_liberties(Board, N, Group) :- group_liberties(Board, N, Group, []).

groups_of_color(Board, Color, Groups) :-
    findall(G, (member(s(X,Y,Color), Board), group_at(Board, X, Y, G)), Raw),
    sort(Raw, Groups).

group_at(Board, X, Y, Group) :-
    occupied(Board, X, Y, C),
    flood_stones([s(X,Y,C)], Board, C, [], Raw),
    sort(Raw, Group).

flood_stones([], _, _, Seen, Seen).
flood_stones([s(X,Y,C)|Rest], Board, C, Seen, Group) :-
    ( memberchk(s(X,Y,C), Seen) -> flood_stones(Rest, Board, C, Seen, Group)
    ; findall(s(NX,NY,C), (adjacent(X,Y,NX,NY), memberchk(s(NX,NY,C), Board)), Ns),
      append(Ns, Rest, Queue),
      flood_stones(Queue, Board, C, [s(X,Y,C)|Seen], Group)
    ).

group_liberties(Board, N, Group, Libs) :-
    findall(e(NX,NY), (member(s(X,Y,_), Group), adjacent(X,Y,NX,NY), in_bounds(N,NX,NY), \+ occupied(Board,NX,NY,_)), Raw),
    sort(Raw, Libs).

remove_stones(Board, Dead, After) :- findall(S, (member(S, Board), \+ memberchk(S, Dead)), After).

update_penalty(C, Add, [C-V|Rest], [C-NV|Rest]) :- !, NV is V + Add.
update_penalty(C, Add, [P|Rest], [P|Out]) :- update_penalty(C, Add, Rest, Out).

color_player(Cur, b, Cur.black).
color_player(Cur, w, Cur.white).
penalty_value(Cur, C, V) :- memberchk(C-V, Cur.penalties).

illegal_game(Color, Cur, Game) :-
    opponent(Color, WinColor),
    color_player(Cur, WinColor, Winner),
    base_game(Cur, illegal, Winner, WinColor, null, null, illegal_play, Game).

forfeit_game(Color, Cur, Game) :-
    opponent(Color, WinColor),
    color_player(Cur, WinColor, Winner),
    base_game(Cur, forfeit, Winner, WinColor, null, null, resignation, Game).

score_current_weak(Cur, Game) :-
    territories(Cur.size, Cur.board, TB, TW),
    count_color(Cur.board, b, SB),
    count_color(Cur.board, w, SW),
    penalty_value(Cur, b, PB), penalty_value(Cur, w, PW),
    ( Cur.rule == area -> BS is SB + TB - PB, WS is SW + TW + Cur.komi - PW
    ; BS is TB + Cur.capt_b - PB, WS is TW + Cur.capt_w + Cur.komi - PW
    ),
    winner_from_scores(Cur, BS, WS, Winner, WinColor),
    base_game(Cur, scored, Winner, WinColor, BS, WS, scored, Game).

count_color(Board, C, N) :- include(stone_color(C), Board, L), length(L, N).
stone_color(C, s(_,_,C)).

territories(N, Board, B, W) :-
    empty_regions(N, Board, Regions),
    foldl(region_score(Board, N), Regions, 0-0, B-W).

region_score(Board, N, Region, B0-W0, B-W) :-
    length(Region, Len),
    adjacent_colors(Board, N, Region, Colors),
    ( Colors = [b] -> B is B0 + Len, W = W0
    ; Colors = [w] -> W is W0 + Len, B = B0
    ; B = B0, W = W0
    ).

adjacent_colors(Board, N, Region, Colors) :-
    findall(C, (member(e(X,Y), Region), adjacent(X,Y,NX,NY), in_bounds(N,NX,NY), occupied(Board,NX,NY,C)), Raw),
    sort(Raw, Colors).

empty_regions(N, Board, Regions) :-
    Hi is N - 1,
    findall(e(X,Y), (between(0, Hi, X), between(0, Hi, Y), \+ occupied(Board,X,Y,_)), Points),
    regions_from_points(Points, N, Board, [], Regions).

regions_from_points([], _, _, _, []).
regions_from_points([P|Rest], N, Board, Seen, Regions) :-
    ( memberchk(P, Seen) -> regions_from_points(Rest, N, Board, Seen, Regions)
    ; empty_flood([P], N, Board, [], Region),
      append(Region, Seen, Seen2),
      regions_from_points(Rest, N, Board, Seen2, More),
      Regions = [Region|More]
    ).

empty_flood([], _, _, Seen, Seen).
empty_flood([e(X,Y)|Rest], N, Board, Seen, Region) :-
    ( memberchk(e(X,Y), Seen) -> empty_flood(Rest, N, Board, Seen, Region)
    ; findall(e(NX,NY), (adjacent(X,Y,NX,NY), in_bounds(N,NX,NY), \+ occupied(Board,NX,NY,_)), Ns),
      append(Ns, Rest, Queue),
      empty_flood(Queue, N, Board, [e(X,Y)|Seen], Region)
    ).

winner_from_scores(Cur, BS, WS, Winner, Color) :-
    ( BS > WS -> Color = b ; WS > BS -> Color = w ; Color = draw ),
    ( Color == draw -> Winner = draw ; color_player(Cur, Color, Winner) ).

base_game(Cur, Status, Winner, WinColor, BS, WS, Reason, json{id:Cur.id,status:Status,black:Cur.black,white:Cur.white,rule:Cur.rule,winner:Winner,winner_color:WinColor,black_score:BS,white_score:WS,reason:Reason}).

add_game(S0, Game, S) :- S = S0.put(_{games:[Game|S0.games],current:none}).

standings(Players0, Games, Standings) :-
    sort(Players0, Players),
    findall(E, (member(P, Players), standing_entry(P, Games, E)), Entries),
    predsort(compare_standing, Entries, Standings).

standing_entry(P, Games, json{player:P,wins:Wins,score_diff:Diff}) :-
    findall(W, game_win(P, Games, W), Ws), sum_list(Ws, Wins),
    findall(D, game_diff(P, Games, D), Ds), sum_list(Ds, Diff).

game_win(P, Games, W) :- member(G, Games), (G.winner == P -> W = 1 ; W = 0).
game_diff(P, Games, D) :-
    member(G, Games),
    ( G.status == scored, G.black == P -> D is G.black_score - G.white_score
    ; G.status == scored, G.white == P -> D is G.white_score - G.black_score
    ; G.winner == P -> D = 1
    ; (G.black == P ; G.white == P) -> D = -1
    ; D = 0
    ).

compare_standing(Order, A, B) :-
    ( A.wins > B.wins -> Order = (<)
    ; A.wins < B.wins -> Order = (>)
    ; A.score_diff > B.score_diff -> Order = (<)
    ; A.score_diff < B.score_diff -> Order = (>)
    ; compare(Order, A.player, B.player)
    ).
