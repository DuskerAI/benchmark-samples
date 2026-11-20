#!/bin/bash
set -euo pipefail

cat > /app/lenscal/Main.hs <<'HS'
{-# LANGUAGE RecordWildCards #-}
{-# LANGUAGE ScopedTypeVariables #-}

import Control.Exception (IOException, bracket, catch, evaluate)
import Data.Char (isAsciiLower, isAsciiUpper, isDigit, isSpace)
import Data.List (intercalate, nub, sort)
import qualified Data.Map.Strict as M
import System.Directory (doesFileExist, removeFile, renameFile)
import System.Environment (getArgs)
import System.Exit (exitFailure)
import System.FilePath (takeBaseName, (</>))
import System.IO (IOMode(ReadMode), hClose, hGetContents, hSetEncoding, openFile, utf8)
import Text.Printf (printf)

data Camera = Camera { width :: Double, height :: Double, cx :: Double, cy :: Double, focal :: Double } deriving Show
data Obs = Obs { frame :: String, marker :: String, bx :: Double, by :: Double, nx :: Double, ny :: Double, baseW :: Double } deriving Show
data Fit = Fit { params :: [Double], frames :: [String], residuals :: [Double], robustScale :: Double, iterations :: Int }

trim :: String -> String
trim = f . f where f = reverse . dropWhile isSpace

split :: Char -> String -> [String]
split _ [] = [""]
split c s = case break (== c) s of
  (a, _ : b) -> a : split c b
  (a, []) -> [a]

finite :: Double -> Bool
finite x = not (isNaN x || isInfinite x)

readD :: String -> Maybe Double
readD s = case reads s of [(x, rest)] | all isSpace rest && finite x -> Just x; _ -> Nothing

validIdent :: String -> Bool
validIdent s = not (null s) && all (\c -> isAsciiUpper c || isAsciiLower c || isDigit c || c == '_' || c == '-' || c == '.') s

removeIfExists :: FilePath -> IO ()
removeIfExists p = doesFileExist p >>= \e -> if e then removeFile p else pure ()

die :: FilePath -> IO a
die out = removeIfExists out >> exitFailure

readSafe :: FilePath -> IO (Maybe String)
readSafe p = (Just <$> bracket (openFile p ReadMode) hClose readAll) `catch` (\(_ :: IOException) -> pure Nothing)
  where readAll h = do
          hSetEncoding h utf8
          s <- hGetContents h
          evaluate (length s)
          pure s

parseCamera :: FilePath -> IO (Maybe Camera)
parseCamera path = do
  mt <- readSafe path
  pure $ do
    txt <- mt
    let pairs = [let (k,v0) = break (=='=') line in (trim k, trim (drop 1 v0)) | line <- lines txt, not (null (trim line))]
        keys = map fst pairs
        mp = M.fromList pairs
    if length keys /= length (nub keys) || sort keys /= ["cx","cy","focal","height","width"] then Nothing else do
      w <- M.lookup "width" mp >>= readD
      h <- M.lookup "height" mp >>= readD
      cxv <- M.lookup "cx" mp >>= readD
      cyv <- M.lookup "cy" mp >>= readD
      f <- M.lookup "focal" mp >>= readD
      if w > 0 && h > 0 && f > 0 then Just (Camera w h cxv cyv f) else Nothing

parseRows :: Camera -> FilePath -> IO (Maybe [Obs])
parseRows cam path = do
  mt <- readSafe path
  pure $ do
    txt <- mt
    case lines txt of
      [] -> Nothing
      (hdr:rows) | trim hdr == "frame,marker,x,y,u,v,weight" -> mapM parseRow rows
      _ -> Nothing
  where
    parseRow row = case split ',' row of
      [fr0,m0,xs,ys,us,vs,ws] -> do
        let fr = trim fr0; m = trim m0
        x <- readD xs; y <- readD ys; u <- readD us; v <- readD vs; w <- readD ws
        if validIdent fr && validIdent m && w > 0
          then Just (Obs fr m x y ((u - cx cam) / focal cam) ((v - cy cam) / focal cam) w)
          else Nothing
      _ -> Nothing

median :: [Double] -> Double
median xs = let ys = sort xs; n = length ys in if odd n then ys !! (n `div` 2) else 0.5 * (ys !! (n `div` 2 - 1) + ys !! (n `div` 2))

frameCountsValid :: [Obs] -> Bool
frameCountsValid obs = length fs >= 2 && all (>=5) counts && length obs >= max 10 (2 + 2 * length fs + 2)
  where grouped = M.fromListWith (+) [(frame o, 1 :: Int) | o <- obs]; fs = M.keys grouped; counts = M.elems grouped

initialParams :: [Obs] -> ([String], [Double])
initialParams obs = (fs, 0 : 0 : concatMap start fs)
  where
    fs = sort (nub (map frame obs))
    start fr = let os = filter ((== fr) . frame) obs
                   sw = sum (map baseW os)
                   tx0 = sum [baseW o * (nx o - bx o) | o <- os] / sw
                   ty0 = sum [baseW o * (ny o - by o) | o <- os] / sw
               in [tx0, ty0]

frameIndex :: [String] -> M.Map String Int
frameIndex fs = M.fromList (zip fs [0..])

predictResidual :: M.Map String Int -> [Double] -> Obs -> Double
predictResidual ix ps o = sqrt (rx * rx + ry * ry)
  where
    off = 2 + 2 * (ix M.! frame o)
    qx = bx o + ps !! off
    qy = by o + ps !! (off + 1)
    r2 = qx*qx + qy*qy
    fac = 1 + ps!!0 * r2 + ps!!1 * r2 * r2
    rx = nx o - fac * qx
    ry = ny o - fac * qy

normalRows :: M.Map String Int -> [Double] -> Obs -> ([Double], Double, [Double], Double)
normalRows ix ps o = (gx, nx o - predx, gy, ny o - predy)
  where
    n = length ps
    off = 2 + 2 * (ix M.! frame o)
    k1 = ps!!0; k2 = ps!!1
    qx = bx o + ps!!off
    qy = by o + ps!!(off+1)
    r2 = qx*qx + qy*qy; r4 = r2*r2
    fac = 1 + k1*r2 + k2*r4
    predx = fac * qx; predy = fac * qy
    cross = 2*k1*qx*qy + 4*k2*r2*qx*qy
    gx = setAt off (fac + 2*k1*qx*qx + 4*k2*r2*qx*qx) $ setAt (off+1) cross $ setAt 0 (r2*qx) $ setAt 1 (r4*qx) (replicate n 0)
    gy = setAt off cross $ setAt (off+1) (fac + 2*k1*qy*qy + 4*k2*r2*qy*qy) $ setAt 0 (r2*qy) $ setAt 1 (r4*qy) (replicate n 0)

setAt :: Int -> a -> [a] -> [a]
setAt i v xs = take i xs ++ [v] ++ drop (i+1) xs

gaussNewton :: [Obs] -> [String] -> [Double] -> [Double] -> Maybe ([Double], Int)
gaussNewton obs fs start robust = loop 1 start
  where
    ix = frameIndex fs
    n = length start
    loop it ps
      | it > 30 = Just (ps, 30)
      | otherwise = do
          let (a0,b0) = foldl (accumulate ix ps) (zero n, replicate n 0) (zip obs robust)
              a = zipWith (\i row -> addAt i 1e-8 row) [0..] a0
          if not (fullRank a0) then Nothing else pure ()
          delta <- solveLinear a b0
          let ps' = zipWith (+) ps delta
          if maximum (map abs delta) < 1e-11 then Just (ps', it) else loop (it+1) ps'

accumulate :: M.Map String Int -> [Double] -> ([[Double]],[Double]) -> (Obs,Double) -> ([[Double]],[Double])
accumulate ix ps (a,b) (o,rw) = addOne gy ey (addOne gx ex (a,b))
  where
    (gx, ex, gy, ey) = normalRows ix ps o
    w = baseW o * rw
    addOne g e (aa,bb) = (zipWith (\row gi -> zipWith (+) row [w * gi * gj | gj <- g]) aa g, zipWith (+) bb [w * gi * e | gi <- g])

zero :: Int -> [[Double]]
zero n = replicate n (replicate n 0)

addAt :: Int -> Double -> [Double] -> [Double]
addAt i v xs = take i xs ++ [xs!!i + v] ++ drop (i+1) xs

solveLinear :: [[Double]] -> [Double] -> Maybe [Double]
solveLinear a b = eliminate 0 (zipWith (++) a (map (:[]) b))
  where
    n = length b
    eliminate col mat
      | col == n = Just (map (!! n) mat)
      | otherwise = do
          let candidates = drop col (zip [0..] mat)
              (pivIdx, pivRow) = maximumByAbs col candidates
          if abs (pivRow !! col) < 1e-12 then Nothing else do
            let mat1 = swapRows col pivIdx mat
                row = mat1 !! col
                divv = row !! col
                norm = map (/ divv) row
                elim r old = if r == col then norm else let f = old !! col in zipWith (-) old (map (* f) norm)
                mat2 = zipWith elim [0..] mat1
            eliminate (col+1) mat2

fullRank :: [[Double]] -> Bool
fullRank a = rank 0 a
  where
    n = length a
    scale = maximum (1 : [abs x | row <- a, x <- row])
    tol = 1e-12 * scale
    rank col mat
      | col == n = True
      | otherwise =
          let candidates = drop col (zip [0..] mat)
              (pivIdx, pivRow) = maximumByAbs col candidates
          in if abs (pivRow !! col) <= tol then False else
               let mat1 = swapRows col pivIdx mat
                   row = mat1 !! col
                   divv = row !! col
                   norm = map (/ divv) row
                   elim r old = if r == col then norm else let f = old !! col in zipWith (-) old (map (* f) norm)
                   mat2 = zipWith elim [0..] mat1
               in rank (col+1) mat2

maximumByAbs :: Int -> [(Int,[Double])] -> (Int,[Double])
maximumByAbs col = foldl1 (\best cur -> if abs ((snd cur)!!col) > abs ((snd best)!!col) then cur else best)

swapRows :: Int -> Int -> [a] -> [a]
swapRows i j xs | i == j = xs
swapRows i j xs = [pick k x | (k,x) <- zip [0..] xs]
  where pick k x | k == i = xs!!j | k == j = xs!!i | otherwise = x

fitModel :: [Obs] -> Maybe Fit
fitModel obs = do
  let (fs,p0) = initialParams obs
  (p1,it1) <- gaussNewton obs fs p0 (replicate (length obs) 1)
  let ix = frameIndex fs
      r1 = map (predictResidual ix p1) obs
      sc = max 1e-9 (1.4826 * median r1)
      rw = [min 1 ((3*sc) / max r 1e-12) | r <- r1]
  (p2,it2) <- gaussNewton obs fs p1 rw
  let r2 = map (predictResidual ix p2) obs
  if all finite p2 then Just (Fit p2 fs r2 sc (it1+it2)) else Nothing

frameReport :: Fit -> [Obs] -> String -> String
frameReport Fit{..} obs fr = printf "{\"frame\":\"%s\",\"tx\":%.8f,\"ty\":%.8f,\"rms\":%.8f,\"inliers\":%.8f,\"outliers\":%.8f}" fr tx ty rms (fromIntegral ins :: Double) (fromIntegral outs :: Double)
  where
    ix = frameIndex frames
    off = 2 + 2 * (ix M.! fr)
    tx = params !! off; ty = params !! (off+1)
    cutoff = max 0.004 (4 * robustScale)
    pairs = [(o,r) | (o,r) <- zip obs residuals, frame o == fr]
    inrs = [r | (_,r) <- pairs, r <= cutoff]
    outs = length pairs - length inrs
    ins = length inrs
    rms = sqrt (sum [r*r | r <- inrs] / fromIntegral ins)

writeReport :: Camera -> [Obs] -> FilePath -> FilePath -> Fit -> IO ()
writeReport cam obs caseDir out fit@Fit{..} = do
  let cutoff = max 0.004 (4 * robustScale)
      inrs = [r | r <- residuals, r <= cutoff]
      outCount = length residuals - length inrs
      wrms = sqrt (sum [r*r | r <- inrs] / fromIntegral (length inrs))
      body = "{\"status\":\"ok\",\"case\":\"" ++ takeBaseName caseDir ++ "\","
        ++ printf "\"camera\":{\"width\":%.8f,\"height\":%.8f,\"cx\":%.8f,\"cy\":%.8f,\"focal\":%.8f}," (width cam) (height cam) (cx cam) (cy cam) (focal cam)
        ++ printf "\"distortion\":{\"k1\":%.8f,\"k2\":%.8f}," (params!!0) (params!!1)
        ++ "\"frames\":[" ++ intercalate "," [frameReport fit obs fr | fr <- frames] ++ "],"
        ++ printf "\"summary\":{\"observations\":%.8f,\"inliers\":%.8f,\"outliers\":%.8f,\"weighted_rms\":%.8f,\"iterations\":%.8f}}\n" (fromIntegral (length obs) :: Double) (fromIntegral (length inrs) :: Double) (fromIntegral outCount :: Double) wrms (fromIntegral iterations :: Double)
      tmp = out ++ ".tmp"
  writeFile tmp body
  renameFile tmp out

main :: IO ()
main = do
  args <- getArgs
  case args of
    [caseDir,out] -> do
      mc <- parseCamera (caseDir </> "camera.txt")
      case mc of
        Nothing -> die out
        Just cam -> do
          mo <- parseRows cam (caseDir </> "observations.csv")
          case mo of
            Just obs | frameCountsValid obs -> case fitModel obs of
              Just fit -> writeReport cam obs caseDir out fit
              Nothing -> die out
            _ -> die out
    _ -> exitFailure
HS

make -C /app/lenscal clean all
