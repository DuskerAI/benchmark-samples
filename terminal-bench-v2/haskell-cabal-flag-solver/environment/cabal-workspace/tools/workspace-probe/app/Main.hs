module Main where

import Acme.Policy (policyMode)
import Acme.Schema (schemaVersion)
import Acme.Schema.Fingerprint (wireFingerprint)
import System.Environment (getArgs)
import System.Exit (die)

main :: IO ()
main = do
  args <- getArgs
  case args of
    [] -> emit (schemaVersion ++ "|" ++ wireFingerprint ++ "|" ++ policyMode)
    ["schema"] -> emit schemaVersion
    ["wire"] -> emit wireFingerprint
    ["policy"] -> emit policyMode
    _ -> die "usage: workspace-probe [schema|wire|policy]"
  where
    emit value = putStrLn ("probe:" ++ value)
