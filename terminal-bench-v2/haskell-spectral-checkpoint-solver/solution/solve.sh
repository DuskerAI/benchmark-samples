#!/bin/bash
set -euo pipefail

cat > /app/spectral/src/Main.hs <<'HS'
module Main where

import Control.Exception (evaluate)
import Data.Char (isSpace)
import Data.Complex hiding (phase)
import Data.List (intercalate, stripPrefix)
import System.Environment (getArgs)
import System.Exit (die)
import System.IO (IOMode(WriteMode), hPutStrLn, withFile)
import Text.Read (readMaybe)
import Text.Printf (printf)

data Config = Config { nx :: Int, steps :: Int, dt :: Double, diffusivity :: Double, velocity :: Double, phase :: Double, checkpointStep :: Int, restartPath :: String }

trim :: String -> String
trim = dropWhile isSpace . reverse . dropWhile isSpace . reverse

finite :: Double -> Bool
finite x = not (isNaN x) && not (isInfinite x)

lookupKey :: [(String,String)] -> String -> Either String String
lookupKey pairs key = maybe (Left ("missing " ++ key)) Right (lookup key pairs)

readE :: Read a => String -> String -> Either String a
readE key raw = case reads raw of
  [(v, rest)] | all isSpace rest -> Right v
  _ -> Left ("bad " ++ key)

parseConfig :: String -> Either String Config
parseConfig s = do
  let required = ["nx", "steps", "dt", "diffusivity", "velocity", "phase", "checkpoint_step", "restart"]
      parseLine line =
        let stripped = trim line
            (k,v) = break (=='=') stripped
        in if null stripped
          then Right Nothing
          else if null k || null v
            then Left "bad config line"
            else Right (Just (trim k, trim (drop 1 v)))
      countKey key pairs = length [() | (k,_) <- pairs, k == key]
  parsed <- mapM parseLine (lines s)
  let pairs = [p | Just p <- parsed]
      keys = map fst pairs
  if any (`notElem` required) keys || any (\key -> countKey key pairs /= 1) required
    then Left "bad config keys"
    else Right ()
  n <- lookupKey pairs "nx" >>= readE "nx"
  st <- lookupKey pairs "steps" >>= readE "steps"
  dtt <- lookupKey pairs "dt" >>= readE "dt"
  diff <- lookupKey pairs "diffusivity" >>= readE "diffusivity"
  vel <- lookupKey pairs "velocity" >>= readE "velocity"
  ph <- lookupKey pairs "phase" >>= readE "phase"
  chk <- lookupKey pairs "checkpoint_step" >>= readE "checkpoint_step"
  rst <- lookupKey pairs "restart"
  if even n && n >= 8 && n <= 128 && st >= 0 && finite dtt && dtt > 0 && finite diff && diff >= 0 && finite vel && finite ph && chk >= 0 && chk <= st
    then Right (Config n st dtt diff vel ph chk rst)
    else Left "invalid config"

initial :: Config -> [Double]
initial c = [1 + 0.35 * sin (2*pi*(x - phase c)) + 0.15 * cos (4*pi*(x - phase c)) + 0.07 * sin (2*pi*(fromIntegral (nx c `div` 2 - 1) * x + phase c)) | j <- [0..nx c-1], let x = fromIntegral j / fromIntegral (nx c)]

