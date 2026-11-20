module Main where

import Acme.Codec (codecMode)
import Acme.Attestation (attestationSummary, authorityMode, stampMode)
import Acme.Attestation.Token (attestationToken)
import Acme.Manifest (manifestSeal, manifestVersion)
import Acme.Manifest.Fingerprint (manifestFingerprint)
import Acme.Policy (policyMode)
import System.Environment (getArgs)
import System.Exit (die)

main :: IO ()
main = do
  args <- getArgs
  case args of
    [] -> emit (manifestVersion ++ "|" ++ attestationToken ++ "|" ++ attestationSummary)
    ["manifest"] -> emit manifestVersion
    ["fingerprint"] -> emit manifestFingerprint
    ["policy"] -> emit policyMode
    ["codec"] -> emit codecMode
    ["seal"] -> emit manifestSeal
    ["token"] -> emit attestationToken
    ["authority"] -> emit authorityMode
    ["stamp"] -> emit stampMode
    _ -> die "usage: release-gate [manifest|fingerprint|policy|codec|seal|token|authority|stamp]"
  where
    emit value = putStrLn ("gate:" ++ value)
