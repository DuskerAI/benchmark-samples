{-# LANGUAGE CPP #-}
module Acme.Policy (policyMode) where

policyMode :: String
#ifdef STRICT_POLICY
strictMode :: String
strictMode = "strict"
#else
strictMode :: String
strictMode = "permissive"
#endif

#ifdef SIGNED_POLICY
signatureMode :: String
signatureMode = "signed"
#else
signatureMode :: String
signatureMode = "unsigned"
#endif

policyMode = strictMode ++ "-" ++ signatureMode
