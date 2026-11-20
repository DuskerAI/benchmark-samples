#!/usr/bin/env elixir

defmodule MatchOfficial do
  def new, do: %{length: nil, jacoby: false, scores: %{"W" => 0, "B" => 0}, used: false, game: nil, games: [], accepted: [], error: nil}

  def replay(lines) do
    Enum.with_index(lines, 1)
    |> Enum.reduce_while(new(), fn {raw, number}, state ->
      text = String.trim(raw)
      if text == "" do
        {:cont, state}
      else
        case accept(state, String.split(text)) do
          {:ok, next} -> {:cont, %{next | accepted: state.accepted ++ [text]}}
          {:error, reason} -> {:halt, %{state | error: %{line: number, reason: reason}}}
        end
      end
    end)
  end

  def accept(%{length: nil} = s, ["MATCH", n, j]) do
    with {length, ""} <- Integer.parse(n), true <- length >= 0, true <- j in ["0", "1"] do
      {:ok, %{s | length: length, jacoby: j == "1"}}
    else _ -> {:error, "bad_match"} end
  end
  def accept(%{length: nil}, _), do: {:error, "match_required"}
  def accept(s, ["MATCH" | _]), do: {:error, "duplicate_match"}

  def accept(%{game: nil} = s, ["GAME", id]) do
    cond do
      id == "" or Enum.any?(s.games, &(&1.id == id)) -> {:error, "bad_game"}
      s.length > 0 and Enum.max(Map.values(s.scores)) >= s.length -> {:error, "match_complete"}
      true ->
        crawford = s.length > 0 and Enum.max(Map.values(s.scores)) == s.length - 1
        g = %{id: id, turn: "W", board: nil, roll: nil, cube: 1, owner: "center", offer: nil, crawford: crawford}
        {:ok, %{s | game: g, used: s.used or crawford}}
    end
  end
  def accept(%{game: nil}, _), do: {:error, "game_required"}
  def accept(%{game: _}, ["GAME" | _]), do: {:error, "game_in_progress"}

  def accept(s, ["POSITION", turn, wp, bp, wb, bb, wo, bo]) do
    g = s.game
    with true <- g.roll == nil and g.offer == nil, true <- turn in ["W", "B"],
         {:ok, w} <- parse_points(wp), {:ok, b} <- parse_points(bp),
         {wbar, ""} <- Integer.parse(wb), {bbar, ""} <- Integer.parse(bb),
         {woff, ""} <- Integer.parse(wo), {boff, ""} <- Integer.parse(bo),
         true <- Enum.sum(Map.values(w)) + wbar + woff == 15,
         true <- Enum.sum(Map.values(b)) + bbar + boff == 15,
         true <- Enum.all?(Map.keys(w), fn p -> not Map.has_key?(b, p) end) do
      board = %{"W" => w, "B" => b, "Wbar" => wbar, "Bbar" => bbar, "Woff" => woff, "Boff" => boff}
      {:ok, %{s | game: %{g | board: board, turn: turn}}}
    else _ -> {:error, "bad_position"} end
  end

  def accept(s, ["ROLL", x, y]) do
    g = s.game
    with true <- g.board != nil and g.roll == nil and g.offer == nil,
         {a, ""} <- Integer.parse(x), {b, ""} <- Integer.parse(y), true <- a in 1..6 and b in 1..6 do
      {:ok, %{s | game: %{g | roll: {a, b}}}}
    else _ -> {:error, "bad_roll"} end
  end

  def accept(s, ["PLAY", player, encoded]) do
    g = s.game
    with true <- player == g.turn and g.roll != nil and g.offer == nil,
         {:ok, moves} <- parse_moves(encoded),
         {:ok, board} <- play_greedily(g.board, player, g.roll, moves) do
      if board[player <> "off"] == 15 do
        settle(s, player, result_level(board, player), "board")
      else
        {:ok, %{s | game: %{g | board: board, roll: nil, turn: opponent(player)}}}
      end
    else _ -> {:error, "illegal_play"} end
  end

  def accept(s, ["DOUBLE", player]) do
    g = s.game
    if player == g.turn and g.board != nil and g.roll == nil and g.offer == nil and not g.crawford and g.cube < 64 and g.owner in ["center", player],
      do: {:ok, %{s | game: %{g | offer: player}}}, else: {:error, "illegal_double"}
  end
  def accept(s, ["TAKE", player]) do
    g = s.game
    if g.offer != nil and player == opponent(g.offer),
      do: {:ok, %{s | game: %{g | cube: g.cube * 2, owner: g.offer, offer: nil}}}, else: {:error, "illegal_take"}
  end
  def accept(s, ["DROP", player]) do
    g = s.game
    if g.offer != nil and player == opponent(g.offer), do: settle(s, g.offer, 1, "drop"), else: {:error, "illegal_drop"}
  end
  def accept(_, _), do: {:error, "bad_event"}

  def parse_points("-"), do: {:ok, %{}}
  def parse_points(text) do
    Enum.reduce_while(String.split(text, ","), {:ok, %{}}, fn part, {:ok, map} ->
      case Regex.run(~r/^(\d+)x(\d+)$/, part) do
        [_, p, c] ->
          point = String.to_integer(p); count = String.to_integer(c)
          if point in 1..24 and count > 0 and not Map.has_key?(map, point), do: {:cont, {:ok, Map.put(map, point, count)}}, else: {:halt, :bad}
        _ -> {:halt, :bad}
      end
    end) |> case do {:ok, map} -> {:ok, map}; _ -> :bad end
  end

  def parse_moves(text) do
    Enum.reduce_while(String.split(text, ","), {:ok, []}, fn part, {:ok, list} ->
      case Regex.run(~r/^(\d+)\/(\d+)$/, part) do
        [_, a, b] -> {:cont, {:ok, list ++ [{String.to_integer(a), String.to_integer(b)}]}}
        _ -> {:halt, :bad}
      end
    end) |> case do {:ok, [_ | _] = list} -> {:ok, list}; _ -> :bad end
  end

  def play_greedily(board, player, {a, b}, moves) do
    dice = if a == b, do: [a, a, a, a], else: [a, b]
    Enum.reduce_while(moves, {:ok, board, dice}, fn move, {:ok, current, left} ->
      case Enum.find(left, &legal?(current, player, move, &1)) do
        nil -> {:halt, :bad}
        die -> {:cont, {:ok, move_one(current, player, move), List.delete(left, die)}}
      end
    end) |> case do
      {:ok, final, []} -> {:ok, final}
      {:ok, final, left} -> if no_moves?(final, player, left), do: {:ok, final}, else: :bad
      _ -> :bad
    end
  end

  def no_moves?(board, player, dice) do
    Enum.all?(dice, fn die ->
      sources = if board[player <> "bar"] > 0, do: [bar(player)], else: Map.keys(board[player])
      Enum.all?(sources, fn src -> not legal?(board, player, {src, target(player, src, die)}, die) end)
    end)
  end

  def legal?(board, player, {src, dst}, die) do
    enemy = opponent(player)
    source_ok = if board[player <> "bar"] > 0, do: src == bar(player), else: Map.get(board[player], src, 0) > 0
    expected = target(player, src, die)
    source_ok and dst == expected and cond do
      dst in 1..24 -> Map.get(board[enemy], dst, 0) < 2
      not home?(board, player) -> false
      player == "W" -> not Enum.any?(Map.keys(board[player]), &(&1 > src))
      true -> not Enum.any?(Map.keys(board[player]), &(&1 < src))
    end
  end

  def target("W", 25, die), do: die
  def target("B", 0, die), do: 25 - die
  def target("W", src, die), do: max(0, src - die)
  def target("B", src, die), do: min(25, src + die)
  def bar("W"), do: 25
  def bar("B"), do: 0
  def off("W"), do: 0
  def off("B"), do: 25
  def home?(b, "W"), do: b["Wbar"] == 0 and Enum.all?(Map.keys(b["W"]), &(&1 in 1..6))
  def home?(b, "B"), do: b["Bbar"] == 0 and Enum.all?(Map.keys(b["B"]), &(&1 in 19..24))

  def move_one(board, player, {src, dst}) do
    enemy = opponent(player)
    own = if src == bar(player), do: board[player], else: decrease(board[player], src)
    next = if src == bar(player), do: Map.update!(board, player <> "bar", &(&1 - 1)), else: board
    next = Map.put(next, player, own)
    cond do
      dst == off(player) -> Map.update!(next, player <> "off", &(&1 + 1))
      Map.get(next[enemy], dst, 0) == 1 -> next |> Map.put(enemy, decrease(next[enemy], dst)) |> Map.update!(enemy <> "bar", &(&1 + 1)) |> Map.put(player, increase(own, dst))
      true -> Map.put(next, player, increase(own, dst))
    end
  end

  def increase(map, p), do: Map.update(map, p, 1, &(&1 + 1))
  def decrease(map, p), do: if(Map.fetch!(map, p) == 1, do: Map.delete(map, p), else: Map.update!(map, p, &(&1 - 1)))
  def opponent("W"), do: "B"
  def opponent("B"), do: "W"

  def result_level(board, winner) do
    loser = opponent(winner)
    cond do
      board[loser <> "off"] > 0 -> 1
      board[loser <> "bar"] > 0 -> 3
      winner == "W" and Enum.any?(Map.keys(board[loser]), &(&1 in 1..6)) -> 3
      winner == "B" and Enum.any?(Map.keys(board[loser]), &(&1 in 19..24)) -> 3
      true -> 2
    end
  end

  def settle(s, winner, level, reason) do
    g = s.game
    multiplier = if s.jacoby and g.cube == 1, do: 1, else: level
    points = g.cube * multiplier
    row = %{id: g.id, winner: winner, points: points, reason: reason, cube: g.cube, crawford: g.crawford}
    {:ok, %{s | scores: Map.update!(s.scores, winner, &(&1 + points)), games: s.games ++ [row], game: nil}}
  end

  def digest(lines), do: :crypto.hash(:sha256, Enum.join(Enum.sort(lines), "\n")) |> Base.encode16(case: :lower)
  def json(s) do
    status = if s.error, do: "invalid", else: "ok"
    err = if s.error, do: ",\"line\":#{s.error.line},\"reason\":\"#{s.error.reason}\"", else: ""
    games = Enum.map_join(s.games, ",", fn g -> "{\"id\":\"#{g.id}\",\"winner\":\"#{g.winner}\",\"points\":#{g.points},\"reason\":\"#{g.reason}\",\"cube\":#{g.cube},\"crawford\":#{g.crawford}}" end)
    current = if s.game, do: "{\"id\":\"#{s.game.id}\",\"turn\":\"#{s.game.turn}\",\"cube\":#{s.game.cube},\"owner\":\"#{s.game.owner}\",\"crawford\":#{s.game.crawford}}", else: "null"
    "{\"status\":\"#{status}\",\"accepted\":#{length(s.accepted)},\"event_digest\":\"#{digest(s.accepted)}\",\"scores\":{\"W\":#{s.scores["W"]},\"B\":#{s.scores["B"]}},\"games\":[#{games}],\"current\":#{current}#{err}}\n"
  end
