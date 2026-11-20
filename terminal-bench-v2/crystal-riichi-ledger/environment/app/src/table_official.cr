require "json"

class TableOfficial
  @scores : Array(Int32)
  @riichi_pool : Int32
  @honba : Int32
  @dealer : Int32
  @round_no : Int32
  getter players : Array(String)

  def initialize(@players : Array(String), start : Int32)
    @scores = Array.new(@players.size, start)
    @riichi_pool = 0
    @honba = 0
    @dealer = 0
    @round_no = 1
  end

  def self.i(v : JSON::Any) : Int32
    v.as_i.to_i
  end

  def self.s(v : JSON::Any) : String
    v.as_s
  end

  def idx(v : JSON::Any) : Int32
    if v.raw.is_a?(Int64)
      v.as_i.to_i
    else
      name = v.as_s
      @players.index(name) || name.to_i
    end
  end

  def tile_code(t : String) : String
    x = t.downcase.gsub("-", "").gsub("_", "")
    return x if x.size == 2
    aliases = {"east" => "E", "south" => "S", "west" => "W", "north" => "N", "white" => "P", "green" => "F", "red" => "C"}
    aliases[x]? || x
  end

  def terminal_or_honor?(t : String) : Bool
    c = tile_code(t)
    return true if ["E", "S", "W", "N", "P", "F", "C"].includes?(c)
    n = c[0].to_s.to_i
    n == 1 || n == 9
  end

  def group_kind(g : JSON::Any) : String
    g["kind"].as_s
  end

  def group_tile(g : JSON::Any) : String
    tile_code(g["tiles"].as_a[0].as_s)
  end

  def is_closed?(g : JSON::Any) : Bool
    !g.as_h.has_key?("open") || !g["open"].as_bool
  end

  def score_hand(r : JSON::Any, winner : Int32, from : Int32?) : Tuple(Int32, Int32, String)
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

  def ceil100(x : Int32) : Int32
    ((x + 99) // 100) * 100
  end

  def base_points(han : Int32, fu : Int32) : Int32
    b = fu * (2 ** (han + 2))
    return 8000 if han >= 13
    return 6000 if han >= 11
    return 4000 if han >= 8
    return 3000 if han >= 6
    return 2000 if han >= 5 || b > 2000
    b
  end

  def apply_win(r : JSON::Any) : Hash(String, JSON::Any)
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

  def apply_draw(r : JSON::Any) : Hash(String, JSON::Any)
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

  def apply_chombo(r : JSON::Any) : Hash(String, JSON::Any)
    offender = idx(r["offender"])
    @players.each_index do |i|
      next if i == offender
      pay = offender == @dealer ? 4000 : (i == @dealer ? 4000 : 2000)
      @scores[offender] -= pay
      @scores[i] += pay
    end
    {"kind" => JSON::Any.new("chombo"), "offender" => JSON::Any.new(@players[offender])}
  end

  def standings
    arr = [] of Hash(String, JSON::Any)
    @players.each_with_index do |p, i|
      arr << {"player" => JSON::Any.new(p), "points" => JSON::Any.new(@scores[i].to_i64), "rank" => JSON::Any.new(0_i64)}
    end
    arr.sort_by! { |h| {-h["points"].as_i, h["player"].as_s} }
    arr.each_with_index { |h, i| h["rank"] = JSON::Any.new((i + 1).to_i64) }
    arr
  end

  def adjudicate(rounds : Array(JSON::Any))
    rows = [] of Hash(String, JSON::Any)
    rounds.each do |rr|
      r = rr["result"]
      @dealer = rr["dealer"].as_i.to_i if rr.as_h.has_key?("dealer")
      @honba = rr["honba"].as_i.to_i if rr.as_h.has_key?("honba")
      @riichi_pool = rr["riichi_sticks"].as_i.to_i if rr.as_h.has_key?("riichi_sticks")
      case r["type"].as_s
      when "win" then rows << apply_win(r)
      when "draw" then rows << apply_draw(r)
      when "chombo" then rows << apply_chombo(r)
      end
    end
    score_rows = [] of JSON::Any
    @players.each_with_index do |p, i|
      score_rows << JSON::Any.new({"player" => JSON::Any.new(p), "points" => JSON::Any.new(@scores[i].to_i64)})
    end
    {"rounds" => JSON::Any.new(rows.map { |h| JSON::Any.new(h) }), "scores" => JSON::Any.new(score_rows), "standings" => JSON::Any.new(standings.map { |h| JSON::Any.new(h) }), "riichi_sticks" => JSON::Any.new(@riichi_pool.to_i64), "honba" => JSON::Any.new(@honba.to_i64)}
  end
end

if ARGV.size != 3 || ARGV[0] != "adjudicate"
  STDERR.puts "usage: table-official adjudicate input.json output.json"
  exit 2
end
input = JSON.parse(File.read(ARGV[1]))
players = input["players"].as_a.map { |p| p.as_s }
start = input.as_h.has_key?("start_points") ? input["start_points"].as_i.to_i : 25000
official = TableOfficial.new(players, start)
report = official.adjudicate(input["rounds"].as_a)
File.write(ARGV[2], report.to_json)
