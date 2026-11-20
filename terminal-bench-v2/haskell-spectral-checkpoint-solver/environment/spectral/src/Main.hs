module Main where

import Data.Char (isSpace)
import Data.Complex hiding (phase)
import Data.List (intercalate)
import System.Environment (getArgs)
import System.Exit (die)
import System.IO (IOMode(WriteMode), hPutStrLn, withFile)
import Text.Printf (printf)

data Config = Config { nx :: Int, steps :: Int, dt :: Double, diffusivity :: Double, velocity :: Double, phase :: Double, checkpointStep :: Int, restartPath :: String }

trim :: String -> String
trim = f . f where f = reverse . dropWhile isSpace

parseConfig :: String -> Either String Config
parseConfig s = do
  let pairs = [(trim k, trim (drop 1 v)) | line <- lines s, let (k,v) = break (=='=') line, not (null k), not (null v)]
      get key = maybe (Left ("missing " ++ key)) Right (lookup key pairs)
  n <- read <$> get "nx"; st <- read <$> get "steps"; dtt <- read <$> get "dt"
  diff <- read <$> get "diffusivity"; vel <- read <$> get "velocity"; ph <- read <$> get "phase"
  chk <- read <$> get "checkpoint_step"; rst <- get "restart"
  if even n && n >= 8 && n <= 128 && st >= 0 && dtt > 0 && diff >= 0 && chk >= 0 && chk <= st
     then Right (Config n st dtt diff vel ph chk rst) else Left "invalid config"

initial :: Config -> [Double]
initial c = [1 + 0.35 * sin (2*pi*(fromIntegral j / fromIntegral (nx c) - phase c)) + 0.15 * cos (4*pi*(fromIntegral j / fromIntegral (nx c) - phase c)) | j <- [0..nx c-1]]

dft :: [Double] -> [Complex Double]
dft xs = [sum [ (x :+ 0) * cis (-2*pi*fromIntegral (j*k)/n') | (j,x) <- zip [0..] xs] / (n' :+ 0) | k <- [0..n-1]] where n = length xs; n' = fromIntegral n

idft :: [Complex Double] -> [Double]
idft hs = [realPart (sum [ h * cis (2*pi*fromIntegral (j*k)/n') | (k,h) <- zip [0..] hs]) | j <- [0..n-1]] where n = length hs; n' = fromIntegral n

mode :: Int -> Int -> Int
mode _ k = k

advance :: Config -> Int -> [Double] -> [Double]
advance c m xs = idft [h * exp (((-diffusivity c * q*q * t) :+ (-velocity c * q * t))) | (k,h) <- zip [0..] (dft xs), let q = 2*pi*fromIntegral (mode (nx c) k), let t = dt c * fromIntegral m]

stats :: [Double] -> (Double, Double)
stats xs = (sum xs / n, sum [x*x | x <- xs] / n) where n = fromIntegral (length xs)

writeReport :: FilePath -> Int -> [Double] -> IO ()
writeReport path st xs = withFile path WriteMode $ \h -> do
  hPutStrLn h "step,x,u,mean,l2"
  let (mu,e) = stats xs; n = length xs
  mapM_ (\(j,u) -> hPutStrLn h (intercalate "," [show st, printf "%.12f" (fromIntegral j / fromIntegral n :: Double), printf "%.12f" u, printf "%.12f" mu, printf "%.12f" e])) (zip [0..] xs)

writeCheckpoint :: FilePath -> Config -> Int -> [Double] -> IO ()
writeCheckpoint path _ st xs = writeFile path (unlines (show st : map (printf "%.12f") xs))

readCheckpoint :: FilePath -> IO (Int, [Double])
readCheckpoint path = do
  ls <- lines <$> readFile path
  case ls of
    [] -> die "bad checkpoint"
    (a:rest) -> pure (read a, map read rest)

main :: IO ()
main = do
  args <- getArgs
  case args of
    [cfgPath,outPath,chkPath] -> do
      ec <- parseConfig <$> readFile cfgPath
      c <- either die pure ec
      (start, u0) <- if null (restartPath c) then pure (0, initial c) else readCheckpoint (restartPath c)
      let stop1 = if null (restartPath c) && checkpointStep c < steps c then checkpointStep c else steps c
          u1 = advance c stop1 u0
      if null (restartPath c) && checkpointStep c < steps c then writeCheckpoint chkPath c stop1 u1 else pure ()
      let remain = steps c - start
          final = if null (restartPath c) then advance c (steps c - stop1) u1 else advance c remain u0
      writeReport outPath (steps c) final
    _ -> die "usage: spectral <config> <report.csv> <checkpoint>"
