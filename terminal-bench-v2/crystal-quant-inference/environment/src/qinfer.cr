require "json"

record TokenEntry, id : Int32, text : String

class QuantModel
  @tokens_by_id : Hash(Int32, TokenEntry)
  @out : JSON::Any
  @norm : JSON::Any?
  @head_mix : JSON::Any?
  @position_bias : JSON::Any?
  @history_bias : JSON::Any?
  @group_bias_rows : Array(Array(Float64))?
  @group_bias_groups : Hash(Int32, Int32)?
  @transition_bias_rows : Array(Array(Float64))?
  @calibration_rows : Array(Array(Float64))?
  @logit_scale : Float64
  @repeat_penalty : Float64
  @readout_layers : JSON::Any?

  getter dim : Int32
  getter unk_id : Int32
  getter tokens : Array(TokenEntry)
  getter bias : Array(Float64)
  getter layers : Array(JSON::Any)

  def initialize(path : String)
    root = JSON.parse(File.read(path))
    @dim = root["dim"].as_i.to_i32
    @unk_id = root["unk_id"].as_i.to_i32
    @tokens = root["tokens"].as_a.map { |t| TokenEntry.new(t["id"].as_i.to_i32, t["text"].as_s) }
    @tokens_by_id = @tokens.to_h { |t| {t.id, t} }
    @out = root["out"]
    @norm = root["norm"]?
    @head_mix = root["head_mix"]?
    @position_bias = root["position_bias"]?
    @history_bias = root["history_bias"]?
    @group_bias_rows = nil
    @group_bias_groups = nil
    if group_bias = root["group_bias"]?
      scale = group_bias["scale"].as_f
      @group_bias_rows = group_bias["rows"].as_a.map { |row| row.as_a.map { |value| value.as_f * scale } }
      groups = {} of Int32 => Int32
      group_bias["groups"].as_h.each { |key, value| groups[key.to_i] = value.as_i.to_i32 }
      @group_bias_groups = groups
    end
    @transition_bias_rows = nil
    if transition_bias = root["transition_bias"]?
      scale = transition_bias["scale"].as_f
      @transition_bias_rows = transition_bias["rows"].as_a.map { |row| row.as_a.map { |value| value.as_f * scale } }
    end
    @calibration_rows = nil
    if calibration = root["calibration"]?
      scale = calibration["scale"].as_f
      @calibration_rows = calibration["rows"].as_a.map { |row| row.as_a.map { |value| value.as_f * scale } }
    end
    @logit_scale = root["logit_scale"]?.try(&.as_f) || 1.0
    @repeat_penalty = root["repeat_penalty"]?.try(&.as_f) || 0.0
    @readout_layers = root["readout_layers"]?
    @bias = root["bias"].as_a.map(&.as_f)
    @layers = root["layers"]?.try(&.as_a) || [root]
  end

  def token_text(id : Int32) : String
    @tokens_by_id[id]?.try(&.text) || ""
  end

  def tokenize(text : String) : Array(Int32)
    bytes = text.to_slice
    ids = [] of Int32
    i = 0
    while i < bytes.size
      best_id = @unk_id
      best_len = 0
      @tokens.each do |tok|
        tb = tok.text.to_slice
        next if tb.empty? || i + tb.size > bytes.size
        matched = true
        tb.size.times do |j|
          if bytes[i + j] != tb[j]
            matched = false
            break
          end
        end
        if matched && tb.size > best_len
          best_id = tok.id
          best_len = tb.size
        end
      end
      if best_len == 0
        ids << @unk_id
        i += 1
      else
        ids << best_id
        i += best_len
      end
    end
    ids
  end

  def deq4(section : JSON::Any, row_id : Int32) : Array(Float64)
    row = section["rows"][row_id].as_a.map(&.as_i)
    zero = section["zero"].as_a.map(&.as_i)
    scale = section["scale"].as_a.map(&.as_f)
    Array(Float64).new(@dim) do |i|
      packed = row[i // 2]
      raw = (packed >> (4 * (i % 2))) & 15
      (raw - zero[i]) * scale[i]
    end
  end

  def deq8(section : JSON::Any, row_id : Int32) : Array(Float64)
    row = section["rows"][row_id].as_a.map(&.as_i)
    zero = section["zero"].as_i
    scale = section["scale"].as_f
    row.map { |v| (v - zero) * scale }
  end

  def output_row(id : Int32) : Array(Float64)
    deq4(@out, id)
  end

  def scoring_state(states : Array(Array(Float64))) : Array(Float64)
    scored = states[-1].dup
    if readout = @readout_layers
      scored = Array(Float64).new(@dim, 0.0)
      readout.as_a.each do |entry|
        layer_idx = entry["layer"].as_i
        weight = entry["weight"].as_f
        @dim.times { |i| scored[i] += weight * states[layer_idx][i] }
      end
    end
    if norm = @norm
      eps = norm["eps"].as_f
      denom = Math.sqrt(scored.sum { |v| v * v } / @dim + eps)
      gain = deq8(norm["gain"], 0)
      scored = scored.map_with_index { |v, i| v / denom * gain[i] }
    end
    if head_mix = @head_mix
      src = scored
      mixed = Array(Float64).new(@dim, 0.0)
      @dim.times do |row_idx|
        row = deq4(head_mix, row_idx)
        @dim.times { |i| mixed[row_idx] += row[i] * src[i] }
      end
      scored = mixed
    end
    scored
  end

  def position_bias(position : Int32) : Array(Float64)?
    bias = @position_bias
    return nil unless bias
    deq8(bias, position % bias["rows"].as_a.size)
  end

  def history_bias(last_token_id : Int32?) : Array(Float64)?
    bias = @history_bias
    return nil unless bias && last_token_id
    deq8(bias, last_token_id)
  end

  def calibration(position : Int32) : Array(Float64)?
    rows = @calibration_rows
    return nil unless rows
    rows[position % rows.size]
  end

  def group_bias(position : Int32, last_token_id : Int32?) : Array(Float64)?
    rows = @group_bias_rows
    groups = @group_bias_groups
    return nil unless rows && groups && last_token_id
    group = groups[last_token_id]?
    return nil unless group
    rows[(group + position) % rows.size]
  end

  def transition_bias(last_token_id : Int32?) : Array(Float64)?
    rows = @transition_bias_rows
    return nil unless rows && last_token_id
    return nil if last_token_id >= rows.size
    rows[last_token_id]
  end

  def logit_scale : Float64
    @logit_scale
  end

  def repeat_penalty : Float64
    @repeat_penalty
  end
end

class Runner
  @states : Array(Array(Float64))
  @histories : Array(Array(Array(Float64)))
  @token_counts : Hash(Int32, Int32)
  @last_token_id : Int32?
  @position : Int32

  def initialize(@model : QuantModel)
    @states = @model.layers.map do |layer|
      if init = layer["state_init"]?
        @model.deq8(init, 0)
      else
        Array(Float64).new(@model.dim, 0.0)
      end
    end
    @histories = @states.map { |state| [state.dup] }
    @token_counts = Hash(Int32, Int32).new(0)
    @last_token_id = nil
    @position = 0
  end

  def clamp(values : Array(Float64), limit : Float64) : Array(Float64)
    values.map { |value| value > limit ? limit : (value < -limit ? -limit : value) }
  end

  def project(section : JSON::Any, vec : Array(Float64)) : Array(Float64)
    Array(Float64).new(@model.dim, 0.0).map_with_index do |_, row_idx|
      row = @model.deq4(section, row_idx)
      sum = 0.0
      @model.dim.times { |i| sum += row[i] * vec[i] }
      sum
    end
  end

  def feed(id : Int32)
    carry = Array(Float64).new(@model.dim, 0.0)
    @model.layers.each_with_index do |layer, layer_idx|
      prior = @states[layer_idx].dup
      emb = @model.deq4(layer["embed"], id)
      pos = @model.deq8(layer["pos"], @position % layer["pos"]["rows"].as_a.size)
      projected = layer["carry_proj"]?.try { |section| project(section, carry) } || carry
      layer_input = Array(Float64).new(@model.dim) { |i| emb[i] + pos[i] + projected[i] }
      if input_mix = layer["input_mix"]?
        layer_input = project(input_mix, layer_input)
      end
      if token_shift = layer["token_shift"]?
        shift = @model.deq8(token_shift, id)
        @model.dim.times { |i| layer_input[i] += shift[i] }
      end
      gate = if gate_tensor = layer["gate"]?
               @model.deq8(gate_tensor, id).map { |v| 1.0 / (1.0 + Math.exp(-v)) }
             else
               Array(Float64).new(@model.dim, 1.0)
             end
      next_state = Array(Float64).new(@model.dim) do |i|
        @states[layer_idx][i] * layer["decay"].as_f + layer_input[i] * gate[i]
      end
      if state_clip = layer["state_clip"]?
        next_state = clamp(next_state, state_clip["limit"].as_f)
      end
      if feedback_mix = layer["feedback_mix"]?
        fb = project(feedback_mix, prior)
        @model.dim.times { |i| next_state[i] += fb[i] }
        if state_clip = layer["state_clip"]?
          next_state = clamp(next_state, state_clip["limit"].as_f)
        end
      end
      if residual = layer["residual_carry"]?.try(&.as_f)
        @model.dim.times { |i| next_state[i] += residual * projected[i] }
        if state_clip = layer["state_clip"]?
          next_state = clamp(next_state, state_clip["limit"].as_f)
        end
      end
      if history_mix = layer["history_mix"]?
        lag = history_mix["lag"].as_i
        history = @histories[layer_idx]
        history_index = history.size - lag - 1
        history_index = 0 if history_index < 0
        selected = history[history_index]
        mixed = next_state.dup
        @model.dim.times do |row_idx|
          row = @model.deq4(history_mix["mix"], row_idx)
          @model.dim.times { |i| mixed[row_idx] += row[i] * selected[i] }
        end
        next_state = mixed
        if state_clip = layer["state_clip"]?
          next_state = clamp(next_state, state_clip["limit"].as_f)
        end
      end
      if state_norm = layer["state_norm"]?
        denom = Math.sqrt(next_state.sum { |v| v * v } / @model.dim + state_norm["eps"].as_f)
        gain = @model.deq8(state_norm["gain"], 0)
        next_state = next_state.map_with_index { |v, i| v / denom * gain[i] }
      end
      @states[layer_idx] = next_state
      carry = next_state
    end
    @states.each_with_index { |state, layer_idx| @histories[layer_idx] << state.dup }
    @position += 1
    @token_counts[id] += 1
    @last_token_id = id
  end

  def logits : Array(Float64)
    score_state = @model.scoring_state(@states)
    pos = @model.position_bias(@position)
    hist = @model.history_bias(@last_token_id)
    cal = @model.calibration(@position)
    grp = @model.group_bias(@position, @last_token_id)
    trans = @model.transition_bias(@last_token_id)
    @model.tokens.map do |tok|
      row = @model.output_row(tok.id)
      dot = 0.0
      @model.dim.times { |i| dot += row[i] * score_state[i] }
      value = dot * @model.logit_scale + @model.bias[tok.id]
      value += pos[tok.id] if pos
      value += hist[tok.id] if hist
      value -= @model.repeat_penalty * @token_counts[tok.id]
      value += cal[tok.id] if cal
      value += grp[tok.id] if grp
      value += trans[tok.id] if trans
      value
    end
  end

  def pick(values : Array(Float64)) : Int32
    best_idx = 0
    values.each_with_index do |value, idx|
      best_id = @model.tokens[best_idx].id
      tok_id = @model.tokens[idx].id
      if value > values[best_idx] || (value == values[best_idx] && tok_id < best_id)
        best_idx = idx
      end
    end
    @model.tokens[best_idx].id
  end

  def run(chunks : Array(String), max_tokens : Int32)
    @model.tokenize(chunks.join).each { |id| feed(id) }
    generated = [] of Int32
    final_logits = [] of Float64
    max_tokens.times do
      final_logits = logits
      next_id = pick(final_logits)
      generated << next_id
      feed(next_id)
    end
    {generated, generated.map { |id| @model.token_text(id) }.join, final_logits}
  end
end

def take_arg(args : Array(String), name : String) : String?
  idx = args.index(name)
  return nil unless idx
  args[idx + 1]?
end

model_path = take_arg(ARGV, "--model") || "/app/model/tinyq.json"
prompt = take_arg(ARGV, "--prompt") || ""
chunks_arg = take_arg(ARGV, "--chunks")
chunks = chunks_arg ? chunks_arg.split("|", remove_empty: false) : [prompt]
max_tokens = (take_arg(ARGV, "--max-tokens") || "1").to_i
model = QuantModel.new(model_path)
tokens, text, logits = Runner.new(model).run(chunks, max_tokens)
puts({"tokens" => tokens, "text" => text, "logits" => logits}.to_json)
