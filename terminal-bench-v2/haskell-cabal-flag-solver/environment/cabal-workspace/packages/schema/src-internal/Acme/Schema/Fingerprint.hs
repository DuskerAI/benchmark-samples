module Acme.Schema.Fingerprint (wireFingerprint) where

import Acme.Api (apiVersion)

wireFingerprint :: String
wireFingerprint = apiVersion ++ "/wire-7"
