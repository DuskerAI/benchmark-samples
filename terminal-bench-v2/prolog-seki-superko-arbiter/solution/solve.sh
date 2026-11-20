#!/bin/bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path

path = Path('/app/src/arbiter.pl')
text = path.read_text()

old = """run([resume, In, StateIn, ReportOut]) :-
    read_json(StateIn, Saved),
    _Count = Saved.count,
    read_terms(In, Terms),
    adjudicate(Terms, Report),
    write_json(ReportOut, Report).
"""
new = """run([resume, In, StateIn, ReportOut]) :-
    read_json(StateIn, Saved),
    Count = Saved.count,
    record_lines(In, Lines),
    prefix_count(Count, Lines, PrefixLines),
    ( PrefixLines = Saved.prefix -> true ; halt(2) ),
    read_terms(In, Terms),
    adjudicate(Terms, Report),
    write_json(ReportOut, Report).
"""
text = text.replace(old, new)

old = """step(game(Id, Size, Komi, Black, White, Rule), S0, S) :-
    empty_board(Board),
    add_unique(Black, S0.players, P1),
    add_unique(White, P1, Players),
    Cur = cur{id:Id,size:Size,komi:Komi,black:Black,white:White,rule:Rule,board:Board,turn:b,passes:0,phase:play,capt_b:0,capt_w:0,dead:[],penalties:[b-0,w-0],history:[Board]},
    S = S0.put(_{players:Players,current:Cur}).
"""
new = """step(game(Id, Size, Komi, Black, White, Rule), S0, S) :-
    empty_board(Board),
    add_unique(Black, S0.players, P1),
    add_unique(White, P1, Players),
    Cur = cur{id:Id,size:Size,komi:Komi,black:Black,white:White,rule:Rule,board:Board,turn:b,passes:0,phase:play,capt_b:0,capt_w:0,dead:[],penalties:[b-0,w-0],history:[Board],setup_open:true},
    ( S0.current == none -> Games = S0.games
    ; invalid_game(S0.current, Interrupted), Games = [Interrupted|S0.games]
    ),
    S = S0.put(_{games:Games,players:Players,current:Cur}).
step(setup(Color, Coord), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 \== none, Cur0.phase == play, Cur0.setup_open == true,
      coord(Coord, X, Y), in_bounds(Cur0.size, X, Y), \+ occupied(Cur0.board, X, Y, _) ->
        sort([s(X,Y,Color)|Cur0.board], Board),
        Cur = Cur0.put(_{board:Board,history:[Board]}),
        S = S0.put(current, Cur)
    ; Cur0 \== none -> illegal_game(Color, Cur0, Game), add_game(S0, Game, S)
    ; S = S0
    ).
"""
text = text.replace(old, new)

old = """step(pass(Color), S0, S) :-
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
"""
new = """step(pass(Color), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 \== none, Cur0.phase == play, Cur0.turn == Color ->
        opponent(Color, Next),
        P is Cur0.passes + 1,
        ( P >= 2 -> Phase = scoring ; Phase = play ),
        Cur = Cur0.put(_{turn:Next,passes:P,phase:Phase,setup_open:false}),
        S = S0.put(current, Cur)
    ; Cur0 \== none -> illegal_game(Color, Cur0, Game), add_game(S0, Game, S)
    ; S = S0
    ).
"""
text = text.replace(old, new)

old = """step(timeout(Color, Points), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 == none -> S = S0
    ; update_penalty(Color, Points, Cur0.penalties, Ps), Cur = Cur0.put(penalties, Ps), S = S0.put(current, Cur)
    ).
"""
new = """step(timeout(Color, Points), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 == none -> S = S0
    ; Cur0.phase == play ->
        update_penalty(Color, Points, Cur0.penalties, Ps),
        Cur = Cur0.put(_{penalties:Ps,setup_open:false}),
        S = S0.put(current, Cur)
    ; illegal_game(Color, Cur0, Game), add_game(S0, Game, S)
    ).
"""
text = text.replace(old, new)

old = """    Cur = Cur1.put(_{board:Board1,turn:Next,passes:0,history:[Board1|Cur0.history]})."""
new = """    Cur = Cur1.put(_{board:Board1,turn:Next,passes:0,history:[Board1|Cur0.history],setup_open:false})."""
text = text.replace(old, new)

