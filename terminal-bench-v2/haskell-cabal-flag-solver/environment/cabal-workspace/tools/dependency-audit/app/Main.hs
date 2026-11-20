module Main where

import Acme.Api (apiVersion)
import Acme.Codec (codecMode, codecVersion)

main :: IO ()
main = putStrLn $ "audit:api=" ++ drop 4 apiVersion ++ ";codec=" ++ codecVersion ++ ";mode=" ++ drop 6 codecMode
