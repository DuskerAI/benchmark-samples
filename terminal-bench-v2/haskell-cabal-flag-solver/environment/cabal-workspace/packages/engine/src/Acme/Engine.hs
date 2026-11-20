{-# LANGUAGE CPP #-}
module Acme.Engine (engineMode, report) where

import Acme.Api (apiVersion)
import Acme.Codec (codecMode)

engineMode :: String
#ifdef RELEASE_ENGINE
engineMode = "engine-release"
#else
engineMode = "engine-debug"
#endif

report :: String
report = "plan:" ++ apiVersion ++ "|" ++ codecMode ++ "|" ++ engineMode
