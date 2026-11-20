defmodule BeamTrail.Replay do
  alias BeamTrail.Json

  def run(input, output) do
    config = load_config(input)

    {state, timeline} =
      input
      |> load_records()
      |> Enum.sort_by(fn rec -> {Map.get(rec, "seq", 0), rec["__file"], rec["__line"]} end)
      |> Enum.reduce({new_state(), []}, fn rec, acc -> step(rec, acc, config) end)

    File.write!(Path.join(output, "summary.json"), Json.encode!(summary(state)) <> "\n")
    File.write!(Path.join(output, "timeline.jsonl"), Enum.map_join(Enum.reverse(timeline), "\n", &Json.encode!/1) <> line_end(timeline))
  end

  defp load_config(input) do
    path = Path.join(input, "config.json")
    if File.exists?(path), do: Json.decode!(File.read!(path)), else: %{}
  end

  defp load_records(input) do
    Path.wildcard(Path.join(input, "**/*.jsonl"))
    |> Enum.sort()
    |> Enum.flat_map(fn file ->
      base = Path.basename(file)
      file
      |> File.stream!()
      |> Stream.with_index(1)
      |> Enum.map(fn {line, idx} ->
        line
        |> String.trim()
        |> Json.decode!()
        |> Map.merge(%{"__file" => base, "__line" => idx})
      end)
    end)
  end

  defp new_state, do: %{traces: %{}}

  defp step(%{"event" => "snapshot"}, acc, _config), do: acc
  defp step(rec, {state, timeline}, config) do
    trace = Map.get(rec, "trace", "default")
    sup = Map.get(rec, "sup", "root")
    id = Map.get(rec, "id", Map.get(rec, "child", "unknown"))
    ts = timestamp(rec)
    state = ensure_child(state, trace, sup, id, ts)
    {state, duplicate?} = mark_seen(state, trace, sup, rec)

    if duplicate? do
      {put_in(state, [:traces, trace, :totals, :duplicates], get_in(state, [:traces, trace, :totals, :duplicates]) + 1), timeline}
    else
      state = put_in(state, [:traces, trace, :totals, :events], get_in(state, [:traces, trace, :totals, :events]) + 1)
      state = apply_event(state, trace, sup, id, rec, ts, config)
      child = get_in(state, [:traces, trace, :supervisors, sup, :children, id])
      entry = %{"trace" => trace, "ts" => ts, "seq" => Map.get(rec, "seq", 0), "sup" => sup, "id" => id, "pid" => Map.get(child, :current_pid), "event" => rec["event"], "status" => Map.get(child, :status)}
      {state, [entry | timeline]}
    end
  end

  defp ensure_child(state, trace, sup, id, ts) do
    state = update_in(state.traces, &Map.put_new(&1, trace, %{supervisors: %{}, totals: %{events: 0, duplicates: 0}}))
    state = update_in(state.traces[trace].supervisors, &Map.put_new(&1, sup, %{children: %{}, seen: MapSet.new()}))
    update_in(state.traces[trace].supervisors[sup].children, fn children ->
      Map.put_new(children, id, %{current_pid: nil, status: "unknown", crashes: 0, restarts: 0, intensity_breached: false, first_ts: ts, last_ts: ts, window: []})
    end)
  end

  defp mark_seen(state, trace, sup, rec) do
    key = {rec["pid"], rec["event"], rec["id"]}
    seen = get_in(state, [:traces, trace, :supervisors, sup, :seen])
    if MapSet.member?(seen, key) do
      {state, true}
    else
      {put_in(state, [:traces, trace, :supervisors, sup, :seen], MapSet.put(seen, key)), false}
    end
  end

  defp apply_event(state, trace, sup, id, rec, ts, config) do
    path = [:traces, trace, :supervisors, sup, :children, id]
    update_in(state, path, fn child ->
      child = %{child | last_ts: ts}
      case rec["event"] do
        "child_start" -> %{child | current_pid: rec["pid"], status: "running"}
        "child_restart" -> %{child | current_pid: rec["pid"], status: "running", restarts: child.restarts + 1}
        "child_exit" -> apply_exit(child, rec, ts, config)
        _ -> child
      end
    end)
  end

  defp apply_exit(child, rec, ts, config) do
    if Map.get(rec, "reason", "shutdown") == "normal" do
      %{child | status: "stopped", current_pid: rec["pid"]}
    else
      period = Map.get(config, "restart_period_us", 5_000_000)
      intensity = Map.get(config, "restart_intensity", 3)
      window = [ts | child.window]
      active = Enum.filter(window, fn seen -> ts - seen <= period end)
      %{child | status: "crashed", current_pid: rec["pid"], crashes: child.crashes + 1, window: window, intensity_breached: child.intensity_breached or length(active) >= intensity}
    end
  end

  defp timestamp(rec) do
    value = Map.get(rec, "mono", Map.get(rec, "ts", 0))
    case Map.get(rec, "unit", "us") do
      "ms" -> value
      "ns" -> value
      _ -> value
    end
  end

  defp summary(state) do
    traces =
      state.traces
      |> Enum.sort_by(fn {name, _} -> name end)
      |> Enum.map(fn {trace, tstate} ->
        supervisors =
          tstate.supervisors
          |> Enum.sort_by(fn {name, _} -> name end)
          |> Enum.map(fn {name, sup} ->
            children =
              sup.children
              |> Enum.sort_by(fn {id, _} -> id end)
              |> Enum.map(fn {id, child} ->
                %{"id" => id, "current_pid" => child.current_pid, "status" => child.status, "crashes" => child.crashes, "restarts" => child.restarts, "intensity_breached" => child.intensity_breached, "first_ts" => child.first_ts, "last_ts" => child.last_ts}
              end)
            %{"name" => name, "children" => children}
          end)

        %{"trace" => trace, "totals" => tstate.totals, "supervisors" => supervisors}
      end)

    %{"generated_by" => "beam_audit", "traces" => traces}
  end

  defp line_end([]), do: ""
  defp line_end(_), do: "\n"
end
