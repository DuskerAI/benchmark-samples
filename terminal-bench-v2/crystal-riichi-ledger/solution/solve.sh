#!/bin/bash
set -euo pipefail
python - <<'PY'
from pathlib import Path

path = Path('/app/src/table_official.cr')
source = path.read_text()

replacements = []
replacements.append((r'''  def tile_code(t : String) : String
    x = t.downcase.gsub("-", "").gsub("_", "")
    return x if x.size == 2
    aliases = {"east" => "E", "south" => "S", "west" => "W", "north" => "N", "white" => "P", "green" => "F", "red" => "C"}
    aliases[x]? || x
  end
''', r'''  def wind_for(i : Int32) : String
    ["E", "S", "W", "N"][i % 4]
  end

  def tile_code(t : String) : String
    x = t.strip.downcase.gsub("-", "").gsub("_", "")
    aliases = {"east" => "E", "ton" => "E", "south" => "S", "nan" => "S", "west" => "W", "shaa" => "W", "north" => "N", "pei" => "N", "white" => "P", "haku" => "P", "green" => "F", "hatsu" => "F", "red" => "C", "chun" => "C"}
    return aliases[x] if aliases.has_key?(x)
    return x.upcase if ["e", "s", "w", "n", "p", "f", "c"].includes?(x)
    if x.size == 2
      n = x[0] == '0' ? '5' : x[0]
      return "#{n}#{x[1]}"
    end
    x
  end
'''))

replacements.append((r'''  def score_hand(r : JSON::Any, winner : Int32, from : Int32?) : Tuple(Int32, Int32, String)
    hand = r["hand"]
    yaku = hand["yaku"].as_a.map { |y| y.as_s }
    han = yaku.size + (hand.as_h.has_key?("dora") ? hand["dora"].as_i.to_i : 0)
    fu = 20
    if yaku.includes?("chiitoitsu")
      fu = 30
    else
      fu += 10 if !from.nil? && hand["closed"].as_bool
      hand["groups"].as_a.each do |g|
        k = group_kind(g)
        next if k == "sequence"
        t = group_tile(g)
        term = terminal_or_honor?(t)
        closed = is_closed?(g)
        if k == "triplet"
          fu += term ? 4 : 2
          fu += 2 if closed
        elsif k == "quad"
          fu += term ? 16 : 8
          fu += 8 if closed
        end
      end
      pair = tile_code(hand["pair"].as_s)
      seat = ["E", "S", "W", "N"][winner]
      round = ["E", "S", "W", "N"][(@round_no - 1) // 4]
      fu += 2 if ["P", "F", "C", seat, round].includes?(pair)
      fu += 2 if hand.as_h.has_key?("wait") && ["pair", "closed", "edge"].includes?(hand["wait"].as_s)
      fu += 2 if from.nil?
      fu = ((fu + 9) // 10) * 10
    end
    basic = fu * (2 ** (han + 2))
    label = "normal"
    if han >= 13
      basic = 8000; label = "yakuman"
    elsif han >= 11
      basic = 6000; label = "sanbaiman"
    elsif han >= 8
      basic = 4000; label = "baiman"
    elsif han >= 6
      basic = 3000; label = "haneman"
    elsif han >= 5 || basic > 2000
      basic = 2000; label = "mangan"
    end
    {han, fu, label}
  end
''', r'''  def yaku_han(name : String) : Int32
    table = {"chiitoitsu" => 2, "riichi" => 1, "pinfu" => 1, "menzen_tsumo" => 1, "yakuhai" => 1, "tanyao" => 1, "iipeikou" => 1, "honitsu" => 3, "chinitsu" => 6, "toitoi" => 2, "sanankou" => 2, "honroutou" => 2}
    table[name]? || 1
  end

  def score_hand(r : JSON::Any, winner : Int32, from : Int32?) : Tuple(Int32, Int32, String)
    hand = r["hand"]
    yaku = hand["yaku"].as_a.map { |y| y.as_s }
    han = yaku.sum { |y| yaku_han(y) } + (hand.as_h.has_key?("dora") ? hand["dora"].as_i.to_i : 0)
    fu = 20
    if yaku.includes?("chiitoitsu")
      fu = 25
    else
      fu += 10 if !from.nil? && hand["closed"].as_bool
      hand["groups"].as_a.each do |g|
        k = group_kind(g)
        next if k == "sequence" || k == "pair"
        term = terminal_or_honor?(group_tile(g))
        closed = is_closed?(g)
        if k == "triplet"
          fu += term ? 4 : 2
          fu += term ? 4 : 2 if closed
        elsif k == "quad"
          fu += term ? 16 : 8
          fu += term ? 16 : 8 if closed
        end
      end
      pair = tile_code(hand["pair"].as_s)
      seat = wind_for(winner)
      round = wind_for((@round_no - 1) // 4)
      fu += 2 if ["P", "F", "C"].includes?(pair)
      fu += 2 if pair == seat
      fu += 2 if pair == round
      fu += 2 if hand.as_h.has_key?("wait") && ["pair", "closed", "edge"].includes?(hand["wait"].as_s)
      fu += 2 if from.nil? && !yaku.includes?("pinfu")
      fu = ((fu + 9) // 10) * 10
    end
    label = limit_label(han, fu)
    {han, fu, label}
  end

  def limit_label(han : Int32, fu : Int32) : String
    b = fu * (2 ** (han + 2))
    return "yakuman" if han >= 13
    return "sanbaiman" if han >= 11
    return "baiman" if han >= 8
    return "haneman" if han >= 6
    return "mangan" if han >= 5 || b > 2000
    "normal"
  end
'''))

