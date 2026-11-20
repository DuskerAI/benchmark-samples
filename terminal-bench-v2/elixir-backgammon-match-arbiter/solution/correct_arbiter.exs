#!/usr/bin/env elixir

defmodule Arbiter do
  def initial do
    %{length: nil, jacoby: false, scores: %{"W" => 0, "B" => 0}, crawford_used: false,
      active: nil, games: [], accepted: [], error: nil}
  end

  def run(lines) do
    Enum.with_index(lines, 1)
    |> Enum.reduce_while(initial(), fn {raw, line}, state ->
      text = String.trim(raw)
      if text == "" do
        {:cont, state}
      else
        case event(state, String.split(text), line) do
          {:ok, next} -> {:cont, %{next | accepted: state.accepted ++ [text]}}
          {:error, reason} -> {:halt, %{state | error: %{line: line, reason: reason}}}
        end
      end
    end)
  end

  def event(%{length: nil} = s, ["MATCH", length, jacoby], _line) do
    with {n, ""} <- Integer.parse(length), true <- n >= 0, true <- jacoby in ["0", "1"] do
      {:ok, %{s | length: n, jacoby: jacoby == "1"}}
    else _ -> {:error, "bad_match"} end
  end
  def event(%{length: nil}, _, _), do: {:error, "match_required"}
  def event(s, ["MATCH" | _], _), do: {:error, "duplicate_match"}

  def event(%{active: nil} = s, ["GAME", id], _line) do
    cond do
      id == "" or Enum.any?(s.games, &(&1.id == id)) -> {:error, "bad_game"}
      s.length > 0 and Enum.max(Map.values(s.scores)) >= s.length -> {:error, "match_complete"}
      true ->
        one_away = s.length > 0 and Enum.any?(Map.values(s.scores), &(&1 == s.length - 1))
        crawford = one_away and not s.crawford_used
        game = %{id: id, turn: "W", board: nil, roll: nil, cube: 1, owner: "center", offer: nil, crawford: crawford}
        {:ok, %{s | active: game, crawford_used: s.crawford_used or crawford}}
    end
  end
  def event(%{active: nil}, _, _), do: {:error, "game_required"}
  def event(%{active: _}, ["GAME" | _], _), do: {:error, "game_in_progress"}

  def event(s, ["POSITION", turn, wp, bp, wb, bb, wo, bo], _line) do
    g = s.active
    with true <- g.roll == nil and g.offer == nil,
         true <- turn in ["W", "B"],
         {:ok, wpts} <- points(wp), {:ok, bpts} <- points(bp),
         {wbar, ""} <- Integer.parse(wb), {bbar, ""} <- Integer.parse(bb),
         {woff, ""} <- Integer.parse(wo), {boff, ""} <- Integer.parse(bo),
         true <- Enum.all?([wbar, bbar, woff, boff], &(&1 >= 0)),
         true <- Enum.sum(Map.values(wpts)) + wbar + woff == 15,
         true <- Enum.sum(Map.values(bpts)) + bbar + boff == 15,
         true <- no_overlap(wpts, bpts) do
      board = %{"W" => wpts, "B" => bpts, "Wbar" => wbar, "Bbar" => bbar, "Woff" => woff, "Boff" => boff}
      {:ok, %{s | active: %{g | turn: turn, board: board}}}
    else _ -> {:error, "bad_position"} end
  end

  def event(s, ["ROLL", a, b], _line) do
    g = s.active
    with true <- g.board != nil and g.roll == nil and g.offer == nil,
         {d1, ""} <- Integer.parse(a), {d2, ""} <- Integer.parse(b),
         true <- d1 in 1..6 and d2 in 1..6 do
      {:ok, %{s | active: %{g | roll: {d1, d2}}}}
    else _ -> {:error, "bad_roll"} end
  end

  def event(s, ["PLAY", player, text], _line) do
    g = s.active
    with true <- player == g.turn and g.board != nil and g.roll != nil and g.offer == nil,
         {:ok, submitted} <- moves(text),
         paths <- allowed_paths(g.board, player, g.roll),
         path when not is_nil(path) <- Enum.find(paths, fn {p, _b, _used} -> p == submitted end) do
      {_p, board, _used} = path
      next = other(player)
      if board[player <> "off"] == 15 do
        finish(s, player, outcome(board, player), "board")
      else
        {:ok, %{s | active: %{g | board: board, roll: nil, turn: next}}}
      end
    else _ -> {:error, "illegal_play"} end
  end

  def event(s, ["DOUBLE", player], _line) do
    g = s.active
    if player == g.turn and g.board != nil and g.roll == nil and g.offer == nil and not g.crawford and
       g.cube < 64 and g.owner in ["center", player] do
      {:ok, %{s | active: %{g | offer: player}}}
    else
      {:error, "illegal_double"}
    end
  end

  def event(s, ["TAKE", player], _line) do
    g = s.active
    if g.offer != nil and player == other(g.offer) do
      {:ok, %{s | active: %{g | cube: g.cube * 2, owner: player, offer: nil}}}
    else
      {:error, "illegal_take"}
    end
  end

  def event(s, ["DROP", player], _line) do
    g = s.active
    if g.offer != nil and player == other(g.offer), do: finish(s, g.offer, 1, "drop"), else: {:error, "illegal_drop"}
  end
  def event(_s, _tokens, _line), do: {:error, "bad_event"}

  def points("-"), do: {:ok, %{}}
  def points(text) do
    Enum.reduce_while(String.split(text, ","), {:ok, %{}}, fn item, {:ok, acc} ->
      case Regex.run(~r/^(\d+)x(\d+)$/, item) do
        [_, ps, cs] ->
          p = String.to_integer(ps); c = String.to_integer(cs)
          if p in 1..24 and c > 0 and not Map.has_key?(acc, p), do: {:cont, {:ok, Map.put(acc, p, c)}}, else: {:halt, :error}
        _ -> {:halt, :error}
      end
    end)
    |> case do {:ok, x} -> {:ok, x}; _ -> :error end
  end

  def no_overlap(w, b), do: Enum.all?(Map.keys(w), fn p -> not Map.has_key?(b, p) end)

  def moves(text) do
    Enum.reduce_while(String.split(text, ","), {:ok, []}, fn item, {:ok, acc} ->
      case Regex.run(~r/^(\d+)\/(\d+)$/, item) do
        [_, a, b] -> {:cont, {:ok, acc ++ [{String.to_integer(a), String.to_integer(b)}]}}
        _ -> {:halt, :error}
      end
    end)
    |> case do {:ok, x} when length(x) > 0 -> {:ok, x}; _ -> :error end
  end

  def allowed_paths(board, player, {a, b}) do
    dice = if a == b, do: [a, a, a, a], else: [a, b]
    raw = search(board, player, dice, [], [])
    max_used = raw |> Enum.map(fn {_p, _b, u} -> length(u) end) |> Enum.max(fn -> 0 end)
    best = Enum.filter(raw, fn {_p, _b, u} -> length(u) == max_used end)
    if a != b and max_used == 1 and Enum.any?(best, fn {_p, _b, u} -> u == [max(a, b)] end) do
      Enum.filter(best, fn {_p, _b, u} -> u == [max(a, b)] end)
    else
      best
    end
  end

  def search(board, player, [], path, used), do: [{path, board, used}]
  def search(board, player, dice, path, used) do
    options =
      dice |> Enum.with_index() |> Enum.flat_map(fn {die, idx} ->
        legal_moves(board, player, die)
        |> Enum.map(fn {move, after_board} -> {move, after_board, die, List.delete_at(dice, idx)} end)
      end)
    if options == [] do
      [{path, board, used}]
    else
      Enum.flat_map(options, fn {move, after_board, die, rest} -> search(after_board, player, rest, path ++ [move], used ++ [die]) end)
    end
  end

  def legal_moves(board, player, die) do
    bar_key = player <> "bar"
    sources = if board[bar_key] > 0, do: [bar_source(player)], else: Map.keys(board[player]) |> Enum.sort()
    Enum.flat_map(sources, fn src ->
      dest = destination(player, src, die)
      if legal_destination?(board, player, src, dest, die), do: [{{src, dest}, apply_move(board, player, src, dest)}], else: []
    end)
  end

  def bar_source("W"), do: 25
  def bar_source("B"), do: 0
  def destination("W", 25, die), do: 25 - die
  def destination("B", 0, die), do: die
  def destination("W", src, die), do: max(0, src - die)
  def destination("B", src, die), do: min(25, src + die)

  def legal_destination?(board, player, src, dest, die) do
    opp = other(player)
    exact = if player == "W", do: src - die, else: src + die
    cond do
      src == bar_source(player) -> dest in 1..24 and Map.get(board[opp], dest, 0) < 2
      dest in 1..24 -> Map.get(board[opp], dest, 0) < 2
      not all_home?(board, player) -> false
      exact == off_dest(player) -> true
      player == "W" and exact < 0 -> not Enum.any?(Map.keys(board[player]), &(&1 > src))
      player == "B" and exact > 25 -> not Enum.any?(Map.keys(board[player]), &(&1 < src))
      true -> false
    end
  end

  def all_home?(board, "W"), do: board["Wbar"] == 0 and Enum.all?(Map.keys(board["W"]), &(&1 in 1..6))
  def all_home?(board, "B"), do: board["Bbar"] == 0 and Enum.all?(Map.keys(board["B"]), &(&1 in 19..24))
  def off_dest("W"), do: 0
  def off_dest("B"), do: 25

  def apply_move(board, player, src, dest) do
    opp = other(player)
    own = if src == bar_source(player), do: board[player], else: dec(board[player], src)
    board1 = if src == bar_source(player), do: Map.update!(board, player <> "bar", &(&1 - 1)), else: board
    board2 = %{board1 | player => own}
    cond do
      dest == off_dest(player) -> Map.update!(board2, player <> "off", &(&1 + 1))
      Map.get(board2[opp], dest, 0) == 1 ->
        board2 |> Map.put(opp, dec(board2[opp], dest)) |> Map.update!(opp <> "bar", &(&1 + 1)) |> Map.put(player, inc(own, dest))
      true -> Map.put(board2, player, inc(own, dest))
    end
  end

  def inc(map, key), do: Map.update(map, key, 1, &(&1 + 1))
  def dec(map, key) do
    case Map.fetch!(map, key) do 1 -> Map.delete(map, key); n -> Map.put(map, key, n - 1) end
  end
  def other("W"), do: "B"
  def other("B"), do: "W"

  def outcome(board, winner) do
    loser = other(winner)
    cond do
      board[loser <> "off"] > 0 -> 1
      board[loser <> "bar"] > 0 -> 3
      winner == "W" and Enum.any?(Map.keys(board[loser]), &(&1 in 1..6)) -> 3
      winner == "B" and Enum.any?(Map.keys(board[loser]), &(&1 in 19..24)) -> 3
      true -> 2
    end
  end

  def finish(s, winner, multiplier, reason) do
    g = s.active
    effective = if s.length == 0 and s.jacoby and g.cube == 1, do: 1, else: multiplier
    points = g.cube * effective
    scores = Map.update!(s.scores, winner, &(&1 + points))
    game = %{id: g.id, winner: winner, points: points, reason: reason, cube: g.cube, crawford: g.crawford}
    {:ok, %{s | scores: scores, games: s.games ++ [game], active: nil}}
  end

  def digest(lines), do: :crypto.hash(:sha256, Enum.join(lines, "\n")) |> Base.encode16(case: :lower)

  def report(s) do
    status = if s.error, do: "invalid", else: "ok"
    error = if s.error, do: ",\"line\":#{s.error.line},\"reason\":\"#{s.error.reason}\"", else: ""
    games = Enum.map_join(s.games, ",", fn g ->
      "{\"id\":\"#{g.id}\",\"winner\":\"#{g.winner}\",\"points\":#{g.points},\"reason\":\"#{g.reason}\",\"cube\":#{g.cube},\"crawford\":#{g.crawford}}"
    end)
    current = case s.active do
      nil -> "null"
      g -> "{\"id\":\"#{g.id}\",\"turn\":\"#{g.turn}\",\"cube\":#{g.cube},\"owner\":\"#{g.owner}\",\"crawford\":#{g.crawford}}"
    end
    "{\"status\":\"#{status}\",\"accepted\":#{length(s.accepted)},\"event_digest\":\"#{digest(s.accepted)}\",\"scores\":{\"W\":#{s.scores["W"]},\"B\":#{s.scores["B"]}},\"games\":[#{games}],\"current\":#{current}#{error}}\n"
  end
