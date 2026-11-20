module Acme.Attestation.Token (attestationToken) where

import Acme.Manifest.Fingerprint (manifestFingerprint)

attestationToken :: String
attestationToken = manifestFingerprint ++ "#attest"
