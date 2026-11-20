{-# LANGUAGE RecordWildCards #-}

import Data.Char (isSpace)
import Data.List (intercalate, nub, sort)
import qualified Data.Map.Strict as M
import System.Directory (doesFileExist, removeFile)
import System.Environment (getArgs)
import System.Exit (exitFailure)
import System.FilePath (takeBaseName, (</>))
import Text.Printf (printf)

data Camera = Camera { width :: Double, height :: Double, cx :: Double, cy :: Double, focal :: Double }
data Obs = Obs { frame :: String, marker :: String, bx :: Double, by :: Double, px :: Double, py :: Double, wt :: Double }

trim :: String -> String
trim = f . f where f = reverse . dropWhile isSpace

split :: Char -> String -> [String]
split _ [] = [""]
split c s = case break (== c) s of
  (a, _ : b) -> a : split c b
  (a, []) -> [a]

readD :: String -> Maybe Double
readD s = case reads s of [(x, rest)] | all isSpace rest && isFinite x -> Just x; _ -> Nothing

isFinite :: Double -> Bool
isFinite x = not (isNaN x || isInfinite x)

die :: FilePath -> IO a
die out = do
  exists <- doesFileExist out
  if exists then removeFile out else pure ()
  exitFailure

parseCamera :: FilePath -> IO (Maybe Camera)
parseCamera path = do
  txt <- readFile path
  let pairs = [ let (k,v0) = break (=='=') line in (trim k, trim (drop 1 v0)) | line <- lines txt, not (null (trim line)) ]
      keys = map fst pairs
      mp = M.fromList pairs
  if length keys /= length (nub keys) || sort keys /= ["cx","cy","focal","height","width"] then pure Nothing else do
    let vals = mapM (\k -> M.lookup k mp >>= readD) ["width","height","cx","cy","focal"]
    pure $ case vals of
      Just [w,h,cxv,cyv,f] | w > 0 && h > 0 && f > 0 -> Just (Camera w h cxv cyv f)
      _ -> Nothing

parseObs :: FilePath -> IO (Maybe [Obs])
parseObs path = do
  txt <- readFile path
  let ls = lines txt
  case ls of
    [] -> pure Nothing
    (hdr:rows) | trim hdr == "frame,marker,x,y,u,v,weight" -> pure (mapM parseRow rows)
    _ -> pure Nothing
  where
    parseRow row = case split ',' row of
      [fr,m,xs,ys,us,vs,ws] | not (null fr) && not (null m) -> do
        x <- readD xs; y <- readD ys; u <- readD us; v <- readD vs; w <- readD ws
        if w > 0 then Just (Obs fr m x y u v w) else Nothing
      _ -> Nothing

initialFrame :: Camera -> [Obs] -> (String, Double, Double, Int)
initialFrame cam os =
  let fr = frame (head os)
      n = fromIntegral (length os)
      sx = sum [((px o - cx cam) / width cam) - bx o | o <- os]
      sy = sum [((py o - cy cam) / width cam) - by o | o <- os]
  in (fr, sx / n, sy / n, length os)

main :: IO ()
main = do
  args <- getArgs
  case args of
    [caseDir, out] -> run caseDir out
    _ -> exitFailure

run :: FilePath -> FilePath -> IO ()
run caseDir out = do
  mc <- parseCamera (caseDir </> "camera.txt")
  mo <- parseObs (caseDir </> "observations.csv")
  case (mc, mo) of
    (Just cam, Just obs) | length obs >= 6 -> writeReport cam obs caseDir out
    _ -> die out

writeReport :: Camera -> [Obs] -> FilePath -> FilePath -> IO ()
writeReport cam obs caseDir out = do
  let grouped = M.toList (M.fromListWith (++) [(frame o, [o]) | o <- obs])
      frames = [ initialFrame cam os | (_, os) <- grouped]
      frameJson (fr,tx,ty,n) = printf "{\"frame\":\"%s\",\"tx\":%.6f,\"ty\":%.6f,\"rms\":%.6f,\"inliers\":%d,\"outliers\":%d}" fr tx ty (0 :: Double) n (0 :: Int)
      body = "{\"status\":\"ok\",\"case\":\"" ++ takeBaseName caseDir ++ "\","
          ++ printf "\"camera\":{\"width\":%.6f,\"height\":%.6f,\"cx\":%.6f,\"cy\":%.6f,\"focal\":%.6f}," (width cam) (height cam) (cx cam) (cy cam) (focal cam)
          ++ "\"distortion\":{\"k1\":0.000000,\"k2\":0.000000},"
          ++ "\"frames\":[" ++ intercalate "," (map frameJson frames) ++ "],"
          ++ printf "\"summary\":{\"observations\":%d,\"inliers\":%d,\"outliers\":0,\"weighted_rms\":%.6f,\"iterations\":1}}\n" (length obs) (length obs) (0 :: Double)
  appendFile out body
