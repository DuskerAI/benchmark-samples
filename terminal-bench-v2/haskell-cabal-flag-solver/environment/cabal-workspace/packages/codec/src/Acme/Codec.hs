{-# LANGUAGE CPP #-}
module Acme.Codec (codecMode, codecVersion) where

codecMode :: String
#ifdef FAST_CODEC
codecMode = "codec-fast"
#else
codecMode = "codec-safe"
#endif

codecVersion :: String
codecVersion = "1.4"
