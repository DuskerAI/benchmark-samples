{-# LANGUAGE CPP #-}
module Acme.Attestation (authorityMode, stampMode, attestationSummary) where

import Acme.Codec (codecMode)
import Acme.Manifest (manifestSeal)
import Acme.Policy (policyMode)

authorityMode :: String
#ifdef LOCAL_KMS
authorityMode = "kms-local"
#else
authorityMode = "kms-remote"
#endif

stampMode :: String
#ifdef REPRODUCIBLE_STAMP
stampMode = "reproducible"
#else
stampMode = "ephemeral"
#endif

attestationSummary :: String
attestationSummary = policyMode ++ "|" ++ codecMode ++ "|" ++ manifestSeal ++ "|" ++ authorityMode ++ "|" ++ stampMode
