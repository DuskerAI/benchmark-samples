module Main where

import Data.Char (isDigit)
import Data.List (intercalate, nub, sort, sortBy)
import qualified Data.Map.Strict as M
import System.Environment (getArgs)
import System.Exit (die)

data Op = Eq | Gt | Ge | Lt | Le deriving (Eq, Show)
data C = C Op [Int] deriving (Eq, Show)
data Req = Req String [C] deriving (Eq, Show)
data Flag = Flag String Bool [Req] [Req] deriving (Eq, Show)
data Pkg = Pkg { pn :: String, pv :: String, ds :: [Req], fs :: [Flag], ps :: [(String,String)], ns :: [(String,String)] } deriving (Eq, Show)

emptyPkg :: String -> String -> Pkg
emptyPkg n v = Pkg n v [] [] [] []

main :: IO ()
main = do
  args <- getArgs
  case args of
    [inp,out] -> do
      txt <- readFile inp
      let (repo, roots) = loadRepo txt
      case solve repo roots of
        Left e -> die e
        Right plan -> writeFile out (render repo plan)
    _ -> die "usage: cabal_plan <repo> <output>"

loadRepo :: String -> (M.Map (String,String) Pkg, [Req])
loadRepo = foldl step (M.empty, []) . filter (not . null) . map words . lines
  where
    upd k f m = M.alter (Just . f . maybe (emptyPkg (fst k) (snd k)) id) k m
    step (m,rs) xs = case xs of
      ["pkg",n,v] -> (M.insertWith (\_ old -> old) (n,v) (emptyPkg n v) m, rs)
      ["dep",n,v,reqs] -> (upd (n,v) (\p -> p { ds = ds p ++ reqList reqs }) m, rs)
      ["flag",n,v,fl,def,t,f] -> (upd (n,v) (\p -> p { fs = fs p ++ [Flag fl (def == "True") (reqList t) (reqList f)] }) m, rs)
      ["provide",n,v,mo,abi] -> (upd (n,v) (\p -> p { ps = ps p ++ [(mo,abi)] }) m, rs)
      ["need",n,v,mo,abi] -> (upd (n,v) (\p -> p { ns = ns p ++ [(mo,abi)] }) m, rs)
      ["root",r] -> (m, rs ++ [parseReq r])
      _ -> (m,rs)

reqList :: String -> [Req]
reqList "-" = []
reqList s = map parseReq (split ',' s)

parseReq :: String -> Req
parseReq s = Req name (map parseC rest)
  where
    (name, tailS) = span (\c -> c /= '<' && c /= '>' && c /= '=') s
    rest = case tailS of "" -> []; _ -> split ',' tailS
    parseC x = case x of
      ('>':'=':v) -> C Ge (ver v)
      ('<':'=':v) -> C Le (ver v)
      ('>':v) -> C Gt (ver v)
      ('<':v) -> C Lt (ver v)
      ('=':v) -> C Eq (ver v)
      _ -> C Eq (ver x)

split :: Char -> String -> [String]
split c s = case break (== c) s of
  (a,[]) -> [a]
  (a,_:b) -> a : split c b

ver :: String -> [Int]
ver [] = []
ver s = map read (split '.' (takeWhile (\x -> isDigit x || x == '.') s))

norm :: [Int] -> [Int]
norm xs = take 6 (xs ++ repeat 0)

cmpV :: String -> String -> Ordering
cmpV a b = compare (norm (ver a)) (norm (ver b))

okReq :: Req -> Pkg -> Bool
okReq (Req n cs) p = n == pn p && all ok cs
  where
    vv = norm (ver (pv p))
    ok (C o x) = case o of
      Eq -> vv == norm x; Gt -> vv > norm x; Ge -> vv >= norm x; Lt -> vv < norm x; Le -> vv <= norm x

solve :: M.Map (String,String) Pkg -> [Req] -> Either String (M.Map String (String, [(String,Bool)]))
solve repo roots = go roots M.empty
  where
    choices (Req n _) = sortBy (\a b -> cmpV (pv a) (pv b)) [p | p <- M.elems repo, pn p == n]
    go [] chosen = if modules chosen then Right chosen else Left "module requirements are not met"
    go (r@(Req n _):todo) chosen = case M.lookup n chosen of
      Just (v,_) -> case M.lookup (n,v) repo of
        Just p | okReq r p -> go todo chosen
        _ -> Left "version conflict"
      Nothing -> case filter (okReq r) (choices r) of
        [] -> Left "no candidate"
        (p:_) -> let vals = [(fl,def) | Flag fl def _ _ <- fs p]
                     more = concat [if def then t else f | Flag _ def t f <- fs p]
                 in go (todo ++ ds p ++ more) (M.insert n (pv p, vals) chosen)
    modules chosen = all needOK selected
      where
        selected = [p | (n,(v,_)) <- M.toList chosen, Just p <- [M.lookup (n,v) repo]]
        have = [(m,a) | p <- selected, (m,a) <- ps p]
        needOK p = all (\(m,a) -> any (\(hm,ha) -> hm == m && ha == a) have) (ns p)

render :: M.Map (String,String) Pkg -> M.Map String (String, [(String,Bool)]) -> String
render repo chosen = "{\"packages\":[" ++ intercalate "," (map pkgJson pkgs) ++ "],\"build_order\":" ++ arr2 order ++ "}\n"
  where
    pkgs = sortBy (\a b -> compare (pn a, pv a) (pn b, pv b)) [p | (n,(v,_)) <- M.toList chosen, Just p <- [M.lookup (n,v) repo]]
    flagsFor p = maybe [] snd (M.lookup (pn p) chosen)
    depIds p = sort . nub $ [n ++ "-" ++ v | Req n _ <- ds p ++ active p, Just (v,_) <- [M.lookup n chosen]]
    active p = concat [if val then t else f | Flag fl _ t f <- fs p, Just val <- [lookup fl (flagsFor p)]]
    pkgJson p = "{\"name\":"++q(pn p)++",\"version\":"++q(pv p)++",\"id\":"++q(pn p++"-"++pv p)++",\"flags\":"++flagJson (flagsFor p)++",\"depends\":"++arr(depIds p)++",\"provides\":"++pairs (ps p)++",\"needs\":"++pairs (ns p)++"}"
    flagJson xs = "[" ++ intercalate "," ["{\"name\":"++q k++",\"value\":"++ if v then "true}" else "false}" | (k,v) <- sort xs] ++ "]"
    pairs xs = "[" ++ intercalate "," ["{\"module\":"++q m++",\"abi\":"++q a++"}" | (m,a) <- sort xs] ++ "]"
    order = map (:[]) (reverse [pn p ++ "-" ++ pv p | p <- pkgs])
    arr xs = "[" ++ intercalate "," (map q xs) ++ "]"
    arr2 xs = "[" ++ intercalate "," (map arr xs) ++ "]"
    q s = show s