end

defmodule CLI do
  def lines(path), do: File.read!(path) |> String.split("\n", trim: true)
  def output(input, report), do: File.write!(report, MatchOfficial.replay(input) |> MatchOfficial.json())
  def main(["adjudicate", events, report]), do: output(lines(events), report)
  def main(["checkpoint", events, count, state, report]) do
    all = lines(events); n = String.to_integer(count); prefix = Enum.take(all, n)
    File.write!(state, "BGSTATE1\ncount=#{n}\ndigest=#{MatchOfficial.digest(prefix)}\n"); output(prefix, report)
  end
  def main(["resume", events, state, report]) do
    all = lines(events)
    case File.read!(state) |> String.split("\n", trim: true) do
      ["BGSTATE1", "count=" <> count, "digest=" <> expected] ->
        n = String.to_integer(count)
        if MatchOfficial.digest(Enum.take(all, n)) == expected, do: output(all, report), else: mismatch(report)
      _ -> mismatch(report)
    end
  end
  def mismatch(report), do: File.write!(report, "{\"status\":\"invalid\",\"accepted\":0,\"event_digest\":\"\",\"scores\":{\"W\":0,\"B\":0},\"games\":[],\"current\":null,\"line\":0,\"reason\":\"resume_mismatch\"}\n")
  def main(_), do: System.halt(64)
end

CLI.main(System.argv())