end

defmodule Main do
  def lines(path), do: File.read!(path) |> String.split("\n", trim: true)
  def write_report(path, lines), do: File.write!(path, Arbiter.run(lines) |> Arbiter.report())

  def main(["adjudicate", input, report]), do: write_report(report, lines(input))
  def main(["checkpoint", input, count_text, state_path, report]) do
    all = lines(input)
    {count, ""} = Integer.parse(count_text)
    if count < 0 or count > length(all), do: raise("bad checkpoint count")
    prefix = Enum.take(all, count)
    snapshot = "BGSTATE1\ncount=#{count}\ndigest=#{Arbiter.digest(prefix)}\n"
    File.write!(state_path, snapshot)
    write_report(report, prefix)
  end
  def main(["resume", input, state_path, report]) do
    all = lines(input)
    fields = File.read!(state_path) |> String.split("\n", trim: true)
    with ["BGSTATE1", "count=" <> cs, "digest=" <> expected] <- fields,
         {count, ""} <- Integer.parse(cs),
         true <- count >= 0 and count <= length(all),
         true <- Arbiter.digest(Enum.take(all, count)) == expected do
      write_report(report, all)
    else _ -> File.write!(report, "{\"status\":\"invalid\",\"accepted\":0,\"event_digest\":\"#{Arbiter.digest([])}\",\"scores\":{\"W\":0,\"B\":0},\"games\":[],\"current\":null,\"line\":0,\"reason\":\"resume_mismatch\"}\n") end
  end
  def main(_), do: System.halt(64)
end

Main.main(System.argv())