replacements.append((r'''  def apply_win(r : JSON::Any) : Hash(String, JSON::Any)
    winner = idx(r["winner"])
    from = r.as_h.has_key?("from") && r["from"].raw ? idx(r["from"]) : nil
    if r.as_h.has_key?("riichi") && r["riichi"].as_bool
      @scores[winner] -= 1000
      @riichi_pool += 1
    end
    han, fu, label = score_hand(r, winner, from)
    basic = base_points(han, fu)
    payments = Array.new(@players.size, 0)
    if from.nil?
      @players.each_index do |i|
        next if i == winner
        mult = winner == @dealer ? 2 : (i == @dealer ? 2 : 1)
        pay = ceil100(basic * mult) + @honba * 100
        payments[i] -= pay; payments[winner] += pay
      end
    else
      pay = ceil100(basic * (winner == @dealer ? 6 : 4)) + @honba * 100
      payments[from.not_nil!] -= pay; payments[winner] += pay
    end
    @players.each_index { |i| @scores[i] += payments[i] }
    if winner == @dealer
      @honba += 1
    else
      @honba = 0
      @dealer = (@dealer + 1) % 4
      @round_no += 1 if @dealer == 0
    end
    {"kind" => JSON::Any.new("win"), "winner" => JSON::Any.new(@players[winner]), "han" => JSON::Any.new(han.to_i64), "fu" => JSON::Any.new(fu.to_i64), "limit" => JSON::Any.new(label)}
  end
''', r'''  def settle_win(winner : Int32, from : Int32?, basic : Int32)
    if from.nil?
      @players.each_index do |i|
        next if i == winner
        mult = winner == @dealer ? 2 : (i == @dealer ? 2 : 1)
        pay = ceil100(basic * mult) + @honba * 100
        @scores[i] -= pay
        @scores[winner] += pay
      end
    else
      pay = ceil100(basic * (winner == @dealer ? 6 : 4)) + @honba * 300
      @scores[from.not_nil!] -= pay
      @scores[winner] += pay
    end
    @scores[winner] += @riichi_pool * 1000
    @riichi_pool = 0
    if winner == @dealer
      @honba += 1
    else
      @honba = 0
      @dealer = (@dealer + 1) % 4
      @round_no += 1 if @dealer == 0
    end
  end

  def chombo(offender : Int32) : Hash(String, JSON::Any)
    @players.each_index do |i|
      next if i == offender
      pay = offender == @dealer ? 4000 : (i == @dealer ? 4000 : 2000)
      @scores[offender] -= pay
      @scores[i] += pay
    end
    {"kind" => JSON::Any.new("chombo"), "offender" => JSON::Any.new(@players[offender])}
  end

  def apply_win(r : JSON::Any) : Hash(String, JSON::Any)
    winner = idx(r["winner"])
    from = r.as_h.has_key?("from") && r["from"].raw ? idx(r["from"]) : nil
    return chombo(winner) if r.as_h.has_key?("furiten") && r["furiten"].as_bool
    if r.as_h.has_key?("riichi") && r["riichi"].as_bool
      @scores[winner] -= 1000
      @riichi_pool += 1
    end
    han, fu, label = score_hand(r, winner, from)
    settle_win(winner, from, base_points(han, fu))
    {"kind" => JSON::Any.new("win"), "winner" => JSON::Any.new(@players[winner]), "han" => JSON::Any.new(han.to_i64), "fu" => JSON::Any.new(fu.to_i64), "limit" => JSON::Any.new(label)}
  end
'''))

