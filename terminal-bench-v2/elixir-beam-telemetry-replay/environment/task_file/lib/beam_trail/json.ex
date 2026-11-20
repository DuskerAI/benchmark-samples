defmodule BeamTrail.Json do
  def decode!(text) when is_binary(text) do
    {value, rest} = parse_value(skip(text))
    case skip(rest) do
      "" -> value
      other -> raise "trailing JSON: #{inspect(String.slice(other, 0, 20))}"
    end
  end

  def encode!(value), do: encode(value)

  defp skip(<<c::utf8, rest::binary>>) when c in [32, 9, 10, 13], do: skip(rest)
  defp skip(text), do: text

  defp parse_value(<<"{", rest::binary>>), do: parse_object(skip(rest), %{})
  defp parse_value(<<"[", rest::binary>>), do: parse_array(skip(rest), [])
  defp parse_value(<<"\"", rest::binary>>), do: parse_string(rest, "")
  defp parse_value(<<"true", rest::binary>>), do: {true, rest}
  defp parse_value(<<"false", rest::binary>>), do: {false, rest}
  defp parse_value(<<"null", rest::binary>>), do: {nil, rest}
  defp parse_value(text), do: parse_number(text)

  defp parse_object(<<"}", rest::binary>>, acc), do: {acc, rest}
  defp parse_object(<<"\"", rest::binary>>, acc) do
    {key, rest1} = parse_string(rest, "")
    <<":", rest2::binary>> = skip(rest1)
    {value, rest3} = parse_value(skip(rest2))
    acc = Map.put(acc, key, value)
    case skip(rest3) do
      <<",", rest4::binary>> -> parse_object(skip(rest4), acc)
      <<"}", rest4::binary>> -> {acc, rest4}
      other -> raise "bad object near #{inspect(String.slice(other, 0, 20))}"
    end
  end

  defp parse_array(<<"]", rest::binary>>, acc), do: {Enum.reverse(acc), rest}
  defp parse_array(text, acc) do
    {value, rest} = parse_value(skip(text))
    case skip(rest) do
      <<",", rest2::binary>> -> parse_array(skip(rest2), [value | acc])
      <<"]", rest2::binary>> -> {Enum.reverse([value | acc]), rest2}
      other -> raise "bad array near #{inspect(String.slice(other, 0, 20))}"
    end
  end

  defp parse_string(<<"\"", rest::binary>>, acc), do: {acc, rest}
  defp parse_string(<<"\\\"", rest::binary>>, acc), do: parse_string(rest, acc <> "\"")
  defp parse_string(<<"\\\\", rest::binary>>, acc), do: parse_string(rest, acc <> "\\")
  defp parse_string(<<"\\/", rest::binary>>, acc), do: parse_string(rest, acc <> "/")
  defp parse_string(<<"\\b", rest::binary>>, acc), do: parse_string(rest, acc <> <<8>>)
  defp parse_string(<<"\\f", rest::binary>>, acc), do: parse_string(rest, acc <> <<12>>)
  defp parse_string(<<"\\n", rest::binary>>, acc), do: parse_string(rest, acc <> "\n")
  defp parse_string(<<"\\r", rest::binary>>, acc), do: parse_string(rest, acc <> "\r")
  defp parse_string(<<"\\t", rest::binary>>, acc), do: parse_string(rest, acc <> "\t")
  defp parse_string(<<c::utf8, rest::binary>>, acc), do: parse_string(rest, acc <> <<c::utf8>>)

  defp parse_number(text) do
    {token, rest} = take_number(text, "")
    cond do
      String.contains?(token, [".", "e", "E"]) -> {String.to_float(token), rest}
      token != "" -> {String.to_integer(token), rest}
      true -> raise "bad JSON value"
    end
  end

  defp take_number(<<c::utf8, rest::binary>>, acc) when c in '0123456789+-.eE', do: take_number(rest, acc <> <<c::utf8>>)
  defp take_number(rest, acc), do: {acc, rest}

  defp encode(map) when is_map(map) do
    inner =
      map
      |> Map.keys()
      |> Enum.sort()
      |> Enum.map(fn key -> encode(to_string(key)) <> ":" <> encode(Map.fetch!(map, key)) end)
      |> Enum.join(",")
    "{" <> inner <> "}"
  end

  defp encode(list) when is_list(list), do: "[" <> Enum.map_join(list, ",", &encode/1) <> "]"
  defp encode(value) when is_binary(value), do: "\"" <> escape(value) <> "\""
  defp encode(value) when is_integer(value), do: Integer.to_string(value)
  defp encode(value) when is_float(value), do: :erlang.float_to_binary(value, [:compact, decimals: 10])
  defp encode(true), do: "true"
  defp encode(false), do: "false"
  defp encode(nil), do: "null"

  defp escape(value) do
    value
    |> String.replace("\\", "\\\\")
    |> String.replace("\"", "\\\"")
    |> String.replace("\n", "\\n")
    |> String.replace("\r", "\\r")
    |> String.replace("\t", "\\t")
  end
end
