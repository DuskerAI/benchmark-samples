defmodule BeamTrail.CLI do
  def main(argv) do
    opts = parse(argv, %{})
    input = Map.fetch!(opts, "--input")
    output = Map.fetch!(opts, "--output")
    File.rm_rf!(output)
    File.mkdir_p!(output)
    BeamTrail.Replay.run(input, output)
  rescue
    e ->
      IO.puts(:stderr, Exception.message(e))
      System.halt(1)
  end

  defp parse([], acc), do: acc
  defp parse([key, value | rest], acc) when key in ["--input", "--output"], do: parse(rest, Map.put(acc, key, value))
  defp parse(_, _), do: raise("usage: beam_audit --input <dir> --output <dir>")
end
