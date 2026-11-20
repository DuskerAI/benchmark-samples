#!/bin/bash
set -euo pipefail

cd /app

python3 - <<'PY'
from pathlib import Path

# Build the Crystal implementation from the public inference contract rather
# than copying expected outputs.  The sections are separated by concern so the
# oracle's repair path is auditable: schema loading, quantized tensor decoding,
# recurrent state update, scoring, CLI parsing, then installation.
Path("src/qinfer.cr").write_text(r'''
require "json"

record TokenEntry, id : Int32, text : String

class LayerSpec
  getter decay : Float64
  getter embed : JSON::Any
  getter pos : JSON::Any
  getter gate : JSON::Any?
  getter carry_proj : JSON::Any?
  getter input_mix : JSON::Any?
  getter token_shift : JSON::Any?
  getter state_clip : JSON::Any?
  getter feedback_mix : JSON::Any?
  getter state_init : JSON::Any?
  getter residual_carry : Float64?
  getter state_norm : JSON::Any?
  getter history_mix : JSON::Any?

  def initialize(@decay : Float64, @embed : JSON::Any, @pos : JSON::Any, @gate : JSON::Any?, @carry_proj : JSON::Any?, @input_mix : JSON::Any?, @token_shift : JSON::Any?, @state_clip : JSON::Any?, @feedback_mix : JSON::Any?, @state_init : JSON::Any?, @residual_carry : Float64?, @state_norm : JSON::Any?, @history_mix : JSON::Any?)
  end
end

class QuantModel
  @tokens_by_id : Hash(Int32, TokenEntry)
  @out : JSON::Any
  @norm : JSON::Any?
  @head_mix : JSON::Any?
  @position_bias : JSON::Any?
  @history_bias : JSON::Any?
  @calibration : Array(Array(Float64))?
  @group_bias_rows : Array(Array(Float64))?
  @group_bias_groups : Hash(Int32, Int32)?
  @transition_bias_rows : Array(Array(Float64))?
  @logit_scale : Float64
  @repeat_penalty : Float64
  @readout_layers : JSON::Any?

  getter dim : Int32
  getter unk_id : Int32
  getter tokens : Array(TokenEntry)
  getter bias : Array(Float64)
  getter layers : Array(LayerSpec)

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
    @calibration = nil
    if calibration = root["calibration"]?
      scale = calibration["scale"].as_f
      @calibration = calibration["rows"].as_a.map do |row|
        row.as_a.map { |value| value.as_f * scale }
      end
    end
    @group_bias_rows = nil
    @group_bias_groups = nil
    if group_bias = root["group_bias"]?
      scale = group_bias["scale"].as_f
      @group_bias_rows = group_bias["rows"].as_a.map do |row|
        row.as_a.map { |value| value.as_f * scale }
      end
      groups = {} of Int32 => Int32
      group_bias["groups"].as_h.each do |key, value|
        groups[key.to_i] = value.as_i.to_i32
      end
      @group_bias_groups = groups
    end
    @transition_bias_rows = nil
    if transition_bias = root["transition_bias"]?
      scale = transition_bias["scale"].as_f
      @transition_bias_rows = transition_bias["rows"].as_a.map do |row|
        row.as_a.map { |value| value.as_f * scale }
      end
    end
    @logit_scale = root["logit_scale"]?.try(&.as_f) || 1.0
    @repeat_penalty = root["repeat_penalty"]?.try(&.as_f) || 0.0
    @readout_layers = root["readout_layers"]?
    @bias = root["bias"].as_a.map(&.as_f)
    @layers = [] of LayerSpec
    if layer_json = root["layers"]?
      layer_json.as_a.each do |layer|
        @layers << LayerSpec.new(layer["decay"].as_f, layer["embed"], layer["pos"], layer["gate"]?, layer["carry_proj"]?, layer["input_mix"]?, layer["token_shift"]?, layer["state_clip"]?, layer["feedback_mix"]?, layer["state_init"]?, layer["residual_carry"]?.try(&.as_f), layer["state_norm"]?, layer["history_mix"]?)
      end
    else
      @layers << LayerSpec.new(root["decay"].as_f, root["embed"], root["pos"], nil, nil, root["input_mix"]?, root["token_shift"]?, root["state_clip"]?, root["feedback_mix"]?, root["state_init"]?, root["residual_carry"]?.try(&.as_f), root["state_norm"]?, root["history_mix"]?)
    end
  end

  def token_text(id : Int32) : String
    @tokens_by_id[id]?.try(&.text) || ""
  end

  def tokenize(text : String) : Array(Int32)
    bytes = text.to_slice
    result = [] of Int32
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
        if matched && (tb.size > best_len || (tb.size == best_len && tok.id < best_id))
          best_id = tok.id
          best_len = tb.size
        end
      end
      if best_len == 0
        result << @unk_id
        i += 1
      else
        result << best_id
        i += best_len
      end
    end
    result
  end

  def deq4(section : JSON::Any, row_id : Int32) : Array(Float64)
    row = section["rows"][row_id].as_a.map(&.as_i)
    zero = section["zero"].as_a.map(&.as_i)
    scale = section["scale"].as_a.map(&.as_f)
    values = [] of Float64
    @dim.times do |i|
      packed = row[i // 2]
      raw = (packed >> (4 * (i % 2))) & 15
      values << (raw - zero[i]) * scale[i]
    end
    values
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

  def score_state(states : Array(Array(Float64))) : Array(Float64)
    norm = @norm
    scored = states[-1].dup
    if readout = @readout_layers
      scored = Array(Float64).new(@dim, 0.0)
      readout.as_a.each do |entry|
        idx = entry["layer"].as_i
        weight = entry["weight"].as_f
        @dim.times { |i| scored[i] += weight * states[idx][i] }
      end
    end
    if norm
      eps = norm["eps"].as_f
      mean_sq = scored.sum { |v| v * v } / @dim
      denom = Math.sqrt(mean_sq + eps)
      gain = deq8(norm["gain"], 0)
      scored = scored.map_with_index { |v, i| v / denom * gain[i] }
    end
    if head_mix = @head_mix
      mixed = Array(Float64).new(@dim, 0.0)
      @dim.times do |row_index|
        row = deq4(head_mix, row_index)
        @dim.times { |i| mixed[row_index] += row[i] * scored[i] }
      end
      scored = mixed
    end
    scored
  end

  def position_bias(position : Int32) : Array(Float64)?
    bias = @position_bias
    return nil unless bias
    rows = bias["rows"].as_a.size
    deq8(bias, position % rows)
  end

  def calibration(position : Int32) : Array(Float64)?
    table = @calibration
    return nil unless table
    table[position % table.size]
  end

  def history_bias(last_token_id : Int32?) : Array(Float64)?
    bias = @history_bias
    return nil unless bias && last_token_id
    deq8(bias, last_token_id)
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

  def repeat_penalty : Float64
    @repeat_penalty
  end

  def logit_scale : Float64
    @logit_scale
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
      if init = layer.state_init
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

  def feed(id : Int32)
    carry = Array(Float64).new(@model.dim, 0.0)
    @model.layers.each_with_index do |layer, layer_index|
      prior_state = @states[layer_index].dup
      emb = @model.deq4(layer.embed, id)
      pos_rows = layer.pos["rows"].as_a.size
      pos = @model.deq8(layer.pos, @position % pos_rows)
      layer_carry = carry
      if carry_proj = layer.carry_proj
        layer_carry = Array(Float64).new(@model.dim, 0.0)
        @model.dim.times do |row_index|
          row = @model.deq4(carry_proj, row_index)
          @model.dim.times { |i| layer_carry[row_index] += row[i] * carry[i] }
        end
      end
      layer_input = Array(Float64).new(@model.dim) { |i| emb[i] + pos[i] + layer_carry[i] }
      if input_mix = layer.input_mix
        mixed = Array(Float64).new(@model.dim, 0.0)
        @model.dim.times do |row_index|
          row = @model.deq4(input_mix, row_index)
          @model.dim.times { |i| mixed[row_index] += row[i] * layer_input[i] }
        end
        layer_input = mixed
      end
      if token_shift = layer.token_shift
        shift = @model.deq8(token_shift, id)
        @model.dim.times { |i| layer_input[i] += shift[i] }
      end
      gate = Array(Float64).new(@model.dim, 1.0)
      if gate_tensor = layer.gate
        gate = @model.deq8(gate_tensor, id).map { |v| 1.0 / (1.0 + Math.exp(-v)) }
      end
      @model.dim.times do |i|
        input = layer_input[i] * gate[i]
        @states[layer_index][i] = @states[layer_index][i] * layer.decay + input
      end
      if state_clip = layer.state_clip
        limit = state_clip["limit"].as_f
        @model.dim.times do |i|
          @states[layer_index][i] = limit if @states[layer_index][i] > limit
          @states[layer_index][i] = -limit if @states[layer_index][i] < -limit
        end
      end
      if feedback_mix = layer.feedback_mix
        mixed = @states[layer_index].dup
        @model.dim.times do |row_index|
          row = @model.deq4(feedback_mix, row_index)
          @model.dim.times { |i| mixed[row_index] += row[i] * prior_state[i] }
        end
        @states[layer_index] = mixed
        if state_clip = layer.state_clip
          limit = state_clip["limit"].as_f
          @model.dim.times do |i|
            @states[layer_index][i] = limit if @states[layer_index][i] > limit
            @states[layer_index][i] = -limit if @states[layer_index][i] < -limit
          end
        end
      end
      if residual = layer.residual_carry
        @model.dim.times { |i| @states[layer_index][i] += residual * layer_carry[i] }
        if state_clip = layer.state_clip
          limit = state_clip["limit"].as_f
          @model.dim.times do |i|
            @states[layer_index][i] = limit if @states[layer_index][i] > limit
            @states[layer_index][i] = -limit if @states[layer_index][i] < -limit
          end
        end
      end
      if history_mix = layer.history_mix
        lag = history_mix["lag"].as_i
        history_index = @histories[layer_index].size - lag - 1
        history_index = 0 if history_index < 0
        selected = @histories[layer_index][history_index]
        mixed = @states[layer_index].dup
        @model.dim.times do |row_index|
          row = @model.deq4(history_mix["mix"], row_index)
          @model.dim.times { |i| mixed[row_index] += row[i] * selected[i] }
        end
        @states[layer_index] = mixed
        if state_clip = layer.state_clip
          limit = state_clip["limit"].as_f
          @model.dim.times do |i|
            @states[layer_index][i] = limit if @states[layer_index][i] > limit
            @states[layer_index][i] = -limit if @states[layer_index][i] < -limit
          end
        end
      end
      if state_norm = layer.state_norm
        eps = state_norm["eps"].as_f
        mean_sq = @states[layer_index].sum { |v| v * v } / @model.dim
        denom = Math.sqrt(mean_sq + eps)
        gain = @model.deq8(state_norm["gain"], 0)
        @states[layer_index] = @states[layer_index].map_with_index { |v, i| v / denom * gain[i] }
      end
      carry = @states[layer_index].dup
    end
    @states.each_with_index do |state, layer_index|
      @histories[layer_index] << state.dup
    end
    @position += 1
    @token_counts[id] += 1
    @last_token_id = id
  end

  def logits : Array(Float64)
    final_state = @model.score_state(@states)
    pos_bias = @model.position_bias(@position)
    hist_bias = @model.history_bias(@last_token_id)
    calibration = @model.calibration(@position)
    group_bias = @model.group_bias(@position, @last_token_id)
    transition_bias = @model.transition_bias(@last_token_id)
    @model.tokens.map do |tok|
      row = @model.output_row(tok.id)
      sum = @model.bias[tok.id]
      dot = 0.0
      @model.dim.times { |i| dot += row[i] * final_state[i] }
      sum += dot * @model.logit_scale
      sum += pos_bias[tok.id] if pos_bias
      sum += hist_bias[tok.id] if hist_bias
      sum -= @model.repeat_penalty * @token_counts[tok.id]
      sum += calibration[tok.id] if calibration
      sum += group_bias[tok.id] if group_bias
      sum += transition_bias[tok.id] if transition_bias
      sum
    end
  end

  def pick(values : Array(Float64)) : Int32
    best_id = @model.tokens.first.id
    best = values.first
    values.each_with_index do |value, idx|
      tok_id = @model.tokens[idx].id
      if value > best || (value == best && tok_id < best_id)
        best = value
        best_id = tok_id
      end
    end
    best_id
  end

  def run(chunks : Array(String), max_tokens : Int32)
    @model.tokenize(chunks.join).each { |id| feed(id) }
    generated = [] of Int32
    final_logits = [] of Float64
    max_tokens.times do
      final_logits = logits
      nxt = pick(final_logits)
      generated << nxt
      feed(nxt)
    end
    {generated, generated.map { |id| @model.token_text(id) }.join, final_logits}
  end
end

def take_arg(args : Array(String), name : String) : String?
  idx = args.index(name)
  return nil unless idx
  args[idx + 1]?
end

args = ARGV.to_a
model_path = take_arg(args, "--model") || "/app/model/tinyq.json"
prompt = take_arg(args, "--prompt") || ""
max_tokens = (take_arg(args, "--max-tokens") || "1").to_i
chunks_arg = take_arg(args, "--chunks")
chunks = chunks_arg ? chunks_arg.split("|", remove_empty: false) : [prompt]
model = QuantModel.new(model_path)
tokens, text, logits = Runner.new(model).run(chunks, max_tokens)
puts({"tokens" => tokens, "text" => text, "logits" => logits}.to_json)
''')
PY

mkdir -p build
crystal build src/qinfer.cr -o build/qinfer
install -m 0755 build/qinfer /usr/local/bin/qinfer
output=$(/usr/local/bin/qinfer --model /app/model/tinyq.json --prompt "crystal quant." --max-tokens 1)
printf '%s' "$output" | python3 -c 'import json,sys; obj=json.load(sys.stdin); assert set(obj) == {"tokens", "text", "logits"}' >/dev/null