replacements.append((r'''  def apply_draw(r : JSON::Any) : Hash(String, JSON::Any)
    tenpai = r["tenpai"].as_a.map { |x| idx(x) }
    if tenpai.size > 0 && tenpai.size < 4
      gain = 3000 // tenpai.size
      loss = 3000 // (4 - tenpai.size)
      @players.each_index do |i|
        @scores[i] += tenpai.includes?(i) ? gain : -loss
      end
    end
    dealer_ready = tenpai.includes?(@dealer)
    @honba += 1
    unless dealer_ready
      @dealer = (@dealer + 1) % 4
      @round_no += 1 if @dealer == 0
    end
    {"kind" => JSON::Any.new("draw"), "tenpai" => JSON::Any.new(tenpai.map { |i| JSON::Any.new(@players[i]) })}
  end
''', r'''  def valid_nagashi?(r : JSON::Any, i : Int32) : Bool
    return false unless r.as_h.has_key?("discards")
    name = @players[i]
    discards = r["discards"][name].as_a.map { |x| x.as_s }
    return false if discards.empty?
    return false unless discards.all? { |t| terminal_or_honor?(t) }
    if r.as_h.has_key?("called_discards")
      return false if r["called_discards"].as_a.any? { |x| idx(x) == i || x.as_s? == name }
    end
    true
  end

  def apply_draw(r : JSON::Any) : Hash(String, JSON::Any)
    if r.as_h.has_key?("riichi")
      r["riichi"].as_a.each do |x|
        declarer = idx(x)
        @scores[declarer] -= 1000
        @riichi_pool += 1
      end
    end
    if r.as_h.has_key?("nagashi")
      r["nagashi"].as_a.each do |x|
        w = idx(x)
        if valid_nagashi?(r, w)
          settle_win(w, nil, 2000)
          return {"kind" => JSON::Any.new("nagashi"), "winner" => JSON::Any.new(@players[w]), "limit" => JSON::Any.new("mangan")}
        end
      end
    end
    tenpai = r["tenpai"].as_a.map { |x| idx(x) }
    if tenpai.size > 0 && tenpai.size < 4
      gain = 3000 // tenpai.size
      loss = 3000 // (4 - tenpai.size)
      @players.each_index { |i| @scores[i] += tenpai.includes?(i) ? gain : -loss }
    end
    dealer_ready = tenpai.includes?(@dealer)
    @honba += 1
    unless dealer_ready
      @dealer = (@dealer + 1) % 4
      @round_no += 1 if @dealer == 0
    end
    {"kind" => JSON::Any.new("draw"), "tenpai" => JSON::Any.new(tenpai.map { |i| JSON::Any.new(@players[i]) })}
  end
'''))

replacements.append((r'''  def standings
    arr = [] of Hash(String, JSON::Any)
    @players.each_with_index do |p, i|
      arr << {"player" => JSON::Any.new(p), "points" => JSON::Any.new(@scores[i].to_i64), "rank" => JSON::Any.new(0_i64)}
    end
    arr.sort_by! { |h| {-h["points"].as_i, h["player"].as_s} }
    arr.each_with_index { |h, i| h["rank"] = JSON::Any.new((i + 1).to_i64) }
    arr
  end
''', r'''  def standings
    rows = [] of Hash(String, JSON::Any)
    @players.each_with_index do |p, i|
      rows << {"player" => JSON::Any.new(p), "points" => JSON::Any.new(@scores[i].to_i64), "seat" => JSON::Any.new(i.to_i64), "rank" => JSON::Any.new(0_i64)}
    end
    rows.sort_by! { |h| {-h["points"].as_i, h["seat"].as_i} }
    last_points = nil
    last_rank = 0
    rows.each_with_index do |h, i|
      pts = h["points"].as_i
      rank = (last_points == pts) ? last_rank : i + 1
      h["rank"] = JSON::Any.new(rank.to_i64)
      last_points = pts
      last_rank = rank
      h.delete("seat")
    end
    rows
  end
'''))

for old, new in replacements:
    if old not in source:
        raise SystemExit('expected source fragment not found')
    source = source.replace(old, new)

source = source.replace(r'''  def apply_chombo(r : JSON::Any) : Hash(String, JSON::Any)
    offender = idx(r["offender"])
    @players.each_index do |i|
      next if i == offender
      pay = offender == @dealer ? 4000 : (i == @dealer ? 4000 : 2000)
      @scores[offender] -= pay
      @scores[i] += pay
    end
    {"kind" => JSON::Any.new("chombo"), "offender" => JSON::Any.new(@players[offender])}
  end
''', r'''  def apply_chombo(r : JSON::Any) : Hash(String, JSON::Any)
    chombo(idx(r["offender"]))
  end
''')

path.write_text(source)
PY
/app/scripts/build.sh