old = """step(status(_Coord, dead), S0, S0)."""
new = """step(status(Coord, dead), S0, S) :-
    Cur0 = S0.current,
    ( Cur0 == none -> S = S0
    ; Cur0.phase == scoring, coord(Coord, X, Y), group_at(Cur0.board, X, Y, Group) ->
        append(Group, Cur0.dead, Dead0),
        sort(Dead0, Dead),
        Cur = Cur0.put(dead, Dead),
        S = S0.put(current, Cur)
    ; invalid_game(Cur0, Game), add_game(S0, Game, S)
    )."""
text = text.replace(old, new)

old = """step(score, S0, S) :-
    Cur = S0.current,
    ( Cur == none -> S = S0 ; score_current_weak(Cur, Game), add_game(S0, Game, S) )."""
new = """step(score, S0, S) :-
    Cur = S0.current,
    ( Cur == none -> S = S0
    ; Cur.phase == scoring -> score_current_weak(Cur, Game), add_game(S0, Game, S)
    ; invalid_game(Cur, Game), add_game(S0, Game, S)
    )."""
text = text.replace(old, new)

old = """    Libs \\= [],
    opponent(Color, Next),"""
new = """    Libs \\= [],
    \\+ memberchk(Board1, Cur0.history),
    opponent(Color, Next),"""
text = text.replace(old, new)

old = """score_current_weak(Cur, Game) :-
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
"""
new = """score_current_weak(Cur, Game) :-
    remove_dead(Cur.board, Cur.dead, Board, DeadB, DeadW),
    territories(Cur.size, Board, TB, TW),
    count_color(Board, b, SB),
    count_color(Board, w, SW),
    penalty_value(Cur, b, PB), penalty_value(Cur, w, PW),
    ( Cur.rule == area -> BS is SB + TB - PB, WS is SW + TW + Cur.komi - PW
    ; BS is TB + Cur.capt_b + DeadW - PB, WS is TW + Cur.capt_w + DeadB + Cur.komi - PW
    ),
    winner_from_scores(Cur, BS, WS, Winner, WinColor),
    base_game(Cur, scored, Winner, WinColor, BS, WS, scored, Game).

remove_dead(Board, Dead0, Clean, DeadB, DeadW) :-
    sort(Dead0, Dead),
    remove_stones(Board, Dead, Clean),
    count_color(Dead, b, DeadB),
    count_color(Dead, w, DeadW).

count_color(Board, C, N) :- include(stone_color(C), Board, L), length(L, N).
"""
text = text.replace(old, new)

old = """forfeit_game(Color, Cur, Game) :-
    opponent(Color, WinColor),
    color_player(Cur, WinColor, Winner),
    base_game(Cur, forfeit, Winner, WinColor, null, null, resignation, Game).
"""
new = """forfeit_game(Color, Cur, Game) :-
    opponent(Color, WinColor),
    color_player(Cur, WinColor, Winner),
    base_game(Cur, forfeit, Winner, WinColor, null, null, resignation, Game).

invalid_game(Cur, Game) :-
    base_game(Cur, illegal, draw, draw, null, null, illegal_play, Game).
"""
text = text.replace(old, new)

old = """region_score(Board, N, Region, B0-W0, B-W) :-
    length(Region, Len),
    adjacent_colors(Board, N, Region, Colors),
    ( Colors = [w] -> W is W0 + Len, B = B0
    ; Colors = [] -> B = B0, W = W0
    ; B is B0 + Len, W = W0
    ).
"""
new = """region_score(Board, N, Region, B0-W0, B-W) :-
    length(Region, Len),
    adjacent_colors(Board, N, Region, Colors),
    ( Colors = [b] -> B is B0 + Len, W = W0
    ; Colors = [w] -> W is W0 + Len, B = B0
    ; B = B0, W = W0
    ).
"""
text = text.replace(old, new)

old = """game_diff(P, Games, D) :-
    member(G, Games),
    ( G.status == scored, G.black == P -> D is G.black_score - G.white_score
    ; G.status == scored, G.white == P -> D is G.white_score - G.black_score
    ; G.winner == P -> D = 1
    ; (G.black == P ; G.white == P) -> D = -1
    ; D = 0
    ).
"""
new = """game_diff(P, Games, D) :-
    member(G, Games),
    ( G.status == scored, G.black == P -> D is G.black_score - G.white_score
    ; G.status == scored, G.white == P -> D is G.white_score - G.black_score
    ; G.winner == P -> D = 1
    ; G.status \== scored, G.winner_color \== draw, (G.black == P ; G.white == P) -> D = -1
    ; D = 0
    ).
"""
text = text.replace(old, new)

path.write_text(text)
PY
/app/scripts/build.sh
