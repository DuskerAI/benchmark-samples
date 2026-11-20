defmodule BeamTrail.MixProject do
  use Mix.Project

  def project do
    [
      app: :beam_trail,
      version: "0.1.0",
      elixir: "~> 1.14",
      escript: [main_module: BeamTrail.CLI, name: "beam_audit"],
      start_permanent: Mix.env() == :prod
    ]
  end

  def application do
    [extra_applications: [:logger]]
  end
end