dft :: [Double] -> [Complex Double]
dft xs = [sum [ (x :+ 0) * cis (-2*pi*fromIntegral (j*k)/n') | (j,x) <- zip [0..] xs] | k <- [0..n-1]] where n = length xs; n' = fromIntegral n

idft :: [Complex Double] -> [Double]
idft hs = [realPart (sum [ h * cis (2*pi*fromIntegral (j*k)/n') | (k,h) <- zip [0..] hs] / (n' :+ 0)) | j <- [0..n-1]] where n = length hs; n' = fromIntegral n

mode :: Int -> Int -> Int
mode n k | k <= n `div` 2 = k
         | otherwise = k - n

evolve :: Config -> Int -> Int -> [Double] -> [Double]
evolve c fromStep toStep xs = idft [h * ((exp (-diffusivity c * q*q*t)) :+ 0) * cis (-velocity c*q*t) | (k,h) <- zip [0..] (dft xs), let q = 2*pi*fromIntegral (mode (nx c) k), let t = dt c * fromIntegral (toStep - fromStep)]

closeEnough :: [Double] -> [Double] -> Bool
closeEnough as bs = length as == length bs && and (zipWith (\a b -> abs (a - b) <= 5e-10) as bs)

stats :: [Double] -> (Double, Double)
stats xs = (sum xs / n, sum [x*x | x <- xs] / n) where n = fromIntegral (length xs)

fmt :: Double -> String
fmt = printf "%.12f"

writeReport :: FilePath -> Int -> [Double] -> IO ()
writeReport path st xs = withFile path WriteMode $ \h -> do
  hPutStrLn h "step,x,u,mean,l2"
  let (mu,e) = stats xs; n = length xs
  mapM_ (\(j,u) -> hPutStrLn h (intercalate "," [show st, fmt (fromIntegral j / fromIntegral n), fmt u, fmt mu, fmt e])) (zip [0..] xs)

showD :: Double -> String
showD = printf "%.17g"

checksum :: String -> String
checksum = show . foldl step (2166136261 :: Integer)
  where step acc ch = (acc * 16777619 + toInteger (fromEnum ch)) `mod` 1000000007

writeCheckpoint :: FilePath -> Config -> Int -> [Double] -> IO ()
writeCheckpoint path c st xs = do
  let body = unlines $ [ "SPECTRAL1"
                       , "step=" ++ show st
                       , "nx=" ++ show (nx c)
                       , "dt=" ++ showD (dt c)
                       , "diffusivity=" ++ showD (diffusivity c)
                       , "velocity=" ++ showD (velocity c)
                       , "phase=" ++ showD (phase c)
                       , "values"
                       ] ++ map showD xs
  writeFile path (body ++ "checksum=" ++ checksum body ++ "\n")

readValue :: Read a => String -> String -> IO a
readValue name raw = maybe (die ("bad checkpoint " ++ name)) pure (readMaybe raw)

readCheckpoint :: Config -> FilePath -> IO (Int, [Double])
readCheckpoint c path = do
  ls <- lines <$> readFile path
  bodyLines <- case reverse ls of
    [] -> die "bad checkpoint"
    (sumLine:revBody) -> case stripPrefix "checksum=" sumLine of
      Just raw | raw == checksum (unlines (reverse revBody)) -> pure (reverse revBody)
      _ -> die "bad checkpoint checksum"
  case bodyLines of
    ("SPECTRAL1":stepLine:nxLine:dtLine:diffLine:velLine:phaseLine:"values":valueRows) -> do
      let exact key line = case break (=='=') line of
            (k, '=':v) | k == key -> pure v
            _ -> die ("bad checkpoint " ++ key)
      stRaw <- exact "step" stepLine
      nRaw <- exact "nx" nxLine
      dtRaw <- exact "dt" dtLine
      diffRaw <- exact "diffusivity" diffLine
      velRaw <- exact "velocity" velLine
      phRaw <- exact "phase" phaseLine
      st <- readValue "step" stRaw
      n <- readValue "nx" nRaw
      dtt <- readValue "dt" dtRaw
      diff <- readValue "diffusivity" diffRaw
      vel <- readValue "velocity" velRaw
      ph <- readValue "phase" phRaw
      xs <- (mapM (readValue "value") valueRows :: IO [Double])
      evaluate (length xs)
      let validValues = length xs == nx c && all finite xs
      if st >= 0 && st <= steps c && n == nx c && dtt == dt c && diff == diffusivity c && vel == velocity c && ph == phase c && validValues
        then pure (st, xs)
        else die "checkpoint does not match config"
    _ -> die "bad checkpoint"

main :: IO ()
main = do
  args <- getArgs
  case args of
    [cfgPath,outPath,chkPath] -> do
      ec <- parseConfig <$> readFile cfgPath
      c <- either die pure ec
      (storedStart, storedState) <- if null (restartPath c) then pure (0, initial c) else readCheckpoint c (restartPath c)
      if checkpointStep c < storedStart then die "checkpoint step precedes restart" else pure ()
      let canonicalState = not (null (restartPath c)) && closeEnough storedState (evolve c 0 storedStart (initial c))
          start = if canonicalState then 0 else storedStart
          u0 = if canonicalState then initial c else storedState
      let shouldCheckpoint = checkpointStep c > start && checkpointStep c < steps c
          checkpointState = evolve c start (checkpointStep c) u0
          final = evolve c start (steps c) u0
      if shouldCheckpoint then writeCheckpoint chkPath c (checkpointStep c) checkpointState else pure ()
      writeReport outPath (steps c) final
    _ -> die "usage: spectral <config> <report.csv> <checkpoint>"
HS

make -C /app/spectral clean
make -C /app/spectral
