{-# LANGUAGE CPP #-}
module Acme.Manifest (manifestSeal, manifestVersion) where

manifestVersion :: String
manifestVersion = "manifest-5.2"

manifestSeal :: String
#ifdef SIGNED_METADATA
manifestSeal = "metadata-signed"
#else
manifestSeal = "metadata-open"
#endif
