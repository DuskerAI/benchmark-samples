module Acme.Manifest.Fingerprint (manifestFingerprint) where

import Acme.Schema (schemaVersion)
import Acme.Schema.Fingerprint (wireFingerprint)

manifestFingerprint :: String
manifestFingerprint = schemaVersion ++ "/" ++ wireFingerprint
